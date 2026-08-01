"""Job aggregators with open APIs: Remotive, Arbeitnow, WeWorkRemotely,
HN Who is Hiring, Adzuna and Jooble (the last two by key).

IMPORTANT (established empirically): the public APIs of Remotive and Arbeitnow
IGNORE the search/tags parameters — whatever you ask for, they hand back the same
general stream of jobs (Remotive a fixed ~32 of them, Arbeitnow the latest jobs
across every trade, not only IT). So:
- we do not spend a call per search term — it changes nothing anyway;
- for Arbeitnow we filter the IT tags on our side and take more pages, to make up
  for the low share of relevant jobs in the general stream.
"""
import html
import re
import xml.etree.ElementTree as ET

import requests

from . import iso_date, web

UA = web.UA   # an honest name rather than pretending to be a browser
TIMEOUT = 30

# Arbeitnow is a general exchange across every trade; we pick the IT tags on our
# side, since the API does not filter by tags/search on the server (established).
ARBEITNOW_IT_TAGS = {
    "engineering", "software development", "internet and software",
    "information systems", "system and network administration",
    "it", "automation engineering", "web-development",
}


def _strip_html(raw: str, limit: int = 5000) -> str:
    text = re.sub(r"<[^>]+>", " ", html.unescape(raw or ""))
    return re.sub(r"\s+", " ", text).strip()[:limit]


def _search_terms(cfg: dict) -> list:
    """For Adzuna/Jooble — these APIs, unlike Remotive/Arbeitnow, really do filter by
    keyword on their side. We take the skills and the priority into account."""
    p, s = cfg["profile"], cfg["search"]
    prio = s.get("match_priority", "both")
    parts = []
    if prio != "skills":
        parts.append(p.get("roles", ""))
    if prio in ("skills", "both"):
        parts.append(p.get("skills", ""))
    parts.append(s.get("keywords_include", ""))
    raw = ",".join(x for x in parts if x)
    terms = [t.strip() for t in raw.split(",") if t.strip()]
    return terms[:3] or [""]


def remotive(cfg: dict, log) -> list:
    jobs = []
    try:
        r = web.get(
            "https://remotive.com/api/remote-jobs",
            params={"limit": 200},
            timeout=TIMEOUT,
        )
        r.raise_for_status()
        for j in r.json().get("jobs", []):
            jobs.append({
                "title": j.get("title", ""),
                "company": j.get("company_name", ""),
                "location": j.get("candidate_required_location", "") or "Remote",
                "url": j.get("url", ""),
                "description": _strip_html(j.get("description", "")),
                "posted_at": iso_date(j.get("publication_date")),
                "source": "remotive",
                "is_direct": False,
            })
    except requests.RequestException as e:
        log(f"remotive: {e}")
    return jobs


def arbeitnow(cfg: dict, log) -> list:
    jobs, url = [], "https://www.arbeitnow.com/api/job-board-api"
    for _ in range(8):  # the API does not filter — take more pages, pick the IT ones ourselves
        try:
            r = web.get(url, timeout=TIMEOUT)
            r.raise_for_status()
            data = r.json()
        except requests.RequestException as e:
            log(f"arbeitnow: {e}")
            break
        for j in data.get("data", []):
            tags = {t.lower() for t in (j.get("tags") or [])}
            if tags and not (tags & ARBEITNOW_IT_TAGS):
                continue  # a non-IT job (marketing, sales, HR and the like) — skip it
            jobs.append({
                "title": j.get("title", ""),
                "company": j.get("company_name", ""),
                "location": (j.get("location", "") or "") + (", Remote" if j.get("remote") else ""),
                "url": j.get("url", ""),
                "description": _strip_html(j.get("description", "")),
                "posted_at": iso_date(j.get("created_at")),
                "source": "arbeitnow",
                "is_direct": False,
            })
        url = (data.get("links") or {}).get("next")
        if not url:
            break
    return jobs


def wwr(cfg: dict, log) -> list:
    """WeWorkRemotely — the RSS of the 'programming' category, genuinely pre-filtered to IT."""
    jobs = []
    try:
        r = web.get(
            "https://weworkremotely.com/categories/remote-programming-jobs.rss",
            timeout=TIMEOUT,
        )
        r.raise_for_status()
        root = ET.fromstring(r.content)
        for item in root.iter("item"):
            raw_title = (item.findtext("title") or "").strip()
            company, _, title = raw_title.partition(": ")
            if not title:  # not in "Company: Role" form — leave it as it is
                company, title = "", raw_title
            jobs.append({
                "title": title.strip(),
                "company": company.strip(),
                "location": "Remote",
                "url": (item.findtext("link") or "").strip(),
                "description": _strip_html(item.findtext("description") or ""),
                "posted_at": iso_date(item.findtext("pubDate")),
                "source": "wwr",
                "is_direct": True,  # on WWR it is mostly the companies who post
            })
    except (requests.RequestException, ET.ParseError) as e:
        log(f"weworkremotely: {e}")
    return jobs


