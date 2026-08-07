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

from . import esco, iso_date, web
from .. import filters

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


# How many words from the profile go into the search. It was three — and three
# turned out to be too few.
#
# Every word costs one query, so a limit is needed. But at three, what decided
# was the order in which the person wrote their roles, and they have no idea such
# a consequence exists. A seamstress who wrote «Швея, Портная, Seamstress,
# Näherin» got zero jobs: Näherin — the one word the German exchange finds
# anything by (466 positions) — came fourth and was dropped in silence. The
# Russian words say nothing at all to that exchange.
#
# Six, because a pause of a second and a half is kept between hosts: six words
# add nine seconds to a run, and a run takes minutes.
MAX_SEARCH_TERMS = 6


# The words a job title is built from in any industry. On their own they say
# nothing: "Product Developer" exists in chemistry, in hardware and in software,
# and "Technical Designer" is an ordinary position at a games studio.
_GENERIC_WORDS = {
    "product", "technical", "project", "program", "senior", "junior", "lead",
    "principal", "staff", "head", "chief", "deputy", "assistant", "associate",
    "developer", "designer", "engineer", "manager", "specialist", "consultant",
    "analyst", "coordinator", "director", "officer", "expert", "architect",
    "administrator", "supervisor", "operator", "technician", "advisor",
    "разработчик", "дизайнер", "инженер", "менеджер", "специалист", "консультант",
    "аналитик", "руководитель", "старший", "младший", "ведущий", "главный",
}


# Spoken languages. The model puts them among the skills, though they have a
# field of their own, and a query for "English" went off to the sources on equal
# footing with a profession. For Dmitry Kirilyak, two of six words went on
# "English" and "Russian" — a third of the search wasted.
_SPOKEN_LANGUAGES = {
    "english", "russian", "german", "french", "spanish", "italian", "polish",
    "dutch", "portuguese", "swedish", "danish", "norwegian", "finnish", "czech",
    "greek", "turkish", "arabic", "chinese", "japanese", "korean", "hindi",
    "ukrainian", "kazakh", "hebrew", "romanian", "hungarian", "bulgarian",
    "английский", "русский", "немецкий", "французский", "испанский", "казахский",
}


def _generic(term: str) -> bool:
    """Whether the query is made up only of words that say nothing about a trade."""
    words = [w for w in re.split(r"[\s/&+-]+", term.lower()) if w]
    if not words:
        return False
    if all(w in _SPOKEN_LANGUAGES for w in words):
        return True
    return all(w in _GENERIC_WORDS for w in words)


def _search_terms(cfg: dict) -> list:
    """The words the sources that can search go by: EURES, Workable, JobTech,
    Jobicy. The other seven hand over their whole feed, and these words do not
    affect them.

    Generic queries get thrown out — but only while at least one specific one
    remains. Regina Mokhova, a lingerie designer, looked at her results and
    wrote: "perhaps the algorithm is thrown off by the job title Product
    Developer". That is exactly what happened: her roles produced six queries,
    and two of them — "Product Developer" and "Technical Designer" — went to the
    sources as they were, and the sources honestly brought back chemists and
    games level designers. We had asked for them ourselves.

    The "at least one specific one" proviso is for people all of whose roles look
    like that: for a programmer, "Developer" is the only word they call
    themselves by, and it must not be taken away.
    """
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
    specific = [t for t in terms if not _generic(t)]
    return (specific or terms)[:MAX_SEARCH_TERMS] or [""]


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


