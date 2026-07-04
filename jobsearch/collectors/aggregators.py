"""Агрегаторы вакансий с открытыми API: Remotive, Arbeitnow, HN Who is Hiring,
Adzuna и Jooble (по ключам)."""
import html
import re

import requests

UA = {"User-Agent": "Mozilla/5.0 (Macintosh) ai-job-search/1.0"}
TIMEOUT = 30


def _strip_html(raw: str, limit: int = 5000) -> str:
    text = re.sub(r"<[^>]+>", " ", html.unescape(raw or ""))
    return re.sub(r"\s+", " ", text).strip()[:limit]


def _search_terms(cfg: dict) -> list:
    raw = cfg["profile"].get("roles", "") + "," + cfg["search"].get("keywords_include", "")
    terms = [t.strip() for t in raw.split(",") if t.strip()]
    return terms[:3] or [""]


def remotive(cfg: dict, log) -> list:
    jobs = []
    for term in _search_terms(cfg):
        try:
            r = requests.get(
                "https://remotive.com/api/remote-jobs",
                params={"search": term, "limit": 100},
                headers=UA, timeout=TIMEOUT,
            )
            r.raise_for_status()
            for j in r.json().get("jobs", []):
                jobs.append({
                    "title": j.get("title", ""),
                    "company": j.get("company_name", ""),
                    "location": j.get("candidate_required_location", "") or "Remote",
                    "url": j.get("url", ""),
                    "description": _strip_html(j.get("description", "")),
                    "source": "remotive",
                    "is_direct": False,
                })
        except requests.RequestException as e:
            log(f"remotive ({term!r}): {e}")
    return jobs


def arbeitnow(cfg: dict, log) -> list:
    jobs, url = [], "https://www.arbeitnow.com/api/job-board-api"
    for _ in range(3):  # первые 3 страницы
        try:
            r = requests.get(url, headers=UA, timeout=TIMEOUT)
            r.raise_for_status()
            data = r.json()
        except requests.RequestException as e:
            log(f"arbeitnow: {e}")
            break
        for j in data.get("data", []):
            jobs.append({
                "title": j.get("title", ""),
                "company": j.get("company_name", ""),
                "location": (j.get("location", "") or "") + (", Remote" if j.get("remote") else ""),
                "url": j.get("url", ""),
                "description": _strip_html(j.get("description", "")),
                "source": "arbeitnow",
                "is_direct": False,
            })
        url = (data.get("links") or {}).get("next")
        if not url:
            break
    return jobs


def hn_hiring(cfg: dict, log) -> list:
    """Последний тред «Ask HN: Who is hiring?» — посты пишут сами компании."""
    jobs = []
    try:
        r = requests.get(
            "https://hn.algolia.com/api/v1/search_by_date",
            params={"tags": "story,author_whoishiring", "query": "who is hiring", "hitsPerPage": 5},
            headers=UA, timeout=TIMEOUT,
        )
        r.raise_for_status()
        hits = [h for h in r.json().get("hits", []) if "who is hiring" in (h.get("title") or "").lower()]
        if not hits:
            return []
        story_id = hits[0]["objectID"]
        r = requests.get(f"https://hn.algolia.com/api/v1/items/{story_id}", headers=UA, timeout=60)
        r.raise_for_status()
        for child in r.json().get("children", []):
            text = _strip_html(child.get("text") or "", limit=3000)
            if len(text) < 60:
                continue
            header = text[:220]
            company = header.split("|")[0].strip()[:80]
            jobs.append({
                "title": " | ".join(p.strip() for p in header.split("|")[1:3]) or header[:100],
                "company": company,
                "location": "",
                "url": f"https://news.ycombinator.com/item?id={child.get('id')}",
                "description": text,
                "source": "hn",
                "is_direct": True,   # в этом треде постят сами компании
            })
    except requests.RequestException as e:
        log(f"hn_hiring: {e}")
    return jobs


def adzuna(cfg: dict, log) -> list:
    src = cfg["sources"]
    app_id, app_key = src.get("adzuna_app_id", ""), src.get("adzuna_app_key", "")
    if not (app_id and app_key):
        return []
    jobs = []
    countries = [c.strip() for c in src.get("adzuna_countries", "").split(",") if c.strip()]
    what = " ".join(_search_terms(cfg))[:100]
    for cc in countries[:10]:
        try:
            r = requests.get(
                f"https://api.adzuna.com/v1/api/jobs/{cc}/search/1",
                params={"app_id": app_id, "app_key": app_key, "what": what,
                        "results_per_page": 50, "content-type": "application/json"},
                headers=UA, timeout=TIMEOUT,
            )
            r.raise_for_status()
            for j in r.json().get("results", []):
                jobs.append({
                    "title": j.get("title", ""),
                    "company": (j.get("company") or {}).get("display_name", ""),
                    "location": (j.get("location") or {}).get("display_name", ""),
                    "url": j.get("redirect_url", ""),
                    "description": (j.get("description") or "")[:5000],
                    "source": f"adzuna:{cc}",
                    "is_direct": False,
                })
        except requests.RequestException as e:
            log(f"adzuna:{cc}: {e}")
    return jobs


def jooble(cfg: dict, log) -> list:
    key = cfg["sources"].get("jooble_key", "")
    if not key:
        return []
    jobs = []
    for loc in ["", "remote"]:
        try:
            r = requests.post(
                f"https://jooble.org/api/{key}",
                json={"keywords": " ".join(_search_terms(cfg)), "location": loc},
                headers=UA, timeout=TIMEOUT,
            )
            r.raise_for_status()
            for j in r.json().get("jobs", []):
                jobs.append({
                    "title": j.get("title", ""),
                    "company": j.get("company", ""),
                    "location": j.get("location", ""),
                    "url": j.get("link", ""),
                    "description": _strip_html(j.get("snippet", "")),
                    "source": "jooble",
                    "is_direct": False,
                })
        except requests.RequestException as e:
            log(f"jooble: {e}")
    return jobs


def collect(cfg: dict, log, coverage: list = None) -> list:
    jobs = []
    src = cfg["sources"]

    def track(enabled, name, url, fn):
        if not enabled:
            return
        got = fn(cfg, log)
        jobs.extend(got)
        if coverage is not None:
            coverage.append({"name": name, "url": url, "kind": "агрегатор",
                             "count": len(got), "error": None})

    track(src.get("use_remotive"), "Remotive", "https://remotive.com", remotive)
    track(src.get("use_arbeitnow"), "Arbeitnow", "https://arbeitnow.com", arbeitnow)
    track(src.get("use_hnhiring"), "HN Who is Hiring", "https://news.ycombinator.com", hn_hiring)
    track(bool(src.get("adzuna_app_id")), "Adzuna", "https://adzuna.com", adzuna)
    track(bool(src.get("jooble_key")), "Jooble", "https://jooble.org", jooble)
    return jobs
