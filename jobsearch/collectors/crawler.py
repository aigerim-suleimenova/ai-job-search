"""Точечный краулинг careers-страниц, не распознанных как ATS.

Страница скачивается, из неё берётся видимый текст и список ссылок,
а Claude (haiku) извлекает из этого структурированный список вакансий.
"""
from urllib.parse import urljoin, urlparse

import requests
from bs4 import BeautifulSoup

from .. import config, i18n, llm
from . import ats, web



def _lk(log, key: str, **fmt) -> None:
    """Строка журнала на языке интерфейса (log приходит из пайплайна)."""
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
        raise PermissionError("robots.txt")   # сайт просит роботов не ходить сюда
    r.raise_for_status()
    base_url = r.url  # сайты часто редиректят (смена домена/ребрендинг) —
    # относительные ссылки резолвим от итогового URL, а не от исходного
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
    """Текст страницы конкретной вакансии — для глубокого разбора."""
    try:
        text, _ = fetch_page(url)
        return text[:limit]
    except (requests.RequestException, PermissionError):
        return ""


# многие careers-страницы — лендинг «о нас», а реальный список вакансий лежит
# на отдельной подстранице вида «Open Roles» / «Job Board» / «See open positions»
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
            continue  # ссылка на саму эту же страницу — не переход
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
        base_url = r.url  # см. комментарий в fetch_page — учитываем редиректы
    except requests.RequestException as e:
        log(f"crawl {name}: {e}")
        return []

    # 1. Многие careers-страницы — это JS-обёртка вокруг встроенного ATS.
    # Если находим его в HTML — читаем вакансии через API (надёжнее и с локациями).
    found = ats.detect_in_html(raw_html)
    if found:
        try:
            jobs = ats.fetch(found[0], found[1], company_hint=name)
            if jobs:
                _lk(log, "log_crawl_ats", name=name, kind=found[0], id=found[1], n=len(jobs))
                return jobs
        except Exception as e:  # noqa: BLE001 — если не вышло, падаем на LLM-извлечение
            _lk(log, "log_crawl_ats_fail", name=name, found=found, error=e)

    # 2. Обычный краулинг: видимый текст + ссылки → LLM извлекает вакансии.
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

    # 3. Присланная страница — лендинг без списка вакансий (0 найдено):
    # ищем ссылку вида «Open Roles» / «Job Board» и пробуем её (один уровень вглубь).
    if not jobs and _depth == 0:
        board_url = _find_board_link(base_url, links)
        if board_url:
            _lk(log, "log_crawl_subpage", name=name, url=base_url, sub=board_url)
            return crawl_company(name, board_url, cfg, log, _depth=1)

    # 4. Последний fallback: careers-страница целиком на JS без обнаружимого API —
    # пробуем угадать ATS-борд по названию компании (напр. Datadog → greenhouse/datadog).
    if not jobs and name:
        guessed = ats.guess_by_name(name)
        if guessed:
            _lk(log, "log_crawl_guessed", name=name, kind=guessed[0], id=guessed[1], n=len(guessed[2]))
            return guessed[2]
        return jobs

    # подтягиваем описания со страниц вакансий: без них лексический отбор
    # и триаж почти слепые (ограничиваем число запросов на компанию)
    for j in jobs[:20]:
        if j["url"] != url:
            j["description"] = fetch_job_text(j["url"], limit=4000)
    return jobs
