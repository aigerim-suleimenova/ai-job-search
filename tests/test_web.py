"""How the program behaves on other people's sites.

Checked without the network: requests.get is stood in for, and a prepared text is
handed back instead of robots.txt. Otherwise the test would depend on the mood of
somebody else's server.
"""
import time

import pytest

from jobsearch.collectors import web


@pytest.fixture(autouse=True)
def clean_cache(monkeypatch):
    web._robots.clear()
    web._last_hit.clear()
    # The address check asks the system where a name leads. The tests do not go
    # to the network — neither for pages nor for names — so we answer ourselves,
    # with an ordinary internet address. The check itself stays in play: stubbing
    # it out entirely would mean testing code with the check taken out of it.
    monkeypatch.setattr(web.socket, "getaddrinfo",
                        lambda host, port, **kw: [(2, 1, 6, "", ("93.184.216.34", port))])
    yield
    web._robots.clear()
    web._last_hit.clear()


class Answer:
    def __init__(self, text="", status=200, headers=None):
        self.text = text
        self.status_code = status
        self.url = "https://example.com/"
        self.headers = headers or {}

    def raise_for_status(self):
        pass

    def close(self):
        pass


def stub_out(monkeypatch, robots="", **kw):
    """Hands back the given robots.txt, and an empty page for every other address."""
    calls = []

    def fake_get(url, **rest):
        calls.append(url)
        if url.endswith("/robots.txt"):
            return Answer(robots, kw.get("robots_status", 200))
        return Answer("<html>ok</html>")

    monkeypatch.setattr(web.requests, "get", fake_get)
    return calls


def test_it_introduces_itself_honestly():
    ua = web.UA["User-Agent"]
    assert "Mozilla" not in ua, "the browser disguise is back"
    assert "ai-job-search" in ua
    assert "github.com" in ua, "the name should make it clear where to write"


def test_a_ban_in_robots_is_honoured(monkeypatch):
    stub_out(monkeypatch, robots="User-agent: *\nDisallow: /careers\n")
    assert web.allowed("https://example.com/about") is True
    assert web.allowed("https://example.com/careers/all") is False


def test_a_forbidden_page_is_not_requested(monkeypatch):
    calls = stub_out(monkeypatch, robots="User-agent: *\nDisallow: /\n")
    assert web.get("https://example.com/careers", respect_robots=True) is None
    assert not [u for u in calls if not u.endswith("robots.txt")], "the request went out after all"


def test_with_no_robots_we_go_as_usual(monkeypatch):
    stub_out(monkeypatch, robots="", robots_status=404)
    assert web.allowed("https://example.com/careers") is True
    assert web.get("https://example.com/careers", respect_robots=True) is not None


def test_a_broken_robots_does_not_bring_us_down(monkeypatch):
    stub_out(monkeypatch, robots="\x00\x01 rubbish ][")
    assert web.allowed("https://example.com/careers") is True


def test_the_pause_between_requests_to_one_host(monkeypatch):
    stub_out(monkeypatch)
    monkeypatch.setattr(web, "DELAY", 0.2)
    started_at = time.monotonic()
    for _ in range(3):
        web.get("https://example.com/page")
    elapsed = time.monotonic() - started_at
    assert elapsed >= 0.4, f"three requests fitted into {elapsed:.2f}s — there is no pause"


def test_different_hosts_do_not_wait_for_each_other(monkeypatch):
    stub_out(monkeypatch)
    monkeypatch.setattr(web, "DELAY", 0.3)
    started_at = time.monotonic()
    web.get("https://a.example/page")
    web.get("https://b.example/page")
    assert time.monotonic() - started_at < 0.3, "hosts must not queue up behind one another"


def test_robots_is_requested_once_per_host(monkeypatch):
    calls = stub_out(monkeypatch, robots="User-agent: *\nDisallow: /nope\n")
    monkeypatch.setattr(web, "DELAY", 0)
    for i in range(3):
        web.get(f"https://example.com/page{i}", respect_robots=True)
    assert len([u for u in calls if u.endswith("robots.txt")]) == 1


