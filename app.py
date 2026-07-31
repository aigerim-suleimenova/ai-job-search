"""Веб-интерфейс: страница настроек, результаты, запуск поиска."""
import json
import sys
import threading
import traceback
import webbrowser
from datetime import date, datetime
from pathlib import Path
from urllib.parse import quote

from fastapi import FastAPI, Request, UploadFile
from fastapi.responses import JSONResponse, RedirectResponse, Response
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from jobsearch import (appstate, autostart, config, coverage as coverage_check,
                       cvcheck, db, discovery, export as export_mod, hardware, i18n,
                       llm, mailer, notify, pipeline, profiles, providers, scheduler,
                       scoring, version)

BASE = Path(__file__).resolve().parent
app = FastAPI(title="AI Job Search")
app.mount("/static", StaticFiles(directory=BASE / "static"), name="static")
templates = Jinja2Templates(directory=BASE / "templates")


@app.middleware("http")
async def _profile_middleware(request: Request, call_next):
    """Активный профиль на время запроса — из cookie (или профиль по умолчанию)."""
    slug = request.cookies.get("profile", "")
    profiles.set_active(slug if profiles.exists(slug) else profiles.default_slug())
    return await call_next(request)


def _provider_status(cfg: dict) -> dict:
    """Готов ли выбранный «мозг» приложения. Без него поиск не заработает, и человек
    должен узнать об этом сразу, а не из ошибки посреди прогона."""
    key = cfg.get("llm", {}).get("provider", "claude_cli")
    provs = providers.available(cfg.get("llm", {}).get("claude_bin", "claude"))
    p = provs.get(key, {})
    return {"key": key, "ready": bool(p.get("ready")), "name": p.get("name", key)}


def _timing() -> dict:
    """Сколько идёт прогон, сколько осталось до конца этапа и сколько он обычно длится.

    Остаток считаем только внутри длинных этапов, где известно «сделано из
    скольких», и по их собственному темпу. Проценты по всему прогону рисовать
    честно нельзя: этапы разные по длине, и пропуск одного сдвинул бы всё.
    """
    import time as _t
    out = {"elapsed_min": 0, "eta_min": 0, "typical_min": 0}
    started = pipeline.state.get("started")
    if started:
        out["elapsed_min"] = max(1, round((_t.time() - started) / 60))
    pr = pipeline.state.get("progress") or {}
    stage_started = pipeline.state.get("stage_started")
    if pr.get("done") and pr.get("total") and stage_started:
        per_item = (_t.time() - stage_started) / pr["done"]
        left = per_item * (pr["total"] - pr["done"])
        if left > 30:
            out["eta_min"] = max(1, round(left / 60))
    done_runs = [r for r in db.recent_runs(6) if r["finished"] and r["status"] == "ok"]
    if done_runs:
        import datetime as _d
        spans = []
        for r in done_runs:
            try:
                a_ = _d.datetime.fromisoformat(r["started"]); b_ = _d.datetime.fromisoformat(r["finished"])
                spans.append((b_ - a_).total_seconds())
            except (TypeError, ValueError):
                pass
        if spans:
            out["typical_min"] = max(1, round(sum(spans) / len(spans) / 60))
    return out


def mine_running() -> bool:
    """Идёт ли прогон именно этого человека."""
    return bool(pipeline.state["running"]
                and pipeline.state.get("profile") == profiles.active())


def _last_run_noauth() -> str:
    """Текст ошибки последнего прогона, если он встал из-за входа в модель."""
    runs = db.recent_runs(1)
    if not runs or runs[0]["status"] != "noauth":
        return ""
    for line in reversed((runs[0]["log"] or "").splitlines()):
        if line.startswith("Модель не отвечает: "):
            return line.split(": ", 1)[1][:200]
    return "—"


def _msg(key: str, **fmt) -> str:
    """Всплывающее сообщение на языке интерфейса, а не всегда по-русски."""
    lang = config.load().get("ui", {}).get("lang", "ru")
    text = i18n.t(lang, key)
    return text.format(**fmt) if fmt else text


def _seed_profile(slug: str) -> None:
    """Новому человеку — тот же CLI и модель, что выбраны при первом запуске."""
    defaults = appstate.default_llm()
    if not defaults:
        return
    prev = profiles.active()
    try:
        profiles.set_active(slug)
        cfg = config.load()
        cfg["llm"].update(defaults)
        config.save(cfg)
    finally:
        profiles.set_active(prev)


def _asset_version() -> str:
    """Метка стилей для адресной строки: меняется, когда меняется файл."""
    try:
        return str(int((BASE / "static" / "style.css").stat().st_mtime))
    except OSError:
        return "1"


def render(request, template: str, ctx: dict, cfg: dict = None):
    """Рендер с языком интерфейса и данными о профилях."""
    cfg_ = cfg or config.load()
    lang = cfg_.get("ui", {}).get("lang", "ru")
    ctx = {**ctx, "provider_status": _provider_status(cfg_), "asset_v": _asset_version(),
           "lang": lang, "t": lambda key: i18n.t(lang, key), "theme": appstate.theme(),
           "app_version": version.current(),
           "rtl": lang in i18n.RTL_LANGS,
           "ui_langs": i18n.UI_LANGS, "output_langs": i18n.OUTPUT_LANGS,
           "profiles": profiles.list_profiles(), "active_profile": profiles.active(),
           "active_name": profiles.name_of(profiles.active())}
    return templates.TemplateResponse(request, template, ctx)


def _log_startup_problem(шаг: str, details: str) -> None:
    try:
        with LOG_PATH.open("a", encoding="utf-8") as f:
            f.write(f"\n=== {datetime.now():%Y-%m-%d %H:%M:%S} — шаг запуска «{шаг}» не удался\n"
                    f"{details}\n")
    except OSError:
        pass


