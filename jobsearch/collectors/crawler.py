"""Точечный краулинг careers-страниц, не распознанных как ATS.

Страница скачивается, из неё берётся видимый текст и список ссылок,
а Claude (haiku) извлекает из этого структурированный список вакансий.
"""
from urllib.parse import urljoin, urlparse

import requests
from bs4 import BeautifulSoup

from .. import llm
from . import ats

UA = {"User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 14_0) AppleWebKit/537.36 ai-job-search/1.0"}
TIMEOUT = 30

EXTRACT_PROMPT = """Ниже — текст и ссылки со страницы вакансий компании «{name}» ({url}).
Извлеки список открытых вакансий. Верни ТОЛЬКО JSON-массив объектов вида
{{"title": "...", "url": "...", "location": "..."}} без пояснений.
Если url вакансии нет в списке ссылок — оставь пустую строку. Если вакансий нет — верни [].

ТЕКСТ СТРАНИЦЫ:
{text}

ССЫЛКИ:
{links}"""


def fetch_page(url: str):
    r = requests.get(url, headers=UA, timeout=TIMEOUT)
    r.raise_for_status()
    soup = BeautifulSoup(r.text, "html.parser")
    for tag in soup(["script", "style", "noscript", "svg"]):
        tag.decompose()
    text = " ".join(soup.get_text(" ", strip=True).split())
    links = []
    for a in soup.find_all("a", href=True):
        label = " ".join(a.get_text(" ", strip=True).split())[:120]
        href = urljoin(url, a["href"])
        if label and href.startswith("http"):
            links.append(f"{label} :: {href}")
    return text, links


def fetch_job_text(url: str, limit: int = 6000) -> str:
    """Текст страницы конкретной вакансии — для глубокого разбора."""
    try:
        text, _ = fetch_page(url)
        return text[:limit]
    except requests.RequestException:
        return ""


def crawl_company(name: str, url: str, cfg: dict, log) -> list:
    try:
        r = requests.get(url, headers=UA, timeout=TIMEOUT)
        r.raise_for_status()
        raw_html = r.text
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
                log(f"crawl {name}: найден встроенный ATS ({found[0]}/{found[1]}) — {len(jobs)}")
                return jobs
        except Exception as e:  # noqa: BLE001 — если не вышло, падаем на LLM-извлечение
            log(f"crawl {name}: встроенный ATS {found} не прочитался ({e}), пробую LLM")

    # 2. Обычный краулинг: видимый текст + ссылки → LLM извлекает вакансии.
    soup = BeautifulSoup(raw_html, "html.parser")
    for tag in soup(["script", "style", "noscript", "svg"]):
        tag.decompose()
    text = " ".join(soup.get_text(" ", strip=True).split())
    links = []
    for a in soup.find_all("a", href=True):
        label = " ".join(a.get_text(" ", strip=True).split())[:120]
        href = urljoin(url, a["href"])
        if label and href.startswith("http"):
            links.append(f"{label} :: {href}")
    if len(text) < 100:
        log(f"crawl {name}: страница почти пустая (вероятно, контент рендерится JS без встроенного ATS)")
        return []
    prompt = EXTRACT_PROMPT.format(
        name=name, url=url, text=text[:12000], links="\n".join(links[:150]),
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
            "url": job_url or url,
            "description": "",
            "source": "crawl",
            "is_direct": True,
        })
    # подтягиваем описания со страниц вакансий: без них лексический отбор
    # и триаж почти слепые (ограничиваем число запросов на компанию)
    for j in jobs[:20]:
        if j["url"] != url:
            j["description"] = fetch_job_text(j["url"], limit=4000)
    return jobs
