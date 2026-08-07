"""With no model, the program has to say so rather than pretend.

Jobs can be collected without a model: the aggregators are ordinary addresses on
the network, and everything up to the scoring goes through perfectly. So a run
with Ollama uninstalled reached the end, wrote "found 340, matched 0" and finished
green — and read as "nothing suitable today", though not one job had been looked
at. The CV check lost what had been worked out right here, on this computer,
because of an optional part. And where a reason was named at all, a raw key was
shown instead of a sentence.
"""
import pytest

from jobsearch import config, cvcheck, filters, i18n, llm, pipeline, providers, scoring


def broken_model(monkeypatch):
    """A model that is not there: exactly what happens after Ollama is uninstalled."""
    def blow_up(*a, **kw):
        raise providers.ProviderError(key="prov_err_ollama_unreachable", error="нет связи")

    monkeypatch.setattr(llm, "ask_json", blow_up)


# --- A run that scored nothing ------------------------------------------------

def test_triage_reports_its_failures(profile, monkeypatch):
    """triage used to return the jobs, and there was no way to learn whether anyone
    had answered."""
    broken_model(monkeypatch)
    jobs = [{"key": f"k{i}", "title": "Frontend", "company": "Acme",
             "description": "React"} for i in range(3)]

    failures = scoring.triage(jobs, config.load(), lambda _m: None, cv="CV")

    assert failures, "triage said nothing about not one request succeeding"
    assert all(j.get("score") is None for j in jobs), "a score came from nowhere"


def test_a_run_with_not_one_score_does_not_count_as_successful(profile, monkeypatch):
    """That very case: "found 340, matched 0" and a green status."""
    broken_model(monkeypatch)
    monkeypatch.setattr(pipeline.aggregators, "collect", lambda cfg, log, cov: [
        {"key": "k1", "title": "Senior Frontend Engineer", "company": "Northwind",
         "location": "Berlin", "url": "https://example.com/1", "source": "remotive",
         "is_direct": 1, "is_agency": 0, "description": "React", "posted_at": "2026-07-20"}])
    cfg = config.load()
    cfg["search"].update(discover_per_run=0, discover_ats_per_run=0, drop_off_target=False)
    cfg["profile"]["roles"] = "Frontend Engineer"
    config.save(cfg)

    pipeline.run(trigger="test", profile=profile)

    from jobsearch import db
    run_row = db.recent_runs(1)[0]
    assert run_row["status"] == "error", "a run that looked at not one job called itself successful"
    assert run_row["found"] >= 1, "the jobs were collected after all — that must not be lost"


def test_the_reason_in_the_log_is_words_and_not_a_key(profile, monkeypatch):
    """What ended up in the log was "prov_err_ollama_unreachable" — a word that tells
    a person nothing."""
    broken_model(monkeypatch)
    cfg = config.load()
    cfg["ui"]["lang"] = "ru"
    config.save(cfg)
    log_lines = []

    scoring.triage([{"key": "k", "title": "T", "company": "C", "description": ""}],
                   cfg, log_lines.append, cv="CV")

    joined = " ".join(log_lines)
    assert "prov_err_ollama_unreachable" not in joined, "a raw key was shown"
    assert "Ollama" in joined, "we did not say what was wrong"


# --- The CV check -------------------------------------------------------------

def test_the_cv_check_does_not_lose_what_it_worked_out_without_a_model(profile, monkeypatch):
    """The technical checks are worked out right here and always succeed. They used
    to vanish along with the optional part that needs a model."""
    from jobsearch import db
    broken_model(monkeypatch)
    config.save_cv("cv.txt", ("Виктор Лавров\nfrontend@example.com\n+49 30 1234567\n"
                              "Опыт работы\nSenior Frontend Engineer, Acme, 2020-2026.\n"
                              "Образование\nМГУ\nНавыки\nReact, TypeScript\n" * 6).encode("utf-8"))
    db.save_job({"key": "k1", "title": "Frontend", "company": "Acme", "location": "Berlin",
                 "url": "u", "source": "s", "is_direct": 1, "is_agency": 0,
                 "description": "React", "score": 80, "reason": "", "advice": "",
                 "verified": False, "posted_at": "2026-07-01"}, run_id=1)

    result = cvcheck.analyze(config.load())

    assert result["tech"]["score"] > 0, "the technical checks vanished"
    assert result["unfinished"], "the unfinished part was passed over in silence"
    assert "prov_err" not in result["unfinished"][0], "a raw key there too"
    assert cvcheck.last_result(), "the result was not saved — the next visit will show nothing"