@app.on_event("startup")
def startup() -> None:
    """Ни один из этих шагов не обязателен, чтобы человек увидел окно.

    Раньше исключение здесь роняло весь запуск: uvicorn выходил с кодом 3,
    поток сервера умирал, окно не открывалось — и всё это из-за расписания,
    без которого программа прекрасно работает. Теперь каждый шаг отвечает
    сам за себя, а причина попадает в журнал.
    """
    for шаг, действие in (("перенос профилей", profiles.ensure_migrated),
                          ("запуск расписания", scheduler.start),
                          ("восстановление расписаний", scheduler.reschedule_all)):
        try:
            действие()
        except Exception:  # noqa: BLE001 — любая беда здесь не повод не открыться
            _log_startup_problem(шаг, traceback.format_exc())


def _redirect(msg: str = "") -> RedirectResponse:
    url = f"/?msg={quote(msg)}" if msg else "/"
    return RedirectResponse(url, status_code=303)


def _redirect_to(path: str, msg: str = "") -> RedirectResponse:
    url = f"{path}?msg={quote(msg)}" if msg else path
    return RedirectResponse(url, status_code=303)


@app.get("/")
def index(request: Request, msg: str = ""):
    cfg = config.load()
    next_run = scheduler.next_run_time()
    # статус показываем как «идёт», только если текущий прогон про этот профиль
    mine = pipeline.state["running"] and pipeline.state.get("profile") == profiles.active()
    state_view = {"running": mine, "step": pipeline.state.get("step", 0),
                  "steps": pipeline.STAGE_COUNT,
                  "stage": _msg(pipeline.state["stage"]) if mine and pipeline.state["stage"] else ""}
    busy = pipeline.state.get("profile", "")
    return render(request, "index.html", {
        "busy_with": profiles.name_of(busy) if (pipeline.state["running"] and not mine) else "",
        "cfg": cfg,
        "msg": msg,
        "autostart_on": autostart.enabled(),
        "autostart_supported": autostart.supported(),
        "is_app": bool(getattr(sys, "frozen", False)),
        "cv": config.cv_meta(),
        "runs": db.recent_runs(8),
        "state": state_view,
        "continuous": cfg["schedule"].get("mode") == "continuous",
        "next_run": next_run.strftime("%Y-%m-%d %H:%M") if next_run else None,
        "companies_text": "\n".join(
            f"{c.get('name', '')} | {c.get('url', '')}".strip(" |")
            for c in cfg["sources"].get("companies", [])
        ),
    }, cfg=cfg)


@app.post("/profile/switch")
async def profile_switch(request: Request):
    form = await request.form()
    slug = str(form.get("slug", "")).strip()
    resp = RedirectResponse("/", status_code=303)
    if profiles.exists(slug):
        resp.set_cookie("profile", slug, max_age=60 * 60 * 24 * 365, httponly=True, samesite="lax")
    return resp


@app.post("/profile/create")
async def profile_create(request: Request):
    form = await request.form()
    name = str(form.get("name", "")).strip()
    if not name:
        return _redirect(_msg("msg_person_name_needed"))
    slug = profiles.create(name)
    _seed_profile(slug)
    resp = _redirect(_msg("msg_person_created", name=name))
    resp.set_cookie("profile", slug, max_age=60 * 60 * 24 * 365, httponly=True, samesite="lax")
    return resp


@app.post("/profile/rename")
async def profile_rename(request: Request):
    form = await request.form()
    name = str(form.get("name", "")).strip()
    if name:
        profiles.rename(profiles.active(), name)
    return _redirect(_msg("msg_person_renamed"))


