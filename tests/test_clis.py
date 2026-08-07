"""The command-line tools you can think with.

We have one requirement of them, and it is not the obvious one: the task must go
through stdin, not as a command-line argument. Our prompts run to thousands of
characters, with newlines, quotes and Cyrillic in them, and the length of a
command line is limited — about two megabytes on Linux. Prompts like these are
exactly where it runs out, and the program dies with "argument list too long".
So tools that can only read an argument will not do here, and the check for that
lives here as well.
"""
import pytest

from jobsearch import i18n, providers

PROMPT = "Оцени вакансию.\nВторая строка с «кавычками» и переносом.\n" * 200


class Run:
    def __init__(self, stdout="the model's answer", code=0, stderr=""):
        self.stdout, self.returncode, self.stderr = stdout, code, stderr


def intercept(monkeypatch, **answer):
    """Stands in for running the program and remembers what it was called with."""
    caught = {}

    def run(cmd, **kw):
        caught.update(cmd=cmd, stdin=kw.get("input"), cwd=kw.get("cwd"))
        return Run(**answer)

    monkeypatch.setattr(providers.subprocess, "run", run)
    monkeypatch.setattr(providers, "resolve_bin", lambda name: f"/usr/bin/{name}")
    return caught


CALLS = [
    ("copilot_cli", providers.call_copilot, "copilot"),
    ("goose_cli", providers.call_goose, "goose"),
    ("qwen_cli", providers.call_qwen, "qwen"),
    ("codex_cli", providers.call_codex, "codex"),
]


@pytest.mark.parametrize("code,call,program", CALLS)
def test_the_task_goes_through_stdin_not_as_an_argument(monkeypatch, code, call, program):
    """That very command-line length limit. The prompt is long on purpose."""
    caught = intercept(monkeypatch)

    call(PROMPT, "auto", timeout=60)

    assert caught["stdin"] == PROMPT, f"{program}: the task was not fed to stdin"
    joined = " ".join(caught["cmd"])
    assert "Оцени вакансию" not in joined, \
        f"{program}: the task went into the arguments — a long prompt would be cut off"


@pytest.mark.parametrize("code,call,program", CALLS)
def test_we_call_the_program_we_promised(monkeypatch, code, call, program):
    caught = intercept(monkeypatch)
    call("hello", "auto", timeout=60)
    assert caught["cmd"][0].endswith(program)


@pytest.mark.parametrize("code,call,program", CALLS)
def test_the_answer_is_stripped_of_whitespace(monkeypatch, code, call, program):
    intercept(monkeypatch, stdout="  done  \n")
    assert call("hello", "auto", timeout=60) == "done"


@pytest.mark.parametrize("code,call,program", CALLS)
def test_auto_does_not_name_a_model(monkeypatch, code, call, program):
    """"Auto" means "do not name a model": then whatever the program itself is set
    up with gets used. Their model names change more often than our versions do,
    and a stale name in the list would break a search, while "Auto" will not."""
    caught = intercept(monkeypatch)
    call("hello", "auto", timeout=60)
    assert not [a for a in caught["cmd"] if a.startswith("--model")], \
        f"{program}: a model was named after all, with Auto chosen"


@pytest.mark.parametrize("code,call,program", CALLS)
def test_a_named_model_arrives(monkeypatch, code, call, program):
    caught = intercept(monkeypatch)
    call("hello", "some-model", timeout=60)
    assert "some-model" in " ".join(caught["cmd"])


@pytest.mark.parametrize("code,call,program", CALLS)
def test_the_programs_error_is_explained(monkeypatch, code, call, program):
    intercept(monkeypatch, code=1, stderr="out of credit")
    with pytest.raises(providers.ProviderError) as caught:
        call("hello", "auto", timeout=60)
    assert "out of credit" in str(caught.value)


@pytest.mark.parametrize("code,call,program", CALLS)
def test_a_silent_failure_is_explained_too(monkeypatch, code, call, program):
    """Empty output and a non-zero code: we still have to say it did not work."""
    intercept(monkeypatch, code=137, stdout="", stderr="")
    with pytest.raises(providers.ProviderError) as caught:
        call("hello", "auto", timeout=60)
    assert caught.value.key == "prov_err_exit_code"


@pytest.mark.parametrize("code,call,program", CALLS)
def test_a_program_that_was_not_found_is_named(monkeypatch, code, call, program):
    monkeypatch.setattr(providers, "resolve_bin", lambda name: "")
    monkeypatch.setattr(providers.subprocess, "run",
                        lambda *a, **kw: pytest.fail("we went off to run something that is not there"))
    with pytest.raises(providers.ProviderError) as caught:
        call("hello", "auto", timeout=60)
    assert program in i18n.t("en", caught.value.key).lower() \
        or "not found" in i18n.t("en", caught.value.key).lower()


@pytest.mark.parametrize("code,call,program", CALLS)
def test_we_work_in_an_empty_scratch_folder(monkeypatch, code, call, program):
    """Otherwise the program looks around itself, and on macOS the system starts
    asking for permission to Documents and Photos — in our name."""
    caught = intercept(monkeypatch)
    call("hello", "auto", timeout=60)
    assert caught["cwd"] and "cli-work" in str(caught["cwd"])


# --- How they look to the rest of the program ---------------------------------

@pytest.mark.parametrize("code,call,program", CALLS)
def test_the_shared_call_leads_where_it_should(monkeypatch, code, call, program):
    caught = intercept(monkeypatch)
    providers.call("hello", code, model="auto", timeout=60)
    assert caught["cmd"][0].endswith(program)


@pytest.mark.parametrize("code", [c for c, _, _ in CALLS])
def test_each_one_has_a_model_list_and_a_name(code):
    """An empty list would leave a person at the second step with nothing at all."""
    models = providers.models_for(code, lang="en")
    assert models, f"{code}: nothing to choose from"
    assert models[0]["id"], f"{code}: the model has no id"
    assert i18n.t("en", "prov_" + code) != "prov_" + code, f"{code}: no name"


@pytest.mark.parametrize("code", [c for c, _, _ in CALLS])
def test_only_those_that_can_search_promise_a_web_search(code):
    """Promising a web search that is not there is worse than not promising one:
    without it, scouting for new companies is switched off, and a person should
    know that in advance."""
    assert providers.supports_web_search(code) is False


def test_every_provider_is_named_and_described():
    """Every card on the first step takes three strings from the translations.
    Missing any one of them would leave the raw key name on the screen."""
    for code in providers.available("claude", {}):
        for suffix in ("", "_hint", "_about"):
            key = f"prov_{code}{suffix}"
            assert i18n.t("en", key) != key, f"no string for {key}"
