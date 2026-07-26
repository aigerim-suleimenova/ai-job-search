"""Веб-интерфейс: страница настроек, результаты, запуск поиска."""
import json
import sys
from datetime import date
from pathlib import Path
from urllib.parse import quote

from fastapi import FastAPI, Request, UploadFile
from fastapi.responses import JSONResponse, RedirectResponse, Response
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from jobsearch import (autostart, config, coverage as coverage_check, cvcheck, db, discovery,
                       export as export_mod, hardware, i18n, llm, mailer, notify, pipeline,
                       profiles, providers, scheduler, scoring)

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


def render(request, template: str, ctx: dict, cfg: dict = None):
    """Рендер с языком интерфейса и данными о профилях."""
    cfg_ = cfg or config.load()
    lang = cfg_.get("ui", {}).get("lang", "ru")
    ctx = {**ctx, "provider_status": _provider_status(cfg_),
           "lang": lang, "t": lambda key: i18n.t(lang, key),
           "ui_langs": i18n.UI_LANGS, "output_langs": i18n.OUTPUT_LANGS,
           "profiles": profiles.list_profiles(), "active_profile": profiles.active(),
           "active_name": profiles.name_of(profiles.active())}
    return templates.TemplateResponse(request, template, ctx)


@app.on_event("startup")
def startup() -> None:
    profiles.ensure_migrated()
    scheduler.start()
    scheduler.reschedule_all()


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
    state_view = {"running": mine, "stage": pipeline.state["stage"] if mine else ""}
    return render(request, "index.html", {
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
        return _redirect("Укажите имя человека")
    slug = profiles.create(name)
    resp = _redirect(f"Профиль «{name}» создан — заполните CV и настройки")
    resp.set_cookie("profile", slug, max_age=60 * 60 * 24 * 365, httponly=True, samesite="lax")
    return resp


@app.post("/profile/rename")
async def profile_rename(request: Request):
    form = await request.form()
    name = str(form.get("name", "")).strip()
    if name:
        profiles.rename(profiles.active(), name)
    return _redirect("Имя профиля обновлено")


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
    return _redirect("Нельзя удалить единственный профиль")


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
        summary=val("summary"), roles=val("roles"), skills=val("skills"),
        seniority=val("seniority"),
        salary=val("salary"), work_format=val("work_format", "any"),
        languages=val("languages"), visa_required=flag("visa_required"),
        visa_note=val("visa_note"), email=val("email"),
        telegram=val("telegram_user"), linkedin=val("linkedin"),
    )
    prio = val("match_priority", "both")
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
        research_company=flag("research_company"),
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
        use_wwr=flag("use_wwr"), use_hnhiring=flag("use_hnhiring"),
        use_remoteok=flag("use_remoteok"), use_jobicy=flag("use_jobicy"),
        use_himalayas=flag("use_himalayas"), use_themuse=flag("use_themuse"),
        use_arbeitsagentur=flag("use_arbeitsagentur"),
        adzuna_app_id=val("adzuna_app_id"), adzuna_app_key=val("adzuna_app_key"),
        adzuna_countries=val("adzuna_countries"), jooble_key=val("jooble_key"),
    )
    cfg["llm"].update(
        claude_bin=val("claude_bin", "claude") or "claude",
        triage_model=val("triage_model", "haiku"),
        deep_model=val("deep_model"),
    )
    cfg["telegram"].update(bot_token=val("bot_token"), chat_id=val("chat_id"))
    mode = val("schedule_mode", "off")
    cfg["schedule"].update(
        mode=mode if mode in ("off", "interval", "continuous") else "off",
        every_value=max(1, num("every_value", 1)),
        every_unit=val("every_unit", "days"),
        continuous_cooldown_min=max(1, num("continuous_cooldown_min", 20)),
    )
    cfg["schedule"].pop("enabled", None)
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


@app.get("/simple")
def simple(request: Request, msg: str = ""):
    cfg = config.load()
    mine = pipeline.state["running"] and pipeline.state.get("profile") == profiles.active()
    return render(request, "simple.html", {
        "cfg": cfg, "msg": msg, "cv": config.cv_meta(),
        "state": {"running": mine, "stage": pipeline.state["stage"] if mine else ""},
    }, cfg=cfg)


