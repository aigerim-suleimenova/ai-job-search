"""Web interface: the settings page, the results, starting a search."""
import html
import json
import os
import re
import sys
import threading
import traceback
import webbrowser
from datetime import date, datetime
from pathlib import Path
from urllib.parse import quote, urlparse

from fastapi import FastAPI, Request, UploadFile
from fastapi.responses import JSONResponse, RedirectResponse, Response
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from jobsearch import (appstate, autostart, config, coverage as coverage_check, filters,
                       cvcheck, db, discovery, export as export_mod, hardware, i18n,
                       llm, mailer, notify, pipeline, profiles, providers, removal, scheduler,
                       websearch,
                       scoring, update as update_mod, version)
from jobsearch.collectors import aggregators

BASE = Path(__file__).resolve().parent
app = FastAPI(title="AI Job Search")
app.mount("/static", StaticFiles(directory=BASE / "static"), name="static")
templates = Jinja2Templates(directory=BASE / "templates")


def _drop_empty_parens(text: str) -> str:
    """Убирает скобки, в которых ничего не оказалось.

    Часть подсказок вставляет в скобки то, что человек ещё не вписал: «как оно
    указано в документации выбранной службы ({base})». Пока адреса нет, в
    предложении посреди строки повисает «(—)». Пустые скобки — это не текст, а
    след от подстановки, и показывать его незачем.

    Скобки берутся и обычные, и полноширинные: по-японски и по-китайски пишут
    （）, и они бы уцелели.
    """
    return re.sub(r"\s*[(（]\s*[)）]", "", text)


templates.env.filters["tidy"] = _drop_empty_parens


# --- Кто вправе разговаривать с этим сервером ----------------------------------
#
# Сервер слушает только петлю (127.0.0.1), поэтому из сети до него не дотянуться.
# Но «только с этого компьютера» — не то же самое, что «только из этой программы»:
# любая страница, открытая в обычном браузере, живёт на том же компьютере и может
# слать сюда запросы, пока человек просто читает.
#
# Отсюда две проверки, обе на заголовках, которые ставит сам браузер и которые со
# страницы не подделать.
#
# Host — под каким именем к нам постучались. Имя, которое минуту назад вело на
# чужой сервер, а теперь ведёт на 127.0.0.1, и есть вся суть подмены DNS: браузер
# после этого считает чужой сценарий нашим и разрешает ему читать наши страницы.
# Мы отзываемся только на свои имена, и подмена рассыпается.
#
# Sec-Fetch-Site / Origin — откуда пришёл запрос. Форма, отправленная с чужого
# сайта, честно об этом сообщает. Проверяется на действиях, меняющих состояние:
# их здесь три десятка, и до сих пор ни одно не спрашивало, кто просит.
_LOCAL_HOSTS = {"127.0.0.1", "localhost", "::1"}
_SAFE_METHODS = {"GET", "HEAD", "OPTIONS"}


def _hostname(raw: str) -> str:
    """Имя без порта. IPv6 приходит в скобках: [::1]:8765."""
    host = (raw or "").strip().lower()
    if host.startswith("["):
        return host[1:host.index("]")] if "]" in host else host.lstrip("[")
    return host.rsplit(":", 1)[0] if ":" in host else host


def _from_this_program(request: Request) -> bool:
    """Пришёл ли запрос от страницы, которую мы сами и отдали.

    Отсутствие заголовков — не повод отказывать: их не ставят ни curl, ни старые
    встроенные браузеры, а разговаривать с собственным окном программа обязана.
    Отказ только тогда, когда браузер прямо назвал чужое происхождение.

    Sec-Fetch-Site решает дело сам, без оглядки на Origin. Он надёжнее: его
    ставит браузер и подделать со страницы его нельзя, тогда как Origin зависит
    от политики отсылок. На этом мы и обожглись: заголовок Referrer-Policy со
    значением no-referrer заставляет Chromium — а с ним и встроенный браузер
    окна — слать при отправке формы «Origin: null». Программа отказывала своему
    же окну, и человек видел «Not from this program.» вместо страницы.
    """
    site = request.headers.get("sec-fetch-site", "")
    if site == "same-origin":
        return True
    if site and site != "none":
        return False
    origin = request.headers.get("origin", "")
    if origin:
        return (urlparse(origin).hostname or "").lower() in _LOCAL_HOSTS
    return True


# Сама проверка зарегистрирована в самом низу файла: Starlette разворачивает
# список middleware наизнанку, и первым срабатывает добавленный последним. Этой
# надо быть первой — до профиля, до знакомства, до всего.


def _activate_profile(request: Request) -> None:
    """The active profile for this request — from the cookie (or the default one)."""
    slug = request.cookies.get("profile", "")
    profiles.set_active(slug if profiles.exists(slug) else profiles.default_slug())


@app.middleware("http")
async def _profile_middleware(request: Request, call_next):
    _activate_profile(request)
    return await call_next(request)


def _provider_status(cfg: dict) -> dict:
    """Is the chosen "brain" of the app ready? Without it no search will run, and
    a person should learn that straight away, not from an error mid-run.

    The name comes from the translations: it is dropped into a sentence in the
    interface language, and it used to arrive from providers.py always in
    Russian — "The app relies on “Локальная модель (Ollama)”".
    """
    key = cfg.get("llm", {}).get("provider", "claude_cli")
    provs = providers.available(cfg.get("llm", {}).get("claude_bin", "claude"),
                                cfg.get("llm", {}))
    p = provs.get(key, {})
    lang = cfg.get("ui", {}).get("lang", "en")
    # Команды идут с собой: экран «осталось одно действие» — это первое, что
    # видит тот, у кого ничего ещё не поставлено, и отправлять его оттуда читать
    # ссылку значит терять его ровно там, где он и терялся.
    return {"key": key, "ready": bool(p.get("ready")), "name": i18n.t(lang, f"prov_{key}"),
            "install_cmd": p.get("install_cmd", ""), "verify_cmd": p.get("verify_cmd", "")}


def _timing() -> dict:
    """How long the run has taken, what is left of the stage, how long it usually takes.

    The remainder is counted only inside long stages, where "done out of how
    many" is known, and at that stage's own pace. Drawing a percentage for the
    whole run would not be honest: the stages differ in length, and skipping
    one would shift everything.
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
    """Is the run in progress this particular person's?"""
    return bool(pipeline.state["running"]
                and pipeline.state.get("profile") == profiles.active())


def _last_run_noauth() -> str:
    """The error of the last run, if it stopped because the model wants a login.

    The line is matched by its beginning in any language: the log is written in
    whatever language was set during the run, and this used to compare against
    the Russian text — for everyone else it never matched, and a dash was shown
    instead of the reason.
    """
    runs = db.recent_runs(1)
    if not runs or runs[0]["status"] != "noauth":
        return ""
    prefixes = [i18n.t(code, "log_noauth").split("{error}")[0]
                for code in i18n.UI_LANGS]
    for line in reversed((runs[0]["log"] or "").splitlines()):
        for prefix in prefixes:
            if prefix and line.startswith(prefix):
                return line[len(prefix):][:200]
    return "—"