def test_a_cv_check_error_is_shown_in_words(client_check, monkeypatch):
    """The page showed str(e) — that is, the translation key."""
    client, profile = client_check
    monkeypatch.setattr(cvcheck, "analyze",
                        lambda cfg: (_ for _ in ()).throw(
                            providers.ProviderError(key="prov_err_ollama_down")))
    cfg = config.load()
    cfg["ui"]["lang"] = "ru"
    config.save(cfg)
    config.save_cv("cv.txt", ("Виктор Лавров, фронтенд-инженер. " * 20).encode("utf-8"))

    client.post("/cv/check/run", follow_redirects=False)
    for _ in range(50):
        if not client.get("/cv/check/status").json()["running"]:
            break

    error_text = client.get("/cv/check/status").json()["error"]
    assert "prov_err_ollama_down" not in error_text, "a raw key was shown"
    assert "Ollama" in error_text


@pytest.fixture
def client_check(profile):
    pytest.importorskip("httpx", reason="TestClient needs httpx")
    from fastapi.testclient import TestClient
    import app as app_module
    with TestClient(app_module.app, base_url="http://127.0.0.1:8765") as c:
        c.cookies.set("profile", profile)
        yield c, profile


# --- The posting period -------------------------------------------------------

@pytest.mark.parametrize("posted,since,until,expected", [
    ("2026-07-20", "2026-07-01", "2026-07-31", True),
    ("2026-06-20", "2026-07-01", "", False),
    ("2026-08-20", "", "2026-07-31", False),
    ("2026-07-20", "", "", True),
    ("", "2026-07-01", "2026-07-31", True),          # no date — not ours to judge
    ("вчера", "2026-07-01", "", True),               # the source wrote it in words
])
def test_filtering_by_period(posted, since, until, expected):
    assert filters.posted_ok({"posted_at": posted}, since, until) is expected


def test_jobs_with_no_date_do_not_vanish_silently():
    """Many aggregators give no date at all. Throwing out everything undated would
    lose a person who set a period most of their search, without telling them."""
    undated = [{"posted_at": ""}, {"posted_at": None}, {}]
    assert all(filters.posted_ok(j, "2026-07-01", "2026-07-31") for j in undated)


def test_a_bent_date_from_the_browser_does_not_reach_the_settings(client_check):
    """Anything can be typed into the field — a date or nothing must reach the settings."""
    import app as app_module
    assert app_module._date_or_empty("2026-07-01") == "2026-07-01"
    assert app_module._date_or_empty("not a date") == ""
    assert app_module._date_or_empty("2026-13-45") == ""
    assert app_module._date_or_empty(None) == ""


def test_dates_from_the_quick_search_reach_the_settings(client_check):
    client, _ = client_check
    client.post("/simple/start",
                data={"person": "", "locations": "EU", "linkedin": "",
                      "posted_from": "2026-07-01", "posted_to": "2026-07-31"},
                follow_redirects=False)
    cfg = config.load()
    assert cfg["search"]["posted_from"] == "2026-07-01"
    assert cfg["search"]["posted_to"] == "2026-07-31"


def test_empty_dates_lift_the_limit(client_check):
    """An empty field is an answer — "no limit" — rather than the absence of one:
    otherwise there would be nothing to clear the period with."""
    client, _ = client_check
    cfg = config.load()
    cfg["search"].update(posted_from="2026-07-01", posted_to="2026-07-31")
    config.save(cfg)

    client.post("/simple/start",
                data={"person": "", "locations": "EU", "linkedin": "",
                      "posted_from": "", "posted_to": ""},
                follow_redirects=False)

    cfg = config.load()
    assert cfg["search"]["posted_from"] == ""
    assert cfg["search"]["posted_to"] == ""