@app.post("/simple/start")
async def simple_start(request: Request, file: UploadFile = None):
    """Простой сценарий: имя, регион, CV, LinkedIn — остальное заполняется само."""
    form = await request.form()
    person = str(form.get("person", "")).strip()
    # новое имя — заводим отдельного человека, чтобы не затирать чужие результаты
    if person and person != profiles.name_of(profiles.active()):
        slug = profiles.create(person)
        profiles.set_active(slug)
    else:
        slug = profiles.active()

    filled_from_cv = False
    if file is not None and file.filename:
        raw = await file.read()
        try:
            config.save_cv(file.filename, raw)
        except Exception as e:  # noqa: BLE001
            return _redirect_to("/simple", f"Не удалось прочитать CV: {e}")

    cfg = config.load()
    cfg["search"]["locations"] = str(form.get("locations", "")).strip() or cfg["search"]["locations"]
    cfg["profile"]["linkedin"] = str(form.get("linkedin", "")).strip()
    config.save(cfg)

    cv = config.cv_text()
    if cv and not (cfg["profile"].get("roles") or cfg["profile"].get("summary")):
        try:
            cfg = scoring.profile_from_cv(cfg, cv)
            config.save(cfg)
            filled_from_cv = True
        except llm.ClaudeError as e:
            return _redirect_to("/simple", f"Не удалось разобрать CV: {e}")
    if not cv:
        return _redirect_to("/simple", "Загрузите CV — по нему определяются роли и навыки")

    started = pipeline.run_async("manual", profile=slug)
    note = " Профиль заполнен из CV." if filled_from_cv else ""
    resp = _redirect_to("/simple", ("Поиск запущен." if started else "Поиск уже идёт.") + note)
    resp.set_cookie("profile", slug, max_age=60 * 60 * 24 * 365, httponly=True, samesite="lax")
    return resp


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
    if pipeline.run_async("manual", profile=profiles.active()):
        return _redirect("Поиск запущен")
    return _redirect("Поиск уже идёт")


@app.post("/stop")
def stop_now():
    """Останавливаем только прогон этого человека: в приложении несколько профилей,
    и один пайплайн — иначе кнопка на чужой странице обрывала бы чужой поиск."""
    if not pipeline.state["running"]:
        return _redirect("Поиск и так не идёт")
    if pipeline.state.get("profile") != profiles.active():
        who = profiles.name_of(pipeline.state.get("profile", ""))
        return _redirect(f"Сейчас идёт поиск для «{who}» — переключитесь на этого человека, чтобы остановить")
    pipeline.request_stop()
    return _redirect("Останавливаем: текущие проверки договорят, новые не начнутся")


@app.get("/status")
def status():
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
        "stage": pipeline.state["stage"] if mine else "",
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
            viewed: str = "all", source: str = "all", run: int = 0):
    cfg = config.load()
    lang = cfg.get("ui", {}).get("lang", "ru")
    jobs = db.matched_jobs(min_score=min, sort=sort, viewed=viewed, source=source, run_id=run)
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
         "counts": db.counts(min), "sorts": list(db.SORTS.keys())},
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
            return _redirect_to("/notify", "Сначала вставьте токен бота")
        try:
            chat_id = notify.detect_chat_id(cfg["telegram"]["bot_token"])
        except RuntimeError as e:
            return _redirect_to("/notify", f"Telegram: {e}")
        cfg["telegram"]["chat_id"] = chat_id
        config.save(cfg)
        return _redirect_to("/notify", f"Готово, ваш chat id: {chat_id}")
    if then == "test":
        try:
            notify.send_message(cfg, "✅ AI Job Search: проверка связи. Уведомления настроены.")
        except RuntimeError as e:
            return _redirect_to("/notify", f"Telegram: {e}")
        return _redirect_to("/notify", "Тестовое сообщение отправлено — проверьте Telegram")
    return _redirect_to("/notify", "Сохранено")


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
        html = f"<p>✅ AI Job Search: проверка связи.</p><p>Письма с вакансиями для «{name}» будут приходить сюда.</p>"
        try:
            mailer.send(cfg, "AI Job Search: проверка связи", html,
                        "AI Job Search: проверка связи. Уведомления настроены.")
        except mailer.MailError as e:
            return _redirect_to("/notify", str(e))
        return _redirect_to("/notify", "Письмо отправлено — проверьте почту")
    return _redirect_to("/notify", "Сохранено")


@app.get("/models")
def models_page(request: Request, msg: str = ""):
    cfg = config.load()
    provider = cfg["llm"].get("provider", "claude_cli")
    provs = providers.available(cfg["llm"].get("claude_bin", "claude"))
    current_model = cfg["llm"].get("triage_model", "haiku")
    return render(request, "models.html", {
        "cfg": cfg, "msg": msg, "provs": provs, "current_provider": provider,
        "current_model": current_model, "models": providers.models_for(provider),
        "specs": hardware.specs(),
    }, cfg=cfg)