@app.post("/profile/delete")
async def profile_delete(request: Request):
    form = await request.form()
    slug = str(form.get("slug", "")).strip()
    if profiles.exists(slug) and len(profiles.list_profiles()) > 1:
        profiles.delete(slug)
        scheduler.reschedule_all()
        resp = RedirectResponse("/", status_code=303)
        resp.set_cookie("profile", profiles.default_slug(), max_age=60 * 60 * 24 * 365,
                        httponly=True, samesite="lax")
        return resp
    return _redirect(_msg("msg_person_last"))


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

    # Форма настроек раньше перезаписывала конфиг целиком, поэтому любая частичная
    # отправка стирала остальное — включая компании, которые прямо сейчас мог
    # добавить идущий прогон. Теперь каждая секция обновляется, только если её
    # поля действительно пришли.
    def section_present(*fields):
        return any(f in form for f in fields)

    if section_present("roles", "summary", "skills"):
        cfg["profile"].update(
            summary=val("summary"), roles=val("roles"), skills=val("skills"),
            seniority=val("seniority"),
            salary=val("salary"), work_format=val("work_format", "any"),
            languages=val("languages"), visa_required=flag("visa_required"),
            visa_note=val("visa_note"), email=val("email"),
            telegram=val("telegram_user"), linkedin=val("linkedin"),
        )
    prio = val("match_priority", "both")
    if section_present("locations", "threshold", "match_priority"):
        cfg["search"].update(
            locations=val("locations"), threshold=max(0, min(100, num("threshold", 70))),
            match_priority=prio if prio in ("role", "skills", "both") else "both",
            drop_off_target=flag("drop_off_target"),
            triage_second_vote=flag("triage_second_vote"),
            keywords_include=val("keywords_include"), keywords_exclude=val("keywords_exclude"),
            include_remote=flag("include_remote"),
            triage_limit=max(5, num("triage_limit", 400)),
            deep_top_n=max(0, num("deep_top_n", 15)),
            discover_per_run=max(0, num("discover_per_run", 5)),
            discover_ats_per_run=max(0, num("discover_ats_per_run", 5)),
            parallelism=max(1, min(10, num("parallelism", 5))),
            deep_during_run=flag("deep_during_run"),
            research_company=flag("research_company"),
        )
    if section_present("companies", "use_remotive", "adzuna_app_id"):
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
            use_wwr=flag("use_wwr"), use_hnhiring=flag("use_hnhiring"),
            use_remoteok=flag("use_remoteok"), use_jobicy=flag("use_jobicy"),
            use_himalayas=flag("use_himalayas"), use_themuse=flag("use_themuse"),
            use_arbeitsagentur=flag("use_arbeitsagentur"),
            adzuna_app_id=val("adzuna_app_id"), adzuna_app_key=val("adzuna_app_key"),
            adzuna_countries=val("adzuna_countries"), jooble_key=val("jooble_key"),
        )
    if section_present("claude_bin", "triage_model", "deep_model"):
        cfg["llm"].update(
            claude_bin=val("claude_bin", "claude") or "claude",
            triage_model=val("triage_model", "haiku"),
            deep_model=val("deep_model"),
        )
    if section_present("bot_token", "chat_id"):
        cfg["telegram"].update(bot_token=val("bot_token"), chat_id=val("chat_id"))
    if section_present("schedule_mode", "every_value", "continuous_cooldown_min"):
        mode = val("schedule_mode", "off")
        cfg["schedule"].update(
            mode=mode if mode in ("off", "interval", "continuous") else "off",
            every_value=max(1, num("every_value", 1)),
            every_unit=val("every_unit", "days"),
            continuous_cooldown_min=max(1, num("continuous_cooldown_min", 20)),
        )
    cfg["schedule"].pop("enabled", None)
    if section_present("ui_lang", "output_lang"):
        ui_lang = val("ui_lang", "ru")
        out_lang = val("output_lang", "ru")
        cfg["ui"].update(
            lang=ui_lang if ui_lang in i18n.UI_LANGS else "ru",
            output_lang=out_lang if out_lang in i18n.OUTPUT_LANGS else "ru",
        )
    config.save(cfg)
    scheduler.reschedule(cfg)

    if then == "tg_detect":
        token = cfg["telegram"].get("bot_token", "")
        if not token:
            return _redirect(_msg("msg_saved_need_token"))
        try:
            chat_id = notify.detect_chat_id(token)
        except RuntimeError as e:
            return _redirect(_msg("msg_saved_tg_error", error=e))
        cfg["telegram"]["chat_id"] = chat_id
        config.save(cfg)
        return _redirect(_msg("msg_saved_chat_found", chat_id=chat_id))
    if then == "tg_test":
        try:
            notify.send_message(cfg, _msg("tg_test_message"))
        except RuntimeError as e:
            return _redirect(_msg("msg_saved_tg_error", error=e))
        return _redirect(_msg("msg_saved_tg_sent"))
    if then == "discover":
        try:
            fresh = discovery.discover(cfg, lambda m: None, n=max(3, int(cfg["search"].get("discover_per_run") or 5)))
        except llm.ClaudeError as e:
            return _redirect(_msg("msg_saved_discover_error", error=e))
        if not fresh:
            return _redirect(_msg("msg_saved_discover_none"))
        cfg["sources"]["companies"] = cfg["sources"].get("companies", []) + fresh
        config.save(cfg)
        names = ", ".join(f["name"] for f in fresh)
        return _redirect(_msg("msg_companies_added", names=names))
    if then == "profile_from_cv":
        cv = config.cv_text()
        if not cv:
            return _redirect(_msg("msg_saved_need_cv"))
        try:
            cfg = scoring.profile_from_cv(cfg, cv)
        except llm.ClaudeError as e:
            return _redirect(_msg("msg_saved_llm_error", error=e))
        config.save(cfg)
        return _redirect(_msg("msg_profile_from_cv"))
    return _redirect(_msg("msg_settings_saved"))


@app.get("/simple")
def simple(request: Request, msg: str = ""):
    cfg = config.load()
    mine = pipeline.state["running"] and pipeline.state.get("profile") == profiles.active()
    return render(request, "simple.html", {
        "cfg": cfg, "msg": msg, "cv": config.cv_meta(),
        "state": {"running": mine, "step": pipeline.state.get("step", 0),
                  "steps": pipeline.STAGE_COUNT,
                  "stage": _msg(pipeline.state["stage"]) if mine and pipeline.state["stage"] else ""},
        "found": db.counts(int(cfg["search"].get("threshold") or 70))["total"],
        "noauth": _last_run_noauth(),
    }, cfg=cfg)


@app.post("/simple/start")
async def simple_start(request: Request, file: UploadFile = None):
    """Простой сценарий: имя, регион, CV, LinkedIn — остальное заполняется само."""
    form = await request.form()
    person = str(form.get("person", "")).strip()
    # Имя нового человека — заводим отдельный профиль. Но если человек с таким
    # именем уже есть, переключаемся на него: иначе повторный ввод плодил бы
    # «Друг», «Друг-2», «Друг-3» с раздельными результатами.
    if person and person != profiles.name_of(profiles.active()):
        existing = next((p["slug"] for p in profiles.list_profiles()
                         if p["name"].strip().casefold() == person.casefold()), "")
        slug = existing or profiles.create(person)
        if not existing:
            _seed_profile(slug)
        profiles.set_active(slug)
    else:
        slug = profiles.active()

    if file is not None and file.filename:
        raw = await file.read()
        try:
            config.save_cv(file.filename, raw)
        except config.CVError as e:
            return _redirect_to("/simple", _msg(e.key, **e.fmt))
        except Exception as e:  # noqa: BLE001
            return _redirect_to("/simple", _msg("msg_cv_unreadable", error=e))

    cfg = config.load()
    cfg["search"]["locations"] = str(form.get("locations", "")).strip() or cfg["search"]["locations"]
    cfg["profile"]["linkedin"] = str(form.get("linkedin", "")).strip()
    config.save(cfg)

    if not config.cv_text():
        return _redirect_to("/simple", _msg("msg_cv_needed"))

    # Разбор резюме и сам поиск уходят в фон: страница возвращается сразу,
    # а ход работы виден в строке статуса и в журнале.
    started = pipeline.prepare_and_run(slug, trigger="manual")
    if started:
        note = _msg("msg_search_started")
    else:
        busy = pipeline.state.get("profile", "")
        note = (_msg("msg_busy_next", name=profiles.name_of(busy))
                if busy and busy != slug else _msg("msg_search_already"))
    resp = _redirect_to("/simple", note)
    resp.set_cookie("profile", slug, max_age=60 * 60 * 24 * 365, httponly=True, samesite="lax")
    return resp


