"""Оркестрация одного прогона поиска."""
import json
import os
import threading
import time
import traceback

from . import (config, db, discovery, filters, i18n, llm, mailer, notify, profiles,
               providers, scoring)
from .collectors import aggregators, ats, crawler

_run_lock = threading.Lock()
_stop = threading.Event()
state = {"running": False, "stage": "", "log": [], "profile": ""}


class Stopped(RuntimeError):
    """Прогон прерван пользователем."""


def request_stop() -> None:
    """Просит текущий прогон остановиться на ближайшей контрольной точке."""
    _stop.set()
    llm.set_cancel()   # длинные параллельные этапы прерываются, не дожидаясь конца


def stop_requested() -> bool:
    return _stop.is_set()


def _check_stop() -> None:
    if _stop.is_set():
        raise Stopped()


def _log(msg: str) -> None:
    state["log"].append(msg)
    if len(state["log"]) > 400:
        del state["log"][:100]



# Порядок этапов прогона: по нему страница показывает «шаг N из M».
# Часть этапов пропускается (веб-поиск выключен, модель не умеет искать),
# поэтому номера могут перескакивать — это честнее, чем рисовать проценты.
STAGE_ORDER = ["stage_cv", "stage_start", "stage_discover", "stage_discover_ats",
               "stage_collect", "stage_dedupe", "stage_prepare", "stage_triage",
               "stage_deep", "stage_deep_research", "stage_save"]
STAGE_COUNT = len(STAGE_ORDER) - 1   # deep и deep_research — одно и то же место

def _logk(key: str, **fmt) -> None:
    """Строка журнала на языке интерфейса.

    Журнал видит человек — значит, он не может быть всегда русским, как бы
    удобно это ни было при написании кода.
    """
    text = i18n.t(config.load()["ui"]["lang"], key)
    _log(text.format(**fmt) if fmt else text)


def _stage(key: str) -> None:
    """Отмечает этап. В state лежит ключ перевода, а не готовая строка: язык
    подставит страница, которая её показывает."""
    _check_stop()          # между этапами — самое безопасное место, чтобы прерваться
    state["stage"] = key
    state["stage_started"] = time.time()
    state["progress"] = None      # счётчик внутри этапа — только у длинных
    state["step"] = STAGE_ORDER.index(key) + 1 if key in STAGE_ORDER else state.get("step", 1)
    _log("— " + i18n.t(config.load()["ui"]["lang"], key))


def run_async(trigger: str = "manual", profile: str = None) -> bool:
    """Запускает прогон в фоновом потоке. False, если уже идёт."""
    if state["running"]:
        return False
    profile = profile or profiles.active()
    t = threading.Thread(target=run, args=(trigger, profile), daemon=True)
    t.start()
    return True


def prepare_and_run(profile: str, trigger: str = "manual") -> bool:
    """Разбор резюме моделью занимает до полутора минут. Раньше это делалось прямо
    в обработчике формы, и человек всё это время смотрел на застывшую страницу без
    единого признака жизни. Теперь подготовка — такая же видимая стадия прогона."""
    if state["running"]:
        return False

    def worker():
        profiles.set_active(profile)
        state.update(running=True, stage="stage_cv", step=1, log=[], profile=profile)
        _logk("log_cv_parse")
        try:
            cfg = config.load()
            cv = config.cv_text()
            if cv and not (cfg["profile"].get("roles") or cfg["profile"].get("summary")):
                cfg = scoring.profile_from_cv(cfg, cv)
                config.save(cfg)
                _logk("log_cv_roles", roles=cfg["profile"].get("roles", "")[:80])
        except Exception as e:  # noqa: BLE001 — не смогли разобрать, ищем по тому, что есть
            _logk("log_cv_failed", error=e)
        finally:
            state.update(running=False)
        run(trigger, profile)

    threading.Thread(target=worker, daemon=True).start()
    return True


