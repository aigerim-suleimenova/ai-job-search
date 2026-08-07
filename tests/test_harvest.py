"""Employers from links we have already downloaded.

The check runs without the network: the assembled addresses are never requested,
only built and recognised back. It is that round trip — assemble, then recognise
— which matters here: without it the same board would be added afresh every run.
"""
import pytest

from jobsearch import harvest
from jobsearch.collectors import ats


def job_at(url, company="Company"):
    return {"url": url, "company": company}


def test_a_board_from_a_link_to_a_job():
    found = harvest.find_new([job_at("https://boards.greenhouse.io/mercury/jobs/4012", "Mercury")], [])
    assert found == [{"name": "Mercury", "url": "https://boards.greenhouse.io/mercury"}]


def test_an_assembled_address_is_recognised_back():
    """Otherwise the board is added a second time on the next run."""
    links = [
        "https://boards.greenhouse.io/mercury/jobs/1",
        "https://jobs.lever.co/xsolla/abc",
        "https://jobs.ashbyhq.com/n8n/xyz",
        "https://apply.workable.com/seeq/j/123",
        "https://careers.smartrecruiters.com/ABOUTYOUGmbH/744",
        "https://payflows.recruitee.com/o/engineer",
        "https://digacon-software.jobs.personio.com/job/1",
    ]
    for c in harvest.find_new([job_at(u) for u in links], [], limit=99):
        assert ats.detect(c["url"]), f"{c['url']} is not recognised back"


def test_a_repeat_run_adds_nothing():
    jobs = [job_at("https://boards.greenhouse.io/mercury/jobs/1"),
            job_at("https://jobs.ashbyhq.com/n8n/xyz")]
    first_pass = harvest.find_new(jobs, [], limit=99)
    assert len(first_pass) == 2
    assert harvest.find_new(jobs, first_pass, limit=99) == []


def test_different_links_to_one_board_give_one_entry():
    jobs = [job_at("https://boards.greenhouse.io/mercury/jobs/1"),
            job_at("https://boards.greenhouse.io/mercury/jobs/2"),
            job_at("https://job-boards.greenhouse.io/mercury/jobs/3")]
    assert len(harvest.find_new(jobs, [], limit=99)) == 1


def test_a_company_already_watched_is_not_duplicated():
    """The person added the board by hand — even if spelled differently."""
    watched = [{"name": "Mercury", "url": "https://boards.greenhouse.io/mercury/"}]
    jobs = [job_at("https://job-boards.greenhouse.io/mercury/jobs/9")]
    assert harvest.find_new(jobs, watched) == []


def test_the_per_run_limit_is_kept():
    jobs = [job_at(f"https://jobs.ashbyhq.com/company{i}/x") for i in range(30)]
    assert len(harvest.find_new(jobs, [], limit=10)) == 10
    assert harvest.find_new(jobs, [], limit=0) == []


def test_ordinary_links_are_skipped():
    jobs = [job_at("https://example.com/careers/frontend"),
            job_at("https://linkedin.com/jobs/view/123"),
            job_at("")]
    assert harvest.find_new(jobs, []) == []


def test_with_no_company_name_we_take_the_boards():
    found = harvest.find_new([job_at("https://jobs.lever.co/xsolla/a", company="")], [])
    assert found[0]["name"] == "xsolla"