@app.post("/upload_cv")
async def upload_cv(file: UploadFile):
    if not file.filename:
        return _redirect(_msg("msg_no_file"))
    raw = await file.read()
    try:
        text = config.save_cv(file.filename, raw)
    except config.CVError as e:
        return _redirect(_msg(e.key, **e.fmt))
    except Exception as e:  # noqa: BLE001
        return _redirect(_msg("msg_cv_unreadable", error=e))
    return _redirect(_msg("msg_cv_uploaded", name=file.filename, chars=len(text)))


@app.post("/run")
def run_now():
    if pipeline.run_async("manual", profile=profiles.active()):
        return _redirect(_msg("msg_search_started"))
    # Пайплайн один на всех: если занят чужим прогоном, человек должен понимать,
    # что ждёт не своих результатов, а очереди.
    busy = pipeline.state.get("profile", "")
    if busy and busy != profiles.active():
        return _redirect(_msg("msg_busy_wait", name=profiles.name_of(busy)))
    return _redirect(_msg("msg_search_already"))


@app.post("/stop")
def stop_now():
    """Останавливаем только прогон этого человека: в приложении несколько профилей,
    и один пайплайн — иначе кнопка на чужой странице обрывала бы чужой поиск."""
    if not pipeline.state["running"]:
        return _redirect(_msg("msg_not_running"))
    if pipeline.state.get("profile") != profiles.active():
        who = profiles.name_of(pipeline.state.get("profile", ""))
        return _redirect(_msg("msg_busy_switch", name=who))
    pipeline.request_stop()
    return _redirect(_msg("msg_stopping"))


@app.get("/status")
def status(min: int = -1):
    next_run = scheduler.next_run_time()
    # прогон идёт «здесь» только если он про активный профиль
    mine = pipeline.state["running"] and pipeline.state.get("profile") == profiles.active()
    busy_with = ""
    if pipeline.state["running"] and not mine:
        busy_with = profiles.name_of(pipeline.state.get("profile", ""))
    return JSONResponse({
        "running": mine,
        "busy_with": busy_with,
        "stopping": pipeline.stop_requested() and mine,
        "stage": _msg(pipeline.state["stage"]) if mine and pipeline.state["stage"] else "",
        "step": pipeline.state.get("step", 0) if mine else 0,
        "steps": pipeline.STAGE_COUNT,
        **(_timing() if mine else {"elapsed_min": 0, "eta_min": 0, "typical_min": 0}),
        "found": db.counts(min if min >= 0 else
                           int(config.load()["search"].get("threshold") or 70))["total"],
        "log": pipeline.state["log"][-30:] if mine else [],
        "next_run": next_run.strftime("%Y-%m-%d %H:%M") if next_run else None,
    })


def _posted_label(posted: str, lang: str) -> str:
    """'2026-07-10' → 'опубл. 4 дн. назад (2026-07-10)' / 'posted 4d ago (…)'."""
    if not posted:
        return ""
    try:
        days = (date.today() - date.fromisoformat(posted)).days
    except ValueError:
        return posted
    today = {"ru": "сегодня", "en": "today", "it": "oggi", "de": "heute"}
    yesterday = {"ru": "вчера", "en": "yesterday", "it": "ieri", "de": "gestern"}
    ago = {"ru": f"{days} дн. назад", "en": f"{days}d ago",
           "it": f"{days} giorni fa", "de": f"vor {days} Tagen"}
    if days <= 0:
        rel = today.get(lang, today["en"])
    elif days == 1:
        rel = yesterday.get(lang, yesterday["en"])
    else:
        rel = ago.get(lang, ago["en"])
    return f"{rel} ({posted})"


@app.get("/results")
def results(request: Request, min: int = 50, sort: str = "default",
            viewed: str = "all", source: str = "all", run: int = 0,
            posted_from: str = "", posted_to: str = "", msg: str = ""):
    cfg = config.load()
    lang = cfg.get("ui", {}).get("lang", "ru")
    jobs = db.matched_jobs(min_score=min, sort=sort, viewed=viewed, source=source, run_id=run,
                           posted_from=posted_from, posted_to=posted_to)
    for j in jobs:
        try:
            j["advice_data"] = json.loads(j["advice"]) if j.get("advice") else None
        except (json.JSONDecodeError, TypeError):
            j["advice_data"] = None
        j["posted_label"] = _posted_label(j.get("posted_at") or "", lang)
    threshold = int(cfg["search"].get("threshold", 70))
    suggest = db.suggest_threshold(threshold)
    runs = db.recent_runs(10)
    return render(
        request, "results.html",
        {"jobs": jobs, "runs": runs, "min_score": min,
         "threshold": threshold, "suggest_threshold": suggest,
         "sort": sort, "viewed": viewed, "source": source, "run": run,
         "posted_from": posted_from, "posted_to": posted_to, "msg": msg,
         "analysing": [int(k.split(":", 1)[1]) for k, v in _deep_state.items()
                       if k.startswith(f"{profiles.active()}:") and v.get("running")],
         "counts": db.counts(min), "sorts": list(db.SORTS.keys()),
         "noauth": _last_run_noauth(),
         "state": {"running": mine_running(), "stage": _msg(pipeline.state["stage"])
                   if mine_running() and pipeline.state["stage"] else "",
                   "step": pipeline.state.get("step", 0), "steps": pipeline.STAGE_COUNT}},
        cfg=cfg,
    )


