"""Веб-интерфейс: страница настроек, результаты, запуск поиска."""
import json
from pathlib import Path
from urllib.parse import quote

from fastapi import FastAPI, Request, UploadFile
from fastapi.responses import JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from jobsearch import config, db, discovery, llm, notify, pipeline, scheduler, scoring

BASE = Path(__file__).resolve().parent
app = FastAPI(title="AI Job Search")
app.mount("/static", StaticFiles(directory=BASE / "static"), name="static")
templates = Jinja2Templates(directory=BASE / "templates")


@app.on_event("startup")
def startup() -> None:
    db.init()
    scheduler.start()
    scheduler.reschedule(config.load())


def _redirect(msg: str = "") -> RedirectResponse:
    url = f"/?msg={quote(msg)}" if msg else "/"
    return RedirectResponse(url, status_code=303)


@app.get("/")
def index(request: Request, msg: str = ""):
    cfg = config.load()
    next_run = scheduler.next_run_time()
    return templates.TemplateResponse(request, "index.html", {
        "cfg": cfg,
        "msg": msg,
        "cv": config.cv_meta(),
        "runs": db.recent_runs(8),
        "state": pipeline.state,
        "next_run": next_run.strftime("%Y-%m-%d %H:%M") if next_run else None,
        "companies_text": "\n".join(
            f"{c.get('name', '')} | {c.get('url', '')}".strip(" |")
            for c in cfg["sources"].get("companies", [])
        ),
    })


@app.post("/save")
async def save(request: Request, then: str = ""):
    form = await request.form()
    cfg = config.load()

    def val(name, default=""):
        return str(form.get(name, default)).strip()

    def flag(name):
        return name in form

    def num(name, default):
        try:
            return int(val(name) or default)
        except ValueError:
            return default

    cfg["profile"].update(
        summary=val("summary"), roles=val("roles"), seniority=val("seniority"),
        salary=val("salary"), work_format=val("work_format", "any"),
        languages=val("languages"), visa_required=flag("visa_required"),
        visa_note=val("visa_note"), email=val("email"),
        telegram=val("telegram_user"), linkedin=val("linkedin"),
    )
    cfg["search"].update(
        locations=val("locations"), threshold=max(0, min(100, num("threshold", 70))),
        keywords_include=val("keywords_include"), keywords_exclude=val("keywords_exclude"),
        include_remote=flag("include_remote"),
        triage_limit=max(5, num("triage_limit", 400)),
        deep_top_n=max(0, num("deep_top_n", 15)),
        discover_per_run=max(0, num("discover_per_run", 5)),
        parallelism=max(1, min(10, num("parallelism", 5))),
    )
    companies = []
    for line in val("companies").splitlines():
        line = line.strip()
        if not line:
            continue
        if "|" in line:
            name, _, url = line.partition("|")
        else:
            name, url = "", line
        companies.append({"name": name.strip(), "url": url.strip()})
    cfg["sources"].update(
        companies=companies,
        use_remotive=flag("use_remotive"), use_arbeitnow=flag("use_arbeitnow"),
        use_hnhiring=flag("use_hnhiring"),
        adzuna_app_id=val("adzuna_app_id"), adzuna_app_key=val("adzuna_app_key"),
        adzuna_countries=val("adzuna_countries"), jooble_key=val("jooble_key"),
    )
    cfg["llm"].update(
        claude_bin=val("claude_bin", "claude") or "claude",
        triage_model=val("triage_model", "haiku"),
        deep_model=val("deep_model"),
    )
    cfg["telegram"].update(bot_token=val("bot_token"), chat_id=val("chat_id"))
    cfg["schedule"].update(
        enabled=flag("schedule_enabled"),
        every_value=max(1, num("every_value", 1)),
        every_unit=val("every_unit", "days"),
    )
    config.save(cfg)
    scheduler.reschedule(cfg)

    if then == "tg_detect":
        token = cfg["telegram"].get("bot_token", "")
        if not token:
            return _redirect("Сохранено. Вставьте bot token и попробуйте снова")
        try:
            chat_id = notify.detect_chat_id(token)
        except RuntimeError as e:
            return _redirect(f"Сохранено. Telegram: {e}")
        cfg["telegram"]["chat_id"] = chat_id
        config.save(cfg)
        return _redirect(f"Сохранено. Найден chat id: {chat_id}")
    if then == "tg_test":
        try:
            notify.send_message(cfg, "✅ ai-job-search: тестовое сообщение. Бот настроен.")
        except RuntimeError as e:
            return _redirect(f"Сохранено. Telegram: {e}")
        return _redirect("Сохранено. Тестовое сообщение отправлено")
    if then == "discover":
        try:
            fresh = discovery.discover(cfg, lambda m: None, n=max(3, int(cfg["search"].get("discover_per_run") or 5)))
        except llm.ClaudeError as e:
            return _redirect(f"Сохранено. Поиск компаний: {e}")
        if not fresh:
            return _redirect("Сохранено. Новых компаний не нашлось — попробуйте ещё раз")
        cfg["sources"]["companies"] = cfg["sources"].get("companies", []) + fresh
        config.save(cfg)
        names = ", ".join(f["name"] for f in fresh)
        return _redirect(f"Добавлены компании: {names}")
    if then == "profile_from_cv":
        cv = config.cv_text()
        if not cv:
            return _redirect("Сохранено. Сначала загрузите CV")
        try:
            cfg = scoring.profile_from_cv(cfg, cv)
        except llm.ClaudeError as e:
            return _redirect(f"Сохранено. LLM: {e}")
        config.save(cfg)
        return _redirect("Пустые поля профиля заполнены из CV — проверьте и поправьте")
    return _redirect("Настройки сохранены")


@app.post("/upload_cv")
async def upload_cv(file: UploadFile):
    if not file.filename:
        return _redirect("Файл не выбран")
    raw = await file.read()
    try:
        text = config.save_cv(file.filename, raw)
    except Exception as e:  # noqa: BLE001
        return _redirect(f"Не удалось прочитать CV: {e}")
    return _redirect(f"CV загружено: {file.filename}, извлечено {len(text)} символов")


@app.post("/run")
def run_now():
    if pipeline.run_async("manual"):
        return _redirect("Поиск запущен")
    return _redirect("Поиск уже идёт")


@app.get("/status")
def status():
    next_run = scheduler.next_run_time()
    return JSONResponse({
        "running": pipeline.state["running"],
        "stage": pipeline.state["stage"],
        "log": pipeline.state["log"][-30:],
        "next_run": next_run.strftime("%Y-%m-%d %H:%M") if next_run else None,
    })


@app.get("/results")
def results(request: Request, min: int = 50):
    jobs = db.matched_jobs(min_score=min)
    for j in jobs:
        try:
            j["advice_data"] = json.loads(j["advice"]) if j.get("advice") else None
        except (json.JSONDecodeError, TypeError):
            j["advice_data"] = None
    return templates.TemplateResponse(
        request, "results.html",
        {"jobs": jobs, "runs": db.recent_runs(5), "min_score": min},
    )


@app.get("/coverage")
def coverage(request: Request, run_id: int = 0):
    runs = db.recent_runs(20)
    run = next((r for r in runs if r["id"] == run_id), runs[0] if runs else None)
    entries = []
    if run and run.get("coverage"):
        try:
            entries = json.loads(run["coverage"])
        except (json.JSONDecodeError, TypeError):
            entries = []
    return templates.TemplateResponse(
        request, "coverage.html",
        {"run": run, "runs": runs, "entries": entries,
         "total": sum(e.get("count", 0) for e in entries)},
    )


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="127.0.0.1", port=8765)
