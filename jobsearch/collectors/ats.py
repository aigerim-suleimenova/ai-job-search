"""Прямые вакансии через публичные API ATS-платформ.

По URL страницы вакансий компании определяем платформу и её slug,
дальше забираем вакансии одним HTTP-запросом.
"""
import html
import re
import xml.etree.ElementTree as ET
from urllib.parse import urlparse

import requests

UA = {"User-Agent": "Mozilla/5.0 (Macintosh) ai-job-search/1.0"}
TIMEOUT = 25


def _strip_html(raw: str, limit: int = 6000) -> str:
    text = re.sub(r"<[^>]+>", " ", html.unescape(raw or ""))
    return re.sub(r"\s+", " ", text).strip()[:limit]


def detect(url: str):
    """Возвращает (платформа, slug) или None."""
    p = urlparse(url if "://" in url else "https://" + url)
    host, path = p.netloc.lower(), [s for s in p.path.split("/") if s]

    def first(default=None):
        return path[0] if path else default

    if host in ("boards.greenhouse.io", "job-boards.greenhouse.io", "job-boards.eu.greenhouse.io") and path:
        return "greenhouse", first()
    if host == "jobs.lever.co" and path:
        return "lever", first()
    if host == "jobs.ashbyhq.com" and path:
        return "ashby", first()
    if host == "apply.workable.com" and path:
        return "workable", first()
    if host in ("careers.smartrecruiters.com", "jobs.smartrecruiters.com") and path:
        return "smartrecruiters", first()
    if host.endswith(".recruitee.com"):
        return "recruitee", host.split(".")[0]
    if host.endswith(".jobs.personio.de") or host.endswith(".jobs.personio.com"):
        return "personio", host
    return None


# поиск встроенного ATS прямо в HTML careers-страницы (когда сама страница на JS,
# но вакансии подгружаются с greenhouse/lever/ashby/...)
_ATS_IN_HTML = [
    ("greenhouse", re.compile(r"greenhouse\.io/(?:embed/job_board(?:/js)?\?for=|[^\"'/]*?/)?([a-zA-Z0-9_]+)")),
    ("greenhouse", re.compile(r"(?:boards|job-boards)(?:\.eu)?\.greenhouse\.io/(?:embed/job_board\?for=)?([a-zA-Z0-9_]+)")),
    ("lever", re.compile(r"jobs(?:\.eu)?\.lever\.co/([a-zA-Z0-9-]+)")),
    ("lever", re.compile(r"api(?:\.eu)?\.lever\.co/v0/postings/([a-zA-Z0-9-]+)")),
    ("ashby", re.compile(r"jobs\.ashbyhq\.com/([a-zA-Z0-9_-]+)")),
    ("ashby", re.compile(r"api\.ashbyhq\.com/posting-api/job-board/([a-zA-Z0-9_-]+)")),
    ("workable", re.compile(r"apply\.workable\.com/([a-zA-Z0-9_-]+)")),
    # виджет Workable «whr_embed(<numeric account id>, ...)» — используется вместо
    # прямой ссылки apply.workable.com/<slug>; API принимает и числовой ID как slug
    ("workable", re.compile(r"whr_embed\(\s*(\d+)")),
    ("smartrecruiters", re.compile(r"(?:careers|jobs|api)\.smartrecruiters\.com/(?:v1/companies/)?([a-zA-Z0-9_-]+)")),
    ("recruitee", re.compile(r"([a-z0-9-]+)\.recruitee\.com")),
    ("personio", re.compile(r"([a-z0-9-]+\.jobs\.personio\.(?:de|com))")),
]
_ATS_SLUG_STOP = {"embed", "js", "job", "jobs", "board", "for", "api", "widget", "www", "careers"}


def detect_in_html(html: str):
    """Ищет встроенный ATS в HTML careers-страницы. Возвращает (платформа, slug) или None."""
    for platform, rx in _ATS_IN_HTML:
        m = rx.search(html)
        if not m:
            continue
        slug = m.group(1)
        if platform == "personio":
            return "personio", slug  # тут slug — это host
        if slug.lower() in _ATS_SLUG_STOP or len(slug) < 2:
            continue
        return platform, slug
    return None


def _slug_variants(name: str) -> list:
    import re as _re
    base = _re.sub(r"\b(gmbh|inc|ltd|llc|ag|the|corp|co)\b", "", name.lower())
    compact = _re.sub(r"[^a-z0-9]", "", base)
    dashed = _re.sub(r"[^a-z0-9]+", "-", base).strip("-")
    seen, out = set(), []
    for v in (compact, dashed):
        if v and len(v) >= 2 and v not in seen:
            seen.add(v)
            out.append(v)
    return out


