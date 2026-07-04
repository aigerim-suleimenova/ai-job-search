"""Автоматический поиск новых компаний под профиль кандидата.

Claude с веб-поиском ищет компании/стартапы, которые сейчас нанимают на
роли кандидата, и возвращает ссылки на их careers-страницы. Найденные
компании добавляются в список мониторинга (sources.companies).
"""
from urllib.parse import urlparse

from . import llm

PROMPT = """Найди через веб-поиск {n} компаний или стартапов, которые ПРЯМО СЕЙЧАС нанимают:
роли: {roles}
уровень: {seniority}
регионы: {locations}

Ищи разнообразно: стартапы, продуктовые компании, скейлапы — предпочитай небольшие
и средние компании, а не общеизвестных гигантов. Для каждой найди прямую ссылку на
её страницу вакансий — лучше всего ссылку на ATS (boards.greenhouse.io/..., jobs.lever.co/...,
jobs.ashbyhq.com/..., apply.workable.com/..., *.recruitee.com, *.jobs.personio.de),
если её нет — обычную careers-страницу на сайте компании. Не давай ссылки на LinkedIn и агрегаторы.

Эти компании уже мониторятся, НЕ предлагай их: {known}

Верни ТОЛЬКО JSON-массив: [{{"name": "...", "careers_url": "https://..."}}]"""


def _domain(url: str) -> str:
    host = urlparse(url if "://" in url else "https://" + url).netloc.lower()
    return host.removeprefix("www.")


def discover(cfg: dict, log, n: int = 5) -> list:
    """Возвращает список новых компаний [{"name", "url"}], которых ещё нет в мониторинге."""
    p, s = cfg["profile"], cfg["search"]
    known = cfg["sources"].get("companies", [])
    known_domains = {_domain(c["url"]) for c in known if c.get("url")}
    known_names = ", ".join(
        (c.get("name") or _domain(c.get("url", ""))) for c in known[:80]
    ) or "—"
    prompt = PROMPT.format(
        n=n,
        roles=p.get("roles") or "software engineer",
        seniority=p.get("seniority") or "любой",
        locations=s.get("locations") or "anywhere",
        known=known_names,
    )
    try:
        items = llm.ask_json(
            prompt,
            model=cfg["llm"].get("triage_model", "haiku"),
            claude_bin=cfg["llm"].get("claude_bin", "claude"),
            timeout=600,
            allowed_tools=["WebSearch", "WebFetch"],
        )
    except llm.ClaudeError as e:
        log(f"поиск компаний: {e}")
        return []
    fresh = []
    for it in items if isinstance(items, list) else []:
        url = str(it.get("careers_url", "")).strip()
        name = str(it.get("name", "")).strip()
        if not url.startswith("http"):
            continue
        dom = _domain(url)
        if not dom or dom in known_domains or "linkedin." in dom:
            continue
        known_domains.add(dom)
        fresh.append({"name": name or dom, "url": url})
    return fresh