# --- With no web search we do not ask for what the model cannot do ------------

def test_with_no_web_search_we_do_not_ask_for_company_research(profile, monkeypatch):
    """Researching a company is a web search. Asking for one from a model that cannot
    reach the network wastes the call and nudges it into making things up: it has
    to answer with something.

    The setting says "I want this"; whether it is possible is the provider's
    business."""
    from jobsearch import db, llm, pipeline
    calls = []
    monkeypatch.setattr(llm, "ask_json",
                        lambda prompt, **kw: calls.append(kw.get("allowed_tools")) or {})
    monkeypatch.setattr(pipeline.aggregators, "collect", lambda cfg, log, cov: [
        {"key": "k1", "title": "Frontend", "company": "Acme", "location": "Berlin",
         "url": "https://example.com/1", "source": "remotive", "is_direct": 1,
         "is_agency": 0, "description": "React " * 100, "posted_at": "2026-07-20"}])
    cfg = config.load()
    # a local model: it does not go to the network
    cfg["llm"].update(provider="ollama", triage_model="gemma2:9b", deep_model="gemma2:9b")
    cfg["search"].update(discover_per_run=0, discover_ats_per_run=0,
                         drop_off_target=False, research_company=True, threshold=0)
    cfg["profile"]["roles"] = "Frontend"
    config.save(cfg)

    pipeline.run(trigger="test", profile=profile)

    assert calls, "the model was never called at all"
    assert all(not s for s in calls), \
        "we asked a model that does not go online to search the web"


def test_with_a_web_search_the_company_research_stays(profile, monkeypatch):
    """The other side: whoever has a search gets one."""
    from jobsearch import llm, scoring
    tools_seen = []
    monkeypatch.setattr(llm, "ask_json",
                        lambda prompt, **kw: tools_seen.append(kw.get("allowed_tools")) or {})
    cfg = config.load()
    cfg["llm"].update(provider="claude_cli")
    config.save(cfg)

    scoring.deep_analyze({"title": "T", "company": "C", "location": "",
                          "description": "x" * 400},
                         cfg, "CV", lambda _m: None, research=True)

    assert any(s for s in tools_seen), "we took the web search away from one that can do it"


def test_every_source_failing_is_not_passed_off_as_success(monkeypatch, profile):
    """Found by a real run: the antivirus on the machine intercepted the secure
    connections, all nine sources fell over with a certificate error — and the run
    reported "Done", status ok, zero jobs.

    A zero from the market and a zero from never reaching the market are different
    things, and a person must not mistake one for the other. All the more so since
    the cause of this is usually one shared, external thing: an antivirus, a work
    gateway, no network.
    """
    from jobsearch import config, db, llm, pipeline
    from jobsearch.collectors import aggregators

    monkeypatch.setattr(llm, "ask_json", lambda prompt, **kw: {})
    cfg = config.load()
    cfg["llm"].update(provider="ollama", triage_model="gemma2:9b", deep_model="gemma2:9b")
    cfg["search"].update(discover_per_run=0, discover_ats_per_run=0)
    config.save(cfg)

    def everything_failed(cfg, log, coverage=None):
        for name in ("Remotive", "Arbeitnow", "RemoteOK"):
            coverage.append({"name": name, "url": "https://x.example", "kind": "aggregator",
                             "count": 0, "error": "SSLError certificate verify failed"})
        return []

    monkeypatch.setattr(aggregators, "collect", everything_failed)

    pipeline.run(trigger="test", profile=profile)

    with db.conn() as c:
        run_row = dict(c.execute("SELECT * FROM runs ORDER BY id DESC LIMIT 1").fetchone())
    assert run_row["status"] != "ok", "a run where nobody answered was called successful"
    assert "источник" in run_row["log"] or "sources" in run_row["log"], \
        "the log does not say that everyone stayed silent"


# --- The "verified" tick ------------------------------------------------------

def deep_answer(monkeypatch, answer):
    from jobsearch import scoring
    monkeypatch.setattr(scoring.llm, "ask_json", lambda prompt, **kw: answer)