def test_the_crawl_delay_from_robots_is_respected(monkeypatch):
    """The standard parser understands whole-number delays only — it ignores
    fractional ones silently, which is why we check a whole one."""
    stub_out(monkeypatch, robots="User-agent: *\nCrawl-delay: 1\n")
    monkeypatch.setattr(web, "DELAY", 0.01)
    web.get("https://example.com/a", respect_robots=True)
    started_at = time.monotonic()
    web.get("https://example.com/b", respect_robots=True)
    assert time.monotonic() - started_at >= 0.9, "the site asked us to wait longer — so we wait"


# --- Where we will not go -----------------------------------------------------
#
# Employers land in the list through links inside jobs already collected, and
# those jobs are written by anyone at all. A link to 127.0.0.1 is a request to
# step inside the machine and fetch what cannot be reached from outside.

INTERNAL = [
    ("http://127.0.0.1:11434/api/tags", "127.0.0.1", "loopback: our own Ollama is there"),
    ("http://localhost:8765/settings", "127.0.0.1", "loopback by name"),
    ("http://169.254.169.254/latest/meta-data/", "169.254.169.254", "the keys of a cloud account"),
    ("http://192.168.1.1/", "192.168.1.1", "somebody's router on the home network"),
    ("http://10.0.0.5/admin", "10.0.0.5", "a private network"),
    ("http://[::1]/", "::1", "loopback over IPv6"),
]


def resolves_to(monkeypatch, ip: str):
    family = 30 if ":" in ip else 2
    monkeypatch.setattr(web.socket, "getaddrinfo",
                        lambda host, port, **kw: [(family, 1, 6, "", (ip, port))])


@pytest.mark.parametrize("url,ip,why", INTERNAL)
def test_we_do_not_go_inside_the_machine_or_the_network(monkeypatch, url, ip, why):
    resolves_to(monkeypatch, ip)
    with pytest.raises(web.Refused):
        web.check(url)


@pytest.mark.parametrize("scheme", ["file", "ftp", "gopher", "data", ""])
def test_foreign_schemes_are_cut_off(scheme):
    address = f"{scheme}://example.com/x" if scheme else "example.com/x"
    with pytest.raises(web.Refused):
        web.check(address)


def test_an_ordinary_site_is_let_through(monkeypatch):
    """The other side: shutting out the internal must not cost us the job
    collection."""
    resolves_to(monkeypatch, "93.184.216.34")
    web.check("https://boards-api.greenhouse.io/v1/boards/acme/jobs")


def test_a_name_leading_inward_does_not_fool_us(monkeypatch):
    """A name can be anything at all and still lead to the loopback: we look not at
    the name but at where it resolves."""
    resolves_to(monkeypatch, "127.0.0.1")
    with pytest.raises(web.Refused):
        web.check("https://careers.example.com/jobs")


def test_every_redirect_is_checked(monkeypatch):
    """Otherwise the check would be worth nothing: the site answers "go to
    127.0.0.1", requests obediently goes, and the checked first address turns out
    to be beside the point."""
    route = {"https://jobs.example.com/": ("93.184.216.34", 302, "http://127.0.0.1:11434/api/tags"),
            "http://127.0.0.1:11434/api/tags": ("127.0.0.1", 200, "")}
    visited = []

    def getaddrinfo(host, port, **kw):
        ip = "127.0.0.1" if host in ("127.0.0.1", "localhost") else "93.184.216.34"
        return [(2, 1, 6, "", (ip, port))]

    def fake_get(url, **kw):
        visited.append(url)
        _, status, location = route[url]
        return Answer(status=status, headers={"location": location} if location else {})

    monkeypatch.setattr(web.socket, "getaddrinfo", getaddrinfo)
    monkeypatch.setattr(web.requests, "get", fake_get)

    with pytest.raises(web.Refused):
        web.get("https://jobs.example.com/")

    assert visited == ["https://jobs.example.com/"], "we followed the redirect after all"