@app.post("/viewed/{job_id}")
async def toggle_viewed(job_id: int, request: Request):
    form = await request.form()
    db.set_viewed(job_id, str(form.get("value", "1")) == "1")
    back = str(form.get("back", "/results"))
    return RedirectResponse(back if back.startswith("/") else "/results", status_code=303)


@app.post("/viewed_all")
async def viewed_all(request: Request):
    form = await request.form()
    try:
        min_score = int(str(form.get("min", "0")))
    except ValueError:
        min_score = 0
    db.mark_all_viewed(min_score)
    back = str(form.get("back", "/results"))
    return RedirectResponse(back if back.startswith("/") else "/results", status_code=303)


# Разбор одной вакансии по кнопке. Модель думает минуту-другую — держать
# страницу всё это время нельзя (WebKit сам оборвёт запрос), поэтому разбор идёт
# в фоне, а страница опрашивает /analyse/status и перерисовывается, когда готово.
_deep_state: dict[str, dict] = {}   # «профиль:id вакансии» → что с ней сейчас


def _deep_key(job_id: int) -> str:
    return f"{profiles.active()}:{job_id}"


@app.post("/analyse/{job_id}")
async def analyse_job(job_id: int, request: Request):
    form = await request.form()
    back = str(form.get("back", "/results"))
    if not back.startswith("/"):
        back = "/results"
    job = db.get_job(job_id)
    if not job:
        return RedirectResponse(back, status_code=303)
    key = _deep_key(job_id)
    if _deep_state.get(key, {}).get("running"):
        return RedirectResponse(back, status_code=303)
    cfg, cv, profile = config.load(), config.cv_text(), profiles.active()
    _deep_state[key] = {"running": True, "error": ""}

    def worker():
        profiles.set_active(profile)
        notes: list = []
        try:
            scoring.deep_analyze(job, cfg, cv, notes.append,
                                 research=bool(cfg["search"].get("research_company", True)))
            if not job.get("verified"):
                # deep_analyze не бросает исключение, а пишет причину в журнал
                raise RuntimeError(notes[-1] if notes else _msg("msg_analyse_failed",
                                                                title=job.get("title", "")))
            db.save_job(job, int(job.get("run_id") or 0))
            _deep_state[key] = {"running": False, "error": "",
                                "msg": _msg("msg_analysed", title=job.get("title", ""),
                                            score=job.get("score", 0))}
        except Exception as e:
            _deep_state[key] = {"running": False, "error": str(e)}

    threading.Thread(target=worker, daemon=True).start()
    return RedirectResponse(back, status_code=303)


@app.get("/analyse/status")
def analyse_status():
    """Какие вакансии этого человека сейчас разбираются, а какие уже готовы."""
    prefix = f"{profiles.active()}:"
    running, done, failed = [], {}, {}
    for key, st in list(_deep_state.items()):
        if not key.startswith(prefix):
            continue
        job_id = key.split(":", 1)[1]
        if st.get("running"):
            running.append(int(job_id))
        elif st.get("error"):
            failed[job_id] = st["error"]
        else:
            done[job_id] = st.get("msg", "")
    return JSONResponse({"running": running, "done": done, "failed": failed})


@app.get("/notify")
def notify_page(request: Request, msg: str = ""):
    cfg = config.load()
    return render(request, "notify.html", {
        "cfg": cfg, "msg": msg, "presets": mailer.PRESETS,
        "presets_json": json.dumps(mailer.PRESETS, ensure_ascii=False),
    }, cfg=cfg)


@app.post("/notify/telegram")
async def notify_telegram(request: Request, then: str = ""):
    form = await request.form()
    cfg = config.load()
    cfg["telegram"].update(bot_token=str(form.get("bot_token", "")).strip(),
                           chat_id=str(form.get("chat_id", "")).strip())
    config.save(cfg)
    if then == "detect":
        if not cfg["telegram"]["bot_token"]:
            return _redirect_to("/notify", _msg("msg_need_token"))
        try:
            chat_id = notify.detect_chat_id(cfg["telegram"]["bot_token"])
        except RuntimeError as e:
            return _redirect_to("/notify", _msg("msg_tg_error", error=e))
        cfg["telegram"]["chat_id"] = chat_id
        config.save(cfg)
        return _redirect_to("/notify", _msg("msg_chat_found", chat_id=chat_id))
    if then == "test":
        try:
            notify.send_message(cfg, _msg("tg_test_message"))
        except RuntimeError as e:
            return _redirect_to("/notify", f"Telegram: {e}")
        return _redirect_to("/notify", _msg("msg_tg_sent"))
    return _redirect_to("/notify", _msg("msg_saved"))


@app.post("/notify/email")
async def notify_email(request: Request, then: str = ""):
    form = await request.form()
    cfg = config.load()
    preset = str(form.get("preset", "gmail"))
    try:
        port = int(str(form.get("port", "587")) or 587)
    except ValueError:
        port = 587
    cfg["email"].update(
        enabled="enabled" in form, preset=preset,
        host=str(form.get("host", "")).strip(), port=port, tls="tls" in form,
        username=str(form.get("username", "")).strip(),
        password=str(form.get("password", "")),
        to=str(form.get("to", "")).strip(),
    )
    config.save(cfg)
    if then == "test":
        name = profiles.name_of(profiles.active())
        html = f"<p>{_msg('mail_test_subject')}</p><p>{_msg('mail_test_body', name=name)}</p>"
        try:
            mailer.send(cfg, _msg("mail_test_subject"), html,
                        _msg("mail_test_body", name=name))
        except mailer.MailError as e:
            return _redirect_to("/notify", _msg(e.key, **e.fmt))
        return _redirect_to("/notify", _msg("msg_mail_sent"))
    return _redirect_to("/notify", _msg("msg_saved"))