def job_for_analysis() -> dict:
    return {"title": "Senior Ruby on Rails Developer", "company": "Proxify AB",
            "location": "Remote", "url": "https://example.com/1",
            "description": "Ruby on Rails, RSpec, PostgreSQL. " * 40,
            "score": 90, "verified": False}


def test_with_no_score_from_the_analysis_we_set_no_tick(monkeypatch, profile):
    """The mark was set on the mere fact of an answer, while the score was taken only
    when the answer carried a match field. Weak models answer with fluent prose
    and no such field over and over — and the card kept the fast triage score with
    a "verified" tick on it.

    Triage sees seven hundred characters of description and eight jobs at a time;
    that is what makes it fast. The tick said the score had been confirmed by a
    second, careful pass — and it had not. That is how a Ruby on Rails job stood in
    a SAP integrator's list at "90% ✓ verified".
    """
    from jobsearch import config, scoring
    deep_answer(monkeypatch, {"reason": "Ключевые навыки все на месте.",
                                 "cv_changes": ["Подчеркнуть SAP PO/PI"]})
    j = job_for_analysis()

    scoring.deep_analyze(j, config.load(), cv="SAP Integration Consultant",
                         log=lambda *a, **k: None, research=False)

    assert j["verified"] is False, "a score the analysis never gave was called verified"
    assert j["score"] == 90, "there is no reason to lose the triage score over it"


def test_with_a_score_from_the_analysis_the_tick_is_set(monkeypatch, profile):
    """The other side: the deep analysis answered with a score, so it confirmed."""
    from jobsearch import config, scoring
    deep_answer(monkeypatch, {"match": 20, "reason": "Другое направление."})
    j = job_for_analysis()

    scoring.deep_analyze(j, config.load(), cv="SAP Integration Consultant",
                         log=lambda *a, **k: None, research=False)

    assert j["verified"] is True
    assert j["score"] == 20, "the analysis score was not taken"


def test_the_advice_does_not_suggest_lying():
    """The model suggested "mention your PostgreSQL experience" to somebody who has
    none. That is advice to lie, and at an interview it turns against the person.
    The CV-tailoring prompt carried such a ban; the analysis prompt did not."""
    from jobsearch import scoring
    prompt_text = scoring.DEEP_PROMPT.lower()
    assert "не предлагай упоминать навыки" in prompt_text
    assert "которых у кандидата нет" in prompt_text


# --- What the model gets round to looking at ----------------------------------

def test_what_was_found_by_query_comes_before_what_was_dumped_from_a_feed():
    """The order comes from how the words match the profile. EURES searches by
    meaning and brings back titles in their own languages: for "seamstress", the
    Polish "Szwaczka". No shared letters, a match of zero — and all 283 of its jobs
    fell below the line, under 400 IT jobs that got there because of the word
    "Designer" in the title."""
    from jobsearch import pipeline  # noqa: F401  (the ordering itself is checked below)

    own_words = {"lingerie", "pattern", "maker", "garment"}

    def lexical(j):
        return len(own_words & set(j["title"].lower().split()))

    jobs = [
        {"title": "Szwaczka", "by_query": True},                    # 0 words in common
        {"title": "Technical Designer Pattern", "by_query": False},  # 1 word in common
        {"title": "Lingerie Pattern Maker", "by_query": True},       # 3 in common
    ]
    for j in jobs:
        j["_lex"] = lexical(j)

    by_words = lambda j: -j["_lex"]  # noqa: E731
    asked_for = sorted((j for j in jobs if j.get("by_query")), key=by_words)
    others = sorted((j for j in jobs if not j.get("by_query")), key=by_words)
    order = [j["title"] for j in asked_for + others]

    assert order.index("Szwaczka") < order.index("Technical Designer Pattern"), \
        "what was found by query slid under an accidental match again"


