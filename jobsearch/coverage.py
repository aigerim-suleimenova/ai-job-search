"""A coverage check: does the program see a particular company's jobs at all?

It answers the question "are we even looking at company X?" — it takes a name or
a URL, works out how the jobs can be read (a direct ATS, an embedded ATS,
crawling) and how many are available. This is a reach check a person runs by hand.
"""
from urllib.parse import urlparse

import requests

from . import config, i18n, llm
from .collectors import web
from .collectors import ats, crawler

UA = web.UA   # an honest name rather than pretending to be a browser
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
    """A company's identity: for shared ATS hosts (greenhouse/lever/ashby/…) that is
    platform+slug, otherwise the domain. Without this GitLab and Anthropic on the
    shared boards.greenhouse.io would look like "one company"."""
    detected = ats.detect(url)
    if detected:
        return f"{detected[0]}:{detected[1].lower()}"
    return _domain(url)


def _resolve_url(name: str, cfg: dict) -> str:
    """For a name with no URL — finds the careers page by web search."""
    try:
        data = llm.ask_json(
            FIND_URL_PROMPT.format(name=name),
            model=cfg["llm"].get("triage_model", "haiku"),
            claude_bin=cfg["llm"].get("claude_bin", "claude"),
            provider=cfg["llm"].get("provider", "claude_cli"),
            llm=cfg["llm"],
            timeout=300, allowed_tools=["WebSearch", "WebFetch"],
        )
    except llm.AuthError:
        raise      # without a signed-in model the run makes no sense
    except llm.ClaudeError:
        return ""
    if isinstance(data, dict) and data.get("found") and str(data.get("careers_url", "")).startswith("http"):
        return str(data["careers_url"])
    return ""


def _st(key: str, **fmt) -> str:
    """The state of the check, in the interface language."""
    text = i18n.t(config.load()["ui"]["lang"], key)
    return text.format(**fmt) if fmt else text


def check_one(entry: str, cfg: dict, monitored_domains: set) -> dict:
    """entry — 'Name | URL', or just a URL, or just a name."""
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

    # 1. A direct ATS link
    detected = ats.detect(url)
    if detected:
        try:
            jobs = ats.fetch(detected[0], detected[1])
            result.update(status=_st("cov_st_api"), platform=detected[0], count=len(jobs))
            return result
        except Exception as e:  # noqa: BLE001
            result.update(status=_st("cov_st_ats_fail", error=str(e)[:100]), platform=detected[0])
            return result

    # 2. An ATS embedded in the HTML, or a jump to a sub-page — let the crawler do it
    try:
        jobs = crawler.crawl_company(name or _domain(url), url, cfg, lambda m: None)
        if jobs:
            src = jobs[0].get("source", "crawl")
            result.update(status=_st("cov_st_ok"), platform=src, count=len(jobs))
            return result
    except Exception as e:  # noqa: BLE001
        result.update(status=_st("cov_st_error", error=str(e)[:120]))
        return result

    # 3. Fallback: guess the ATS board from the name (a JS careers page with no API)
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
