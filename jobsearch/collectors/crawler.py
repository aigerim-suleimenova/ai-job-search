"""Careful crawling of careers pages that were not recognised as an ATS.

The page is downloaded, its visible text and list of links are taken from it, and
Claude (haiku) extracts a structured list of jobs out of that.
"""
from urllib.parse import urljoin, urlparse

import requests
from bs4 import BeautifulSoup

from .. import config, i18n, llm
from . import ats, web



def _lk(log, key: str, **fmt) -> None:
    """A log line in the interface language (log comes in from the pipeline)."""
    text = i18n.t(config.load()["ui"]["lang"], key)
    log(text.format(**fmt) if fmt else text)


TIMEOUT = web.TIMEOUT

EXTRACT_PROMPT = """Ниже — текст и ссылки со страницы вакансий компании «{name}» ({url}).
Извлеки список открытых вакансий. Верни ТОЛЬКО JSON-массив объектов вида
{{"title": "...", "url": "...", "location": "..."}} без пояснений.
Если url вакансии нет в списке ссылок — оставь пустую строку. Если вакансий нет — верни [].

ТЕКСТ СТРАНИЦЫ:
{text}

ССЫЛКИ:
{links}"""


def fetch_page(url: str):
    r = web.get(url, respect_robots=True)
    if r is None:
        raise PermissionError("robots.txt")   # the site asks robots not to come here
    r.raise_for_status()
    base_url = r.url  # sites redirect often (a domain change, a rebrand) — we
    # resolve relative links against the final URL, not the original one
    soup = BeautifulSoup(r.text, "html.parser")
    for tag in soup(["script", "style", "noscript", "svg"]):
        tag.decompose()
    text = " ".join(soup.get_text(" ", strip=True).split())
    links = []
    for a in soup.find_all("a", href=True):
        label = " ".join(a.get_text(" ", strip=True).split())[:120]
        href = urljoin(base_url, a["href"])
        if label and href.startswith("http"):
            links.append(f"{label} :: {href}")
    return text, links


def fetch_job_text(url: str, limit: int = 6000) -> str:
    """The text of one job's own page — for the deep analysis."""
    try:
        text, _ = fetch_page(url)
        return text[:limit]
    except (requests.RequestException, PermissionError):
        return ""


# many careers pages are an "about us" landing page, while the real list of jobs
# sits on a separate sub-page called "Open Roles" / "Job Board" / "See open positions"
_BOARD_LINK_WORDS = (
    "open role", "open position", "job board", "current opening",
    "view all job", "view open job", "see open", "all openings",
    "vacanc", "view jobs", "browse jobs", "open jobs", "open vacanc",
)


def _find_board_link(page_url: str, links: list) -> str:
    base_path = urlparse(page_url).path.rstrip("/")
    for entry in links:
        label, _, href = entry.partition(" :: ")
        if urlparse(href).path.rstrip("/") == base_path:
            continue  # a link back to this very page is not a step anywhere
        if any(w in label.lower() for w in _BOARD_LINK_WORDS):
            return href
    return ""


def crawl_company(name: str, url: str, cfg: dict, log, _depth: int = 0) -> list:
    try:
        r = web.get(url, respect_robots=True)
        if r is None:
            _lk(log, "log_robots_skip", name=name)
            return []
        r.raise_for_status()
        raw_html = r.text
        base_url = r.url  # see the comment in fetch_page — we allow for redirects
    except requests.RequestException as e:
        log(f"crawl {name}: {e}")
        return []

    # 1. Many careers pages are a JS wrapper around an embedded ATS.
    # If we find it in the HTML we read the jobs through the API — more reliable,
    # and with locations.
    found = ats.detect_in_html(raw_html)
    if found:
        try:
            jobs = ats.fetch(found[0], found[1], company_hint=name)
            if jobs:
                _lk(log, "log_crawl_ats", name=name, kind=found[0], id=found[1], n=len(jobs))
                return jobs
        except Exception as e:  # noqa: BLE001 — if that failed, fall back to the model
            _lk(log, "log_crawl_ats_fail", name=name, found=found, error=e)

    # 2. Ordinary crawling: visible text plus links → the model extracts the jobs.
    soup = BeautifulSoup(raw_html, "html.parser")
    for tag in soup(["script", "style", "noscript", "svg"]):
        tag.decompose()
    text = " ".join(soup.get_text(" ", strip=True).split())
    links = []
    for a in soup.find_all("a", href=True):
        label = " ".join(a.get_text(" ", strip=True).split())[:120]
        href = urljoin(base_url, a["href"])
        if label and href.startswith("http"):
            links.append(f"{label} :: {href}")
    if len(text) < 100:
        _lk(log, "log_crawl_empty", name=name)
        return []
    prompt = EXTRACT_PROMPT.format(
        name=name, url=base_url, text=text[:12000], links="\n".join(links[:150]),
    )
    try:
        items = llm.ask_json(
            prompt,
            model=cfg["llm"].get("triage_model", "haiku"),
            claude_bin=cfg["llm"].get("claude_bin", "claude"),
            provider=cfg["llm"].get("provider", "claude_cli"),
            llm=cfg["llm"],
            timeout=300,
        )
    except llm.ClaudeError as e:
        log(f"crawl {name} (LLM): {e}")
        return []
    jobs = []
    for it in items if isinstance(items, list) else []:
        title = str(it.get("title", "")).strip()
        if not title:
            continue
        job_url = str(it.get("url", "")).strip()
        if job_url and urlparse(job_url).scheme not in ("http", "https"):
            job_url = ""
        jobs.append({
            "title": title,
            "company": name,
            "location": str(it.get("location", "")).strip(),
            "url": job_url or base_url,
            "description": "",
            "source": "crawl",
            "is_direct": True,
        })

    # 3. The page we were given is a landing page with no job list (0 found): we
    # look for an "Open Roles" / "Job Board" link and try it (one level deeper).
    if not jobs and _depth == 0:
        board_url = _find_board_link(base_url, links)
        if board_url:
            _lk(log, "log_crawl_subpage", name=name, url=base_url, sub=board_url)
            return crawl_company(name, board_url, cfg, log, _depth=1)

    # 4. The last fallback: a careers page entirely in JS with no detectable API —
    # we try to guess the ATS board from the company name (Datadog → greenhouse/datadog).
    if not jobs and name:
        guessed = ats.guess_by_name(name)
        if guessed:
            _lk(log, "log_crawl_guessed", name=name, kind=guessed[0], id=guessed[1], n=len(guessed[2]))
            return guessed[2]
        return jobs

    # we pull the descriptions from the job pages: without them the word-level sift
    # and the triage are all but blind (capping the requests per company)
    for j in jobs[:20]:
        if j["url"] != url:
            j["description"] = fetch_job_text(j["url"], limit=4000)
    return jobs
