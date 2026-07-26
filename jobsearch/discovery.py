"""Автоматический поиск новых компаний под профиль кандидата.

Claude с веб-поиском ищет компании/стартапы, которые сейчас нанимают на
роли кандидата, и возвращает ссылки на их careers-страницы. Найденные
компании добавляются в список мониторинга (sources.companies).
"""
from urllib.parse import urlparse

from . import llm
from .collectors import ats

PROMPT = """Найди через веб-поиск {n} работодателей, которые ПРЯМО СЕЙЧАС нанимают:
{target}
уровень: {seniority}
регионы: {locations}{visa}

ГЛАВНОЕ: ищи работодателей ИМЕННО ЭТОЙ профессии и отрасли, а не только технологические
компании. Где такого специалиста нанимают массово, там и ищи: для юриста это юридические
фирмы, нотариальные и адвокатские конторы, юротделы банков и корпораций; для медика —
клиники и больницы; для маркетолога — агентства; для инженера — продуктовые компании и
стартапы. Технологический стартап подходит только если он реально нанимает на эту роль.

Предпочитай небольшие и средние организации, а не общеизвестных гигантов. Для каждой найди
прямую ссылку на страницу вакансий — лучше всего ссылку на ATS (boards.greenhouse.io/...,
jobs.lever.co/..., jobs.ashbyhq.com/..., apply.workable.com/..., *.recruitee.com,
*.jobs.personio.de), если её нет — обычную careers-страницу на сайте организации.
Не давай ссылки на LinkedIn и агрегаторы.
{angle}
Эти работодатели уже мониторятся или уже предложены, НЕ предлагай их снова: {known}

Верни ТОЛЬКО JSON-массив: [{{"name": "...", "careers_url": "https://..."}}]"""

# разные «углы» поиска по раундам — чтобы каждый заход давал новых работодателей
_ANGLES = [
    "",
    "В этот раз ищи профильных отраслевых работодателей — тех, для кого эта профессия "
    "является основной (например, юридические фирмы для юриста, клиники для врача).",
    "В этот раз ищи компании из другой ниши/сектора, чем обычно.",
    "В этот раз ищи крупных локальных работодателей указанного региона: банки, страховые, "
    "производственные и государственные организации с внутренними отделами под эту роль.",
    "В этот раз ищи ранние стартапы (seed / Series A) и скейлапы, если они нанимают на эту роль.",
    "В этот раз ищи работодателей в других городах указанного региона.",
]


def _domain(url: str) -> str:
    host = urlparse(url if "://" in url else "https://" + url).netloc.lower()
    return host.removeprefix("www.")


def _visa_note(p: dict) -> str:
    if not p.get("visa_required"):
        return ""
    note = f" ({p['visa_note']})" if p.get("visa_note") else ""
    return (f"\nВАЖНО: кандидату нужны релокация и визовое спонсорство{note} — "
            "предпочитай компании, известные поддержкой visa sponsorship / relocation.")


def _target(cfg: dict) -> str:
    """Что ищем — роль, навыки или и то, и другое (общее для всех видов discovery)."""
    p, s = cfg["profile"], cfg["search"]
    roles = p.get("roles") or "software engineer"
    skills = (p.get("skills") or "").strip()
    prio = s.get("match_priority", "both")
    if prio == "skills" and skills:
        return (f"кандидат владеет навыками/технологиями: {skills}\n"
                f"Ищи вакансии, которым нужны ЭТИ навыки — название должности не важно "
                f"(например, для навыков SAP+Java подойдут и SAP-интеграция, и Java-бэкенд).\n"
                f"(для справки желаемые роли: {roles})")
    if prio == "both" and skills:
        return f"роли: {roles}\nа также вакансии под навыки/технологии: {skills}"
    return f"роли: {roles}"


_PER_CALL = 8  # надёжный размер за один веб-поиск; больше — набираем несколькими заходами