def _lang(cfg: dict = None) -> str:
    return (cfg if cfg is not None else config.load()).get("ui", {}).get("lang", "en")


def _err(exc) -> str:
    """An exception in the interface language: our own errors carry a translation key."""
    return i18n.err(_lang(), exc)


def _msg(key: str, **fmt) -> str:
    """A notice in the interface language, rather than always in Russian."""
    text = i18n.t(_lang(), key)
    return text.format(**fmt) if fmt else text


def _date_or_empty(raw) -> str:
    """ГГГГ-ММ-ДД или пусто. Поле приходит из браузера, а туда можно вписать что
    угодно — в настройки должно попасть либо настоящая дата, либо ничего."""
    text = str(raw or "").strip()
    try:
        return date.fromisoformat(text).isoformat() if text else ""
    except ValueError:
        return ""


def _seed_profile(slug: str) -> None:
    """A new person gets the same CLI and model that were chosen on first launch."""
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
    """A stylesheet stamp for the URL: it changes when the file changes."""
    try:
        return str(int((BASE / "static" / "style.css").stat().st_mtime))
    except OSError:
        return "1"


def render(request, template: str, ctx: dict, cfg: dict = None):
    """Render with the interface language and the profile data."""
    cfg_ = cfg or config.load()
    lang = cfg_.get("ui", {}).get("lang", "ru")
    # Раз в сутки заодно спрашиваем, не вышла ли новая версия. Раньше спрашивали
    # только при запуске, и человек, у которого программа стоит открытой сутками
    # (для того у неё и фоновый режим, и расписание), про новую версию так и не
    # узнавал: при запуске её ещё не было, а потом никто не переспрашивал.
    # Страницу это не задерживает — поток заводится, только когда вышел срок, и
    # ответа его тут никто не ждёт; страница читает, что записал прошлый.
    update_mod.check_in_background()
    ctx = {**ctx, "provider_status": _provider_status(cfg_), "asset_v": _asset_version(),
           # Сколько источников опрашивается — считаем, а не пишем словом:
           # написанное словом разошлось с делом и годами обещало девять из двенадцати.
           "sources_on": len(aggregators.enabled(cfg_)),
           "lang": lang, "t": lambda key: i18n.t(lang, key), "theme": appstate.theme(),
           "app_version": version.current(),
           # read from what the last check wrote down — the page never waits on the network
           "update": update_mod.state(),
           "rtl": lang in i18n.RTL_LANGS,
           "ui_langs": i18n.UI_LANGS, "output_langs": i18n.OUTPUT_LANGS,
           "profiles": profiles.list_profiles(), "active_profile": profiles.active(),
           "active_name": profiles.name_of(profiles.active()),
           # Где удаления нет (macOS, Linux), программа должна объяснить, что
           # останется после неё, — сама она об этом сказать больше некому.
           "removal": {"has_uninstaller": removal.has_uninstaller(),
                       "program": removal.program_path(),
                       "leftovers": removal.leftovers()}}
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
    """Not one of these steps is required for a person to see a window.

    An exception here used to bring down the whole launch: uvicorn exited with
    code 3, the server thread died, the window never opened — all because of
    the scheduler, without which the app works perfectly well. Now every step
    answers for itself, and the reason lands in the log.
    """
    for шаг, действие in (("перенос профилей", profiles.ensure_migrated),
                          ("имя профиля по умолчанию", profiles.rename_auto_defaults),
                          ("запуск расписания", scheduler.start),
                          ("восстановление расписаний", scheduler.reschedule_all),
                          # спрашиваем о новой версии в фоне: иначе о ней никто не узнает,
                          # а ждать сети при запуске программа не должна
                          ("проверка обновления", update_mod.check_in_background)):
        try:
            действие()
        except Exception:  # noqa: BLE001 — no trouble here justifies not opening
            _log_startup_problem(шаг, traceback.format_exc())


def _redirect(msg: str = "") -> RedirectResponse:
    url = f"/?msg={quote(msg)}" if msg else "/"
    return RedirectResponse(url, status_code=303)


def _here(path: str, default: str = "/") -> str:
    """Адрес внутри этой программы — или главная, если подсунули чужой.

    Куда вернуть человека после действия, приходит со страницы, полем back.
    Проверялось оно только на «а показывается ли ещё эта страница», и в него
    можно было положить https://чужой-сайт: программа отвечала переходом туда.
    Своё окно человек считает своей программой и на чужой странице, открытой из
    него, ведёт себя доверчиво — потому это и стоит закрыть.

    Годится только путь: одна косая черта в начале и никакой схемы. Две косые
    («//чужой-сайт») браузер понимает как адрес с той же схемой — это тот же
    уход наружу, только записанный короче. Обратная косая тоже: браузеры
    выправляют её в обычную, и «/\\чужой-сайт» доедет туда же, куда «//».
    """
    if not path.startswith("/") or path[1:2] in ("/", "\\") or "\\" in path:
        return default
    # «/https:/x» — уже не путь, а схема, как её ни записывай
    первый = path.split("/", 2)[1].split("?")[0].split("#")[0]
    return default if ":" in первый else path


def _redirect_to(path: str, msg: str = "", anchor: str = "") -> RedirectResponse:
    """anchor — a place on the page: after an action a person should end up
    where they clicked, not at the top of a long list."""
    path = _here(path)
    # «?» или «&» — смотря есть ли в адресе запрос: знакомство возвращает на
    # /welcome?step=model, и второй вопросительный знак сделал бы адрес негодным.
    url = f"{path}{'&' if '?' in path else '?'}msg={quote(msg)}" if msg else path
    if anchor:
        url += "#" + anchor
    return RedirectResponse(url, status_code=303)


def _reachable(path: str) -> str:
    """Where a person can actually be sent right now.

    Deleting the model that was in use leaves nothing to think with, and the page
    they came from stops being shown at all. Sending them back there anyway means
    the gate below bounces them to the introduction and the explanation is lost
    on the way — they arrive at a strange screen with nothing said. So we address
    the introduction ourselves and the words survive the journey.

    (_PAGES is defined with the gate further down; it is read when this runs.)
    """
    return "/welcome" if path.split("?")[0] in _PAGES and appstate.needs_setup() else path