def workable(cfg: dict, log) -> list:
    """Jobs from employers' own boards — with no key and no web search.

    The idea of the program is to take jobs straight from the companies, past the
    middlemen the companies already pay to find people. There was one road to
    that: the model goes onto the internet, finds companies, the program reads
    their boards. But only Claude Code can go onto the internet, and everyone
    else — and every local model — needs a key for a search service. Without one
    the step was silently skipped, and nothing of the idea was left: the program
    retold the aggregators.

    Workable is not an aggregator but a system where companies run their own
    hiring, and it has an open search across all of their boards at once. So an
    employer can be reached directly with no web search at all: no key, no model,
    no subscription. Every profession is there — "electrician" finds six hundred
    and more positions, "barista" a hundred.

    This does not replace scouting by the model: that searches the whole
    internet, while here there is one hiring system, large as it may be. But the
    aim of the idea — a job from the employer rather than from a middleman — it
    reaches even with a local model.
    """
    jobs, seen = [], set()
    for term in _search_terms(cfg):
        if not term:
            continue
        # Twenty at a time: at twenty-five the service answers with a refusal,
        # and that was established by trying rather than read somewhere — the
        # limit is not written down anywhere. We take several pages in a row
        # while there are any.
        pages, token = [], None
        for _ in range(5):
            query = {"query": term, "limit": 20}
            if token:
                query["pageToken"] = token
            try:
                r = web.get("https://jobs.workable.com/api/v1/jobs",
                            params=query, timeout=TIMEOUT)
                r.raise_for_status()
                data = r.json()
            except (requests.RequestException, ValueError) as e:
                log(f"workable: {e}")
                break
            pages += data.get("jobs") or []
            token = data.get("nextPageToken")
            if not token:
                break
        for j in pages:
            ref = j.get("id", "")
            if not ref or ref in seen:
                continue
            seen.add(ref)
            place = j.get("location") or {}
            parts = [place.get("city"), place.get("countryName")]
            if_remote = ", Remote" if j.get("workplace") == "remote" else ""
            description = " ".join(x for x in (j.get("description"),
                                               j.get("requirementsSection")) if x)
            jobs.append({
                "title": j.get("title", ""),
                "company": (j.get("company") or {}).get("title", ""),
                "location": (", ".join(x for x in parts if x) or "Remote") + if_remote,
                "url": j.get("url", ""),
                "description": _strip_html(description, limit=6000),
                "posted_at": iso_date(j.get("created")),
                "source": "workable",
                # The posting is put up by the company itself in its own hiring
                # system — there is no middleman between it and the person here.
                "is_direct": True,
            })
    return jobs