def test_the_checkbox_is_visible_and_can_be_turned_off(profile):
    """The company list fills up by itself — a person has to know that, and be
    able to forbid it without reading the source."""
    import pytest
    pytest.importorskip("httpx", reason="TestClient needs httpx")
    from fastapi.testclient import TestClient

    import app as app_module
    from jobsearch import appstate, config, i18n

    appstate.mark_setup_done(config.load()["llm"])   # or first launch sends us to the introduction
    with TestClient(app_module.app, base_url="http://127.0.0.1:8765") as c:
        c.cookies.set("profile", profile)
        page = c.get("/").text
        assert 'name="harvest_boards"' in page
        lang = config.load()["ui"]["lang"]
        assert i18n.t(lang, "harvest_boards") in page
        assert i18n.t(lang, "harvest_boards_hint")[:40] in page

        # the box is clear — the field simply is not in the form
        c.post("/save", data={"companies": "", "use_remotive": "on"}, follow_redirects=False)
        assert config.load()["sources"]["harvest_boards"] is False

        c.post("/save", data={"companies": "", "harvest_boards": "on"}, follow_redirects=False)
        assert config.load()["sources"]["harvest_boards"] is True


# --- What a source said when it gave nothing ----------------------------------

def only_remotive() -> dict:
    """One source on and the rest off: each has settings requirements of its own,
    and they are not what we are checking here."""
    from jobsearch import config
    cfg = config._merge(config.DEFAULTS, {})
    for key in list(cfg["sources"]):
        if key.startswith("use_"):
            cfg["sources"][key] = False
    cfg["sources"]["use_remotive"] = True
    return cfg


def test_a_sources_failure_reaches_the_coverage(monkeypatch):
    """The error field in coverage was always there — and always empty: the
    collectors catch their own errors and return an empty list, so from the
    outside a failure looked exactly like "nothing was found". On the "Coverage"
    page the sources stood there with zeros and not a word, and a person whose
    antivirus intercepts connections read that as "there are no such jobs"."""
    import requests

    from jobsearch.collectors import aggregators

    def broken(cfg, log):
        log("remotive: SSLError certificate verify failed")
        return []

    monkeypatch.setattr(aggregators, "remotive", broken)
    coverage = []

    aggregators.collect(only_remotive(), lambda m: None, coverage)

    ours = [c for c in coverage if c["name"] == "Remotive"][0]
    assert ours["count"] == 0
    assert ours["error"] and "certificate" in ours["error"], "the failure was lost on the way"


def test_a_working_source_has_no_error(monkeypatch):
    """The other side: saying "error" where there was none is no better than silence."""
    from jobsearch.collectors import aggregators

    monkeypatch.setattr(aggregators, "remotive",
                        lambda cfg, log: [{"title": "SAP Integration Consultant"}])
    coverage = []

    aggregators.collect(only_remotive(), lambda m: None, coverage)

    ours = [c for c in coverage if c["name"] == "Remotive"][0]
    assert ours["count"] == 1 and ours["error"] is None


# --- "Remote" is not the same as "from anywhere" ------------------------------

CASES = [
    # (the job's location, what was searched for, whether to let it through)
    ("United States, Remote", ["germany"], False),   # exactly what we were caught by
    ("Remote (US only)", ["germany"], False),
    ("Remote — Europe", ["germany"], True),
    ("Remote", ["germany"], True),                   # nothing named — genuinely from anywhere
    ("Anywhere", ["germany"], True),
    ("United States, Remote", ["usa"], True),        # the US was asked for, the US is what came
    ("Berlin, Remote", ["germany"], True),
    ("Remote, Poland", ["eu"], True),
    ("APAC, Remote", ["germany"], False),
    ("Remote, Canada", ["eu"], False),
    ("Remote, London", ["germany"], False),
    # People often write "remote" themselves, among their own places. The
    # previous fix closed only the "remote too" checkbox, and through this word
    # the hole opened again: on a run for a front-end developer searching in
    # Russia, the top held "USA, Remote" and "Sunnyvale, CA".
    ("USA, Remote", ["россия", "москва", "удалённо"], False),
    ("Sunnyvale, CA", ["россия", "москва", "удалённо"], False),
    ("Remote", ["россия", "москва", "удалённо"], True),
    ("Москва, удалённо", ["россия", "москва", "удалённо"], True),
    # And if nothing but "remote" is named, the work really is from anywhere
    ("USA, Remote", ["удалённо"], True),
    ("Berlin, Remote", ["remote"], True),
]


