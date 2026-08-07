"""What the command-line tools themselves get installed with.

Copilot and Qwen come only through npm, and npm comes with Node.js, which an
ordinary computer does not have. The card used to say "npm install -g
@github/copilot"; a person went off to a terminal and got "npm: command not
found" — by then outside our program, where we can do nothing for them. What is
checked here is that this gets said in time, and inside the program.
"""
import pytest

from conftest import client_for
from jobsearch import config, providers


def node_is(monkeypatch, found=True, version="v22.14.0"):
    """What the program sees when it asks the computer about Node.js."""
    monkeypatch.setattr(providers, "tool_state",
                        lambda tool: (found and providers._major(version) >= 22, version)
                        if found else (False, ""))


def without_any_cli(monkeypatch):
    """None of them is installed — otherwise there is nothing to install."""
    monkeypatch.setattr(providers, "_bin_exists", lambda name: False)
    monkeypatch.setattr(providers, "ollama_running", lambda: False)


# --- Who needs what -----------------------------------------------------------

@pytest.mark.parametrize("provider", ["copilot_cli", "qwen_cli"])
def test_installed_through_npm_so_node_is_needed(monkeypatch, provider):
    node_is(monkeypatch, found=False)
    assert providers.missing_tool(provider) == "node"


@pytest.mark.parametrize("provider", ["claude_cli", "cursor_cli", "goose_cli",
                                       "ollama", "openai_api", "codex_cli"])
def test_those_with_their_own_installer_require_nothing(monkeypatch, provider):
    """Driving a person to install Node.js for something that does perfectly well
    without it is worse than never mentioning Node.js. Codex has a ready
    binary."""
    node_is(monkeypatch, found=False)
    assert providers.missing_tool(provider) == ""


def test_node_is_there_so_there_is_no_question(monkeypatch):
    node_is(monkeypatch)
    assert providers.missing_tool("copilot_cli") == ""


def test_an_old_node_is_not_the_same_as_none_at_all(monkeypatch):
    """Telling someone who has Node.js to "install Node.js" sends them round in a
    circle: they install the same thing and come back with the same."""
    node_is(monkeypatch, version="v18.20.0")
    assert providers.missing_tool("copilot_cli") == "node"
    assert providers.tool_info("node")["too_old"] is True
    assert providers.tool_info("node")["version"] == "v18.20.0"


def test_an_unreadable_version_does_not_bar_the_way(monkeypatch):
    """An unfamiliar output format is our problem, not theirs: let them try."""
    monkeypatch.setattr(providers, "resolve_bin", lambda name: "/usr/bin/node")

    class Output:
        stdout, stderr = "some kind of gibberish", ""

    monkeypatch.setattr(providers.subprocess, "run", lambda *a, **kw: Output())
    providers.tool_state.cache_clear()
    assert providers.tool_state("node")[0] is True


def test_we_say_nothing_about_an_unknown_tool():
    assert providers.tool_state("no-such-tool") == (True, "")
    assert providers.missing_tool("no-such-provider") == ""


# --- The screen ---------------------------------------------------------------

def test_choosing_leads_to_where_it_says_what_to_install_with(monkeypatch, profile):
    """People used to be returned to the first step, to the "Download" button, follow
    it into instructions saying "npm install -g …", and find out about npm in a
    terminal."""
    import app as app_module
    without_any_cli(monkeypatch)
    node_is(monkeypatch, found=False)

    response = client_for(app_module.app, profile).post(
        "/models/provider", data={"provider": "copilot_cli", "back": "/welcome",
                                  "anchor": "welcome-continue"},
        follow_redirects=False)

    assert response.status_code == 303
    assert "step=tools" in response.headers["location"]


def test_choosing_one_that_needs_nothing_leads_nowhere(monkeypatch, profile):
    import app as app_module
    without_any_cli(monkeypatch)
    node_is(monkeypatch, found=False)

    response = client_for(app_module.app, profile).post(
        "/models/provider", data={"provider": "claude_cli", "back": "/welcome",
                                  "anchor": "welcome-continue"},
        follow_redirects=False)

    assert "step=tools" not in response.headers["location"]


def test_the_screen_says_what_and_why(monkeypatch, profile):
    import app as app_module
    without_any_cli(monkeypatch)
    node_is(monkeypatch, found=False)
    cfg = config.load()
    cfg["llm"]["provider"] = "copilot_cli"
    config.save(cfg)

    page = client_for(app_module.app, profile).get("/welcome?step=tools").text

    assert "Node.js" in page
    assert "npm" in page, "it does not say what Node.js has to do with it"
    assert "GitHub Copilot" in page, "it does not say what all this is for"
    assert 'action="/tool/install"' in page
    assert 'action="/tool/recheck"' in page
    # No going further: there is nothing to install with anyway
    assert "disabled" in page