def eures(cfg: dict, log) -> list:
    """EURES — the Europe-wide jobs portal: all thirty-one countries and every
    profession.

    The whole thing was started for its sake. Eight of our nine sources are IT
    only, and the ninth, the German exchange, knows every profession but one
    country, and understands only German words. A seamstress who wrote her role
    in Russian and in English got zero.

    EURES searches by meaning rather than by letters: underneath it lies ESCO,
    the European occupations taxonomy, and a query for "electrician" brings back
    the Dutch "Elektricien" and "Bordenbouwer". So a person no longer has to
    guess which language to write their profession in.

    In exchange the results are broader than asked for: "seamstress" also brings
    back a stress engineer. That is no disaster — the model looks at the jobs
    afterwards anyway, and sifts out what does not belong. An empty list has
    nothing to sift.

    The address is not in the published API listing: the EURES site itself uses
    it. The same kind of thing as the German exchange's key — it works today and
    may stop tomorrow, and then the source simply returns zero with a line in the
    log.
    """
    jobs, seen = [], set()
    # We ask about the wanted countries up front. We used not to, and a query for
    # Italy and Germany brought back Norway and Belgium — fifty positions per
    # query went on countries nobody had asked about. If no countries were named,
    # or "EU" was, we do not narrow: then the whole union really is wanted.
    countries = filters.country_codes(
        filters.parse_locations(cfg["search"].get("locations", "")),
        only=filters.EURES_CODES)
    words = _search_terms(cfg)
    # First we turn the roles into taxonomy occupation codes and ask by those.
    # Searching EURES by words depends on the word you guessed rather than on the
    # meaning: "dressmaker" brings 28 suitable jobs out of 50, "seamstress" not
    # one, "Lingerie Pattern Maker" one. And the roles the program writes out of a
    # CV come out long, so they find nothing at all. With codes, the same query
    # became 29 out of 50. If no codes are found, we ask by words as before.
    codes = []
    for term in words[:4]:
        for uri in esco.occupations(term):
            if uri not in codes:
                codes.append(uri)
    # And occupations derived from the skills alone. This is for people whose role
    # misleads: a security guard who has learned front-end will write "security
    # guard" on their CV and will never see a junior front-end job. From three of
    # their skills the taxonomy names web developer and webmaster, asking nothing.
    #
    # As a separate query rather than mixed in with the roles: for the lingerie
    # designer the taxonomy knows one skill out of eight, and that one leads to
    # footwear occupations — mixing those into the roles would have spoiled what
    # already works.
    skills = [s.strip() for s in (cfg["profile"].get("skills") or "").split(",") if s.strip()]
    by_skills = [u for u in esco.occupations_by_skills(skills[:8]) if u not in codes]
    # We ask both ways: codes give density, words give breadth. Neither replaces
    # the other. Roles written out of a CV do not always map onto the taxonomy
    # accurately: "Lingerie Pattern Maker" leads to footwear and foundry
    # occupations, because a lingerie pattern maker is not in the taxonomy at all
    # — while the word "lingerie" does find something.
    queries = ([([], codes)] if codes else []) + \
              ([([], by_skills)] if by_skills else []) + \
              [([{"keyword": t, "specificSearchCode": "EVERYWHERE"}], []) for t in words if t]
    for keywords, occ in queries:
        term = keywords[0]["keyword"] if keywords else "by taxonomy code"
        try:
            r = web.post(
                "https://europa.eu/eures/api/jv-searchengine/public/jv-search/search",
                json={
                    "resultsPerPage": 50, "page": 1, "sortSearch": "MOST_RECENT",
                    "keywords": keywords,
                    "occupationUris": occ, "skillUris": [], "requiredExperienceCodes": [],
                    "positionScheduleCodes": [], "sectorCodes": [],
                    "educationAndQualificationLevelCodes": [], "positionOfferingCodes": [],
                    "locationCodes": countries, "euresFlagCodes": [], "otherBenefitsCodes": [],
                    "requiredLanguages": [], "requestLanguage": "en",
                },
                headers={"Content-Type": "application/json"}, timeout=TIMEOUT)
            r.raise_for_status()
            data = r.json()
        except (requests.RequestException, ValueError) as e:
            log(f"eures: {e}")
            continue
        for j in data.get("jvs") or []:
            ref = j.get("id", "")
            if not ref or ref in seen:
                continue
            seen.add(ref)
            # The location arrives as a bare code: {"FR": ["FRL04"]}. There is no
            # city in the answer at all, and a code is not what a person writes
            # in the settings, so the filter threw out every single job. We turn
            # it into a name.
            where = [filters.country_name(k) for k in (j.get("locationMap") or {})]
            # The occupation as an ESCO code — the same one in every language. The
            # posting will arrive in Polish, and the model will read "tailor" and
            # understand whose work this is.
            codes = j.get("jobCategoriesCodes") or []
            jobs.append({
                "title": j.get("title", ""),
                "occupation": esco.label(codes[0]) if codes else "",
                "company": (j.get("employer") or {}).get("name", ""),
                "location": ", ".join(where) or "EU",
                "url": f"https://europa.eu/eures/portal/jv-se/jv-details/{ref}?lang=en",
                "description": _strip_html(j.get("description") or "")[:6000],
                "posted_at": _from_millis(j.get("creationDate")),
                "source": "eures",
                "is_direct": j.get("positionOfferingCode") == "directhire",
            })
    return jobs