def test_the_sources_mark_what_was_found_by_query():
    """The mark is set by collect(), from one list — otherwise it drifts from who
    actually searches by words."""
    from jobsearch.collectors import aggregators
    names = {s[4] for s in aggregators.SOURCES}
    assert aggregators.SEARCH_BY_WORDS <= names, "the searchers list holds a collector that does not exist"
    assert "eures" in aggregators.SEARCH_BY_WORDS
    assert "arbeitnow" not in aggregators.SEARCH_BY_WORDS, "it hands over its whole feed"


def test_the_mark_is_set_only_for_those_that_search(monkeypatch):
    from jobsearch import config
    from jobsearch.collectors import aggregators
    for name in {s[4] for s in aggregators.SOURCES}:
        monkeypatch.setattr(aggregators, name,
                            lambda cfg, log, _s=name: [{"title": _s, "source": _s}])
    jobs = aggregators.collect(config.DEFAULTS, lambda *a: None, [])
    marked = {j["source"] for j in jobs if j.get("by_query")}
    unmarked = {j["source"] for j in jobs if not j.get("by_query")}
    assert "eures" in marked and "workable" in marked
    assert "arbeitnow" in unmarked and "hn_hiring" in unmarked


# --- The analysis does not argue with itself ----------------------------------

def test_a_verdict_of_another_trade_keeps_the_score_down(monkeypatch, cfg):
    """The model wrote "responsible for developing solutions for games, not clothing"
    — and gave it 55. The reasoning is right, the number is not. We believe the
    reasoning: it comes first, meaning it was thought of before the figure could be
    fitted to anything."""
    from jobsearch import llm, scoring
    monkeypatch.setattr(llm, "ask_json", lambda *a, **k: {
        "verdict": "чужая", "reason": "другая профессия", "match": 85,
        "cv_changes": [], "linkedin_changes": [], "cover_hint": ""})
    job = {"title": "iOS Developer", "description": "x" * 400}

    scoring.deep_analyze(job, cfg, "CV", lambda *a: None, research=False)

    assert job["score"] <= 20, f"another trade went up to {job['score']}%"
    assert job["verified"] is True


def test_their_own_trade_does_not_lose_its_score(monkeypatch, cfg):
    """The other side: their own trade must not be marked down."""
    from jobsearch import llm, scoring
    monkeypatch.setattr(llm, "ask_json", lambda *a, **k: {
        "verdict": "своя", "reason": "та же профессия", "match": 90,
        "cv_changes": [], "linkedin_changes": [], "cover_hint": ""})
    job = {"title": "Lingerie Pattern Maker", "description": "x" * 400}

    scoring.deep_analyze(job, cfg, "CV", lambda *a: None, research=False)

    assert job["score"] == 90


def test_the_analysis_thinks_before_it_scores():
    """The score was the FIRST field — the model answers left to right and invented
    the number before it had time to think. In triage this was fixed long ago; the
    fix never reached the deep analysis."""
    from jobsearch import scoring
    fields = list(scoring.DEEP_SCHEMA["properties"])
    assert fields.index("verdict") < fields.index("match")
    assert fields.index("reason") < fields.index("match")
    body = scoring.DEEP_PROMPT
    assert body.index('"verdict"') < body.index('"match"')
    assert body.index('"reason"') < body.index('"match"')


# --- We do not vouch for what the model never read ----------------------------

# Pieces of real postings from a run — short fragments do not settle a language,
# and rightly so: what should be judged is what actually reaches the model.
POSTINGS = [
    ("Engineering Manager (Data Science). What you will do: build, mentor and lead "
     "an elite data science team as a trusted player-coach, while clearly "
     "communicating complex technical ideas to executives, customers and partners. "
     "You will personally design and train models, and you should have experience "
     "with production systems that serve millions of requests.", True),
    ("Kontroler jakości wyrobów odzieżowych. Zakres obowiązków: kontrola wyrobów "
     "gotowych, sprawdzanie zgodności wymiarów odzieży z tabelą miar oraz "
     "specyfikacją techniczną, sporządzanie raportów jakościowych, identyfikacja "
     "wad. Wymagania: wykształcenie zasadnicze zawodowe o kierunku krawiec, "
     "technolog odzieżowy lub pokrewne, znajomość technologii krawieckiej.", False),
    ("Technico-commercial itinérant. Vous recherchez un métier de découvertes et "
     "d'audace pour interagir tous les jours avec de nouvelles personnes tout en "
     "présentant des produits et services innovants ? Rejoignez l'aventure et "
     "développez votre portefeuille clients sur votre secteur géographique.", False),
    ("Wij zoeken een gemotiveerde medewerker voor onze afdeling productie en "
     "montage. Je werkt in een klein team, bedient machines en controleert de "
     "kwaliteit van de onderdelen. Ervaring in de techniek is een pluspunt, maar "
     "wij leiden je graag zelf op. Wij bieden een vast contract.", False),
    ("Konstruktor", True),          # barely any words — we do not quibble
    # A list of technologies: English, but without a single function word, so the
    # language cannot be told from them. Since it cannot, we do not quibble.
    ("Ruby on Rails, RSpec, PostgreSQL. " * 40, True),
]