def test_a_redirect_to_an_ordinary_site_goes_through(monkeypatch):
    """The other side again: sites move, and that is an ordinary thing."""
    def fake_get(url, **kw):
        if url == "https://jobs.example.com/":
            return Answer(status=301, headers={"location": "/careers/"})
        return Answer("<html>jobs</html>")

    monkeypatch.setattr(web.requests, "get", fake_get)
    r = web.get("https://jobs.example.com/")
    assert "jobs" in r.text


def test_a_redirect_loop_is_broken_off(monkeypatch):
    monkeypatch.setattr(web.requests, "get",
                        lambda url, **kw: Answer(status=302, headers={"location": "/again"}))
    monkeypatch.setattr(web, "DELAY", 0)
    with pytest.raises(web.Refused):
        web.get("https://example.com/")


def test_we_do_not_ask_an_internal_address_for_robots_either(monkeypatch):
    """Asking 127.0.0.1 "may we come in?" is already a trip to 127.0.0.1."""
    resolves_to(monkeypatch, "127.0.0.1")
    visited = []
    monkeypatch.setattr(web.requests, "get",
                        lambda url, **kw: (visited.append(url), Answer(""))[1])

    web.allowed("http://127.0.0.1:11434/robots-ok")

    assert visited == [], "we went inside the machine for robots.txt after all"


# --- How many sources we name to a person -------------------------------------

def test_we_count_the_sources_rather_than_writing_the_number_out():
    """The "Model" page said "collected from nine aggregators" for years, and there
    were twelve: the number lived in the translations, the sources in the code,
    and there was nothing to check one against the other."""
    from jobsearch import config
    from jobsearch.collectors import aggregators
    now_on = aggregators.enabled(config.DEFAULTS)
    # Eleven: the German exchange is off — its open API refuses every path, and
    # switched on it produced only a line of error text in "Coverage" every run.
    assert len(now_on) == 11, f"there are {len(now_on)} sources, and the notice promises otherwise"
    assert "arbeitsagentur" not in {s[4] for s in now_on}


def test_the_source_list_and_the_polling_do_not_drift_apart(monkeypatch):
    """enabled() and collect() must walk the same list, or the counter will start
    promising something other than what is done again."""
    from jobsearch import config
    from jobsearch.collectors import aggregators
    # The collectors are stubbed: we check who was called, not what the network says.
    for *_, collector in aggregators.SOURCES:
        monkeypatch.setattr(aggregators, collector, lambda cfg, log: [])
    polled = []
    cfg = config.DEFAULTS
    aggregators.collect(cfg, lambda *a: None, polled)
    polled_names = {c["name"] for c in polled}
    listed_names = {s[2] for s in aggregators.enabled(cfg)}
    assert polled_names == listed_names


# --- A location that arrived as a country code --------------------------------

def test_a_country_code_turns_into_a_name():
    """EURES hands back a location as a bare code — "BE", "SE", "FR" — and we put it
    into the location field as it was. The filter compared the code against the
    word «нидерланды» and threw everything out: 227 jobs per run, every single
    one, every time. And EURES is the one source that knows every profession and
    every country in the EU."""
    from jobsearch import filters
    assert filters.country_name("NL") == "Netherlands"
    assert filters.country_name("fr") == "France"
    assert filters.country_name("") == ""
    assert filters.country_name("ZZ") == "ZZ", "we do not invent an unfamiliar code"


def test_a_country_from_a_code_passes_the_filter():
    from jobsearch import filters
    wanted = filters.parse_locations("Италия, Германия, Франция, Нидерланды")
    for code in ("IT", "DE", "FR", "NL"):
        place = filters.country_name(code)
        assert filters.location_ok(place, wanted, True), f"{code} → {place} did not pass"
    assert not filters.location_ok(filters.country_name("NO"), wanted, True)