def hn_hiring(cfg: dict, log) -> list:
    """The latest "Ask HN: Who is hiring?" thread — the posts are written by the companies."""
    jobs = []
    try:
        r = web.get(
            "https://hn.algolia.com/api/v1/search_by_date",
            params={"tags": "story,author_whoishiring", "query": "who is hiring", "hitsPerPage": 5},
            timeout=TIMEOUT,
        )
        r.raise_for_status()
        hits = [h for h in r.json().get("hits", []) if "who is hiring" in (h.get("title") or "").lower()]
        if not hits:
            return []
        story_id = hits[0]["objectID"]
        r = web.get(f"https://hn.algolia.com/api/v1/items/{story_id}", timeout=60)
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
                "posted_at": iso_date(child.get("created_at")),
                "source": "hn",
                "is_direct": True,   # in this thread the companies post themselves
            })
    except requests.RequestException as e:
        log(f"hn_hiring: {e}")
    return jobs


def remoteok(cfg: dict, log) -> list:
    """RemoteOK — a general feed of ~100 fresh remote jobs (nearly all of them IT)."""
    jobs = []
    try:
        r = web.get("https://remoteok.com/api", timeout=TIMEOUT)
        r.raise_for_status()
        for j in r.json()[1:]:  # element zero is a legal notice, not a job
            if not j.get("position"):
                continue
            jobs.append({
                "title": j.get("position", ""),
                "company": j.get("company", ""),
                "location": j.get("location", "") or "Remote",
                "url": j.get("url", ""),
                "description": _strip_html(j.get("description", "")),
                "posted_at": iso_date(j.get("date") or j.get("epoch")),
                "source": "remoteok",
                "is_direct": False,
            })
    except (requests.RequestException, ValueError) as e:
        log(f"remoteok: {e}")
    return jobs


def jobicy(cfg: dict, log) -> list:
    """Jobicy — remote jobs; the tag parameter really does filter on the server."""
    jobs, seen = [], set()
    # the general feed plus one tag per search term
    queries = [{}] + [{"tag": t} for t in _search_terms(cfg)[:2] if t]
    for q in queries:
        try:
            r = web.get("https://jobicy.com/api/v2/remote-jobs",
                             params={"count": 50, **q}, timeout=TIMEOUT)
            r.raise_for_status()
            data = r.json()
        except (requests.RequestException, ValueError) as e:
            log(f"jobicy: {e}")
            continue
        for j in data.get("jobs", []):
            if j.get("id") in seen:
                continue
            seen.add(j.get("id"))
            jobs.append({
                "title": j.get("jobTitle", ""),
                "company": j.get("companyName", ""),
                "location": (j.get("jobGeo", "") or "Remote") + ", Remote",
                "url": j.get("url", ""),
                "description": _strip_html(j.get("jobDescription") or j.get("jobExcerpt") or ""),
                "posted_at": iso_date(j.get("pubDate")),
                "source": "jobicy",
                "is_direct": False,
            })
    return jobs


def himalayas(cfg: dict, log) -> list:
    """Himalayas — remote jobs, a general feed (there is no server-side search).
    The API gives at most 20 per request, so we page through with an offset.
    Sometimes the placeholder "name" arrives instead of companyName (an API bug) —
    then we take companySlug."""
    jobs = []
    for offset in range(0, 100, 20):
        try:
            r = web.get("https://himalayas.app/jobs/api",
                             params={"limit": 20, "offset": offset},
                             timeout=TIMEOUT)
            r.raise_for_status()
            batch = r.json().get("jobs", [])
        except (requests.RequestException, ValueError) as e:
            log(f"himalayas: {e}")
            break
        for j in batch:
            locs = j.get("locationRestrictions") or []
            company = j.get("companyName", "")
            if company in ("", "name"):
                company = (j.get("companySlug") or "").replace("-", " ").title()
            jobs.append({
                "title": j.get("title", ""),
                "company": company,
                "location": (", ".join(locs) + ", Remote") if locs else "Remote",
                "url": j.get("applicationLink", ""),
                "description": _strip_html(j.get("description") or j.get("excerpt") or ""),
                "posted_at": iso_date(j.get("pubDate")),
                "source": "himalayas",
                "is_direct": False,
            })
        if len(batch) < 20:
            break
    return jobs


def themuse(cfg: dict, log) -> list:
    """The Muse — a free API with a category filter (the companies post themselves)."""
    jobs = []
    for page in (1, 2):
        try:
            r = web.get(
                "https://www.themuse.com/api/public/jobs",
                params={"page": page,
                        "category": ["Software Engineering", "IT", "Data Science"]},
            timeout=TIMEOUT,
            )
            r.raise_for_status()
            results = r.json().get("results", [])
        except (requests.RequestException, ValueError) as e:
            log(f"themuse: {e}")
            break
        for j in results:
            jobs.append({
                "title": j.get("name", ""),
                "company": (j.get("company") or {}).get("name", ""),
                "location": ", ".join(l.get("name", "") for l in j.get("locations", [])[:3]),
                "url": (j.get("refs") or {}).get("landing_page", ""),
                "description": _strip_html(j.get("contents", "")),
                "posted_at": iso_date(j.get("publication_date")),
                "source": "themuse",
                "is_direct": True,  # the companies post the jobs themselves
            })
        if not results:
            break
    return jobs


