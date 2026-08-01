"""Finding new companies for a person's profile automatically.

Claude with web search looks for companies and start-ups hiring right now for
this person's roles, and returns links to their careers pages. The companies it
finds are added to the watch list (sources.companies).
"""
from urllib.parse import urlparse

from . import config, i18n, llm
from .collectors import ats



def _lk(log, key: str, **fmt) -> None:
    """A log line in the interface language (log comes in from the pipeline)."""
    text = i18n.t(config.load()["ui"]["lang"], key)
    log(text.format(**fmt) if fmt else text)


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

# a different angle each round, so every pass brings up new employers
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
    """What we are looking for — role, skills or both (shared by every kind of discovery)."""
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


_PER_CALL = 8  # a reliable haul from one web search; for more we make several passes


def discover(cfg: dict, log, n: int = 5) -> list:
    """Finds up to n new companies for the profile. One web search yields about
    five to eight, so for a larger n we make several passes from different angles,
    collecting the unique ones and stopping when nothing new turns up."""
    p, s = cfg["profile"], cfg["search"]
    known = cfg["sources"].get("companies", [])
    known_domains = {_domain(c["url"]) for c in known if c.get("url")}
    seen_names = [(c.get("name") or _domain(c.get("url", ""))) for c in known[:80]]

    visa = _visa_note(p)
    target = _target(cfg)

    fresh = []
    rounds = max(1, -(-n // _PER_CALL))          # ceil(n / _PER_CALL)
    dry = 0
    for r in range(rounds + 2):                  # +2 spare passes to top up
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
        except llm.AuthError:
            raise      # without a signed-in model the run makes no sense
        except llm.ClaudeError as e:
            _lk(log, "log_disc_err", r=r + 1, error=e)
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
            _lk(log, "log_disc_pass", r=r + 1, added=added, found=len(fresh), want=n)
    return fresh[:n]


# ---------------------------------------------------------------------------
# Searching for JOBS (rather than companies) on the ATS domains directly.
#
# Thousands of companies host their jobs on shared domains (boards.greenhouse.io,
# jobs.lever.co, …). A search for `site:boards.greenhouse.io <skills> <region>`
# turns up particular matching postings at companies that a search "by name"
# would never have produced. Every hit is converted into a company for the watch
# list — and its board is then taken whole through the ATS API.
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
    "personio": "https://{slug}",  # for personio the slug is the whole host
}


def _ats_key(url: str):
    det = ats.detect(url or "")
    return f"{det[0]}:{det[1].lower()}" if det else None


def discover_ats_jobs(cfg: dict, log, n: int = 5) -> list:
    """Looks for matching jobs on the ATS domains directly and returns their
    companies (name plus the canonical board URL) to add to the watch list."""
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
    except llm.AuthError:
        raise      # without a signed-in model the run makes no sense
    except llm.ClaudeError as e:
        _lk(log, "log_ats_err", error=e)
        return []

    fresh = []
    for it in items if isinstance(items, list) else []:
        url = str(it.get("url", "")).strip()
        det = ats.detect(url)
        if not det:
            continue  # not an ATS link — the company cannot be pinned down reliably
        platform, slug = det
        key = f"{platform}:{slug.lower()}"
        if key in known_keys:
            continue
        known_keys.add(key)
        board = _BOARD_URL[platform].format(slug=slug)
        name = str(it.get("company", "")).strip() or slug
        title = str(it.get("title", "")).strip()
        fresh.append({"name": name, "url": board})
        _lk(log, "log_ats_found", title=title, name=name, board=board)
        if len(fresh) >= n:
            break
    return fresh
