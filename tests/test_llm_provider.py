"""The chosen provider has to reach every call to the model.

Triage is the one stage that calls the model in batches, and it was the one that
forgot to pass provider along. A person picked a local model and the app went
quietly to claude CLI: on Windows that fell over on the encoding of a Russian
prompt, and the log said "charmap codec can't encode" instead of "the local
model is not answering".
"""
import subprocess
import sys

import pytest

from jobsearch import llm, providers, scoring
from jobsearch.collectors import crawler


@pytest.fixture
def local(cfg):
    cfg["llm"]["provider"] = "ollama"
    cfg["llm"]["triage_model"] = "qwen2.5:7b"
    return cfg


def _intercept(monkeypatch) -> list:
    """Stands in for providers.call and remembers what it was called with."""
    calls = []

    # llm is the whole settings block: your own endpoint needs not only the model
    # name but the address and the key, and this argument is how they arrive.
    # schema is the answer's shape, for small models. The stub is obliged to know
    # about it: without that it falls over on an unknown argument, and falls over
    # in every test at once.
    def fake(prompt, provider, model, timeout=600, allowed_tools=None,
             claude_bin="claude", llm=None, schema=None):
        calls.append({"provider": provider, "model": model, "prompt": prompt,
                       "llm": llm, "schema": schema})
        return "[]"

    monkeypatch.setattr(providers, "call", fake)
    return calls


def _claude_forbidden(monkeypatch) -> None:
    """Any trip to claude CLI is a failed test, not a trip to the network."""
    def boom(*a, **kw):
        raise AssertionError("claude CLI was called although a local model is chosen")

    monkeypatch.setattr(subprocess, "run", boom)


def test_triage_goes_to_the_chosen_provider(local, monkeypatch):
    calls = _intercept(monkeypatch)
    _claude_forbidden(monkeypatch)

    scoring.triage([{"title": "Frontend Engineer", "company": "Northwind",
                     "description": "React"}], local, log=lambda *_: None)

    assert [c["provider"] for c in calls] == ["ollama"]
    assert calls[0]["model"] == "qwen2.5:7b"


def test_the_crawler_goes_to_the_chosen_provider(local, monkeypatch):
    calls = _intercept(monkeypatch)
    _claude_forbidden(monkeypatch)

    class Page:
        url = "https://example.com/careers"
        text = ("<html><body>" + "Open positions at Northwind. " * 20 +
                "<a href='/jobs/1'>Frontend Engineer</a></body></html>")

        def raise_for_status(self):
            pass

    monkeypatch.setattr(crawler.web, "get", lambda *a, **kw: Page())

    crawler.crawl_company("Northwind", "https://example.com", local, log=lambda *_: None)

    assert calls, "the crawler never called the model"
    assert all(c["provider"] == "ollama" for c in calls)


def test_a_local_model_never_touches_the_systems_encoding(local, monkeypatch):
    """A Russian prompt reaches Ollama whole: the HTTP path goes round cp1252.

    We check that the Cyrillic arrived, not which word the prompt starts with: a
    language banner may stand in front of it when the answer's language differs
    from the prompt's own. It was on those first Cyrillic letters that cp1252
    used to fall over.
    """
    calls = _intercept(monkeypatch)
    scoring.triage([{"title": "Frontend Engineer", "company": "Northwind"}],
                   local, log=lambda *_: None)
    prompt_text = calls[0]["prompt"]
    assert "Ты" in prompt_text, "the Russian text of the prompt did not reach the model"
    assert any("А" <= c <= "я" for c in prompt_text)


# --- Encoding when calling the CLI -----------------------------------------

@pytest.mark.skipif(sys.platform != "win32", reason="cp1252 is the default only on Windows")
def test_the_system_encoding_really_would_have_fallen_over():
    """Checking the check: without encoding= a Russian prompt would not survive stdin."""
    with pytest.raises(UnicodeEncodeError):
        subprocess.run([sys.executable, "-c", "import sys; sys.stdin.read()"],
                       input=scoring.TRIAGE_PROMPT, capture_output=True,
                       text=True, timeout=30)


def test_the_cli_gets_a_russian_prompt_in_utf8(monkeypatch):
    """The prompt has to reach the CLI byte for byte, whatever the system locale is."""
    got = {}

    class Done:
        returncode = 0
        stdout = '{"result": "[]"}'
        stderr = ""

    def fake_run(cmd, **kw):
        got.update(kw)
        # the same thing a real subprocess does: encodes the input with the given encoding
        kw["input"].encode(kw["encoding"], kw.get("errors", "strict"))
        return Done()

    monkeypatch.setattr(subprocess, "run", fake_run)
    monkeypatch.setattr(providers, "resolve_bin", lambda name: "/usr/bin/claude")

    llm._ask_once("Ты — ассистент по поиску работы.", model="haiku",
                  claude_bin="claude", timeout=30, allowed_tools=None)

    assert got["encoding"] == "utf-8"


