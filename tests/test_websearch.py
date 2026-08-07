"""Searching the web with the application's own hands.

Only Claude Code can search the web: the other model programs do not have it, and
we cannot hand it to them — it is their capability, not ours. So with everyone
else, scouting for new companies and salary ranges were switched off, and the page
carried a notice saying the model cannot search the web.

Here the move is the other way round: the application searches, and the model gets
what was found as text. Its work is the same — it never searched before either, it
read the results — but searching no longer depends on which program the person
thinks with.

And it is sounder in one more sense: the model is handed no network tools at all,
and what gets downloaded is our decision rather than an instruction hidden in
somebody else's text.
"""
import pytest
import requests

from jobsearch import config, providers, websearch

KEY = "sk-search-secret"


def with_key(cfg: dict, service: str = "brave") -> dict:
    cfg["sources"].update(web_search_provider=service, web_search_key=KEY)
    return cfg


class Answer:
    def __init__(self, payload):
        self._payload = payload

    def raise_for_status(self):
        pass

    def json(self):
        return self._payload


RESULTS = {
    "brave": {"web": {"results": [
        {"title": "Acme careers", "url": "https://acme.example/jobs", "description": "we are hiring"}]}},
    "tavily": {"results": [
        {"title": "Acme careers", "url": "https://acme.example/jobs", "content": "we are hiring"}]},
    "serper": {"organic": [
        {"title": "Acme careers", "link": "https://acme.example/jobs", "snippet": "we are hiring"}]},
}


@pytest.mark.parametrize("service", ["brave", "tavily", "serper"])
def test_the_different_services_come_out_in_one_shape(monkeypatch, service, profile):
    """From here the results go into the prompt as text — the shape must be one."""
    monkeypatch.setattr(websearch.requests, "get", lambda *a, **kw: Answer(RESULTS[service]))
    monkeypatch.setattr(websearch.requests, "post", lambda *a, **kw: Answer(RESULTS[service]))

    found = websearch.search(with_key(config.load(), service), "acme jobs")

    assert found == [{"title": "Acme careers", "url": "https://acme.example/jobs",
                        "snippet": "we are hiring"}], service


def test_the_key_stays_out_of_the_error_text(monkeypatch, profile):
    """The error goes into the run log, and people show that to others."""
    def blow_up(*a, **kw):
        raise requests.ConnectionError(f"no connection, token={KEY}")

    monkeypatch.setattr(websearch.requests, "get", blow_up)

    with pytest.raises(websearch.SearchError) as caught:
        websearch.search(with_key(config.load()), "acme")

    assert KEY not in str(caught.value) + str(caught.value.fmt)


def test_with_no_key_we_say_it_is_not_set_up(profile):
    with pytest.raises(websearch.SearchError) as caught:
        websearch.search(config.load(), "acme")
    assert caught.value.key == "search_err_not_set"


def test_foreign_schemes_in_the_results_are_cut_off(monkeypatch, profile):
    """Addresses from the results land in the company list and get fetched later."""
    monkeypatch.setattr(websearch.requests, "get", lambda *a, **kw: Answer({"web": {"results": [
        {"title": "bad", "url": "javascript:alert(1)", "description": ""},
        {"title": "also bad", "url": "file:///etc/passwd", "description": ""},
        {"title": "fine", "url": "https://acme.example/jobs", "description": ""}]}}))

    found = websearch.search(with_key(config.load()), "acme")

    assert [r["url"] for r in found] == ["https://acme.example/jobs"]


# --- How the rest of the program sees this ------------------------------------

def test_with_a_key_any_model_has_a_search(profile):
    """Exactly what this was all for: the notice goes away for more than Claude Code."""
    cfg = config.load()
    cfg["llm"]["provider"] = "ollama"
    assert providers.web_search_possible(cfg) is False, "precondition: with no key there is no search"

    assert providers.web_search_possible(with_key(cfg)) is True


def test_claude_has_a_search_even_without_a_key(profile):
    cfg = config.load()
    cfg["llm"]["provider"] = "claude_cli"
    assert providers.web_search_possible(cfg) is True


def test_the_models_capability_and_the_capability_at_all_are_different_questions(profile):
    """supports_web_search is about the model program itself; web_search_possible is
    about whether there will be a search at all. Confusing the two would tie the
    capability back to the choice of provider, which is what we were getting away
    from."""
    cfg = with_key(config.load())
    cfg["llm"]["provider"] = "ollama"
    assert providers.supports_web_search("ollama") is False
    assert providers.web_search_possible(cfg) is True


def test_the_model_is_given_no_network_tools(monkeypatch, profile):
    """The application searches, so the model needs no tools whatsoever. That is the
    gain in soundness: what gets downloaded is our decision, not a stranger's
    text."""
    from jobsearch import llm, scoring
    monkeypatch.setattr(websearch.requests, "get", lambda *a, **kw: Answer(RESULTS["brave"]))
    prompts = []
    monkeypatch.setattr(llm, "ask_json",
                        lambda prompt, **kw: prompts.append(kw.get("allowed_tools")) or {})
    cfg = with_key(config.load())
    cfg["llm"].update(provider="ollama", deep_model="gemma2:9b")
    config.save(cfg)

    scoring.deep_analyze({"title": "T", "company": "Acme", "location": "Berlin",
                          "description": "x" * 400},
                         cfg, "CV", lambda _m: None, research=True)

    assert prompts, "the model was never called"
    assert all(not tools for tools in prompts), "the model was handed network tools after all"


def test_what_was_found_reaches_the_prompt(monkeypatch, profile):
    from jobsearch import llm, scoring
    monkeypatch.setattr(websearch.requests, "get", lambda *a, **kw: Answer(RESULTS["brave"]))
    prompts = []
    monkeypatch.setattr(llm, "ask_json", lambda prompt, **kw: prompts.append(prompt) or {})
    cfg = with_key(config.load())
    cfg["llm"].update(provider="ollama", deep_model="gemma2:9b")
    config.save(cfg)

    scoring.deep_analyze({"title": "T", "company": "Acme", "location": "",
                          "description": "x" * 400},
                         cfg, "CV", lambda _m: None, research=True)

    assert any("acme.example/jobs" in p for p in prompts), \
        "the results never reached the prompt — the model has nothing to retell"


def test_with_no_results_we_do_not_ask_the_model_to_invent(monkeypatch, profile):
    """An empty search is no reason to ask: it has to answer with something, and
    that something will be invention."""
    from jobsearch import llm, scoring
    monkeypatch.setattr(websearch.requests, "get", lambda *a, **kw: Answer({"web": {"results": []}}))
    prompts = []
    monkeypatch.setattr(llm, "ask_json", lambda prompt, **kw: prompts.append(prompt) or {})
    cfg = with_key(config.load())
    cfg["llm"].update(provider="ollama", deep_model="gemma2:9b")
    config.save(cfg)

    scoring.deep_analyze({"title": "T", "company": "Acme", "location": "",
                          "description": "x" * 400},
                         cfg, "CV", lambda _m: None, research=True)

    # Russian because it matches RESEARCH_PROMPT, which is addressed to the model
    # and stays in Russian.
    assert not any("Найди в интернете" in p for p in prompts), \
        "we asked about something the application never found"
