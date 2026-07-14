"""Автоматический поиск новых компаний под профиль кандидата.

Claude с веб-поиском ищет компании/стартапы, которые сейчас нанимают на
роли кандидата, и возвращает ссылки на их careers-страницы. Найденные
компании добавляются в список мониторинга (sources.companies).
"""
from urllib.parse import urlparse

from . import llm

PROMPT = """Найди через веб-поиск {n} компаний или стартапов, которые ПРЯМО СЕЙЧАС нанимают:
{target}
уровень: {seniority}
регионы: {locations}{visa}

Ищи разнообразно: стартапы, продуктовые компании, скейлапы — предпочитай небольшие
и средние компании, а не общеизвестных гигантов. Для каждой найди прямую ссылку на
её страницу вакансий — лучше всего ссылку на ATS (boards.greenhouse.io/..., jobs.lever.co/...,
jobs.ashbyhq.com/..., apply.workable.com/..., *.recruitee.com, *.jobs.personio.de),
если её нет — обычную careers-страницу на сайте компании. Не давай ссылки на LinkedIn и агрегаторы.
{angle}
Эти компании уже мониторятся или уже предложены, НЕ предлагай их снова: {known}

Верни ТОЛЬКО JSON-массив: [{{"name": "...", "careers_url": "https://..."}}]"""

# разные «углы» поиска по раундам — чтобы каждый заход давал новые компании
_ANGLES = [
    "",
    "В этот раз ищи ранние стартапы (seed / Series A), недавно поднявшие раунд.",
    "В этот раз ищи компании из другой ниши/сектора, чем обычно.",
    "В этот раз ищи remote-first компании и распределённые команды.",
    "В этот раз ищи скейлапы (Series B+) и известные в своей нише продуктовые компании.",
    "В этот раз ищи компании в других странах/городах указанного региона.",
]


def _domain(url: str) -> str:
    host = urlparse(url if "://" in url else "https://" + url).netloc.lower()
    return host.removeprefix("www.")


_PER_CALL = 8  # надёжный размер за один веб-поиск; больше — набираем несколькими заходами


def discover(cfg: dict, log, n: int = 5) -> list:
    """Находит до n новых компаний под профиль. Один веб-поиск даёт ~5-8 штук,
    поэтому для больших n делаем несколько заходов с разными «углами» поиска,
    накапливая уникальные и останавливаясь, когда новые перестают находиться."""
    p, s = cfg["profile"], cfg["search"]
    known = cfg["sources"].get("companies", [])
    known_domains = {_domain(c["url"]) for c in known if c.get("url")}
    seen_names = [(c.get("name") or _domain(c.get("url", ""))) for c in known[:80]]

    visa = ""
    if p.get("visa_required"):
        note = f" ({p['visa_note']})" if p.get("visa_note") else ""
        visa = (f"\nВАЖНО: кандидату нужны релокация и визовое спонсорство{note} — "
                "предпочитай компании, известные поддержкой visa sponsorship / relocation.")

    # что ищем: роль, навыки или и то, и другое
    roles = p.get("roles") or "software engineer"
    skills = (p.get("skills") or "").strip()
    prio = s.get("match_priority", "both")
    if prio == "skills" and skills:
        target = (f"кандидат владеет навыками/технологиями: {skills}\n"
                  f"Ищи компании, которым нужны ЭТИ навыки — название должности не важно "
                  f"(например, для навыков SAP+Java подойдут и SAP-интеграция, и Java-бэкенд).\n"
                  f"(для справки желаемые роли: {roles})")
    elif prio == "both" and skills:
        target = f"роли: {roles}\nа также вакансии под навыки/технологии: {skills}"
    else:
        target = f"роли: {roles}"

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