def run(trigger: str = "manual", profile: str = None) -> None:
    if not _run_lock.acquire(blocking=False):
        return
    if profile:
        profiles.set_active(profile)  # свой поток → задаём активный профиль явно
    _stop.clear()
    llm.clear_cancel()
    llm.mark_run_thread()
    state.update(started=time.time(), stage_started=time.time(), progress=None)
    state.update(running=True, stage="stage_start", step=1, log=[], profile=profiles.active())
    cfg = config.load()
    cv = config.cv_text()
    run_id = db.start_run()
    found = fresh = matched_count = 0
    status = "ok"
    coverage = []
    try:
        _logk("log_run", n=run_id, trigger=trigger)

        # 0. Поиск новых компаний под профиль (веб-поиск через claude).
        # Веб-поиск умеет только Claude Code CLI: на других провайдерах этот шаг
        # пропускается, иначе модель начнёт выдумывать компании и ссылки.
        web_ok = providers.supports_web_search(cfg["llm"].get("provider", "claude_cli"))
        n_disc = int(cfg["search"].get("discover_per_run", 0))
        if n_disc > 0 and not web_ok:
            _logk("log_discover_skipped")
            _logk("log_discover_skipped_why")
        if n_disc > 0 and web_ok:
            _stage("stage_discover")
            fresh_companies = discovery.discover(cfg, _log, n=n_disc)
            if fresh_companies:
                cfg["sources"]["companies"] = cfg["sources"].get("companies", []) + fresh_companies
                config.save(cfg)
                for f in fresh_companies:
                    _logk("log_new_company", name=f["name"], url=f["url"])
            else:
                _logk("log_no_new_companies")

        # 0b. Поиск вакансий прямо на доменах ATS (site:boards.greenhouse.io ...) —
        # каждая находка даёт и вакансию, и новую компанию, чью доску забираем целиком.
        n_ats = int(cfg["search"].get("discover_ats_per_run", 0))
        if n_ats > 0 and web_ok:
            _stage("stage_discover_ats")
            ats_companies = discovery.discover_ats_jobs(cfg, _log, n=n_ats)
            if ats_companies:
                cfg["sources"]["companies"] = cfg["sources"].get("companies", []) + ats_companies
                config.save(cfg)
            else:
                _logk("log_no_new_boards")

        # 1. Сбор
        _stage("stage_collect")
        jobs = []
        def _company(comp):
            name, url = comp.get("name", ""), comp.get("url", "")
            if not url:
                return None
            detected = ats.detect(url)
            entry = {"name": name or url, "url": url,
                     "kind": f"ATS: {detected[0]}" if detected else "crawl",
                     "count": 0, "error": None}
            try:
                if detected:
                    got = ats.fetch(detected[0], detected[1], company_hint=name)
                else:
                    got = crawler.crawl_company(name or url, url, cfg, _log)
                for j in got:  # компании из списка пользователя всегда идут на LLM-оценку
                    j["from_watchlist"] = True
                entry["count"] = len(got)
                _log(f"{name or url} [{entry['kind']}]: {len(got)}")
            except Exception as e:  # noqa: BLE001 — один источник не должен ронять прогон
                _log(f"{name or url}: {e}")
                entry["error"] = str(e)[:300]
                got = []
            return entry, got

        workers = int(cfg["search"].get("parallelism", 5))
        for r in llm.pmap(_company, cfg["sources"].get("companies", []), workers=workers):
            if isinstance(r, Exception) or r is None:
                continue
            entry, got = r
            coverage.append(entry)
            jobs += got
        jobs += aggregators.collect(cfg, _log, coverage)
        found = len(jobs)
        _logk("log_collected", n=found)

        # 2. Нормализация и дедупликация (прямые вакансии приоритетнее)
        _stage("stage_dedupe")
        by_key = {}
        for j in jobs:
            j["key"] = filters.job_key(j.get("company", ""), j.get("title", ""))
            prev = by_key.get(j["key"])
            if prev is None or (j.get("is_direct") and not prev.get("is_direct")):
                by_key[j["key"]] = j
        jobs = list(by_key.values())

        # 3. Жёсткие фильтры (с примерами отсева в журнале — чтобы ловить ложные срабатывания)
        wanted = filters.parse_locations(cfg["search"].get("locations", ""))
        exclude = [t.strip().lower() for t in cfg["search"].get("keywords_exclude", "").split(",") if t.strip()]
        include_remote = bool(cfg["search"].get("include_remote", True))
        kept, drop_loc, drop_kw = [], [], []
        for j in jobs:
            if not filters.location_ok(j.get("location", ""), wanted, include_remote):
                drop_loc.append(j)
            elif filters.has_excluded(j, exclude):
                drop_kw.append(j)
            else:
                kept.append(j)
        jobs = kept
        for j in jobs:
            if filters.looks_like_agency(j.get("company", "")):
                j["is_agency"] = True
        _logk("log_after_filters", n=len(jobs), loc=len(drop_loc), kw=len(drop_kw))
        for j in drop_loc[:5]:
            _logk("log_drop_location", title=j.get("title", "")[:60], loc=j.get("location", "")[:60])
        for j in drop_kw[:5]:
            _logk("log_drop_keyword", title=j.get("title", "")[:60], company=j.get("company", "")[:40])

        # 4. Только новые
        seen = db.seen_keys()
        jobs = [j for j in jobs if j["key"] not in seen]
        fresh = len(jobs)
        _logk("log_fresh", n=fresh)

        # 5. Порядок и верхний предел на триаж (при параллельном триаже успеваем оценить всё).
        # Приоритет — вакансиям компаний из списка мониторинга, затем по лексике.
        _stage("stage_prepare")
        terms = scoring.profile_terms(cfg, cv)

        # 5a. Дешёвый пре-фильтр: отсекаем заведомо не ту профессию (продажи/HR/саппорт)
        # ДО дорогой LLM-оценки — освобождает бюджет триажа под реальных кандидатов.
        if cfg["search"].get("drop_off_target", True):
            before = len(jobs)
            jobs = [j for j in jobs if not filters.off_target(j, terms)]
            dropped = before - len(jobs)
            # Отсеянных НЕ помечаем виденными: проверка бесплатная (строки, без LLM)
            # и повторится в следующем прогоне, зато при смене ролей/навыков профиля
            # такие вакансии автоматически вернутся в рассмотрение.
            if dropped:
                _logk("log_dropped_offtarget", n=dropped)

        for j in jobs:
            j["_lex"] = scoring.lexical_score(j, terms)
        limit = int(cfg["search"].get("triage_limit", 400))
        watch = sorted((j for j in jobs if j.get("from_watchlist")), key=lambda j: -j["_lex"])
        others = sorted((j for j in jobs if not j.get("from_watchlist")), key=lambda j: -j["_lex"])
        candidates = (watch + others)[:limit]
        deferred = (watch + others)[limit:]  # не помечаем виденными — дойдут в след. прогонах
        _logk("log_to_triage", n=len(candidates), own=min(len(watch), limit))
        if deferred:
            _logk("log_deferred", n=len(deferred))

        # 6. LLM-триаж (haiku, параллельно)
        _stage("stage_triage")
        if not (cfg["profile"].get("roles") or cfg["profile"].get("summary") or cv):
            _logk("log_empty_profile")
            _logk("log_empty_profile_fix")
        # Кладём в базу по мере оценки: человек мог открыть «Результаты» сразу
        # после запуска, и ждать конца прогона, чтобы увидеть первую строку, — плохо.
        def _save_batch(batch, done=0, total=0):
            for j in batch:
                if j.get("score") is not None:
                    db.save_job(j, run_id)
            if total:
                state["progress"] = {"done": done, "total": total}

        scoring.triage(candidates, cfg, _log, cv=cv, on_batch=_save_batch)
        threshold = int(cfg["search"].get("threshold", 70))
        scored = [j for j in candidates if j.get("score") is not None]

        # 6b. Второе мнение для «серой зоны». Триаж шумит ±15-20 п.п.; завышение потом
        # исправит глубокий разбор, а ЗАНИЖЕНИЕ невосполнимо: вакансия сохранится с низким
        # баллом и больше не пересматривается. Поэтому всем, кто попал заметно ниже порога,
        # даём второй независимый голос и берём максимум из двух.
        if cfg["search"].get("triage_second_vote", True):
            band = [j for j in scored if threshold - 40 <= (j["score"] or 0) < threshold]
            if band:
                _logk("log_second_vote", n=len(band), lo=threshold - 40, hi=threshold - 1)
                first = {j["key"]: (j["score"], j.get("reason", "")) for j in band}
                scoring.triage(band, cfg, _log, cv=cv)
                rescued = 0
                for j in band:
                    s1, r1 = first[j["key"]]
                    if (j.get("score") or 0) < (s1 or 0):  # первый голос был выше — берём его
                        j["score"], j["reason"] = s1, r1
                    if (j.get("score") or 0) >= threshold and (s1 or 0) < threshold:
                        rescued += 1
                if rescued:
                    _logk("log_second_vote_rescued", n=rescued)

        # Триаж (haiku) систематически оптимистичен и шумит ±15-20 п.п.
        # ВСЕ вакансии, которые триаж поднял до порога, проверяем глубоко — именно они
        # формируют дайджест, и без проверки туда просачиваются ложные проходы.
        # Плюс запас near-miss'ов чуть ниже порога — ловим занижения триажа (как Matcha 70→82).
        keep_margin = 20
        above = sorted((j for j in scored if j["score"] >= threshold),
                       key=lambda j: (not j.get("from_watchlist"), -j["score"]))
        near = sorted((j for j in scored if threshold - keep_margin <= j["score"] < threshold),
                      key=lambda j: (not j.get("from_watchlist"), -j["score"]))
        top_n = int(cfg["search"].get("deep_top_n", 15))
        max_deep = max(top_n, len(above)) + 5  # мягкий потолок, чтобы не разориться
        to_deep = (above + near[:top_n])[:max_deep]
        for j in scored:  # остальные сохраняем сразу с триажным баллом
            if j not in to_deep:
                db.save_job(j, run_id)
        _logk("log_triage_done", n=len(scored), above=len(above),
              near=len(to_deep) - len(above), margin=threshold - keep_margin)

        # 7. Глубокий разбор (параллельно): точный %, правки CV и LinkedIn,
        # плюс, если включено, зарплата и факты о компании из веб-поиска
        research = bool(cfg["search"].get("research_company", True))
        _stage("stage_deep_research" if research else "stage_deep")
        deep_done = {"n": 0}

        def _deep(j):
            was = j.get("score")
            scoring.deep_analyze(j, cfg, cv, _log, research=research)
            deep_done["n"] += 1
            tail = f" ({was}%→{j.get('score')}%)" if j.get("score") != was else ""
            state["progress"] = {"done": deep_done["n"], "total": len(to_deep)}
            _logk("log_deep_item", i=deep_done["n"], total=len(to_deep),
                  title=j.get("title"), company=j.get("company"), tail=tail)

        # глубокие вызовы тяжёлые (запрос страницы + длинный промпт) — параллелим осторожнее
        deep_workers = min(int(cfg["search"].get("parallelism", 5)), 3)
        for r in llm.pmap(_deep, to_deep, workers=deep_workers):
            if isinstance(r, Exception):
                deep_done["n"] += 1
                _logk("log_deep_error", error=r)
        for j in to_deep:
            db.save_job(j, run_id)

        matched = [j for j in scored if (j.get("score") or 0) >= threshold]
        matched.sort(key=lambda j: (not (j.get("is_direct") and not j.get("is_agency")), -(j.get("score") or 0)))
        final = matched
        # «близко, но ниже порога» для дайджеста — только разобранные, чтобы не шуметь триажными
        demoted = sorted(
            (j for j in to_deep if (j.get("score") or 0) < threshold and (j.get("score") or 0) >= threshold - 15),
            key=lambda j: -(j.get("score") or 0),
        )[:5]
        matched_count = len(final)

        # 8. Сохранение и уведомление
        _stage("stage_save")
        for j in matched:
            db.save_job(j, run_id)
        base_url = os.environ.get("AIJS_BASE_URL", "http://127.0.0.1:8765")
        digest = notify.format_digest(final, threshold, base_url=base_url, demoted=demoted,
                                      lang=cfg.get("ui", {}).get("output_lang", "ru"))
        if cfg["telegram"].get("bot_token") and cfg["telegram"].get("chat_id"):
            try:
                notify.send_message(cfg, digest)
                _logk("log_tg_sent")
            except RuntimeError as e:
                _log(f"Telegram: {e}")
                status = "warn"
        else:
            _logk("log_tg_not_set")
        if mailer.configured(cfg):
            try:
                mailer.send_digest(cfg, final, profiles.name_of(profiles.active()),
                                   db.now(), threshold)
                _logk("log_mail_sent")
            except mailer.MailError as e:
                _logk("log_mail_error", error=i18n.t(cfg["ui"]["lang"], e.key).format(**e.fmt))
                status = "warn"
        _logk("log_done")
    except (Stopped, llm.Cancelled):
        status = "stopped"
        _logk("log_stopped")
    except llm.AuthError as e:
        # Вакансии собрать можно и без модели, но оценить их — нельзя, поэтому
        # прогон останавливается здесь, а не молча доходит до конца с нулём.
        status = "noauth"
        _logk("log_noauth", error=e)
        _logk("log_noauth_stop")
    except Exception:  # noqa: BLE001
        status = "error"
        _log(i18n.t(config.load()["ui"]["lang"], "log_error") + "\n" + traceback.format_exc(limit=6))
    finally:
        db.finish_run(run_id, found, fresh, matched_count, status, "\n".join(state["log"]),
                      coverage=json.dumps(coverage, ensure_ascii=False))
        state.update(running=False, stage="")
        _stop.clear()
        llm.clear_cancel()
        _run_lock.release()