def test_searching_in_russian_works_beyond_italy_and_germany():
    """Countries other than these two were not listed here at all, and «Франция» was
    checked by plain substring: "Paris, France" did not suit a person who had
    written «Франция». Searching in Russian worked for two countries out of
    thirty-one."""
    from jobsearch import filters
    cases = [("Paris, France", "Франция"), ("Madrid", "Испания"),
              ("Amsterdam", "Нидерланды"), ("Warszawa", "Польша"),
              ("Lisboa", "Португалия"), ("Stockholm", "Швеция"),
              ("Praha", "Чехия"), ("Wien", "Австрия")]
    for place, country in cases:
        assert filters.location_ok(place, filters.parse_locations(country), True), \
            f"«{place}» was not found by the word «{country}»"


def test_the_country_codes_for_a_source():
    """The EURES query was not limited by country, and a search for Italy and
    Germany brought back Norway and Belgium."""
    from jobsearch import filters
    codes = filters.country_codes(filters.parse_locations("Италия, Germany, нидерланды"))
    assert set(codes) == {"IT", "DE", "NL"}
    assert filters.country_codes(filters.parse_locations("ЕС")) == [], \
        "the whole union was named — we do not narrow"


# --- Queries that belong to no trade ------------------------------------------

def test_generic_queries_do_not_go_to_the_sources():
    """Regina, a lingerie designer, having looked at her results: "perhaps the
    algorithm is thrown off by the job title Product Developer". So it was — we
    had asked for it ourselves, and the sources honestly brought back chemists."""
    from jobsearch.collectors.aggregators import _search_terms
    cfg = {"profile": {"roles": "Lingerie Pattern Maker, Technical Designer, "
                                "Product Developer, Garment Technologist", "skills": ""},
           "search": {}}
    came_out = _search_terms(cfg)
    assert "Product Developer" not in came_out
    assert "Technical Designer" not in came_out
    assert "Lingerie Pattern Maker" in came_out and "Garment Technologist" in came_out


def test_when_every_role_is_generic_the_queries_stay():
    """For a programmer "Developer" is the only word they call themselves by."""
    from jobsearch.collectors.aggregators import _search_terms
    cfg = {"profile": {"roles": "Developer, Senior Engineer", "skills": ""}, "search": {}}
    assert _search_terms(cfg) == ["Developer", "Senior Engineer"]


# --- Locations: what let us down on real runs ---------------------------------

PLACES = [
    # (what the person wrote, what the job says, whether it fits)
    #
    # Canada arrived in a search for the US twenty-six times per run: "ca", the
    # abbreviation for California, was searched for as a plain substring and found
    # inside "Calgary, Canada".
    ("США, Германия, ЕС", "Calgary, Canada", False),
    ("США, Германия, ЕС", "Ottawa, Canada", False),
    ("США, Германия, ЕС", "Berkeley, CA", True),
    # Greece and Sweden arrived correctly: the person asked for the EU, and they are in it.
    ("США, Германия, ЕС", "Gaios, Greece", True),
    ("США, Германия, ЕС", "Umeå, Sweden", True),
    ("США, Германия, ЕС", "Missoula, United States", True),
    # Russia was not in the table at all, and «Москва» was filtered out for anyone
    # who wrote «Россия»: the word «россия» is not in the string, and there was
    # nothing to link the two.
    ("Россия, Омск, удалённо", "Москва", True),
    ("Россия, Омск, удалённо", "Омск", True),
    ("Россия, Омск, удалённо", "Novosibirsk", True),
    # And the other way round: a country not on the lists read as "no location
    # named", meaning "work from anywhere", and went into any search.
    ("Россия, Омск, удалённо", "Kenya, Remote", False),
    ("Россия, Омск, удалённо", "Panama, Remote", False),
    ("Германия, ЕС", "Amman, Jordan", False),
    ("Германия, ЕС", "Morocco, Remote", False),
    # Genuinely remote with no country — suits everyone.
    ("Россия, Омск, удалённо", "Remote", True),
    ("Германия, Нидерланды, ЕС", "Walldorf, Germany", True),
]


