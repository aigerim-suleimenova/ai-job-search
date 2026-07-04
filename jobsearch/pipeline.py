"""Оркестрация одного прогона поиска."""
import json
import threading
import traceback

from . import config, db, discovery, filters, llm, notify, scoring
from .collectors import aggregators, ats, crawler

_run_lock = threading.Lock()
state = {"running": False, "stage": "", "log": []}


def _log(msg: str) -> None:
    state["log"].append(msg)
    if len(state["log"]) > 400:
        del state["log"][:100]


def _stage(name: str) -> None:
    state["stage"] = name
    _log(f"— {name}")


def run_async(trigger: str = "manual") -> bool:
    """Запускает прогон в фоновом потоке. False, если уже идёт."""
    if state["running"]:
        return False
    t = threading.Thread(target=run, args=(trigger,), daemon=True)
    t.start()
    return True


def run(trigger: str = "manual") -> None:
    if not _run_lock.acquire(blocking=False):
        return
    state.update(running=True, stage="старт", log=[])
    cfg = config.load()
    cv = config.cv_text()
    run_id = db.start_run()
    found = fresh = matched_count = 0
    status = "ok"
    coverage = []
    try:
        _log(f"Прогон #{run_id} ({trigger})")

        # 0. Поиск новых компаний под профиль (веб-поиск через claude)
        n_disc = int(cfg["search"].get("discover_per_run", 0))
        if n_disc > 0:
            _stage("поиск новых компаний (веб-поиск)")
            fresh_companies = discovery.discover(cfg, _log, n=n_disc)
            if fresh_companies:
                cfg["sources"]["companies"] = cfg["sources"].get("companies", []) + fresh_companies
                config.save(cfg)
                for f in fresh_companies:
                    _log(f"новая компания: {f['name']} — {f['url']}")
            else:
                _log("новых компаний не найдено")

        # 1. Сбор
        _stage("сбор вакансий")
        jobs = []
        def _company(comp):
            name, url = comp.get("name", ""), comp.get("url", "")
            if not url:
                return None
            detected = ats.detect(url)
            entry = {"name": name or url, "url": url,
                     "kind": f"ATS: {detected[0]}" if detected else "краулинг",
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
        _log(f"Собрано всего: {found}")

        # 2. Нормализация и дедупликация (прямые вакансии приоритетнее)
        _stage("дедупликация и фильтры")
        by_key = {}
        for j in jobs:
            j["key"] = filters.job_key(j.get("company", ""), j.get("title", ""))
            prev = by_key.get(j["key"])
            if prev is None or (j.get("is_direct") and not prev.get("is_direct")):
                by_key[j["key"]] = j
        jobs = list(by_key.values())

        # 3. Жёсткие фильтры
        wanted = filters.parse_locations(cfg["search"].get("locations", ""))
        exclude = [t.strip().lower() for t in cfg["search"].get("keywords_exclude", "").split(",") if t.strip()]
        include_remote = bool(cfg["search"].get("include_remote", True))
        jobs = [
            j for j in jobs
            if filters.location_ok(j.get("location", ""), wanted, include_remote)
            and not filters.has_excluded(j, exclude)
        ]
        for j in jobs:
            if filters.looks_like_agency(j.get("company", "")):
                j["is_agency"] = True
        _log(f"После фильтров локации/стоп-слов: {len(jobs)}")

        # 4. Только новые
        seen = db.seen_keys()
        jobs = [j for j in jobs if j["key"] not in seen]
        fresh = len(jobs)
        _log(f"Новых (не виденных ранее): {fresh}")

        # 5. Порядок и верхний предел на триаж (при параллельном триаже успеваем оценить всё).
        # Приоритет — вакансиям компаний из списка мониторинга, затем по лексике.
        _stage("подготовка к оценке")
        terms = scoring.profile_terms(cfg, cv)
        for j in jobs:
            j["_lex"] = scoring.lexical_score(j, terms)
        limit = int(cfg["search"].get("triage_limit", 400))
        watch = sorted((j for j in jobs if j.get("from_watchlist")), key=lambda j: -j["_lex"])
        others = sorted((j for j in jobs if not j.get("from_watchlist")), key=lambda j: -j["_lex"])
        candidates = (watch + others)[:limit]
        deferred = (watch + others)[limit:]  # не помечаем виденными — дойдут в след. прогонах
        _log(f"На LLM-триаж: {len(candidates)} (из них от ваших компаний: {min(len(watch), limit)})"
             + (f", отложено до следующего прогона: {len(deferred)}" if deferred else ""))

        # 6. LLM-триаж (haiku, параллельно)
        _stage("LLM-триаж")
        if not (cfg["profile"].get("roles") or cfg["profile"].get("summary") or cv):
            _log("ВНИМАНИЕ: профиль пуст и CV не загружено — оценки будут случайными. "
                 "Заполните «Профиль» на странице настроек.")
        scoring.triage(candidates, cfg, _log, cv=cv)
        threshold = int(cfg["search"].get("threshold", 70))
        scored = [j for j in candidates if j.get("score") is not None]

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
        _log(f"Оценено триажем: {len(scored)}. Глубоко проверяем: {len(above)} прошедших порог "
             f"+ {len(to_deep) - len(above)} близких (near-miss ≥{threshold - keep_margin}%)")

        # 7. Глубокий разбор (параллельно): точный %, правки CV и LinkedIn
        _stage("глубокий разбор и советы по CV")
        deep_done = {"n": 0}

        def _deep(j):
            was = j.get("score")
            scoring.deep_analyze(j, cfg, cv, _log)
            deep_done["n"] += 1
            tail = f" ({was}%→{j.get('score')}%)" if j.get("score") != was else ""
            _log(f"разбор {deep_done['n']}/{len(to_deep)}: {j.get('title')} @ {j.get('company')}{tail}")

        # глубокие вызовы тяжёлые (запрос страницы + длинный промпт) — параллелим осторожнее
        deep_workers = min(int(cfg["search"].get("parallelism", 5)), 3)
        for r in llm.pmap(_deep, to_deep, workers=deep_workers):
            if isinstance(r, Exception):
                deep_done["n"] += 1
                _log(f"разбор: {r}")
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
        _stage("сохранение и отправка")
        for j in matched:
            db.save_job(j, run_id)
        digest = notify.format_digest(final, threshold, base_url="http://127.0.0.1:8765", demoted=demoted)
        if cfg["telegram"].get("bot_token") and cfg["telegram"].get("chat_id"):
            try:
                notify.send_message(cfg, digest)
                _log("Дайджест отправлен в Telegram")
            except RuntimeError as e:
                _log(f"Telegram: {e}")
                status = "warn"
        else:
            _log("Telegram не настроен — дайджест только на странице результатов")
        _log("Готово")
    except Exception:  # noqa: BLE001
        status = "error"
        _log("ОШИБКА:\n" + traceback.format_exc(limit=6))
    finally:
        db.finish_run(run_id, found, fresh, matched_count, status, "\n".join(state["log"]),
                      coverage=json.dumps(coverage, ensure_ascii=False))
        state.update(running=False, stage="")
        _run_lock.release()