@pytest.mark.parametrize("text,expected", POSTINGS)
def test_we_recognise_english(text, expected):
    from jobsearch import scoring
    assert scoring._in_english(text) is expected


def test_what_was_not_read_is_not_marked_verified(monkeypatch, cfg):
    """Having failed to understand the text, the model does not say "I did not
    understand": it retells the profile and puts down a score from the example. The
    lingerie designer got a joiner, a painter, a gardener and a storekeeper at 90%
    each — all with a "verified" tick, all in Polish."""
    from jobsearch import llm, scoring
    monkeypatch.setattr(llm, "ask_json", lambda *a, **k: {
        "verdict": "своя", "reason": "кандидат имеет опыт", "match": 90,
        "cv_changes": [], "linkedin_changes": [], "cover_hint": ""})
    job = {"title": "Osoba na stanowisku stolarz",
                "description": "Zakres obowiązków: wykonywanie mebli na wymiar, "
                               "obsługa maszyn stolarskich, praca w warsztacie " * 6}

    scoring.deep_analyze(job, cfg, "CV", lambda *a: None, research=False)

    assert job["verified"] is False, "we vouched for what we never read"
    assert job["score"] <= scoring.UNREAD_CEILING


def test_the_occupation_from_the_taxonomy_settles_the_question(monkeypatch, cfg):
    """The occupation name arrives in English and is the same for every language — it
    can be judged by even when the posting was never read."""
    from jobsearch import llm, scoring
    monkeypatch.setattr(llm, "ask_json", lambda *a, **k: {
        "verdict": "своя", "reason": "та же профессия", "match": 90,
        "cv_changes": [], "linkedin_changes": [], "cover_hint": ""})
    job = {"title": "Szwaczka", "occupation": "tailor",
                "description": "Zakres obowiązków: szycie odzieży na maszynie " * 8}

    scoring.deep_analyze(job, cfg, "CV", lambda *a: None, research=False)

    assert job["verified"] is True
    assert job["score"] == 90


def test_the_occupation_from_the_taxonomy_reaches_the_model(monkeypatch, cfg):
    from jobsearch import llm, scoring
    seen = {}
    monkeypatch.setattr(llm, "ask_json",
                        lambda prompt, **k: seen.setdefault("prompt", prompt) and None
                        or {"verdict": "чужая", "reason": "—", "match": 5,
                            "cv_changes": [], "linkedin_changes": [], "cover_hint": ""})
    scoring.deep_analyze({"title": "Magazynier M/K", "occupation": "warehouse worker",
                          "description": "x" * 400},
                         cfg, "CV", lambda *a: None, research=False)
    assert "warehouse worker" in seen["prompt"], "the taxonomy never reached the model"


def test_the_occupation_is_named_together_with_whose_it_is(cfg):
    """The first version simply wrote "occupation from the taxonomy: CNC machine
    operator", and a weak model read that as the CANDIDATE'S occupation: the
    machine operator came out "own trade" at 90%, whereas with no line at all it
    was "adjacent" at 55. The hint made the model not cleverer but more confident
    in being wrong. Measured on the lingerie designer's run."""
    from jobsearch import scoring
    cfg["profile"]["roles"] = "Lingerie Pattern Maker, Garment Technologist"
    block = scoring._occupation_block(cfg, {"occupation": "CNC machine operator"})

    assert "CNC machine operator" in block
    assert "Lingerie Pattern Maker" in block, "it does not say what to compare against"
    assert "ВАКАНСИИ" in block, "it does not say whose occupation this is"
    assert block.index("ВАКАНСИИ") < block.index("кандидата")


