"""Secrets must not settle in files a person shows to somebody else.

A Telegram bot token has to sit right in the URL — that is how their API works.
And requests puts the URL into the text of its own exception, and that exception
ended up in the run log: it is kept in the database, shown on the results page,
and is the first thing attached to a bug report. The token is full power over the
bot and all of its correspondence.

On top of that requests.RequestException is an OSError rather than a
RuntimeError, so it flew past every one of our `except RuntimeError` clauses
straight into the catch-all handler — and that one writes the whole traceback.
"""
import pytest
import requests

from jobsearch import notify

TOKEN = "123456789:AAHfake_TOKEN_value_do_not_use"


def break_the_connection(monkeypatch):
    def blow_up(*a, **kw):
        raise requests.ConnectionError(
            f"HTTPSConnectionPool(host='api.telegram.org', port=443): "
            f"Max retries exceeded with url: /bot{TOKEN}/sendMessage")

    monkeypatch.setattr(notify.requests, "post", blow_up)
    monkeypatch.setattr(notify.requests, "get", blow_up)


def test_the_token_stays_out_of_the_error_text(monkeypatch):
    break_the_connection(monkeypatch)
    cfg = {"telegram": {"bot_token": TOKEN, "chat_id": "42"}}

    with pytest.raises(notify.NotifyError) as caught:
        notify.send_message(cfg, "hello")

    text = str(caught.value) + str(caught.value.fmt)
    assert TOKEN not in text, "the token is visible in an error headed for the log"
    assert "***" in text, "the token was not masked, it just vanished along with the reason"


def test_the_token_does_not_leak_while_finding_the_chat_id(monkeypatch):
    break_the_connection(monkeypatch)

    with pytest.raises(notify.NotifyError) as caught:
        notify.detect_chat_id(TOKEN)

    assert TOKEN not in str(caught.value) + str(caught.value.fmt)


def test_a_network_error_is_caught_by_our_own_handlers(monkeypatch):
    """They are written for RuntimeError, while requests raises a descendant of
    OSError — so everything flew past, into the catch-all with a full traceback."""
    assert not issubclass(requests.RequestException, RuntimeError), \
        "the premise has changed — this check should be rewritten"
    assert issubclass(notify.NotifyError, RuntimeError)

    break_the_connection(monkeypatch)
    cfg = {"telegram": {"bot_token": TOKEN, "chat_id": "42"}}

    try:
        notify.send_message(cfg, "hello")
    except RuntimeError:
        pass                      # what the callers actually expect
    except Exception as e:        # noqa: BLE001
        pytest.fail(f"{type(e).__name__} flew past the handlers")


def test_the_run_log_stays_free_of_the_token(profile, monkeypatch):
    """End to end: all the way to the log itself, which sits in the database."""
    from jobsearch import config, db, pipeline
    break_the_connection(monkeypatch)
    monkeypatch.setattr(pipeline.aggregators, "collect", lambda cfg, log, cov: [])
    cfg = config.load()
    cfg["telegram"].update(bot_token=TOKEN, chat_id="42")
    cfg["search"].update(discover_per_run=0, discover_ats_per_run=0)
    config.save(cfg)

    pipeline.run(trigger="test", profile=profile)

    log_text = db.recent_runs(1)[0]["log"] or ""
    assert TOKEN not in log_text, "the token was written into the run log"


# --- The CV and the network tools never meet in one prompt --------------------

def test_nothing_personal_reaches_the_prompt_that_has_the_network(profile, monkeypatch):
    """A job description is written by an outsider, and up to six thousand of its
    characters used to land in the same prompt as the CV, the salary expectations
    and the visa — with WebSearch and WebFetch allowed and nobody there to
    object."""
    from jobsearch import config, llm, scoring

    prompts = []

    def remember(prompt, **kw):
        prompts.append({"prompt": prompt, "tools": kw.get("allowed_tools")})
        return {}

    monkeypatch.setattr(llm, "ask_json", remember)
    cfg = config.load()
    cfg["profile"].update(summary="Виктор Лавров", salary="120000 EUR",
                          visa_note="needs a Blue Card", roles="Frontend")
    config.save(cfg)
    cv = "SECRET-CV Viktor Lavrov frontend@example.com +49 30 1234567"

    scoring.deep_analyze({"title": "Frontend", "company": "Acme", "location": "Berlin",
                          "url": "https://example.com/1",
                          "description": "React. " * 100},
                         cfg, cv, lambda _m: None, research=True)

    with_network = [p for p in prompts if p["tools"]]
    without_network = [p for p in prompts if not p["tools"]]
    assert with_network, "the prompt with the web search was never made at all"
    assert without_network, "the candidate-specific analysis was not made"

    for p in with_network:
        assert "SECRET-CV" not in p["prompt"], "the CV went into the prompt with the network"
        assert "120000 EUR" not in p["prompt"], "the salary expectations went into the prompt with the network"
        assert "Blue Card" not in p["prompt"], "the visa went into the prompt with the network"


def test_without_company_research_the_network_stays_off(profile, monkeypatch):
    from jobsearch import config, llm, scoring
    tools_seen = []
    monkeypatch.setattr(llm, "ask_json",
                        lambda prompt, **kw: tools_seen.append(kw.get("allowed_tools")) or {})

    scoring.deep_analyze({"title": "T", "company": "C", "location": "",
                          "description": "x" * 400},
                         config.load(), "CV", lambda _m: None, research=False)

    assert all(not tools for tools in tools_seen), "tools were handed out where nobody asked for them"


def test_a_javascript_link_from_the_model_never_reaches_the_page(profile, monkeypatch):
    """Escaping guards against breaking out of the attribute, but "javascript:" is a
    legal href value, and a click on a source number would run somebody else's
    script."""
    import json as _json
    from jobsearch import config, llm, scoring

    monkeypatch.setattr(llm, "ask_json", lambda prompt, **kw: {
        "match": 80, "reason": "ok",
        "sources": ["javascript:fetch('/app/reset',{method:'POST'})",
                    "data:text/html,<script>x</script>",
                    "https://glassdoor.com/acme"]})

    job = {"title": "T", "company": "C", "location": "", "description": "x" * 400}
    scoring.deep_analyze(job, config.load(), "CV", lambda _m: None, research=False)

    links = _json.loads(job["advice"])["sources"]
    assert links == ["https://glassdoor.com/acme"], f"a dangerous link got through: {links}"