@pytest.mark.parametrize("typed,job_location,fits", PLACES)
def test_locations_from_real_runs(typed, job_location, fits):
    from jobsearch import filters
    assert filters.location_ok(job_location, filters.parse_locations(typed), True) is fits


def test_a_marker_is_matched_as_a_whole_word():
    """A short marker settled inside a longer word. That is the root of every miss
    above: "ca" inside "Canada", and "us" would be found in "Belarus"."""
    from jobsearch import filters
    assert filters._has(" berkeley, ca ", ["ca"]) is True
    assert filters._has(" calgary, canada ", ["ca"]) is False
    assert filters._has(" minsk, belarus ", ["us"]) is False
    assert filters._has(" austin, us ", ["us"]) is True


def test_we_ask_eures_only_about_its_own_countries():
    """EURES knows thirty-one countries. Asking it about Brazil wastes a query, and
    there is only one query per word."""
    from jobsearch import filters
    places = filters.parse_locations("США, Германия, Бразилия, Нидерланды")
    assert set(filters.country_codes(places, only=filters.EURES_CODES)) == {"DE", "NL"}
    assert set(filters.country_codes(places)) == {"US", "DE", "BR", "NL"}


# --- We ask EURES with occupation codes, not guessed words --------------------

def test_a_role_maps_onto_taxonomy_codes(monkeypatch):
    """Searching EURES by words depends on the word you guessed rather than on the
    meaning. Measured live, out of fifty jobs in the profession there were:
    "dressmaker" 28, "lingerie" 3, "Lingerie Pattern Maker" 1, "seamstress" 0.
    «Швея» in English is seamstress, and that finds nothing at all."""
    from jobsearch.collectors import esco
    esco.forget()
    monkeypatch.setattr(esco.web, "get", lambda url, **kw: type("Resp", (), {
        "status_code": 200,
        "json": lambda s: {"_embedded": {"results": [
            {"uri": "http://data.europa.eu/esco/occupation/aaa", "title": "dressmaker"},
            {"uri": "http://data.europa.eu/esco/occupation/bbb", "title": "tailor"}]}},
    })())
    assert esco.occupations("seamstress") == [
        "http://data.europa.eu/esco/occupation/aaa",
        "http://data.europa.eu/esco/occupation/bbb"]


def test_when_the_taxonomy_is_silent_we_invent_no_role(monkeypatch):
    from jobsearch.collectors import esco
    esco.forget()
    monkeypatch.setattr(esco.web, "get", lambda url, **kw: (_ for _ in ()).throw(
        esco.requests.RequestException("no connection")))
    assert esco.occupations("seamstress") == []
    assert esco.occupations("") == []


def test_eures_is_asked_with_both_codes_and_words(monkeypatch):
    """Codes give density, words give breadth, and neither replaces the other: roles
    written out of a CV do not always map onto the taxonomy accurately."""
    from jobsearch import config
    from jobsearch.collectors import aggregators, esco
    esco.forget()
    sent = []

    class Answer:
        status_code = 200

        def raise_for_status(self):
            pass

        def json(self):
            return {"jvs": []}

    monkeypatch.setattr(esco, "occupations", lambda role, **kw: ["esco:" + role])
    monkeypatch.setattr(aggregators.web, "post",
                        lambda url, **kw: sent.append(kw["json"]) or Answer())
    cfg = dict(config.DEFAULTS)
    cfg["profile"] = {**cfg["profile"], "roles": "Швея, Портная", "skills": ""}
    cfg["search"] = {**cfg["search"], "locations": "Германия"}

    aggregators.eures(cfg, lambda *a: None)

    with_codes = [q for q in sent if q["occupationUris"]]
    with_words = [q for q in sent if q["keywords"]]
    assert with_codes, "we did not ask with codes"
    assert with_words, "we did not ask with words"
    assert with_codes[0]["keywords"] == [], "codes and words got mixed into one query"