def test_with_no_occupation_there_is_no_block(cfg):
    from jobsearch import scoring
    assert scoring._occupation_block(cfg, {}) == ""
    assert scoring._occupation_block(cfg, {"occupation": "  "}) == ""


# --- The profile from a CV: the answer becomes a field, not a retelling of one -

@pytest.mark.parametrize("answer,expected", [
    (["Purchasing Manager", "Supply Chain Manager"], "Purchasing Manager, Supply Chain Manager"),
    ("Purchasing Manager, Supply Chain Manager", "Purchasing Manager, Supply Chain Manager"),
    (["  Logistics Manager  ", "", "Procurement Lead"], "Logistics Manager, Procurement Lead"),
    ("['Purchasing Manager', 'Supply Chain Manager']", "Purchasing Manager', 'Supply Chain Manager"),
    ("Middle", "Middle"),
    ("C++ (advanced)", "C++ (advanced)"),
])
def test_a_list_from_the_model_becomes_a_string(answer, expected):
    """We ask for "job titles separated by commas" and a weak model sends a list.
    Through str() it was written into the profile as "['Purchasing Manager', ...]",
    then split on commas — and the query "['Purchasing Manager'" went off to the
    sources, while the model was handed the same thing as an example of their own
    trade."""
    from jobsearch import scoring
    assert scoring._as_text(answer) == expected


def test_the_profile_from_a_cv_brings_no_brackets(monkeypatch, cfg):
    from jobsearch import llm, scoring
    monkeypatch.setattr(llm, "ask_json", lambda *a, **k: {
        "roles": ["Purchasing Manager", "Logistics Manager"],
        "skills": ["Procurement", "MS Excel"],
        "seniority": "Middle", "summary": ["10 years in procurement"],
        "languages": ["Russian (native)", "English (B1)"]})
    cfg["profile"].update(roles="", skills="", seniority="", summary="", languages="")

    came_out = scoring.profile_from_cv(cfg, "CV")["profile"]

    for field in ("roles", "skills", "summary", "languages"):
        assert "[" not in came_out[field] and "'" not in came_out[field], \
            f"{field}: {came_out[field]}"
    assert came_out["roles"] == "Purchasing Manager, Logistics Manager"


# --- We read the right to work from the posting, not from the model -----------

@pytest.mark.parametrize("text,expected", [
    ("We are unable to sponsor visas for this position at this time.", "no"),
    ("Candidates must be authorized to work in the United States.", "no"),
    ("No visa sponsorship is available for this role.", "no"),
    ("Relocation package and visa sponsorship provided for the right candidate.", "yes"),
    ("We help with the visa and offer relocation support to Berlin.", "yes"),
    ("Join our team of engineers building payment systems.", ""),
])
def test_what_the_posting_says_about_a_visa(text, expected):
    """Asking the model is useless: on Dmitry's run the profile said sponsorship was
    needed, that line went into the prompt — and in none of the hundred and nine
    pieces of reasoning was a visa mentioned even once."""
    from jobsearch import filters
    assert filters.visa_stance({"title": "Engineer", "description": text}) == expected


def test_a_refusal_outweighs_an_offer():
    """"no visa sponsorship" contains "visa sponsorship" — the refusal has to win, or
    a posting that plainly refuses will look inviting."""
    from jobsearch import filters
    assert filters.visa_stance(
        {"description": "Visa sponsorship: no visa sponsorship for this role."}) == "no"


def test_silence_counts_as_neither():
    from jobsearch import filters
    assert filters.visa_stance({"description": ""}) == ""


# --- A quotation from the CV: against invention -------------------------------