@app.get("/")
def index(request: Request, msg: str = ""):
    cfg = config.load()
    next_run = scheduler.next_run_time()
    # show the status as "running" only if the current run belongs to this profile
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

    # The settings form used to overwrite the whole config, so any partial
    # submission wiped the rest — including companies a running search might be
    # adding at that very moment. Now a section is updated only if its own
    # fields actually arrived.
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
        cfg["sources"]["harvest_boards"] = flag("harvest_boards")
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
            use_eures=flag("use_eures"),
            use_jobtech=flag("use_jobtech"),
            use_workable=flag("use_workable"),
            adzuna_app_id=val("adzuna_app_id"), adzuna_app_key=val("adzuna_app_key"),
            adzuna_countries=val("adzuna_countries"), jooble_key=val("jooble_key"),
            # Поиск в интернете силами приложения: служба выбирается из списка,
            # чужое значение сюда попасть не должно.
            web_search_provider=(val("web_search_provider")
                                 if val("web_search_provider") in websearch.PROVIDERS else ""),
            web_search_key=val("web_search_key"),
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
        # An unfamiliar value keeps the current language instead of resetting to
        # Russian: that reset silently switched both interface and results.
        ui_lang = val("ui_lang", cfg["ui"].get("lang", "en"))
        out_lang = val("output_lang", cfg["ui"].get("output_lang", "") or ui_lang)
        cfg["ui"].update(
            lang=ui_lang if ui_lang in i18n.UI_LANGS else cfg["ui"].get("lang", "en"),
            output_lang=out_lang if out_lang in i18n.OUTPUT_LANGS else ui_lang,
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
            return _redirect(_msg("msg_saved_tg_error", error=_err(e)))
        cfg["telegram"]["chat_id"] = chat_id
        config.save(cfg)
        return _redirect(_msg("msg_saved_chat_found", chat_id=chat_id))
    if then == "tg_test":
        try:
            notify.send_message(cfg, _msg("tg_test_message"))
        except RuntimeError as e:
            return _redirect(_msg("msg_saved_tg_error", error=_err(e)))
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
    """The simple path: name, region, CV, LinkedIn — the rest fills itself in."""
    form = await request.form()
    person = str(form.get("person", "")).strip()
    # What a changed name means depends on how many people exist.
    #
    # One — the person is editing their own name (on first launch it reads "Me")
    # and expects a rename. Instead a second profile with an empty history used
    # to be created silently, while the first stayed hanging next to it.
    #
    # Several — the field works as "who are we searching for now": we switch to
    # someone who already exists, or add a new one. Typing the same name twice
    # must not breed "Friend", "Friend-2", "Friend-3" with separate results.
    if person and person != profiles.name_of(profiles.active()):
        existing = next((p["slug"] for p in profiles.list_profiles()
                         if p["name"].strip().casefold() == person.casefold()), "")
        if existing:
            slug = existing
            profiles.set_active(slug)
        elif len(profiles.list_profiles()) == 1:
            slug = profiles.active()
            profiles.rename(slug, person)
        else:
            slug = profiles.create(person)
            _seed_profile(slug)
            profiles.set_active(slug)
    else:
        slug = profiles.active()

    def ответ(resp):
        """The chosen person must survive every outcome, not only the happy one.
        The "CV unreadable" and "CV needed" paths did not set the cookie, so the
        switch silently rolled back: the message was about one person while the
        page returned to another."""
        resp.set_cookie("profile", slug, max_age=60 * 60 * 24 * 365,
                        httponly=True, samesite="lax")
        return resp

    if file is not None and file.filename:
        raw = await file.read()
        try:
            config.save_cv(file.filename, raw)
        except config.CVError as e:
            return ответ(_redirect_to("/simple", _msg(e.key, **e.fmt)))
        except Exception as e:  # noqa: BLE001
            return ответ(_redirect_to("/simple", _msg("msg_cv_unreadable", error=e)))

    cfg = config.load()
    cfg["search"]["locations"] = str(form.get("locations", "")).strip() or cfg["search"]["locations"]
    # За какой срок искать. Пустое поле — это ответ («без ограничения»), а не
    # отсутствие ответа, поэтому берётся как есть, без подстановки прежнего.
    cfg["search"]["posted_from"] = _date_or_empty(form.get("posted_from"))
    cfg["search"]["posted_to"] = _date_or_empty(form.get("posted_to"))
    cfg["profile"]["linkedin"] = str(form.get("linkedin", "")).strip()
    config.save(cfg)

    if not config.cv_text():
        return ответ(_redirect_to("/simple", _msg("msg_cv_needed")))

    # Reading the CV and the search itself go to the background: the page comes
    # back at once, and the progress is visible in the status line and the log.
    started = pipeline.prepare_and_run(slug, trigger="manual")
    if started:
        note = _msg("msg_search_started")
    else:
        busy = pipeline.state.get("profile", "")
        note = (_msg("msg_busy_next", name=profiles.name_of(busy))
                if busy and busy != slug else _msg("msg_search_already"))
    return ответ(_redirect_to("/simple", note))


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
    # One pipeline for everyone: if it is busy with someone else's run, a person
    # should understand they are waiting for a queue, not for their own results.
    busy = pipeline.state.get("profile", "")
    if busy and busy != profiles.active():
        return _redirect(_msg("msg_busy_wait", name=profiles.name_of(busy)))
    return _redirect(_msg("msg_search_already"))


@app.post("/stop")
def stop_now():
    """Stop only this person's run: the app holds several profiles and a single
    pipeline — otherwise the button on one page would cut short another's search."""
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
    # the run is happening "here" only if it belongs to the active profile
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
    """'2026-07-10' → 'posted 4d ago (2026-07-10)' / 'опубл. 4 дн. назад (…)'."""
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
    # Сеть заведений вешает одну вакансию по всем своим точкам, меняя город в
    # названии. У Виктора Белоногова первые двадцать строк принадлежали четырём
    # работодателям, а десять подряд — одной сети. Схлопываем в одну строку, но
    # ничего не выбрасываем: остальные города видны в ней же.
    jobs = filters.group_same_role(jobs)
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
        # cfg нужен и самой странице: метку про право на работу показываем
        # только тому, кому нужна виза, — остальным она лишний шум.
        # Профессия из тех, что за границей требуют подтверждения. Смотрим по
        # ролям, а не по вакансиям: вопрос про человека, а не про работу.
        {"cfg": cfg, "jobs": jobs, "runs": runs, "min_score": min,
         "regulated": filters.regulated_profession(cfg["profile"].get("roles", ""),
                                                   config.cv_text()),
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
    return RedirectResponse(_here(back, "/results"), status_code=303)


@app.post("/viewed_all")
async def viewed_all(request: Request):
    form = await request.form()
    try:
        min_score = int(str(form.get("min", "0")))
    except ValueError:
        min_score = 0
    db.mark_all_viewed(min_score)
    back = str(form.get("back", "/results"))
    return RedirectResponse(_here(back, "/results"), status_code=303)


# Analysing a single job on demand. The model thinks for a minute or two, and the
# page cannot be held all that time (WebKit would cut the request off itself), so
# the work goes to the background while the page polls /analyse/status and redraws
# once it is done.
_deep_state: dict[str, dict] = {}   # "profile:job id" → what is happening to it now


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
        return RedirectResponse(_here(back), status_code=303)
    key = _deep_key(job_id)
    if _deep_state.get(key, {}).get("running"):
        return RedirectResponse(_here(back), status_code=303)
    cfg, cv, profile = config.load(), config.cv_text(), profiles.active()
    _deep_state[key] = {"running": True, "error": ""}

    def worker():
        profiles.set_active(profile)
        notes: list = []
        try:
            scoring.deep_analyze(job, cfg, cv, notes.append,
                                 research=bool(cfg["search"].get("research_company", True)))
            if not job.get("verified"):
                # deep_analyze does not raise: it writes the reason to the log
                raise RuntimeError(notes[-1] if notes else _msg("msg_analyse_failed",
                                                                title=job.get("title", "")))
            db.save_job(job, int(job.get("run_id") or 0))
            _deep_state[key] = {"running": False, "error": "",
                                "msg": _msg("msg_analysed", title=job.get("title", ""),
                                            score=job.get("score", 0))}
        except Exception as e:
            _deep_state[key] = {"running": False, "error": str(e)}

    threading.Thread(target=worker, daemon=True).start()
    return RedirectResponse(_here(back), status_code=303)


@app.get("/analyse/status")
def analyse_status():
    """Which of this person's jobs are being analysed right now and which are done."""
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
    presets = mailer.presets(_lang(cfg))
    return render(request, "notify.html", {
        "cfg": cfg, "msg": msg, "presets": presets,
        "presets_json": json.dumps(presets, ensure_ascii=False),
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
            return _redirect_to("/notify", _msg("msg_tg_error", error=_err(e)))
        cfg["telegram"]["chat_id"] = chat_id
        config.save(cfg)
        return _redirect_to("/notify", _msg("msg_chat_found", chat_id=chat_id))
    if then == "test":
        try:
            notify.send_message(cfg, _msg("tg_test_message"))
        except RuntimeError as e:
            return _redirect_to("/notify", _msg("msg_tg_error", error=_err(e)))
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
    provs = providers.available(cfg["llm"].get("claude_bin", "claude"), cfg["llm"])
    current_model = providers.current_model(cfg["llm"])
    catalog = providers.models_for(provider, lang=_lang(cfg))
    return render(request, "models.html", {
        "cfg": cfg, "msg": msg, "provs": provs, "current_provider": provider,
        "current_model": current_model, "models": catalog,
        "current_model_name": next((m["name"] for m in catalog if m["id"] == current_model), current_model),
        "specs": hardware.specs(),
        "provider_ready": bool(provs.get(provider, {}).get("ready")),
        # Провайдера меняют и отсюда, уже после знакомства, — и упираются в ту же
        # стену. Экрана «что нужно установить» здесь нет, но сказать про это
        # надо и здесь: без этого карточка молча предлагает npm, которого нет.
        "tool": (providers.tool_info(providers.missing_tool(provider))
                 if not provs.get(provider, {}).get("ready")
                 and providers.missing_tool(provider) else None),
    }, cfg=cfg)


@app.post("/models/provider")
async def models_set_provider(request: Request):
    form = await request.form()
    key = str(form.get("provider", "claude_cli"))
    cfg = config.load()
    if key in providers.available(cfg["llm"].get("claude_bin", "claude"), cfg["llm"]):
        cfg["llm"]["provider"] = key
        # the old provider's model means nothing to the new one — take the strongest available
        catalog = providers.models_for(key, lang=_lang(cfg))
        picks = [m for m in catalog if m.get("kind") != "local" or m.get("installed")]
        # nothing installed — pencil in the strongest model that fits in memory:
        # otherwise the settings would keep the previous provider's model
        picks = picks or [m for m in catalog if m.get("fits") in ("yes", "tight")] or catalog
        if picks:
            cfg["llm"]["triage_model"] = picks[0]["id"]
            cfg["llm"]["deep_model"] = picks[0]["id"]
        config.save(cfg)
    msg = _msg("msg_provider_set", provider=i18n.t(
        config.load().get("ui", {}).get("lang", "ru"), "prov_" + key))
    # Choosing what powers the search answers only half the question; which model
    # is the other half, further down the same page. A person used to be thrown
    # to the very top instead, above everything they had already read.
    back = str(form.get("back") or "/models")
    where = _reachable(back)
    # Выбрали то, что и установить-то нечем, — ведём прямо туда, где сказано чем.
    # Иначе человек уходит по кнопке «Скачать» в инструкцию с «npm install -g …»,
    # а npm у него нет, и узнаёт он об этом уже в терминале.
    if where.startswith("/welcome") and providers.missing_tool(key):
        return _redirect_to("/welcome?step=tools", msg)
    # Куда возвращать, решает страница, с которой пришли: у знакомства это кнопка
    # «Далее», у страницы моделей — список. А если гейт увёл нас в другое место,
    # то и якорь оттуда там ничего не значит.
    anchor = str(form.get("anchor", "")) if where == back else ""
    return _redirect_to(where, msg, anchor=anchor)


@app.post("/models/select")
async def models_select(request: Request):
    form = await request.form()
    model = str(form.get("model", "")).strip()
    cfg = config.load()
    cfg["llm"]["triage_model"] = model
    cfg["llm"]["deep_model"] = model
    config.save(cfg)
    return _redirect_to(str(form.get("back") or "/models"),
                        _msg("msg_model_set", model=model),
                        anchor=str(form.get("anchor", "")))


@app.post("/models/pull")
async def models_pull(request: Request):
    """The download runs in the background: a model weighs gigabytes, and the page
    cannot wait for it."""
    form = await request.form()
    model = str(form.get("model", "")).strip()
    if providers.pull_in_progress():
        return _redirect_to(str(form.get("back") or "/models"), _msg("msg_pull_busy"))
    providers.pull_async(model)
    return _redirect_to(str(form.get("back") or "/models"), _msg("msg_pull_started", model=model))


@app.post("/models/api")
async def models_set_api(request: Request):
    """Свой адрес, говорящий на языке OpenAI: адрес, ключ и имя модели.

    У этого провайдера нечего искать на диске и нечего перечислять списком —
    вместо кнопки «Выбрать» у него поля. Пустой ключ оставлен возможным: у
    LM Studio и llama.cpp на своём компьютере его попросту нет.
    """
    form = await request.form()
    cfg = config.load()
    base = str(form.get("api_base", "")).strip()
    if base and not base.startswith(("http://", "https://")):
        return _redirect_to(str(form.get("back") or "/models"), _msg("prov_err_api_base_bad"))
    cfg["llm"]["api_base"] = base.rstrip("/")
    cfg["llm"]["api_key"] = str(form.get("api_key", "")).strip()
    cfg["llm"]["api_model"] = str(form.get("api_model", "")).strip()
    if base:
        cfg["llm"]["provider"] = "openai_api"
    config.save(cfg)
    back = str(form.get("back") or "/models")
    where = _reachable(back)
    return _redirect_to(where, _msg("msg_saved"),
                        anchor=str(form.get("anchor", "")) if where == back else "")


@app.post("/models/delete")
async def models_delete(request: Request):
    """Removes a downloaded model from the computer.

    A model weighs gigabytes, and until now nothing in the app could take one
    back off the disk: it could only be downloaded. Deleting the one in use is
    allowed — it leaves the app with nothing to think with, and the introduction
    says so and asks for another, which is a plainer state of affairs than a
    setting pointing at a model that is no longer there.
    """
    form = await request.form()
    model = str(form.get("model", "")).strip()
    back = str(form.get("back") or "/models")
    if providers.pull_in_progress():
        return _redirect_to(back, _msg("msg_pull_busy"))
    try:
        providers.delete_model(model)
    except providers.ProviderError as e:
        return _redirect_to(_reachable(back), _err(e))
    return _redirect_to(_reachable(back), _msg("msg_model_deleted", model=model))


@app.get("/ping")
def ping():
    """Кто отвечает на этом порту.

    Оболочка спрашивает это перед тем, как решить «программа уже запущена, просто
    покажу окно». Раньше она смотрела лишь на то, слушает ли кто-нибудь порт, —
    и во время обновления новая копия принимала за живую старую, которую
    установщик как раз закрывал: окно открывалось на её порт, свой сервер не
    поднимался, а через мгновение там уже никого не было.

    Поэтому здесь и версия: копия прицепляется только к своей ровеснице.
    """
    return JSONResponse({"app": "ai-job-search", "version": version.current(),
                         "pid": os.getpid()})


@app.get("/models/pull_status")
def models_pull_status():
    """The download state, already in words and in the interface language.

    Our own steps and errors sit in that state as keys: the page shows whatever
    it is given, and in every language it used to see the Russian "начинаем"
    and "готово". Ollama's own messages ("pulling manifest") have nothing to be
    translated with — they go through as they arrived.
    """
    lang = _lang()
    st = providers.pull_status()
    if st.get("status_key"):
        st["status"] = i18n.t(lang, st["status_key"])
    if st.get("error_key"):
        st["error"] = i18n.t(lang, st["error_key"]).format(**(st.get("error_fmt") or {}))
    st.pop("status_key", None)
    st.pop("error_key", None)
    st.pop("error_fmt", None)
    return JSONResponse(st)


# The CV check calls the model, and with a local one it takes minutes. This used
# to be an ordinary link: a person clicked it and sat in front of a page that
# never changed, unsure whether anything was happening. Now the work goes to the
# background and the page shows progress and refreshes itself — as with a job.
_check_state: dict = {}      # "profile" → what is happening to the check now


@app.get("/cv/check")
def cv_check(request: Request, msg: str = ""):
    cfg = config.load()
    состояние = _check_state.get(profiles.active(), {})
    return render(request, "cvcheck.html",
                  {"result": cvcheck.last_result(), "cv": config.cv_meta(), "msg": msg,
                   "checking": bool(состояние.get("running")),
                   "check_error": состояние.get("error", "")}, cfg=cfg)


@app.post("/cv/check/run")
def cv_check_run():
    slug = profiles.active()
    if _check_state.get(slug, {}).get("running"):
        return RedirectResponse("/cv/check", status_code=303)
    if not config.cv_text().strip():
        return _redirect_to("/cv/check", _msg("cv_no_source"))
    cfg = config.load()
    lang = _lang(cfg)      # взято здесь: в потоке настройки читать уже не у кого
    _check_state[slug] = {"running": True, "error": ""}

    def worker():
        profiles.set_active(slug)
        try:
            cvcheck.analyze(cfg)
            _check_state[slug] = {"running": False, "error": ""}
        except Exception as e:  # noqa: BLE001 — show the reason, do not hide it
            # i18n.err, а не str(e): наши собственные ошибки носят с собой ключ
            # перевода вместо готовой фразы, и человеку показывалось голое
            # «prov_err_ollama_unreachable» — слово, которое ему ничего не говорит.
            _check_state[slug] = {"running": False, "error": i18n.err(lang, e)[:400]}

    threading.Thread(target=worker, daemon=True).start()
    return RedirectResponse("/cv/check", status_code=303)


@app.get("/cv/check/status")
def cv_check_status():
    состояние = _check_state.get(profiles.active(), {})
    return JSONResponse({"running": bool(состояние.get("running")),
                         "error": состояние.get("error", "")})


@app.post("/app_settings")
async def app_settings(request: Request):
    """How the app itself behaves: start at login and keep working in the background."""
    form = await request.form()
    cfg = config.load()
    cfg["ui"]["background"] = "background" in form
    config.save(cfg)
    err = autostart.set_enabled("autostart" in form)
    return _redirect(_msg(err) if err else _msg("msg_app_settings_saved"))


# Скачивание установщика идёт в фоне: это десятки мегабайт, и страница не должна
# на них стоять. Здесь же видно, чем всё кончилось.
_update_state: dict = {"running": False, "percent": 0, "error": ""}


@app.post("/app/update/check")
async def app_update_check(request: Request):
    """Спросить о новой версии прямо сейчас, не дожидаясь суточной проверки.

    Возвращаем туда, откуда нажали. Прежде возвращали на главную всегда — это
    годилось, пока кнопка была одна и стояла в настройках. Теперь она есть и в
    шапке, то есть на всякой странице, и человека, спросившего про версию с
    «Результатов», уносило в настройки, к чужому для него разговору.
    """
    form = await request.form()
    back = str(form.get("back", "/"))
    found = update_mod.check(force=True)
    if found.get("version"):
        # Нашлось — ведём к кнопке установки. Она одна на всю программу и живёт
        # в настройках, а сообщение говорит «поставить её можно ниже»: на всякой
        # другой странице ниже нет ничего, и человек ищет то, чего там нет.
        # Ровно это мы правили у своего адреса — и завели заново тем, что стали
        # возвращать на страницу, откуда спросили.
        return _redirect_to("/", _msg("update_found", version=found["version"]),
                            anchor="update")
    # А когда ставить нечего, делать человеку негде — и уводить его незачем.
    return _redirect_to(back, _msg("update_none", version=version.current()))


@app.post("/app/update/install")
def app_update_install():
    """Скачивает установщик и передаёт ему работу.

    Программа при этом закрывается: установщик не может заменить файлы, которые
    открыты. Он дождётся, пока старая копия уйдёт, и откроет новую.
    """
    if _update_state["running"]:
        return _redirect(_msg("update_busy"))
    asset = (update_mod.state().get("asset") or {})
    if not asset.get("url"):
        return _redirect(_msg("update_manual"))
    if pipeline.state["running"]:
        return _redirect(_msg("update_busy_search"))
    _update_state.update(running=True, percent=0, error="")
    lang = _lang()

    def worker():
        try:
            path = update_mod.download(asset, progress=lambda p: _update_state.update(percent=p))
            update_mod.install(path)
            _update_state.update(running=False, percent=100)
            update_mod.quit_soon()      # установщик ждёт, пока мы закроемся
        except update_mod.UpdateError as e:
            _update_state.update(running=False, error=i18n.t(lang, e.key).format(**{
                k: str(v) for k, v in e.fmt.items()}))
        except Exception as e:  # noqa: BLE001 — причину надо показать, а не спрятать
            _update_state.update(running=False, error=str(e)[:300])

    threading.Thread(target=worker, daemon=True).start()
    return _redirect(_msg("update_started"))


@app.get("/app/update/status")
def app_update_status():
    return JSONResponse(dict(_update_state))


@app.post("/app/uninstall")
def app_uninstall():
    """Готовит программу к удалению там, где удалять её нечем.

    На Windows этим занимается установщик. На macOS и Linux образ просто
    перетаскивают в корзину — и данные с автозапуском остаются навсегда, потому
    что убрать их может только сама программа, до того как её удалят. О чём
    никто не догадывается.

    Поэтому здесь: выключаем автозапуск, стираем данные и показываем, что
    осталось убрать руками. Закрываться сама программа не спешит — иначе
    человек не успеет прочитать, что ему делать дальше.
    """
    if pipeline.state["running"]:
        return _redirect(_msg("msg_reset_busy"))
    lang = _lang()
    autostart.set_enabled(False)
    survived = profiles.wipe_all()
    db.forget_databases()
    providers.forget_binaries()
    profiles.ensure_migrated()
    profiles.set_active(profiles.default_slug())
    try:
        scheduler.reschedule_all()
    except Exception:  # noqa: BLE001
        _log_startup_problem("восстановление расписаний", traceback.format_exc())
    # Путь — прямо в сообщении: отдельная страница «а теперь удалите вот это»
    # потерялась бы, а закрыть программу за человека нельзя, пока он не прочёл.
    where = removal.program_path() or i18n.t(lang, "uninstall_from_source")
    msg = i18n.t(lang, "msg_reset_failed").format(files=", ".join(survived)) if survived \
        else i18n.t(lang, "uninstall_ready").format(path=where)
    response = _redirect_to("/welcome", msg)
    response.delete_cookie("profile")
    return response


@app.post("/app/reset")
def app_reset():
    """Erases everything the app knows and starts from the introduction again.

    Uninstalling the program leaves this data where it is — no installer may
    throw away someone's CV and results on a hunch. The price is that
    reinstalling is not the fresh start it looks like, and the only honest way
    to one runs from inside the program.

    A run in progress is not stopped for you: it writes into the very files
    being erased, and would quietly put some of them back. Better to say so and
    let the person stop it.
    """
    if pipeline.state["running"]:
        return _redirect(_msg("msg_reset_busy"))
    # The chosen language is about to be erased along with everything else, and
    # the sentence explaining that should still arrive in it.
    lang = _lang()
    autostart.set_enabled(False)      # a shortcut left behind would outlive the data
    survived = profiles.wipe_all()
    db.forget_databases()
    providers.forget_binaries()
    profiles.ensure_migrated()        # an empty profile again, as on a first launch
    profiles.set_active(profiles.default_slug())
    try:
        scheduler.reschedule_all()    # the schedules pointed at profiles that are gone
    except Exception:  # noqa: BLE001 — the data is already gone; this must not undo that
        _log_startup_problem("восстановление расписаний", traceback.format_exc())
    msg = i18n.t(lang, "msg_reset_failed").format(files=", ".join(survived)) if survived \
        else i18n.t(lang, "msg_reset_done")
    response = _redirect_to("/welcome", msg)
    response.delete_cookie("profile")  # it names a profile that no longer exists
    return response


@app.post("/set_lang")
async def set_lang(request: Request):
    form = await request.form()
    code = str(form.get("ui_lang", ""))
    cfg = config.load()
    cfg["ui"]["lang"] = code if code in i18n.UI_LANGS else cfg["ui"].get("lang", "en")
    config.save(cfg)
    back = str(form.get("back", "/"))
    return RedirectResponse(_here(back), status_code=303)


@app.post("/set_theme")
async def set_theme(request: Request):
    """The theme is shared by the whole app: it is about the screen, not the person."""
    form = await request.form()
    appstate.set_theme(str(form.get("theme", "auto")))
    back = str(form.get("back", "/"))
    return RedirectResponse(_here(back), status_code=303)


def _export_jobs(min_score: int, sort: str = "default", viewed: str = "all",
                 source: str = "all", run: int = 0,
                 posted_from: str = "", posted_to: str = ""):
    """The same selection as on the results page — an export has to match what a
    person sees on screen."""
    jobs = db.matched_jobs(limit=1000, min_score=min_score, sort=sort,
                           viewed=viewed, source=source, run_id=run,
                           posted_from=posted_from, posted_to=posted_to)
    slug = profiles.active()
    name = profiles.name_of(slug)
    safe = "".join(ch if ch.isalnum() or ch in "-_" else "_" for ch in slug)[:40]
    return jobs, name, safe


def _filter_note(lang: str, min_score: int, sort: str, viewed: str, source: str, run: int,
                 posted_from: str = "", posted_to: str = "") -> str:
    """A note saying what exactly was exported, so the file cannot be mistaken
    for the complete list."""
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


@app.get("/export/md")
def export_markdown(min: int = 0, sort: str = "default", viewed: str = "all",
                    source: str = "all", run: int = 0,
                    posted_from: str = "", posted_to: str = ""):
    """Разметкой — её вставляют в заметки, письмо или переписку."""
    cfg = config.load()
    lang = cfg.get("ui", {}).get("lang", "ru")
    jobs, name, safe = _export_jobs(min, sort, viewed, source, run, posted_from, posted_to)
    text = export_mod.to_markdown(jobs, name, db.now(), min,
                                  note=_filter_note(lang, min, sort, viewed, source, run,
                                                    posted_from, posted_to))
    return Response(
        content=text, media_type="text/markdown; charset=utf-8",
        headers={"Content-Disposition": f'attachment; filename="jobs_{safe}.md"'},
    )


@app.get("/export/json")
def export_json(min: int = 0, sort: str = "default", viewed: str = "all",
                source: str = "all", run: int = 0,
                posted_from: str = "", posted_to: str = ""):
    """Для тех, кто хочет обработать выгрузку своей программой."""
    cfg = config.load()
    lang = cfg.get("ui", {}).get("lang", "ru")
    jobs, name, safe = _export_jobs(min, sort, viewed, source, run, posted_from, posted_to)
    text = export_mod.to_json(jobs, name, db.now(), min,
                              note=_filter_note(lang, min, sort, viewed, source, run,
                                                posted_from, posted_to))
    return Response(
        content=text, media_type="application/json; charset=utf-8",
        headers={"Content-Disposition": f'attachment; filename="jobs_{safe}.json"'},
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
                                                    posted_from, posted_to),
                                  print_label=i18n.t(lang, "export_print"))
    # Отчёт открывается, а не скачивается, и это не мелочь: подсказка рядом с
    # кнопкой обещает «открыть и распечатать в PDF», а отдавался он файлом на
    # скачивание. В окне программы от этого не происходило вообще ничего —
    # pywebview скачивать не даёт, — и человек жал кнопку впустую. Печать в PDF
    # делается из открытой страницы, так что открыть её и надо.
    return Response(content=html_doc, media_type="text/html; charset=utf-8")


@app.get("/cv/{job_id}")
def tailored_cv(job_id: int):
    причина = ""          # the failure text, if the model could not manage
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
            # without the original CV the model invented a document full of
            # "<MISSING>" instead of refusing
            return Response(content=f"<p style='font:15px -apple-system;padding:32px'>"
                                    f"{_msg('cv_no_source')}</p>",
                            media_type="text/html", status_code=200)
        try:
            cv_data = scoring.generate_cv(job, cfg, source)
        except Exception as e:  # noqa: BLE001 — showing the reason beats crashing
            _log_startup_problem("подготовка CV под вакансию", traceback.format_exc())
            cv_data = None
            причина = str(e)[:400]
        else:
            причина = ""
        if cv_data:
            db.save_tailored_cv(job_id, json.dumps(cv_data, ensure_ascii=False))
    if not cv_data:
        # A blank tab with "502" explains nothing. What usually leads here is a
        # small local model: it writes the document as prose rather than strict
        # JSON, and there is nothing to parse it with.
        подробности = ""
        if причина:
            # Экранировано: в тексте исключения оседает то, что написали не мы —
            # ответ модели и куски описания вакансии, а описание пишет кто угодно.
            # Без этого <script> из чужой вакансии выполнялся бы на нашей странице,
            # а она ходит по адресам этой программы как своя.
            подробности = ("<pre style='background:#f2f2f7;padding:12px;border-radius:8px;"
                           "white-space:pre-wrap;font-size:12px'>"
                           + html.escape(причина) + "</pre>")
        страница = (
            "<!doctype html><meta charset='utf-8'>"
            "<body style='font:15px/1.55 -apple-system,sans-serif;padding:32px;max-width:38em'>"
            f"<h2 style='margin:0 0 10px'>{_msg('cv_gen_failed_title')}</h2>"
            f"<p style='color:#6e6e73'>{_msg('cv_gen_failed_hint')}</p>"
            f"{подробности}</body>"
        )
        return Response(content=страница, media_type="text/html; charset=utf-8", status_code=200)

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
    """Adds a verified company to the watch list."""
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


# --- Recording errors ------------------------------------------------------

# The packaged app wrote no log anywhere: on a failure the window showed the line
# "Internal Server Error", and there was nowhere to learn what had actually
# broken. We write to a file next to the data and tell the person where it is.
LOG_PATH = profiles.DATA_ROOT / "errors.log"


def _log_error(request: Request, exc: BaseException) -> str:
    from datetime import datetime as _dt
    text = "".join(traceback.format_exception(type(exc), exc, exc.__traceback__))
    entry = f"\n{'=' * 70}\n{_dt.now().isoformat(timespec='seconds')}  {request.method} {request.url}\n{text}"
    try:
        profiles.DATA_ROOT.mkdir(parents=True, exist_ok=True)
        with LOG_PATH.open("a", encoding="utf-8") as fh:
            fh.write(entry)
        # the file must not grow forever
        if LOG_PATH.stat().st_size > 512_000:
            tail = LOG_PATH.read_text(encoding="utf-8", errors="ignore")[-256_000:]
            LOG_PATH.write_text(tail, encoding="utf-8")
    except OSError:
        pass
    print(entry, file=sys.stderr)
    return text


@app.exception_handler(Exception)
async def on_error(request: Request, exc: Exception):
    """An error page instead of a bare "Internal Server Error"."""
    text = _log_error(request, exc)
    last = text.strip().splitlines()[-1][:300] if text.strip() else str(exc)[:300]
    lang = "en"
    try:
        lang = config.load().get("ui", {}).get("lang", "en")
    except Exception:  # noqa: BLE001 — an error page must not fail a second time
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


# --- First launch ----------------------------------------------------------

# Pages there is no point showing before a model is chosen: without one nothing
# works. Everything else (static files, download status, changing the language)
# has to stay reachable, or the introduction itself could not work.
_PAGES = {"/", "/simple", "/results", "/coverage", "/cv/check", "/notify", "/models"}


@app.middleware("http")
async def first_run_gate(request: Request, call_next):
    """Without a model that works there is one screen worth showing, and this is
    the one place that can insist on it.

    The profile is settled here as well as in the middleware above: this one
    wraps that one (Starlette runs the last-registered outermost), and the model
    being asked about belongs to the profile the request came for.
    """
    if request.method == "GET" and request.url.path in _PAGES:
        _activate_profile(request)
        if appstate.needs_setup():
            return RedirectResponse("/welcome", status_code=303)
    return await call_next(request)


# Зарегистрирована последней и потому срабатывает первой — см. пояснение к
# _from_this_program наверху файла.
@app.middleware("http")
async def only_from_here(request: Request, call_next):
    if _hostname(request.headers.get("host", "")) not in _LOCAL_HOSTS:
        return Response(content="Not this address.", status_code=403,
                        media_type="text/plain")
    if request.method not in _SAFE_METHODS and not _from_this_program(request):
        return Response(content="Not from this program.", status_code=403,
                        media_type="text/plain")
    return await call_next(request)


# --- Что странице позволено ----------------------------------------------------
#
# Ни одного заголовка безопасности программа не ставила. Сами по себе они ничего
# не чинят — они ограничивают ущерб, когда что-то другое уже не удержало: чужой
# текст просочился в страницу, ответ не того типа, ссылка наружу.
#
# Главное здесь — connect-src и form-action. Страница этой программы знает адрес
# почтового пароля, токена Telegram и ключей от моделей. Скрипт, попавший на неё
# любым способом, прочесть их сможет, а вот отправить наружу — уже нет: некуда.
#
# 'unsafe-inline' стоит потому, что вёрстка на нём и держится: пятьдесят
# обработчиков прямо в разметке, десяток блоков <script> и сотни style="…".
# Убрать его — переписать все шаблоны на nonce, и это отдельная работа, а не
# строчка в заголовке. Зато внешних ресурсов у программы нет ни одного, так что
# default-src 'self' ничего не ломает и запрещает подтянуть что угодно со стороны.
CSP = "; ".join((
    "default-src 'self'",
    "script-src 'self' 'unsafe-inline'",
    "style-src 'self' 'unsafe-inline'",
    "img-src 'self' data:",
    "connect-src 'self'",       # наружу не дозвониться
    "form-action 'self'",       # и форму на чужой адрес не отправить
    "frame-ancestors 'none'",   # и в чужую рамку нас не вставить
    "base-uri 'none'",
    "object-src 'none'",
))

ЗАГОЛОВКИ = {
    "Content-Security-Policy": CSP,
    # Ответ читается как тот тип, который мы назвали, а не как тот, который
    # браузеру померещился по содержимому.
    "X-Content-Type-Options": "nosniff",
    # Уходя по ссылке на вакансию, человек не должен приносить чужому сайту
    # адрес страницы, с которой ушёл. Наружу отсылка и не уходит.
    #
    # Не no-referrer, хотя он тут и просился: при нём Chromium — а с ним и
    # встроенный браузер окна — шлёт при отправке формы «Origin: null», и
    # программа отказывала своему же окну. Ради приличия к чужим сайтам она
    # переставала работать вовсе. same-origin даёт ровно то же снаружи: чужой
    # сайт не узнаёт ничего.
    "Referrer-Policy": "same-origin",
    "X-Frame-Options": "DENY",
}


@app.middleware("http")
async def with_headers(request: Request, call_next):
    ответ = await call_next(request)
    for имя, значение in ЗАГОЛОВКИ.items():
        ответ.headers.setdefault(имя, значение)
    return ответ


@app.post("/provider/install")
async def provider_install(request: Request):
    """Opens the download page in the browser.

    Installing the program for someone is not ours to do — that is an installer
    asking for administrator rights. But retelling it in words, "download it
    from the website", is poor too: we open the right page ourselves, exactly
    the one written down in the code.
    """
    form = await request.form()
    key = str(form.get("provider", ""))
    back = str(form.get("back") or "/models")
    cfg_ = config.load()
    provs = providers.available(cfg_["llm"].get("claude_bin", "claude"), cfg_["llm"])
    url = provs.get(key, {}).get("install_url", "")
    anchor = str(form.get("anchor", ""))
    if not url:
        return _redirect_to(back, _msg("msg_install_unknown"), anchor=anchor)
    webbrowser.open(url)
    return _redirect_to(back, _msg("msg_install_opened", name=_msg("prov_" + key)),
                        anchor=anchor)


@app.post("/provider/recheck")
async def provider_recheck(request: Request):
    """Check again whether the program has appeared — without restarting the app."""
    form = await request.form()
    back = str(form.get("back") or "/models")
    providers.forget_binaries()
    cfg = config.load()
    provs = providers.available(cfg["llm"].get("claude_bin", "claude"), cfg["llm"])
    key = str(form.get("provider") or cfg["llm"].get("provider", "claude_cli"))
    ready = bool(provs.get(key, {}).get("ready"))
    name = _msg("prov_" + key)
    # back to the card that was asked about, not to the top of the page
    return _redirect_to(back, _msg("msg_recheck_found" if ready else "msg_recheck_none", name=name),
                        anchor=str(form.get("anchor", "")))


@app.post("/tool/install")
async def tool_install(request: Request):
    """Открывает страницу загрузки того, без чего провайдера не установить.

    Отдельно от /provider/install, потому что это разные вещи: там ставят саму
    программу, здесь — то, чем её ставят. Адрес берётся из таблицы по коду, как
    и у пожертвований: со страницы приходит только код, и открыть по этой кнопке
    произвольный адрес нельзя.
    """
    form = await request.form()
    key = str(form.get("tool", ""))
    back = str(form.get("back") or "/welcome?step=tools")
    url = providers.TOOLS.get(key, {}).get("url", "")
    if not url:
        return _redirect_to(_reachable(back), _msg("msg_install_unknown"))
    webbrowser.open(url)
    return _redirect_to(_reachable(back), _msg("msg_install_opened", name=_msg("tool_" + key)))


@app.post("/tool/recheck")
async def tool_recheck(request: Request):
    """Посмотреть заново — человек только что вернулся с установки."""
    form = await request.form()
    back = str(form.get("back") or "/welcome?step=tools")
    key = str(form.get("tool", ""))
    # Про незнакомый код сказать нечего, а сказать что-нибудь всё равно придётся —
    # и в сообщение уехал бы сам ключ перевода, «tool_чтототам». Это мы уже
    # проходили: непереведённый ключ на экране выглядит как поломка.
    if key not in providers.TOOLS:
        return _redirect_to(_reachable(back), _msg("msg_install_unknown"))
    providers.forget_binaries()
    ready, version = providers.tool_state(key)
    name = _msg("tool_" + key)
    if ready:
        msg = _msg("msg_recheck_found", name=name)
    elif version:
        # Нашёлся, но старый. Сказать «не найден» тому, у кого он есть, — значит
        # отправить его ставить второй раз то же самое.
        msg = _msg("tool_too_old", name=name, found=version,
                   need=providers.TOOLS.get(key, {}).get("min_major", ""))
    else:
        msg = _msg("msg_recheck_none", name=name)
    return _redirect_to(_reachable(back), msg)


# External addresses are listed here: the page passes only a key, so a button
# cannot be made to open an arbitrary address.
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
    return RedirectResponse(_here(back), status_code=303)


@app.get("/welcome")
def welcome(request: Request, msg: str = "", step: str = ""):
    """Знакомство идёт по шагам: сперва чем считать, потом какой моделью.

    Оба вопроса стояли на одной странице, и человек читал их как один. Под
    заголовком «Какой моделью» у выбранного Claude Code он видел кнопку
    «Скачать» и подпись про Ollama — и спрашивал, надо ли скачивать модель на
    компьютер. Это разные вопросы, и задавать их надо порознь.
    """
    cfg = config.load()
    provider = cfg["llm"].get("provider", "claude_cli")
    provs = providers.available(cfg["llm"].get("claude_bin", "claude"), cfg["llm"])
    catalog = providers.models_for(provider, lang=_lang(cfg))
    current_model = providers.current_model(cfg["llm"])
    chosen = next((m for m in catalog if m["id"] == current_model), None)
    # Which of the two is missing, so the page can say so. Both used to be
    # explained as "the tool is not installed" — and that sent a person off to
    # install Ollama, which was running perfectly well; what was missing was
    # the model inside it. The same answer decides whether the app opens at all,
    # so it is worked out in one place for both.
    blocked_by = providers.missing_piece(cfg["llm"])
    # Чего не хватает, чтобы установить саму программу. Спрашиваем только про
    # выбранного провайдера и только когда он ещё не установлен: остальным семи
    # это стоило бы по запуску node на каждую отрисовку страницы ни за чем.
    tool = providers.missing_tool(provider) if blocked_by == "provider" else ""
    # На второй шаг нечего идти, пока не решён первый: без установленной
    # программы список моделей — это список того, чем всё равно не воспользуешься.
    if step == "model" and blocked_by == "provider":
        step = "tools" if tool else ""
    # И обратно: экран «что нужно установить» не должен становиться тупиком.
    # Нужное поставили — человека там больше нечем занять.
    if step == "tools" and not tool:
        step = "model" if blocked_by == "model" else ""
    return render(request, "welcome.html", {
        # cfg нужен самой странице: у своего адреса на втором шаге вместо списка
        # моделей стоят поля, и значения в них берутся из настроек
        "cfg": cfg,
        "msg": msg, "provs": provs, "current_provider": provider, "step": step,
        "current_model": current_model, "models": catalog,
        "current_model_name": chosen["name"] if chosen else current_model,
        "specs": hardware.specs(),
        "provider_ready": blocked_by != "provider",
        # going on makes sense only if the chosen provider can really do the thinking
        "ready": not blocked_by,
        "blocked_by": blocked_by,
        "tool": providers.tool_info(tool) if tool else None,
        # Somebody arriving here after a reinstall has data to clear; somebody
        # genuinely new has nothing, and should not be shown the offer at all.
        "can_reset": appstate.has_data(),
    }, cfg=cfg)


@app.post("/welcome/done")
def welcome_done():
    """The button is disabled without a model, which settles it for the page but
    not for the address: this is the last check before the app opens."""
    cfg = config.load()
    if providers.missing_piece(cfg["llm"]):
        return _redirect_to("/welcome", _msg("welcome_blocked"))
    appstate.mark_setup_done(cfg["llm"])
    return RedirectResponse("/simple", status_code=303)


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="127.0.0.1", port=8765)