# --- One and the same position in different cities ----------------------------

def test_a_restaurant_chain_collapses_into_one_row():
    """A chain posts one job at every location, changing the city in the title. For
    Viktor Belonogov ten rows in a row belonged to a single chain, "Pollo Regio",
    and he read them as ten different jobs."""
    from jobsearch import filters
    jobs = [
        {"title": "Restaurant Assistant Manager (Austin) TX", "company": "Pollo Regio",
         "location": "Austin, United States", "score": 95},
        {"title": "Restaurant Assistant Manager Fort Worth", "company": "Pollo Regio",
         "location": "Fort Worth, United States", "score": 95},
        {"title": "Restaurant Assistant Manager - Kyle TX", "company": "Pollo Regio",
         "location": "Kyle, United States", "score": 95},
        {"title": "Restaurant Manager", "company": "Pollo Regio",
         "location": "Dallas, United States", "score": 90},
    ]
    came_out = filters.group_same_role(jobs)

    assert len(came_out) == 2, [j["title"] for j in came_out]
    assistant = came_out[0]
    assert assistant["title"].startswith("Restaurant Assistant Manager")
    assert len(assistant["siblings"]) == 2, "the other cities were lost"
    assert came_out[1]["title"] == "Restaurant Manager", "a different position must not stick to it"


def test_different_employers_do_not_stick_together():
    from jobsearch import filters
    jobs = [
        {"title": "Restaurant Manager", "company": "Carbon", "location": "Göteborg"},
        {"title": "Restaurant Manager", "company": "The June", "location": "Jacksonville"},
    ]
    assert len(filters.group_same_role(jobs)) == 2


def test_the_order_does_not_break():
    """The first in a group stays the one that was higher in the list: it is already
    sorted."""
    from jobsearch import filters
    jobs = [
        {"title": "Cook", "company": "A", "location": "X", "score": 90},
        {"title": "Restaurant Manager Austin", "company": "B", "location": "Austin", "score": 80},
        {"title": "Restaurant Manager Dallas", "company": "B", "location": "Dallas", "score": 70},
    ]
    came_out = filters.group_same_role(jobs)
    assert [j["title"] for j in came_out] == ["Cook", "Restaurant Manager Austin"]


# --- Regulated professions ----------------------------------------------------

@pytest.mark.parametrize("roles,expected", [
    ("Lawyer, Legal Counsel", "law"),
    ("Avvocato civilista", "law"),
    ("Registered Nurse", "medicine"),
    ("Архитектор", "architecture"),
    ("Restaurant Manager", ""),
    ("Frontend Engineer", ""),
    ("Lingerie Pattern Maker", ""),
])
def test_we_recognise_a_regulated_profession(roles, expected):
    """Elisabetta Matassi is an advocate who passed the bar in Italy. The program
    found her twenty-four lawyer jobs: Sweden 9, Greece 7, Italy 2. In Sweden she
    is not an advocate until her qualification is recognised, and that has to be
    known before applying."""
    from jobsearch import filters
    assert filters.regulated_profession(roles) == expected


def test_the_roles_outweigh_the_cv():
    """The word "lawyer" can turn up in the CV of someone who does not work as one:
    the roles are what a person calls themselves."""
    from jobsearch import filters
    assert filters.regulated_profession(
        "Frontend Engineer", "Работал с юристами над договорами, lawyer review") == ""


# --- Skills to occupations: what a person could work as -----------------------