def discover(cfg: dict, log, n: int = 5) -> list:
    """Находит до n новых компаний под профиль. Один веб-поиск даёт ~5-8 штук,
    поэтому для больших n делаем несколько заходов с разными «углами» поиска,
    накапливая уникальные и останавливаясь, когда новые перестают находиться."""
    p, s = cfg["profile"], cfg["search"]
    known = cfg["sources"].get("companies", [])
    known_domains = {_domain(c["url"]) for c in known if c.get("url")}
    seen_names = [(c.get("name") or _domain(c.get("url", ""))) for c in known[:80]]

    visa = _visa_note(p)
    target = _target(cfg)

    fresh = []
    rounds = max(1, -(-n // _PER_CALL))          # ceil(n / _PER_CALL)
    dry = 0
    for r in range(rounds + 2):                  # +2 запасных захода на добор
        if len(fresh) >= n or dry >= 2:
            break
        want = min(_PER_CALL, n - len(fresh))
        prompt = PROMPT.format(
            n=want,
            target=target,
            seniority=p.get("seniority") or "любой",
            locations=s.get("locations") or "anywhere",
            visa=visa,
            angle=_ANGLES[r % len(_ANGLES)],
            known=", ".join(seen_names[:120]) or "—",
        )
        try:
            items = llm.ask_json(
                prompt,
                model=cfg["llm"].get("triage_model", "haiku"),
                claude_bin=cfg["llm"].get("claude_bin", "claude"),
                provider=cfg["llm"].get("provider", "claude_cli"),
                timeout=600,
                allowed_tools=["WebSearch", "WebFetch"],
            )
        except llm.ClaudeError as e:
            log(f"поиск компаний (заход {r + 1}): {e}")
            dry += 1
            continue
        added = 0
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
            seen_names.append(name or dom)
            added += 1
            if len(fresh) >= n:
                break
        dry = dry + 1 if added == 0 else 0
        if n > _PER_CALL:
            log(f"поиск компаний: заход {r + 1}, +{added}, всего {len(fresh)}/{n}")
    return fresh[:n]


# ---------------------------------------------------------------------------
# Поиск ВАКАНСИЙ (а не компаний) прямо на доменах ATS-систем.
#
# Тысячи компаний хостят вакансии на общих доменах (boards.greenhouse.io,
# jobs.lever.co, ...). Поиск `site:boards.greenhouse.io <навыки> <регион>`
# находит конкретные подходящие объявления у компаний, которые поиск
# «по именам» никогда не выдал бы. Каждая находка конвертируется в компанию
# для списка мониторинга — её доску мы затем целиком забираем через API ATS.
# ---------------------------------------------------------------------------

ATS_JOBS_PROMPT = """Найди через веб-поиск {n} ОТКРЫТЫХ вакансий, подходящих кандидату:
{target}
уровень: {seniority}
регионы: {locations}{visa}

Ищи ТОЛЬКО прямые ссылки на страницы вакансий на доменах ATS-систем — строй запросы вида:
- site:boards.greenhouse.io <ключевые слова> <регион>
- site:job-boards.greenhouse.io ... / site:job-boards.eu.greenhouse.io ...
- site:jobs.lever.co ...
- site:jobs.ashbyhq.com ...
- site:apply.workable.com ...
- site:jobs.smartrecruiters.com ... / site:careers.smartrecruiters.com ...
- site:recruitee.com ...
Сделай несколько разных запросов (по навыкам, по роли, по региону), чтобы охватить разные ATS.
Каждый результат — реальная страница вакансии на одном из этих доменов, НЕ главная страница ATS.
Вакансии этих компаний уже мониторятся, ищи ДРУГИЕ компании: {known}

Верни ТОЛЬКО JSON-массив: [{{"company": "...", "title": "...", "url": "https://..."}}]"""

_BOARD_URL = {
    "greenhouse": "https://boards.greenhouse.io/{slug}",
    "lever": "https://jobs.lever.co/{slug}",
    "ashby": "https://jobs.ashbyhq.com/{slug}",
    "workable": "https://apply.workable.com/{slug}",
    "smartrecruiters": "https://careers.smartrecruiters.com/{slug}",
    "recruitee": "https://{slug}.recruitee.com",
    "personio": "https://{slug}",  # для personio slug — это целиком host
}


def _ats_key(url: str):
    det = ats.detect(url or "")
    return f"{det[0]}:{det[1].lower()}" if det else None


def discover_ats_jobs(cfg: dict, log, n: int = 5) -> list:
    """Ищет подходящие вакансии прямо на доменах ATS и возвращает их компании
    (name + канонический URL доски) для добавления в список мониторинга."""
    known = cfg["sources"].get("companies", [])
    known_keys = {k for k in (_ats_key(c.get("url", "")) for c in known) if k}
    known_names = [(c.get("name") or _domain(c.get("url", ""))) for c in known[:80]]

    p, s = cfg["profile"], cfg["search"]
    prompt = ATS_JOBS_PROMPT.format(
        n=max(n, 5),
        target=_target(cfg),
        seniority=p.get("seniority") or "любой",
        locations=s.get("locations") or "anywhere",
        visa=_visa_note(p),
        known=", ".join(known_names[:120]) or "—",
    )
    try:
        items = llm.ask_json(
            prompt,
            model=cfg["llm"].get("triage_model", "haiku"),
            claude_bin=cfg["llm"].get("claude_bin", "claude"),
            provider=cfg["llm"].get("provider", "claude_cli"),
            timeout=600,
            allowed_tools=["WebSearch", "WebFetch"],
        )
    except llm.ClaudeError as e:
        log(f"поиск вакансий по ATS: {e}")
        return []

    fresh = []
    for it in items if isinstance(items, list) else []:
        url = str(it.get("url", "")).strip()
        det = ats.detect(url)
        if not det:
            continue  # не ATS-ссылка — компанию не определить надёжно
        platform, slug = det
        key = f"{platform}:{slug.lower()}"
        if key in known_keys:
            continue
        known_keys.add(key)
        board = _BOARD_URL[platform].format(slug=slug)
        name = str(it.get("company", "")).strip() or slug
        title = str(it.get("title", "")).strip()
        fresh.append({"name": name, "url": board})
        log(f"вакансия на ATS: {title} @ {name} → мониторим доску {board}")
        if len(fresh) >= n:
            break
    return fresh