def jobtech(cfg: dict, log) -> list:
    """The Swedish labour exchange. Asks for no key at all, knows every
    profession, and — unlike the others — hands over the description right in the
    results rather than one request per job."""
    jobs, seen = [], set()
    for term in _search_terms(cfg):
        if not term:
            continue
        try:
            r = web.get("https://jobsearch.api.jobtechdev.se/search",
                        params={"q": term, "limit": 50}, timeout=TIMEOUT)
            r.raise_for_status()
            data = r.json()
        except (requests.RequestException, ValueError) as e:
            log(f"jobtech: {e}")
            continue
        for j in data.get("hits") or []:
            ref = j.get("id", "")
            if not ref or ref in seen:
                continue
            seen.add(ref)
            place = ((j.get("workplace_address") or {}).get("municipality")
                     or (j.get("workplace_address") or {}).get("region") or "")
            jobs.append({
                "title": j.get("headline", ""),
                "company": (j.get("employer") or {}).get("name", ""),
                "location": (f"{place}, Sweden" if place else "Sweden"),
                "url": (j.get("webpage_url")
                        or (j.get("application_details") or {}).get("url") or ""),
                "description": ((j.get("description") or {}).get("text") or "")[:6000],
                "posted_at": iso_date(j.get("publication_date")),
                "source": "jobtech",
                "is_direct": True,
            })
    return jobs


def _from_millis(v) -> str:
    """A date from epoch milliseconds — that is how EURES hands them over."""
    from datetime import datetime, timezone
    try:
        return datetime.fromtimestamp(int(v) / 1000, timezone.utc).strftime("%Y-%m-%d")
    except (TypeError, ValueError, OSError):
        return ""


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


# The nineteen countries Adzuna knows, in its own codes. Asking about the rest is
# pointless: it answers with a refusal and wastes the request.
ADZUNA_CODES = frozenset({"AT", "AU", "BE", "BR", "CA", "CH", "DE", "ES", "FR",
                         "GB", "IN", "IT", "MX", "NL", "NZ", "PL", "SG", "US", "ZA"})