def test_the_screen_does_not_become_a_dead_end(monkeypatch, profile):
    """Node.js is installed — there is nothing left to keep the person on this
    screen, and leaving them there means demanding they install what is already
    there."""
    import app as app_module
    without_any_cli(monkeypatch)
    node_is(monkeypatch)
    cfg = config.load()
    cfg["llm"]["provider"] = "copilot_cli"
    config.save(cfg)

    page = client_for(app_module.app, profile).get("/welcome?step=tools").text

    assert "welcome-tools" not in page


def test_an_old_node_is_told_about_the_version_not_the_install(monkeypatch, profile):
    import app as app_module
    without_any_cli(monkeypatch)
    node_is(monkeypatch, version="v18.20.0")
    cfg = config.load()
    cfg["llm"]["provider"] = "qwen_cli"
    config.save(cfg)

    page = client_for(app_module.app, profile).get("/welcome?step=tools").text

    assert "v18.20.0" in page, "it does not say what was actually found"
    assert "22" in page, "it does not say what is needed"


# --- The buttons --------------------------------------------------------------

def test_download_opens_the_node_page(monkeypatch, profile):
    import app as app_module
    opened = []
    monkeypatch.setattr(app_module.webbrowser, "open", opened.append)

    client_for(app_module.app, profile).post(
        "/tool/install", data={"tool": "node"}, follow_redirects=False)

    assert opened == ["https://nodejs.org/en/download"]


def test_a_foreign_address_cannot_be_opened_with_this_button(monkeypatch, profile):
    """Only the tool's code arrives from the page. Otherwise the button could be
    made to open anything at all."""
    import app as app_module
    opened = []
    monkeypatch.setattr(app_module.webbrowser, "open", opened.append)

    client_for(app_module.app, profile).post(
        "/tool/install", data={"tool": "https://example.com/evil"},
        follow_redirects=False)

    assert opened == []


def test_check_again_really_asks_again(monkeypatch, profile):
    """The person has just come back from installing — the old answer is stale."""
    import app as app_module
    forgotten = []
    monkeypatch.setattr(providers, "forget_binaries", lambda: forgotten.append(True))
    node_is(monkeypatch)

    client_for(app_module.app, profile).post(
        "/tool/recheck", data={"tool": "node"}, follow_redirects=False)

    assert forgotten, "the answer came from memory, and the install went unnoticed"


def test_an_unknown_code_does_not_drop_a_translation_key_on_the_screen(profile):
    """An untranslated key on the screen looks like a breakage — been there."""
    import app as app_module
    response = client_for(app_module.app, profile).post(
        "/tool/recheck", data={"tool": "whatever"}, follow_redirects=False)
    assert "tool_" not in response.headers["location"]


def test_the_models_page_says_this_too(monkeypatch, profile):
    """People change the provider after the introduction too — and run into the same
    wall. There is no "what needs installing" screen here, but staying silent
    about an npm that is not there will not do."""
    import app as app_module
    without_any_cli(monkeypatch)
    node_is(monkeypatch, found=False)
    cfg = config.load()
    cfg["llm"]["provider"] = "copilot_cli"
    config.save(cfg)

    page = client_for(app_module.app, profile).get("/models").text

    assert "Node.js" in page


def test_the_other_cards_say_nothing_about_node(monkeypatch, profile):
    """Claude Code has trouble of its own, and Node.js has nothing to do with it."""
    import app as app_module
    without_any_cli(monkeypatch)
    node_is(monkeypatch, found=False)
    cfg = config.load()
    cfg["llm"]["provider"] = "claude_cli"
    config.save(cfg)

    page = client_for(app_module.app, profile).get("/models").text

    assert "Node.js" not in page


def test_the_models_page_has_a_button_and_not_only_a_line_of_text(monkeypatch, profile):
    """Saying what is missing without saying where to get it is half the job. The
    introduction has a screen of its own for that; here a button is enough."""
    import app as app_module
    without_any_cli(monkeypatch)
    node_is(monkeypatch, found=False)
    cfg = config.load()
    cfg["llm"]["provider"] = "qwen_cli"
    config.save(cfg)

    page = client_for(app_module.app, profile).get("/models").text

    assert 'action="/tool/install"' in page, "nowhere to get what is missing"


def test_there_is_no_button_when_everything_is_there(monkeypatch, profile):
    import app as app_module
    without_any_cli(monkeypatch)
    node_is(monkeypatch)
    cfg = config.load()
    cfg["llm"]["provider"] = "qwen_cli"
    config.save(cfg)

    page = client_for(app_module.app, profile).get("/models").text

    assert 'action="/tool/install"' not in page