@app.post("/models/provider")
async def models_set_provider(request: Request):
    form = await request.form()
    key = str(form.get("provider", "claude_cli"))
    cfg = config.load()
    if key in providers.available(cfg["llm"].get("claude_bin", "claude")):
        cfg["llm"]["provider"] = key
        # модель прежнего провайдера бессмысленна для нового — берём самую сильную доступную
        picks = [m for m in providers.models_for(key)
                 if m.get("kind") != "local" or m.get("installed")]
        if picks:
            cfg["llm"]["triage_model"] = picks[0]["id"]
            cfg["llm"]["deep_model"] = picks[0]["id"]
        config.save(cfg)
    return _redirect_to("/models", "Провайдер выбран")


@app.post("/models/select")
async def models_select(request: Request):
    form = await request.form()
    model = str(form.get("model", "")).strip()
    cfg = config.load()
    cfg["llm"]["triage_model"] = model
    cfg["llm"]["deep_model"] = model
    config.save(cfg)
    return _redirect_to("/models", f"Модель выбрана: {model}")


@app.post("/models/pull")
async def models_pull(request: Request):
    form = await request.form()
    model = str(form.get("model", "")).strip()
    try:
        providers.pull(model, log=lambda m: None)
    except providers.ProviderError as e:
        return _redirect_to("/models", f"Не удалось скачать: {e}")
    return _redirect_to("/models", f"Модель скачана: {model}")


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
    return _redirect(err or "Настройки программы сохранены")


@app.post("/set_lang")
async def set_lang(request: Request):
    form = await request.form()
    code = str(form.get("ui_lang", "ru"))
    cfg = config.load()
    cfg["ui"]["lang"] = code if code in i18n.UI_LANGS else "ru"
    config.save(cfg)
    back = str(form.get("back", "/"))
    return RedirectResponse(back if back.startswith("/") else "/", status_code=303)


def _export_jobs(min_score: int, sort: str = "default", viewed: str = "all",
                 source: str = "all", run: int = 0):
    """Та же выборка, что и на странице результатов — экспорт обязан совпадать с тем,
    что человек видит на экране."""
    jobs = db.matched_jobs(limit=1000, min_score=min_score, sort=sort,
                           viewed=viewed, source=source, run_id=run)
    slug = profiles.active()
    name = profiles.name_of(slug)
    safe = "".join(ch if ch.isalnum() or ch in "-_" else "_" for ch in slug)[:40]
    return jobs, name, safe


def _filter_note(lang: str, min_score: int, sort: str, viewed: str, source: str, run: int) -> str:
    """Подпись «что именно выгружено» — чтобы файл нельзя было принять за полный список."""
    parts = [f"{i18n.t(lang, 'results_shown')} {min_score}%"]
    if sort != "default":
        parts.append(f"{i18n.t(lang, 'sort_by')}: {i18n.t(lang, 'sort_' + sort)}")
    if viewed != "all":
        parts.append(f"{i18n.t(lang, 'filter_viewed')}: {i18n.t(lang, 'viewed_' + viewed)}")
    if source != "all":
        key = {"direct": "badge_direct", "agency": "badge_agency", "aggregator": "badge_aggregator"}[source]
        parts.append(f"{i18n.t(lang, 'filter_source')}: {i18n.t(lang, key)}")
    if run:
        parts.append(f"{i18n.t(lang, 'filter_run')}: #{run}")
    return " · ".join(parts)


@app.get("/export/csv")
def export_csv(min: int = 0, sort: str = "default", viewed: str = "all",
               source: str = "all", run: int = 0):
    jobs, name, safe = _export_jobs(min, sort, viewed, source, run)
    data = export_mod.to_csv(jobs)
    return Response(
        content=data, media_type="text/csv; charset=utf-8",
        headers={"Content-Disposition": f'attachment; filename="jobs_{safe}.csv"'},
    )


@app.get("/export/report")
def export_report(min: int = 0, sort: str = "default", viewed: str = "all",
                  source: str = "all", run: int = 0):
    cfg = config.load()
    lang = cfg.get("ui", {}).get("lang", "ru")
    jobs, name, safe = _export_jobs(min, sort, viewed, source, run)
    for j in jobs:
        j["posted_label"] = _posted_label(j.get("posted_at") or "", lang)
    html_doc = export_mod.to_html(jobs, name, db.now(), min,
                                  note=_filter_note(lang, min, sort, viewed, source, run))
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
        cv_data = scoring.generate_cv(job, cfg, config.cv_text())
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


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="127.0.0.1", port=8765)