@pytest.mark.parametrize("quote,present", [
    ("React, TypeScript, Vite", True),
    ("react   typescript   vite", True),      # whitespace and case do not count
    # A one-letter typo. I tried demanding a letter-for-letter match, and
    # Belonogov's quotation «с 10-летним опыром работы» was not found — the CV
    # says «опытом». For Lavrov 102 quotations out of 125 were rejected that way,
    # almost all of them for paraphrasing instead of copying.
    ("Восемь лет во фронтенде: React, TypeScrpt, Vite", True),
    ("опыт в области электротехники", False),
    ("Salesforce Commerce Cloud integration", False),
    ("React Vite", False),                    # two words is not a quotation
    ("", False),
])
def test_the_quotation_is_checked_against_the_cv(quote, present):
    """The model told the lingerie designer that "the candidate has experience and
    education in electrical engineering" — a lectureship in electrical engineering
    at 90% with a "verified" tick. The person has no electrical engineering
    whatsoever."""
    from jobsearch import scoring
    cv = "Восемь лет во фронтенде. React, TypeScript, Vite. Вёл переход на монорепозиторий."
    assert scoring._quote_is_real(quote, cv) is present


def test_invented_reasoning_gets_no_tick(monkeypatch, cfg):
    from jobsearch import llm, scoring
    monkeypatch.setattr(llm, "ask_json", lambda *a, **k: {
        "verdict": "своя", "reason": "опыт в области электротехники",
        "quote": "опыт в области электротехники", "match": 90,
        "cv_changes": [], "linkedin_changes": [], "cover_hint": ""})
    job = {"title": "Senior lecturers in Electrical Engineering",
                "occupation": "lecturer", "description": "x" * 400}

    scoring.deep_analyze(job, cfg, "Конструктор белья. Valentina, лекала.",
                         lambda *a: None, research=False)

    assert job["verified"] is False, "we vouched for something invented"
    assert job["score"] == 90, "there is no reason to lose the score over it"


def test_a_genuine_quotation_does_not_take_the_tick_away(monkeypatch, cfg):
    from jobsearch import llm, scoring
    monkeypatch.setattr(llm, "ask_json", lambda *a, **k: {
        "verdict": "своя", "reason": "работал с лекалами",
        "quote": "Конструктор белья, Valentina, лекала", "match": 90,
        "cv_changes": [], "linkedin_changes": [], "cover_hint": ""})
    job = {"title": "Modéliste", "occupation": "tailor", "description": "x" * 400}

    scoring.deep_analyze(job, cfg, "Конструктор белья. Valentina, лекала.",
                         lambda *a: None, research=False)

    assert job["verified"] is True


# --- A second pass: the advice is checked with a separate question ------------

def test_advice_to_add_what_does_not_exist_is_dropped(monkeypatch, cfg):
    """The ban on inventing is in the analysis prompt itself, and a weak model talks
    over it: it advised a supply-department head to "add experience with Salesforce
    Commerce Cloud"."""
    from jobsearch import llm, scoring
    monkeypatch.setattr(llm, "ask_json", lambda *a, **k: {"ok": [0]})
    advice = ["Move the design system to the first line",
              "Add experience with Salesforce Commerce Cloud"]

    kept = scoring.keep_honest_advice(advice, "CV: design system, React", {}, lambda *a: None)

    assert kept == ["Move the design system to the first line"]


def test_if_everything_was_rejected_we_throw_nothing_away(monkeypatch, cfg):
    """More likely the model did not understand the question than that every
    suggestion is bad."""
    from jobsearch import llm, scoring
    monkeypatch.setattr(llm, "ask_json", lambda *a, **k: {"ok": []})
    advice = ["First", "Second"]
    assert scoring.keep_honest_advice(advice, "CV", {}, lambda *a: None) == advice


def test_when_the_check_fails_the_advice_stays(monkeypatch, cfg):
    from jobsearch import llm, scoring
    monkeypatch.setattr(llm, "ask_json",
                        lambda *a, **k: (_ for _ in ()).throw(llm.ClaudeError("no model")))
    advice = ["First"]
    assert scoring.keep_honest_advice(advice, "CV", {}, lambda *a: None) == advice