@pytest.mark.parametrize("place,wanted,allow", CASES)
def test_remote_work_is_still_somewhere(place, wanted, allow):
    """The "remote" check used to come first and let through everything the word
    appeared in. On a real run from a SAP integrator's CV, eight of thirty-two
    jobs turned out to be from one American outfit marked "United States, Remote"
    — not one of them suited a person in Europe. The model noticed and wrote "US
    only", and the filter, which exists for exactly this, let it through."""
    from jobsearch import filters
    assert filters.location_ok(place, wanted, include_remote=True) is allow, place


def test_without_the_checkbox_remote_jobs_are_cut_as_before():
    from jobsearch import filters
    assert filters.location_ok("Remote", ["germany"], include_remote=False) is False


def test_an_unknown_location_is_still_not_cut():
    """An empty location is no reason to throw a job out: triage will sort it."""
    from jobsearch import filters
    assert filters.location_ok("", ["germany"]) is True


# --- What words we ask with ---------------------------------------------------

def test_there_are_enough_search_words_for_several_languages():
    """It was three, and three turned out to be too few. A seamstress who wrote
    «Швея, Портная, Seamstress, Näherin» got zero jobs: Näherin — the one word the
    German exchange finds anything by (466 positions) — came fourth and was
    dropped in silence. What decided was the order in which the person wrote their
    roles, and they have no idea such a consequence exists."""
    from jobsearch import config
    from jobsearch.collectors import aggregators

    cfg = config._merge(config.DEFAULTS, {})
    cfg["profile"]["roles"] = "Швея, Портная, Seamstress, Näherin"
    cfg["profile"]["skills"] = ""
    cfg["search"]["match_priority"] = "role"

    words = aggregators._search_terms(cfg)

    assert "Näherin" in words, "the one word that finds anything was dropped"


def test_the_limit_on_words_stays():
    """With no limit at all, every word would cost a separate request to somebody
    else's server, and a person can list thirty skills."""
    from jobsearch import config
    from jobsearch.collectors import aggregators

    cfg = config._merge(config.DEFAULTS, {})
    cfg["profile"]["roles"] = ", ".join(f"role{i}" for i in range(30))
    cfg["profile"]["skills"] = ", ".join(f"skill{i}" for i in range(30))

    assert len(aggregators._search_terms(cfg)) == aggregators.MAX_SEARCH_TERMS


# --- Employer boards are looked for in the posting text too -------------------

def test_a_board_from_the_posting_text():
    """The address alone is not enough, and that is measured: across fifteen hundred
    collected jobs not one address led to an employer's own board — every one came
    back to an aggregator. It could hardly be otherwise: an aggregator has no
    reason to send a person past itself.

    In the text, though, the links are there — wherever the company wrote the
    posting itself. In a single "Ask HN: Who is hiring?" thread there were
    eighty-two of them, and the employer list grew from nothing to forty-eight."""
    post = {
        "url": "https://news.ycombinator.com/item?id=123",
        "company": "Nova Credit",
        "description": ("Nova Credit | Senior Engineer | Remote\n"
                        "We are hiring. Apply at "
                        "https://boards.greenhouse.io/novacredit/jobs/4012 — "
                        "or write to jobs@example.com"),
    }
    found = harvest.find_new([post], [])
    assert found == [{"name": "Nova Credit",
                      "url": "https://boards.greenhouse.io/novacredit"}]


def test_every_board_in_one_post_is_taken():
    post = {"url": "https://news.ycombinator.com/item?id=1", "company": "Two outfits",
            "description": ("https://jobs.ashbyhq.com/rho and "
                            "https://jobs.lever.co/xsolla/abc")}
    addresses = {c["url"] for c in harvest.find_new([post], [], limit=99)}
    assert addresses == {"https://jobs.ashbyhq.com/rho", "https://jobs.lever.co/xsolla"}