def arbeitsagentur(cfg: dict, log) -> list:
    """Germany's official employment exchange — a real full-text search (a public
    client key, free of charge). The list comes without descriptions, which is
    enough for the word-level sift and triage; the deep analysis fetches the
    job's own page."""
    jobs, seen = [], set()
    for term in _search_terms(cfg):
        if not term:
            continue
        try:
            r = web.get(
                "https://rest.arbeitsagentur.de/jobboerse/jobsuche-service/pc/v4/jobs",
                params={"was": term, "size": 100, "angebotsart": 1},
                headers={**UA, "X-API-Key": "jobboerse-jobsuche"}, timeout=TIMEOUT,
            )
            r.raise_for_status()
            data = r.json()
        except (requests.RequestException, ValueError) as e:
            log(f"arbeitsagentur: {e}")
            continue
        for j in data.get("stellenangebote", []):
            ref = j.get("refnr", "")
            if not ref or ref in seen:
                continue
            seen.add(ref)
            ort = j.get("arbeitsort") or {}
            place = ", ".join(x for x in (ort.get("ort"), ort.get("region")) if x)
            jobs.append({
                "title": j.get("titel", "") or j.get("beruf", ""),
                "company": j.get("arbeitgeber", ""),
                "location": (place + ", Germany") if place else "Germany",
                "url": j.get("externeUrl")
                       or f"https://www.arbeitsagentur.de/jobsuche/jobdetail/{ref}",
                "description": "",
                "posted_at": iso_date(j.get("aktuelleVeroeffentlichungsdatum")),
                "source": "arbeitsagentur",
                "is_direct": False,
            })
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
            r = web.get(
                f"https://api.adzuna.com/v1/api/jobs/{cc}/search/1",
                params={"app_id": app_id, "app_key": app_key, "what": what,
                        "results_per_page": 50, "content-type": "application/json"},
            timeout=TIMEOUT,
            )
            r.raise_for_status()
            for j in r.json().get("results", []):
                jobs.append({
                    "title": j.get("title", ""),
                    "company": (j.get("company") or {}).get("display_name", ""),
                    "location": (j.get("location") or {}).get("display_name", ""),
                    "url": j.get("redirect_url", ""),
                    "description": (j.get("description") or "")[:5000],
                    "posted_at": iso_date(j.get("created")),
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
            timeout=TIMEOUT,
            )
            r.raise_for_status()
            for j in r.json().get("jobs", []):
                jobs.append({
                    "title": j.get("title", ""),
                    "company": j.get("company", ""),
                    "location": j.get("location", ""),
                    "url": j.get("link", ""),
                    "description": _strip_html(j.get("snippet", "")),
                    "posted_at": iso_date(j.get("updated")),
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
        """Сколько источник дал — и, если ничего, почему.

        Поле error было здесь всегда, и всегда было пустым: сборщики ловят свои
        ошибки сами и возвращают пустой список, так что снаружи отказ выглядел
        точь-в-точь как «ничего не нашлось». На странице «Покрытие» девять
        источников стояли с нулями и без единого слова — человек с антивирусом,
        который перехватывает соединения, читал это как «таких вакансий нет».

        Ошибку берём из того, что сборщик написал в журнал: все одиннадцать
        пишут туда только при отказе, и записать больше им нечего.
        """
        if not enabled:
            return
        сказанное = []

        def свой_журнал(msg):
            сказанное.append(str(msg))
            log(msg)

        got = fn(cfg, свой_журнал)
        jobs.extend(got)
        if coverage is not None:
            coverage.append({"name": name, "url": url, "kind": "aggregator",
                             "count": len(got),
                             "error": "; ".join(сказанное)[:300] or None})

    track(src.get("use_remotive"), "Remotive", "https://remotive.com", remotive)
    track(src.get("use_arbeitnow"), "Arbeitnow", "https://arbeitnow.com", arbeitnow)
    track(src.get("use_wwr", True), "WeWorkRemotely", "https://weworkremotely.com", wwr)
    track(src.get("use_hnhiring"), "HN Who is Hiring", "https://news.ycombinator.com", hn_hiring)
    track(src.get("use_remoteok", True), "RemoteOK", "https://remoteok.com", remoteok)
    track(src.get("use_jobicy", True), "Jobicy", "https://jobicy.com", jobicy)
    track(src.get("use_himalayas", True), "Himalayas", "https://himalayas.app", himalayas)
    track(src.get("use_themuse", True), "The Muse", "https://themuse.com", themuse)
    track(src.get("use_arbeitsagentur", True), "Arbeitsagentur (DE)", "https://arbeitsagentur.de", arbeitsagentur)
    track(bool(src.get("adzuna_app_id")), "Adzuna", "https://adzuna.com", adzuna)
    track(bool(src.get("jooble_key")), "Jooble", "https://jooble.org", jooble)
    return jobs
