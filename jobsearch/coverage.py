"""Проверка покрытия: видит ли скрипт вакансии конкретной компании.

Отвечает на вопрос «а компанию X мы вообще просматриваем?» — принимает
название/URL, определяет способ чтения (прямой ATS, встроенный ATS, краулинг)
и сколько вакансий доступно. Это ручная проверка охвата для пользователя.
"""
from urllib.parse import urlparse

import requests

from . import config, i18n, llm
from .collectors import ats, crawler

UA = {"User-Agent": "Mozilla/5.0 (Macintosh) ai-job-search/1.0"}
TIMEOUT = 25

FIND_URL_PROMPT = """Найди через веб-поиск официальную страницу вакансий (careers/jobs) компании «{name}».
Предпочитай прямую ссылку на ATS (boards.greenhouse.io/..., jobs.lever.co/...,
jobs.ashbyhq.com/..., apply.workable.com/..., *.recruitee.com, *.jobs.personio.de),
иначе — careers-страницу на сайте компании. Не давай ссылки на LinkedIn/агрегаторы.
Верни ТОЛЬКО JSON: {{"careers_url": "https://...", "found": true}} или {{"found": false}}."""


def _domain(url: str) -> str:
    host = urlparse(url if "://" in url else "https://" + url).netloc.lower()
    return host.removeprefix("www.")


def _identity(url: str) -> str:
    """Идентичность компании: для общих ATS-хостов (greenhouse/lever/ashby/...)
    это платформа+slug, иначе — домен. Иначе GitLab и Anthropic на общем
    boards.greenhouse.io выглядели бы «одной компанией»."""
    detected = ats.detect(url)
    if detected:
        return f"{detected[0]}:{detected[1].lower()}"
    return _domain(url)


def _resolve_url(name: str, cfg: dict) -> str:
    """Для названия без URL — находит careers-страницу веб-поиском."""
    try:
        data = llm.ask_json(
            FIND_URL_PROMPT.format(name=name),
            model=cfg["llm"].get("triage_model", "haiku"),
            claude_bin=cfg["llm"].get("claude_bin", "claude"),
            provider=cfg["llm"].get("provider", "claude_cli"),
            timeout=300, allowed_tools=["WebSearch", "WebFetch"],
        )
    except llm.AuthError:
        raise      # без входа в модель прогон смысла не имеет
    except llm.ClaudeError:
        return ""
    if isinstance(data, dict) and data.get("found") and str(data.get("careers_url", "")).startswith("http"):
        return str(data["careers_url"])
    return ""


def _st(key: str, **fmt) -> str:
    """Статус проверки на языке интерфейса."""
    text = i18n.t(config.load()["ui"]["lang"], key)
    return text.format(**fmt) if fmt else text


def check_one(entry: str, cfg: dict, monitored_domains: set) -> dict:
    """entry — 'Название | URL', либо просто URL, либо просто название."""
    entry = entry.strip()
    name, url = "", ""
    if "|" in entry:
        name, _, url = entry.partition("|")
        name, url = name.strip(), url.strip()
    elif entry.startswith("http") or "." in entry.split()[0] if entry else False:
        url = entry
    else:
        name = entry

    resolved = False
    if not url and name:
        url = _resolve_url(name, cfg)
        resolved = bool(url)

    result = {"name": name or _domain(url), "url": url, "monitored": False,
              "status": "", "platform": "", "count": 0, "resolved_by_search": resolved}

    if not url:
        result["status"] = _st("cov_st_notfound")
        return result

    result["monitored"] = _identity(url) in monitored_domains

    # 1. Прямая ATS-ссылка
    detected = ats.detect(url)
    if detected:
        try:
            jobs = ats.fetch(detected[0], detected[1])
            result.update(status=_st("cov_st_api"), platform=detected[0], count=len(jobs))
            return result
        except Exception as e:  # noqa: BLE001
            result.update(status=_st("cov_st_ats_fail", error=str(e)[:100]), platform=detected[0])
            return result

    # 2. Встроенный ATS в HTML / переход на подстраницу — используем сам краулер
    try:
        jobs = crawler.crawl_company(name or _domain(url), url, cfg, lambda m: None)
        if jobs:
            src = jobs[0].get("source", "crawl")
            result.update(status=_st("cov_st_ok"), platform=src, count=len(jobs))
            return result
    except Exception as e:  # noqa: BLE001
        result.update(status=_st("cov_st_error", error=str(e)[:120]))
        return result

    # 3. Fallback: угадать ATS-борд по названию (careers-страница на JS без API)
    guess_name = name or _domain(url).split(".")[0]
    guessed = ats.guess_by_name(guess_name)
    if guessed:
        result.update(status=_st("cov_st_ok_guessed"),
                      platform=guessed[0], count=len(guessed[2]))
    else:
        result.update(status=_st("cov_st_zero"))
    return result


def check(entries: list, cfg: dict) -> list:
    monitored = {_identity(c["url"]) for c in cfg["sources"].get("companies", []) if c.get("url")}
    workers = int(cfg["search"].get("parallelism", 5))
    results = llm.pmap(lambda e: check_one(e, cfg, monitored), entries, workers=workers)
    out = []
    for i, r in enumerate(results):
        if isinstance(r, Exception):
            out.append({"name": entries[i] if i < len(entries) else "?", "url": "",
                        "status": _st("cov_st_error", error=str(r)[:120]), "count": 0})
        else:
            out.append(r)
    return out