@pytest.mark.parametrize("query,answer,take_it", [
    ("CSS", "CSS", True),                                    # word for word
    ("pattern grading", "pattern grading", True),
    ("restaurant management", "manage restaurant service", True),   # differently put, the same thing
    ("SOAP", "harden soap", False),                          # soap instead of the protocol
    ("REST", "promote balance between rest and activity", False),
    ("XML", "AJAX", False),
    ("vendor management", "use office systems", False),
    ("bra construction", "construction industry", False),
])
def test_the_taxonomys_answer_is_checked_against_the_query(query, answer, take_it):
    """Taking the first match in order will not do: instead of "I do not know" the
    taxonomy answers with whatever is nearest by letters, and from there the graph
    spreads into rubbish. The filter raised the number of people whose own
    occupation the taxonomy names from one in six to three."""
    from jobsearch.collectors import esco
    assert esco.looks_like(query, answer) is take_it


def test_occupations_from_skills(monkeypatch):
    """A security guard who has learned front-end will write "security guard" on
    their CV and will never see a junior front-end job. From three of their skills
    the taxonomy names web developer, asking nothing."""
    from jobsearch.collectors import esco
    esco.forget()
    ANSWERS = {
        "skill?": {"_links": {
            "isEssentialForOccupation": [{"uri": "esco:web-developer", "title": "web developer"}],
            "isOptionalForOccupation": [{"uri": "esco:webmaster", "title": "webmaster"}]}},
        "search": {"_embedded": {"results": [{"uri": "esco:css", "title": "CSS"}]}},
    }

    def fake(url, **kw):
        which = "skill?" if "/resource/skill" in url else "search"
        return type("Resp", (), {"status_code": 200, "json": lambda s: ANSWERS[which]})()

    monkeypatch.setattr(esco.web, "get", fake)
    came_out = esco.occupations_by_skills(["CSS"])
    assert set(came_out) == {"esco:web-developer", "esco:webmaster"}


def test_essential_and_optional_links_weigh_the_same(monkeypatch):
    """At first essential weighed triple, and it came out badly: in the taxonomy
    "JavaScript" is essential for exactly two occupations — CNC machine operator
    and CAD operator — while for web developer it is merely optional, one of some
    fifty. Two quirks outweighed fifty sensible links, and the security guard who
    had learned front-end got a CNC machine."""
    from jobsearch.collectors import esco
    esco.forget()
    LINKS = {
        "esco:js": {"isEssentialForOccupation": [{"uri": "cnc", "title": "CNC"}],
                    "isOptionalForOccupation": [{"uri": "web", "title": "web developer"}]},
        "esco:css": {"isEssentialForOccupation": [],
                     "isOptionalForOccupation": [{"uri": "web", "title": "web developer"}]},
    }
    found = {"JavaScript": "esco:js", "CSS": "esco:css"}

    def fake(url, **kw):
        if "/resource/skill" in url:
            uri = "esco:js" if "esco%3Ajs" in url or "esco:js" in url else "esco:css"
            body = {"_links": LINKS[uri]}
        else:
            word = "JavaScript" if "JavaScript" in url else "CSS"
            body = {"_embedded": {"results": [{"uri": found[word], "title": word}]}}
        return type("Resp", (), {"status_code": 200, "json": lambda s: body})()

    monkeypatch.setattr(esco.web, "get", fake)
    came_out = esco.occupations_by_skills(["JavaScript", "CSS"])

    assert came_out[0] == "web", "the occupation both skills need should come first"


def test_an_unrecognised_skill_brings_back_nothing(monkeypatch):
    from jobsearch.collectors import esco
    esco.forget()
    monkeypatch.setattr(esco.web, "get", lambda url, **kw: type("Resp", (), {
        "status_code": 200,
        "json": lambda s: {"_embedded": {"results": [{"uri": "esco:soap", "title": "harden soap"}]}},
    })())
    assert esco.skill("SOAP") == ""
    assert esco.occupations_by_skills(["SOAP"]) == []