def test_the_provider_call_to_claude_is_in_utf8_too(monkeypatch):
    got = {}

    class Done:
        returncode = 0
        stdout = '{"result": "[]"}'
        stderr = ""

    def fake_run(cmd, **kw):
        got.update(kw)
        kw["input"].encode(kw["encoding"], kw.get("errors", "strict"))
        return Done()

    monkeypatch.setattr(subprocess, "run", fake_run)
    monkeypatch.setattr(providers, "resolve_bin", lambda name: "/usr/bin/claude")
    monkeypatch.setattr(providers, "login_env", lambda: {})

    providers.call_claude("Ты — ассистент по поиску работы.", "haiku", 30, None, "claude")

    assert got["encoding"] == "utf-8"


# --- How to ask a small model -------------------------------------------------

def test_jobs_are_split_up_for_a_local_model(monkeypatch):
    """Eight at a time is beyond a small model, and that is measured. On eight jobs
    with a known-good answer, mistral:7b mixed up the numbers: a score meant for
    one landed on another — which is how the front-end job got 85 from the SAP
    integrator's profile. On three: eight right out of eight, three runs in a
    row."""
    from jobsearch import scoring
    assert scoring.batch_for("ollama") == 3
    for cloud in ("claude_cli", "openai_api", "codex_cli", "cursor_cli"):
        assert scoring.batch_for(cloud) == 8, f"{cloud}: splitting is pointless here and costly"


def test_the_schema_reaches_the_model(monkeypatch, profile):
    """Without it a small model answers with fluent prose and no required field,
    and asking again is pointless — it will answer the same way."""
    from jobsearch import config, providers, scoring
    caught = {}

    def fake(prompt, provider, model, timeout=600, allowed_tools=None,
             claude_bin="claude", llm=None, schema=None):
        caught["schema"] = schema
        return "[]"

    monkeypatch.setattr(providers, "call", fake)
    cfg = config.load()
    cfg["llm"]["provider"] = "ollama"
    scoring.triage([{"title": "SAP", "description": "x", "company": "A"}],
                   cfg, log=lambda *a, **k: None, cv="CV")

    schema_seen = caught.get("schema")
    assert schema_seen, "the schema never reached the provider"
    fields = list(schema_seen["items"]["properties"])
    assert fields.index("reason") < fields.index("match"), \
        "the number is named before the reasoning — the reasoning is then fitted to it"
    assert "verdict" in schema_seen["items"]["required"]


def test_ollama_gets_the_window_and_a_zero_temperature(monkeypatch):
    """Ollama has a default window of its own, and it is smaller than what the model
    itself holds. And a temperature above zero means the same question gives
    different scores — nothing is being invented here, things are being judged."""
    from jobsearch import providers
    caught = {}

    class Answer:
        status_code = 200

        @staticmethod
        def raise_for_status():
            pass

        @staticmethod
        def json():
            return {"response": "[]"}

    def fake_post(url, json=None, timeout=None):
        caught.update(json or {})
        return Answer()

    monkeypatch.setattr(providers.requests, "post", fake_post)
    providers.call_ollama("привет", "mistral:7b", timeout=30, schema={"type": "object"})

    assert caught["options"]["temperature"] == 0
    assert caught["options"]["num_ctx"] >= 8192
    assert caught["format"] == {"type": "object"}


def test_the_examples_are_built_from_the_candidates_trade(profile):
    """Generic examples do not work, and that is measured: across fifteen real jobs,
    examples about an accountant gave exactly as much as no examples at all — six
    right out of fifteen. Examples about the candidate's own trade gave ten, and
    not one job from another trade rose above 50, meaning none reached the
    threshold of 70."""
    from jobsearch import config, scoring
    cfg = config.load()
    cfg["profile"]["roles"] = "SAP Integration Consultant, Integration Architect"

    block = scoring._examples_block(cfg)

    assert "SAP Integration Consultant" in block, "the own-trade example is not about the candidate"
    assert "своя" in block and "чужая" in block and "смежная" in block


def test_the_other_trade_example_is_not_the_candidates_own(profile):
    """Otherwise the "here is someone else's trade" example would show the candidate
    their own."""
    from jobsearch import config, scoring
    cfg = config.load()
    for role in ("Frontend-разработчик", "Бухгалтер", "Повар", "Медицинская сестра"):
        cfg["profile"]["roles"] = role
        block = scoring._examples_block(cfg)
        lines = [s for s in block.splitlines() if "чужая" in s]
        assert lines, role
        assert role.split("-")[0].lower() not in lines[0].lower(), \
            f"{role}: the candidate's own trade is named as someone else's"


def test_with_no_roles_there_are_no_examples(profile):
    """An invented role would pull the score further astray than no example at all."""
    from jobsearch import config, scoring
    cfg = config.load()
    cfg["profile"]["roles"] = ""
    assert scoring._examples_block(cfg) == ""