@app.get("/models")
def models_page(request: Request, msg: str = ""):
    cfg = config.load()
    provider = cfg["llm"].get("provider", "claude_cli")
    provs = providers.available(cfg["llm"].get("claude_bin", "claude"))
    current_model = cfg["llm"].get("triage_model", "haiku")
    catalog = providers.models_for(provider)
    return render(request, "models.html", {
        "cfg": cfg, "msg": msg, "provs": provs, "current_provider": provider,
        "current_model": current_model, "models": catalog,
        "current_model_name": next((m["name"] for m in catalog if m["id"] == current_model), current_model),
        "specs": hardware.specs(),
        "provider_ready": bool(provs.get(provider, {}).get("ready")),
    }, cfg=cfg)


@app.post("/models/provider")
async def models_set_provider(request: Request):
    form = await request.form()
    key = str(form.get("provider", "claude_cli"))
    cfg = config.load()
    if key in providers.available(cfg["llm"].get("claude_bin", "claude")):
        cfg["llm"]["provider"] = key
        # модель прежнего провайдера бессмысленна для нового — берём самую сильную доступную
        catalog = providers.models_for(key)
        picks = [m for m in catalog if m.get("kind") != "local" or m.get("installed")]
        # ничего не установлено — намечаем самую сильную модель, которая влезет
        # в память: иначе в настройках осталась бы модель прежнего способа
        picks = picks or [m for m in catalog if m.get("fits") in ("yes", "tight")] or catalog
        if picks:
            cfg["llm"]["triage_model"] = picks[0]["id"]
            cfg["llm"]["deep_model"] = picks[0]["id"]
        config.save(cfg)
    return _redirect_to(str(form.get("back") or "/models"),
                        _msg("msg_provider_set", provider=i18n.t(
                            config.load().get("ui", {}).get("lang", "ru"), "prov_" + key)))


@app.post("/models/select")
async def models_select(request: Request):
    form = await request.form()
    model = str(form.get("model", "")).strip()
    cfg = config.load()
    cfg["llm"]["triage_model"] = model
    cfg["llm"]["deep_model"] = model
    config.save(cfg)
    return _redirect_to(str(form.get("back") or "/models"), _msg("msg_model_set", model=model))


@app.post("/models/pull")
async def models_pull(request: Request):
    """Скачивание идёт в фоне: модель весит гигабайты, ждать ответа страницы нельзя."""
    form = await request.form()
    model = str(form.get("model", "")).strip()
    if providers.pull_in_progress():
        return _redirect_to(str(form.get("back") or "/models"), _msg("msg_pull_busy"))
    providers.pull_async(model)
    return _redirect_to(str(form.get("back") or "/models"), _msg("msg_pull_started", model=model))


@app.get("/models/pull_status")
def models_pull_status():
    return JSONResponse(providers.pull_status())


@app.get("/cv/check")
def cv_check(request: Request, run: int = 0):
    cfg = config.load()
    result = cvcheck.analyze(cfg) if run else cvcheck.last_result()
    return render(request, "cvcheck.html",
                  {"result": result, "cv": config.cv_meta()}, cfg=cfg)


@app.post("/app_settings")
async def app_settings(request: Request):
    """Поведение самой программы: автозапуск и работа в фоне."""
    form = await request.form()
    cfg = config.load()
    cfg["ui"]["background"] = "background" in form
    config.save(cfg)
    err = autostart.set_enabled("autostart" in form)
    return _redirect(_msg(err) if err else _msg("msg_app_settings_saved"))


@app.post("/set_lang")
async def set_lang(request: Request):
    form = await request.form()
    code = str(form.get("ui_lang", "ru"))
    cfg = config.load()
    cfg["ui"]["lang"] = code if code in i18n.UI_LANGS else "ru"
    config.save(cfg)
    back = str(form.get("back", "/"))
    return RedirectResponse(back if back.startswith("/") else "/", status_code=303)


@app.post("/set_theme")
async def set_theme(request: Request):
    """Оформление общее на всё приложение: это про экран, а не про человека."""
    form = await request.form()
    appstate.set_theme(str(form.get("theme", "auto")))
    back = str(form.get("back", "/"))
    return RedirectResponse(back if back.startswith("/") else "/", status_code=303)


def _export_jobs(min_score: int, sort: str = "default", viewed: str = "all",
                 source: str = "all", run: int = 0,
                 posted_from: str = "", posted_to: str = ""):
    """Та же выборка, что и на странице результатов — экспорт обязан совпадать с тем,
    что человек видит на экране."""
    jobs = db.matched_jobs(limit=1000, min_score=min_score, sort=sort,
                           viewed=viewed, source=source, run_id=run,
                           posted_from=posted_from, posted_to=posted_to)
    slug = profiles.active()
    name = profiles.name_of(slug)
    safe = "".join(ch if ch.isalnum() or ch in "-_" else "_" for ch in slug)[:40]
    return jobs, name, safe