def adzuna(cfg: dict, log) -> list:
    src = cfg["sources"]
    app_id, app_key = src.get("adzuna_app_id", ""), src.get("adzuna_app_key", "")
    if not (app_id and app_key):
        return []
    jobs = []
    # The countries come from what the person wrote under "Locations". There used
    # to be a separate list in the settings, and it lived a life of its own: search
    # the US only and Adzuna still polled nine countries; write Japan and it would
    # never ask about it. The separate list stays as a fallback: it is also what
    # will show up if our own table does not know something.
    named = filters.country_codes(
        filters.parse_locations(cfg["search"].get("locations", "")), only=ADZUNA_CODES)
    countries = named or [c.strip() for c in src.get("adzuna_countries", "").split(",")
                          if c.strip()]
    what = " ".join(_search_terms(cfg))[:100]
    for cc in countries[:10]:
        try:
            r = web.get(
                f"https://api.adzuna.com/v1/api/jobs/{cc.lower()}/search/1",
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
                    "source": f"adzuna:{cc.lower()}",
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
    # The places are the ones the person wrote. There used to be a hard-coded ""
    # and "remote" here, meaning Jooble was asked with no country at all: it
    # claims some seventy countries, and it was the one source able to cover
    # Russia, the UAE or India — and we never once asked it about them.
    places = [p for p in filters.parse_locations(cfg["search"].get("locations", ""))
              if p not in ("ес", "eu", "европа", "europe")][:5]
    for loc in (places or [""]) + ["remote"]:
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


# Every source in one list. They used to be spelled out as calls inside
# collect(), and there was no way to count them from outside — which is why the
# "Model" page said "collected from nine aggregators" for years, when there had
# long been twelve. Now there is nowhere to count them but here, and the two can
# no longer drift apart.
#
#   (settings key, on without being asked, name, address, collector's name)
#
# The last two have a key instead of a checkbox: without one the service does not
# answer, so "on" for them means "a key has been entered".
#
# The collector is named by a string rather than put here itself, and that is not
# a slip. The list is built when the module loads, and a function put into it
# then stays in it forever: substituting a collector by name — which is how both
# the tests and the failure handling substitute it — would become impossible, and
# the original would be called regardless. This has happened once already: a test
# with a stub in place of Remotive quietly went to the real Remotive and brought
# back thirty-three jobs.
SOURCES = [
    ("use_remotive", True, "Remotive", "https://remotive.com", "remotive"),
    ("use_arbeitnow", True, "Arbeitnow", "https://arbeitnow.com", "arbeitnow"),
    ("use_wwr", True, "WeWorkRemotely", "https://weworkremotely.com", "wwr"),
    ("use_hnhiring", True, "HN Who is Hiring", "https://news.ycombinator.com", "hn_hiring"),
    ("use_remoteok", True, "RemoteOK", "https://remoteok.com", "remoteok"),
    ("use_jobicy", True, "Jobicy", "https://jobicy.com", "jobicy"),
    ("use_himalayas", True, "Himalayas", "https://himalayas.app", "himalayas"),
    ("use_themuse", True, "The Muse", "https://themuse.com", "themuse"),
    # Off: the service refuses every path and every name, and no OAuth token is
    # issued. The site itself opens fine — it looks as though the open API was
    # closed or fenced off from robots. Left on, it produced only a line of error
    # text in "Coverage" every run and not one job. Germany is now covered by
    # EURES, which knows it along with every other EU country.
    ("use_arbeitsagentur", False, "Arbeitsagentur (DE)", "https://arbeitsagentur.de", "arbeitsagentur"),
    # Sources that know every profession, not only IT. The rest are boards for
    # programmers, and no seamstress, barista or fitter is to be found on them.
    ("use_eures", True, "EURES (EU)", "https://europa.eu/eures", "eures"),
    ("use_jobtech", True, "JobTech (SE)", "https://jobtechdev.se", "jobtech"),
    # Employers' own boards: the posting is put up by the company in its own
    # hiring system, with no middleman between it and the person.
    ("use_workable", True, "Workable (employers)", "https://jobs.workable.com", "workable"),
    ("adzuna_app_id", False, "Adzuna", "https://adzuna.com", "adzuna"),
    ("jooble_key", False, "Jooble", "https://jooble.org", "jooble"),
]


# Who searches by our words, and who simply hands over their whole feed.
#
# The difference decides more than it seems. Seven sources hand over a feed, and
# all seven are boards for programmers: to a lingerie designer's query they send
# their five hundred IT jobs in full. Four search by words, and among them is
# EURES, which searches by meaning through the European occupations taxonomy and
# brings back "Szwaczka" for a query of "seamstress".
#
# After that the jobs are ordered by how their words match the profile — and the
# Polish "Szwaczka" shares not one letter with the English "Lingerie Pattern
# Maker", while an IT job with the word "Designer" in it does match. So what was
# found by meaning went to the tail, below the line, and what matched by accident
# went off to be scored.
SEARCH_BY_WORDS = {"eures", "workable", "jobtech", "jobicy", "arbeitsagentur",
                  "adzuna", "jooble"}


def enabled(cfg: dict) -> list:
    """Which sources are polled right now — those are the ones we name to a person."""
    src = cfg.get("sources", {})
    return [s for s in SOURCES if bool(src.get(s[0], s[1]))]


def collect(cfg: dict, log, coverage: list = None) -> list:
    jobs = []

    def track(enabled, name, url, fn):
        """How much a source gave — and, if nothing, why.

        The error field has always been here, and has always been empty: the
        collectors catch their own errors and return an empty list, so from the
        outside a failure looked exactly like "nothing was found". On the
        "Coverage" page the sources stood there with zeros and not a word — and a
        person whose antivirus intercepts connections read that as "there are no
        such jobs".

        We take the error from what the collector wrote to the log: they all write
        there only on failure, and have nothing else to write.
        """
        if not enabled:
            return
        said = []

        def own_log(msg):
            said.append(str(msg))
            log(msg)

        got = fn(cfg, own_log)
        jobs.extend(got)
        if coverage is not None:
            coverage.append({"name": name, "url": url, "kind": "aggregator",
                             "count": len(got),
                             "error": "; ".join(said)[:300] or None})

    src = cfg["sources"]
    for key, unasked, name, url, collector in SOURCES:
        before = len(jobs)
        track(bool(src.get(key, unasked)), name, url, globals()[collector])
        if collector in SEARCH_BY_WORDS:
            for j in jobs[before:]:
                j["by_query"] = True
    return jobs