def test_unrelated_links_in_the_text_are_not_taken():
    """A job description is full of links — to the company site, to an article, to
    LinkedIn. None of them is an employer's board."""
    post = {"url": "https://news.ycombinator.com/item?id=2", "company": "Acme",
            "description": ("https://acme.com https://linkedin.com/company/acme "
                            "https://twitter.com/acme https://blog.acme.com/hiring")}
    assert harvest.find_new([post], []) == []


def test_a_board_already_watched_is_not_duplicated_from_the_text():
    watched = [{"name": "Nova Credit", "url": "https://boards.greenhouse.io/novacredit/"}]
    post = {"url": "https://news.ycombinator.com/item?id=3", "company": "Nova Credit",
            "description": "https://boards.greenhouse.io/novacredit/jobs/9"}
    assert harvest.find_new([post], watched) == []


# --- Jobs from the employers themselves, with no key --------------------------

def only_workable() -> dict:
    from jobsearch import config
    cfg = config._merge(config.DEFAULTS, {})
    for key in list(cfg["sources"]):
        if key.startswith("use_"):
            cfg["sources"][key] = False
    cfg["sources"]["use_workable"] = True
    cfg["profile"]["roles"] = "Electrician"
    cfg["profile"]["skills"] = ""
    cfg["search"]["match_priority"] = "role"
    return cfg


def test_jobs_from_the_employer_are_marked_direct(monkeypatch):
    """The idea of the program is to take jobs from the companies, past the
    middlemen they already pay to find people. Here the posting is put up by the
    company itself in its own hiring system, and that is not "almost direct" but
    direct."""
    from jobsearch.collectors import aggregators

    class Answer:
        status_code = 200

        @staticmethod
        def raise_for_status():
            pass

        @staticmethod
        def json():
            return {"jobs": [{
                "id": "abc", "title": "Elektriker (m/w/d)",
                "company": {"title": "Skeleton Technologies"},
                "location": {"city": "Markranstädt", "countryName": "Germany"},
                "url": "https://jobs.workable.com/view/x/elektriker",
                "description": "<p>Wartung und Instandhaltung</p>",
                "requirementsSection": "<p>Ausbildung als Elektriker</p>",
                "created": "2026-07-29T17:24:17.324Z", "workplace": "onsite",
            }]}

    monkeypatch.setattr(aggregators.web, "get", lambda *a, **kw: Answer())
    jobs_found = aggregators.workable(only_workable(), lambda m: None)

    assert len(jobs_found) == 1
    v = jobs_found[0]
    assert v["is_direct"] is True, "a job from the company is marked as coming from a middleman"
    assert v["company"] == "Skeleton Technologies"
    assert "Markranstädt" in v["location"] and "Germany" in v["location"]
    assert "Wartung" in v["description"] and "Ausbildung" in v["description"], \
        "the requirements were lost — they sit in a field of their own"


def test_the_pages_are_taken_one_after_another(monkeypatch):
    """Twenty at a time: at twenty-five the service answers with a refusal, and that
    was established by trying — the limit is written down nowhere."""
    from jobsearch.collectors import aggregators
    requested = []

    class Answer:
        def __init__(self, token, job_id):
            self._token, self._job_id = token, job_id

        status_code = 200

        def raise_for_status(self):
            pass

        def json(self):
            return {"jobs": [{"id": self._job_id, "title": "x", "company": {"title": "A"},
                              "location": {}, "url": "u", "description": "d"}],
                    "nextPageToken": self._token}

    pages = [Answer("second", "1"), Answer(None, "2")]

    def fake(url, params=None, timeout=None):
        requested.append((params or {}).get("pageToken"))
        return pages[len(requested) - 1]

    monkeypatch.setattr(aggregators.web, "get", fake)
    jobs_found = aggregators.workable(only_workable(), lambda m: None)

    assert requested == [None, "second"], "the second page was never requested"
    assert len(jobs_found) == 2