def _filter_note(lang: str, min_score: int, sort: str, viewed: str, source: str, run: int,
                 posted_from: str = "", posted_to: str = "") -> str:
    """Подпись «что именно выгружено» — чтобы файл нельзя было принять за полный список."""
    parts = [f"{i18n.t(lang, 'results_shown')} {min_score}%"]
    if sort != "default":
        parts.append(f"{i18n.t(lang, 'sort_by')}: {i18n.t(lang, 'sort_' + sort)}")
    if viewed != "all":
        parts.append(f"{i18n.t(lang, 'filter_viewed')}: {i18n.t(lang, 'viewed_' + viewed)}")
    if posted_from or posted_to:
        parts.append(f"{i18n.t(lang, 'filter_posted_from')} {posted_from or '…'} "
                     f"{i18n.t(lang, 'filter_posted_to')} {posted_to or '…'}")
    if source != "all":
        key = {"direct": "badge_direct", "agency": "badge_agency", "aggregator": "badge_aggregator"}[source]
        parts.append(f"{i18n.t(lang, 'filter_source')}: {i18n.t(lang, key)}")
    if run:
        parts.append(f"{i18n.t(lang, 'filter_run')}: #{run}")
    return " · ".join(parts)


@app.get("/export/csv")
def export_csv(min: int = 0, sort: str = "default", viewed: str = "all",
               source: str = "all", run: int = 0,
               posted_from: str = "", posted_to: str = ""):
    jobs, name, safe = _export_jobs(min, sort, viewed, source, run, posted_from, posted_to)
    data = export_mod.to_csv(jobs)
    return Response(
        content=data, media_type="text/csv; charset=utf-8",
        headers={"Content-Disposition": f'attachment; filename="jobs_{safe}.csv"'},
    )


@app.get("/export/report")
def export_report(min: int = 0, sort: str = "default", viewed: str = "all",
                  source: str = "all", run: int = 0,
                  posted_from: str = "", posted_to: str = ""):
    cfg = config.load()
    lang = cfg.get("ui", {}).get("lang", "ru")
    jobs, name, safe = _export_jobs(min, sort, viewed, source, run, posted_from, posted_to)
    for j in jobs:
        j["posted_label"] = _posted_label(j.get("posted_at") or "", lang)
    html_doc = export_mod.to_html(jobs, name, db.now(), min,
                                  note=_filter_note(lang, min, sort, viewed, source, run,
                                                    posted_from, posted_to))
    return Response(
        content=html_doc, media_type="text/html; charset=utf-8",
        headers={"Content-Disposition": f'attachment; filename="report_{safe}.html"'},
    )


@app.get("/cv/{job_id}")
def tailored_cv(job_id: int):
    job = db.get_job(job_id)
    if not job:
        return Response(content="Job not found", status_code=404)
    cv_data = None
    if job.get("tailored_cv"):
        try:
            cv_data = json.loads(job["tailored_cv"])
        except (json.JSONDecodeError, TypeError):
            cv_data = None
    if not cv_data:
        cfg = config.load()
        source = config.cv_text()
        if not source.strip():
            # без исходного резюме модель сочиняла документ с «<MISSING>» вместо отказа
            return Response(content=f"<p style='font:15px -apple-system;padding:32px'>"
                                    f"{_msg('cv_no_source')}</p>",
                            media_type="text/html", status_code=200)
        cv_data = scoring.generate_cv(job, cfg, source)
        if cv_data:
            db.save_tailored_cv(job_id, json.dumps(cv_data, ensure_ascii=False))
    if not cv_data:
        return Response(content="Could not generate CV — try again.", status_code=502)
    html_doc = export_mod.cv_html(cv_data, job.get("title", ""), job.get("company", ""))
    return Response(content=html_doc, media_type="text/html; charset=utf-8")


@app.post("/set_threshold")
async def set_threshold(request: Request):
    form = await request.form()
    try:
        v = int(str(form.get("value", "")))
    except ValueError:
        return RedirectResponse("/results", status_code=303)
    cfg = config.load()
    cfg["search"]["threshold"] = max(0, min(100, v))
    config.save(cfg)
    return RedirectResponse(f"/results?min={max(0, min(100, v))}", status_code=303)


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
    return render(
        request, "coverage.html",
        {"run": run, "runs": runs, "entries": entries,
         "total": sum(e.get("count", 0) for e in entries),
         "checked": None, "checked_input": ""},
    )


@app.post("/coverage/check")
async def coverage_do_check(request: Request):
    form = await request.form()
    raw = str(form.get("companies", "")).strip()
    lines = [ln.strip() for ln in raw.splitlines() if ln.strip()][:20]
    cfg = config.load()
    checked = coverage_check.check(lines, cfg) if lines else []
    runs = db.recent_runs(20)
    run = runs[0] if runs else None
    entries = []
    if run and run.get("coverage"):
        try:
            entries = json.loads(run["coverage"])
        except (json.JSONDecodeError, TypeError):
            entries = []
    return render(
        request, "coverage.html",
        {"run": run, "runs": runs, "entries": entries,
         "total": sum(e.get("count", 0) for e in entries),
         "checked": checked, "checked_input": raw},
    )


@app.post("/coverage/add")
async def coverage_add(request: Request):
    """Добавляет проверенную компанию в список мониторинга."""
    form = await request.form()
    name, url = str(form.get("name", "")).strip(), str(form.get("url", "")).strip()
    if url:
        cfg = config.load()
        companies = cfg["sources"].get("companies", [])
        if not any(c.get("url") == url for c in companies):
            companies.append({"name": name, "url": url})
            cfg["sources"]["companies"] = companies
            config.save(cfg)
    return RedirectResponse("/coverage", status_code=303)


# --- Запись ошибок -------------------------------------------------------

# Собранное приложение не писало лога никуда: при сбое в окне появлялась строка
# «Internal Server Error», и узнать, что именно упало, было неоткуда. Пишем
# в файл рядом с данными и показываем человеку, где он лежит.
LOG_PATH = profiles.DATA_ROOT / "errors.log"