def guess_by_name(name: str):
    """Пробует угадать ATS-борд по названию компании (когда careers-страница на JS
    без обнаружимого API). Возвращает (платформа, slug, jobs) или None.
    Проверяет только реальным запросом к API — ложные срабатывания почти исключены."""
    for slug in _slug_variants(name):
        for platform in ("greenhouse", "lever", "ashby"):
            try:
                jobs = fetch(platform, slug, company_hint=name)
            except Exception:  # noqa: BLE001
                continue
            if jobs:
                return platform, slug, jobs
    return None


def fetch(platform: str, slug: str, company_hint: str = "") -> list:
    fn = {
        "greenhouse": _greenhouse,
        "lever": _lever,
        "ashby": _ashby,
        "workable": _workable,
        "smartrecruiters": _smartrecruiters,
        "recruitee": _recruitee,
        "personio": _personio,
    }[platform]
    jobs = fn(slug)
    for j in jobs:
        j.setdefault("company", company_hint or slug)
        j["source"] = platform
        j["is_direct"] = True
    return jobs


def _get_json(url: str, **kw):
    r = requests.get(url, headers=UA, timeout=TIMEOUT, **kw)
    r.raise_for_status()
    return r.json()


def _greenhouse(slug: str) -> list:
    data = _get_json(f"https://boards-api.greenhouse.io/v1/boards/{slug}/jobs?content=true")
    return [
        {
            "title": j.get("title", ""),
            "location": (j.get("location") or {}).get("name", ""),
            "url": j.get("absolute_url", ""),
            "description": _strip_html(j.get("content", "")),
        }
        for j in data.get("jobs", [])
    ]


def _lever(slug: str) -> list:
    # аккаунты бывают на глобальном и на EU-хосте (data residency)
    try:
        data = _get_json(f"https://api.lever.co/v0/postings/{slug}?mode=json")
    except requests.HTTPError:
        data = _get_json(f"https://api.eu.lever.co/v0/postings/{slug}?mode=json")
    return [
        {
            "title": j.get("text", ""),
            "location": (j.get("categories") or {}).get("location", "") or "",
            "url": j.get("hostedUrl", ""),
            "description": (j.get("descriptionPlain") or "")[:6000],
        }
        for j in data
    ]


def _ashby(slug: str) -> list:
    data = _get_json(f"https://api.ashbyhq.com/posting-api/job-board/{slug}")
    return [
        {
            "title": j.get("title", ""),
            "location": j.get("location", "") or "",
            "url": j.get("jobUrl", "") or j.get("applyUrl", ""),
            "description": _strip_html(j.get("descriptionHtml", "")),
        }
        for j in data.get("jobs", [])
    ]


def _workable(slug: str) -> list:
    data = _get_json(f"https://apply.workable.com/api/v1/widget/accounts/{slug}")
    company = data.get("name", slug)
    return [
        {
            "title": j.get("title", ""),
            "company": company,
            "location": ", ".join(filter(None, [j.get("city", ""), j.get("country", "")])),
            "url": j.get("url", ""),
            "description": "",
        }
        for j in data.get("jobs", [])
    ]


def _smartrecruiters(slug: str) -> list:
    data = _get_json(f"https://api.smartrecruiters.com/v1/companies/{slug}/postings?limit=100")
    jobs = []
    for j in data.get("content", []):
        loc = j.get("location") or {}
        jobs.append({
            "title": j.get("name", ""),
            "company": (j.get("company") or {}).get("name", slug),
            "location": ", ".join(filter(None, [loc.get("city", ""), loc.get("country", "")])),
            "url": f"https://jobs.smartrecruiters.com/{slug}/{j.get('id', '')}",
            "description": "",
        })
    return jobs


def _recruitee(slug: str) -> list:
    data = _get_json(f"https://{slug}.recruitee.com/api/offers/")
    return [
        {
            "title": j.get("title", ""),
            "location": j.get("location", "") or "",
            "url": j.get("careers_url", ""),
            "description": _strip_html(j.get("description", "")),
        }
        for j in data.get("offers", [])
    ]


def _personio(host: str) -> list:
    r = requests.get(f"https://{host}/xml", headers=UA, timeout=TIMEOUT)
    r.raise_for_status()
    root = ET.fromstring(r.content)
    jobs = []
    for pos in root.iter("position"):
        job_id = (pos.findtext("id") or "").strip()
        desc = " ".join(_strip_html(v.text or "") for v in pos.iter("value"))
        jobs.append({
            "title": (pos.findtext("name") or "").strip(),
            "location": (pos.findtext("office") or "").strip(),
            "url": f"https://{host}/job/{job_id}",
            "description": desc[:6000],
        })
    return jobs