def _log_error(request: Request, exc: BaseException) -> str:
    from datetime import datetime as _dt
    text = "".join(traceback.format_exception(type(exc), exc, exc.__traceback__))
    entry = f"\n{'=' * 70}\n{_dt.now().isoformat(timespec='seconds')}  {request.method} {request.url}\n{text}"
    try:
        profiles.DATA_ROOT.mkdir(parents=True, exist_ok=True)
        with LOG_PATH.open("a", encoding="utf-8") as fh:
            fh.write(entry)
        # файл не должен расти бесконечно
        if LOG_PATH.stat().st_size > 512_000:
            tail = LOG_PATH.read_text(encoding="utf-8", errors="ignore")[-256_000:]
            LOG_PATH.write_text(tail, encoding="utf-8")
    except OSError:
        pass
    print(entry, file=sys.stderr)
    return text


@app.exception_handler(Exception)
async def on_error(request: Request, exc: Exception):
    """Страница ошибки вместо голого «Internal Server Error»."""
    text = _log_error(request, exc)
    last = text.strip().splitlines()[-1][:300] if text.strip() else str(exc)[:300]
    lang = "en"
    try:
        lang = config.load().get("ui", {}).get("lang", "en")
    except Exception:  # noqa: BLE001 — на странице ошибки нельзя падать второй раз
        pass
    html = (
        "<!doctype html><meta charset='utf-8'>"
        "<style>body{font:14px/1.6 -apple-system,'Segoe UI',sans-serif;max-width:760px;"
        "margin:0 auto;padding:48px 24px;color:#1d1d1f}code{background:#f2f2f7;padding:2px 6px;"
        "border-radius:5px;font-size:12.5px}a{color:#007aff}</style>"
        f"<h2>{i18n.t(lang, 'error_title')}</h2>"
        f"<p>{i18n.t(lang, 'error_hint')}</p>"
        f"<p><code>{last.replace('<', '&lt;')}</code></p>"
        f"<p>{i18n.t(lang, 'error_log')} <code>{LOG_PATH}</code></p>"
        f"<p><a href='/simple'>{i18n.t(lang, 'error_back')}</a></p>"
    )
    return Response(content=html, media_type="text/html", status_code=500)


# --- Первый запуск ---------------------------------------------------------

# Страницы, которые до выбора модели показывать бессмысленно: без неё ничего
# не работает. Всё остальное (статика, статус скачивания, смена языка) должно
# оставаться доступным, иначе само знакомство не сможет работать.
_PAGES = {"/", "/simple", "/results", "/coverage", "/cv/check", "/notify", "/models"}


@app.middleware("http")
async def first_run_gate(request: Request, call_next):
    if (request.method == "GET" and request.url.path in _PAGES
            and not appstate.setup_done()):
        return RedirectResponse("/welcome", status_code=303)
    return await call_next(request)


@app.post("/provider/install")
async def provider_install(request: Request):
    """Открывает страницу загрузки в браузере.

    Поставить программу за человека нельзя — это установщик с правами
    администратора. Но и пересказывать словами «скачайте с сайта» плохо:
    открываем нужную страницу сами, ровно ту, что записана в коде.
    """
    form = await request.form()
    key = str(form.get("provider", ""))
    back = str(form.get("back") or "/models")
    provs = providers.available(config.load()["llm"].get("claude_bin", "claude"))
    url = provs.get(key, {}).get("install_url", "")
    if not url:
        return _redirect_to(back, _msg("msg_install_unknown"))
    webbrowser.open(url)
    return _redirect_to(back, _msg("msg_install_opened", name=_msg("prov_" + key)))


@app.post("/provider/recheck")
async def provider_recheck(request: Request):
    """Перепроверить, появилась ли программа — без перезапуска приложения."""
    form = await request.form()
    back = str(form.get("back") or "/models")
    providers.forget_binaries()
    cfg = config.load()
    provs = providers.available(cfg["llm"].get("claude_bin", "claude"))
    key = str(form.get("provider") or cfg["llm"].get("provider", "claude_cli"))
    ready = bool(provs.get(key, {}).get("ready"))
    name = _msg("prov_" + key)
    return _redirect_to(back, _msg("msg_recheck_found" if ready else "msg_recheck_none", name=name))


# Внешние адреса заданы здесь списком: страница передаёт только ключ, чтобы
# кнопкой нельзя было открыть произвольный адрес.
EXTERNAL_URLS = {
    "bmc": "https://buymeacoffee.com/ipupok",
    "kofi": "https://ko-fi.com/ipupok",
    "paypal": "https://www.paypal.com/donate/?hosted_button_id=VBNDB5AHYLGCY",
    "projects": "https://mrwd.github.io/",
}


@app.post("/donate")
async def donate(request: Request):
    form = await request.form()
    url = EXTERNAL_URLS.get(str(form.get("target", "")))
    back = str(form.get("back") or "/simple")
    if url:
        webbrowser.open(url)
    return RedirectResponse(back, status_code=303)


@app.get("/welcome")
def welcome(request: Request, msg: str = ""):
    cfg = config.load()
    provider = cfg["llm"].get("provider", "claude_cli")
    provs = providers.available(cfg["llm"].get("claude_bin", "claude"))
    catalog = providers.models_for(provider)
    current_model = cfg["llm"].get("triage_model", "haiku")
    chosen = next((m for m in catalog if m["id"] == current_model), None)
    return render(request, "welcome.html", {
        "msg": msg, "provs": provs, "current_provider": provider,
        "current_model": current_model, "models": catalog,
        "current_model_name": chosen["name"] if chosen else current_model,
        "specs": hardware.specs(),
        "provider_ready": bool(provs.get(provider, {}).get("ready")),
        # продолжать есть смысл, только если выбранным способом реально можно считать
        "ready": bool(provs.get(provider, {}).get("ready")
                      and (not chosen or chosen.get("kind") != "local" or chosen.get("installed"))),
    }, cfg=cfg)


@app.post("/welcome/done")
def welcome_done():
    cfg = config.load()
    appstate.mark_setup_done(cfg["llm"])
    return RedirectResponse("/simple", status_code=303)


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="127.0.0.1", port=8765)