"""Smoke: the pages have to open.

These eight addresses used to be checked by hand after every change — and one
skipped check cost somebody an "Internal Server Error" on their screen. Neither
the network nor the model is involved: the pages are drawn from the database and
the settings.
"""
import re

import pytest

pytest.importorskip("httpx", reason="TestClient needs httpx")

from fastapi.testclient import TestClient  # noqa: E402

from jobsearch import db  # noqa: E402

from conftest import job  # noqa: E402

PAGES = ["/", "/simple", "/results", "/coverage", "/models", "/notify", "/cv/check", "/welcome"]


@pytest.fixture
def client(profile):
    import app as app_module
    with TestClient(app_module.app, base_url="http://127.0.0.1:8765") as c:
        c.cookies.set("profile", profile)
        yield c


@pytest.mark.parametrize("path", PAGES)
def test_the_page_opens_on_an_empty_database(client, path):
    assert client.get(path).status_code == 200


@pytest.mark.parametrize("path", PAGES)
def test_the_page_opens_with_data(client, path, profile):
    db.save_job(job("k1", score=88, verified=True,
                    advice='{"cv_changes":["one"],"linkedin_changes":[],"cover_hint":"",'
                           '"salary_estimate":"60k","company_insights":[],"sources":[]}'), run_id=1)
    db.save_job(job("k2", score=40, posted_at=""), run_id=1)
    assert client.get(path).status_code == 200


def test_the_results_filters_do_not_bring_the_page_down(client, profile):
    db.save_job(job("k1", score=88), run_id=1)
    queries = [
        "/results?min=70",
        "/results?sort=posted&viewed=new&source=direct",
        "/results?posted_from=2026-07-01&posted_to=2026-07-31",
        "/results?posted_from=rubbish",          # someone typed nonsense by hand
        "/results?run=999",                    # there is no run with that number
        "/results?min=not-a-number",
    ]
    for q in queries:
        r = client.get(q)
        assert r.status_code in (200, 422), f"{q} → {r.status_code}"


def test_every_language_draws_the_pages(client, profile):
    from jobsearch import config, i18n
    db.save_job(job("k1", score=88), run_id=1)
    for lang in i18n.UI_LANGS:
        cfg = config.load()
        cfg["ui"]["lang"] = lang
        config.save(cfg)
        r = client.get("/results")
        assert r.status_code == 200, f"the results page fell over in {lang}"


def test_a_job_that_does_not_exist_breaks_nothing(client, profile):
    assert client.get("/cv/999999").status_code in (404, 502)


def test_choosing_a_model_returns_to_where_it_was_clicked(client, profile):
    """After "Use" the page reloads — and a person used to end up at the top of a
    long list rather than where they had clicked."""
    r = client.post("/models/select",
                    data={"model": "claude-haiku-4-5", "back": "/models", "anchor": "model-claude-haiku-4-5"},
                    follow_redirects=False)
    assert r.status_code == 303
    assert r.headers["location"].endswith("#model-claude-haiku-4-5"), r.headers["location"]


def test_in_the_introduction_it_leads_to_the_continue_button(client, profile):
    r = client.post("/models/select",
                    data={"model": "claude-haiku-4-5", "back": "/welcome", "anchor": "welcome-continue"},
                    follow_redirects=False)
    assert r.status_code == 303
    assert r.headers["location"].endswith("#welcome-continue")


def test_choosing_a_provider_leads_to_the_next_question(client, profile, monkeypatch):
    """Having chosen what to work through, a person is not finished: next comes which
    model, and that is further down the same page. They were thrown to the very
    top, back to the beginning of everything they had already read."""
    available_are(monkeypatch, claude_cli=True)
    r = client.post("/models/provider",
                    data={"provider": "claude_cli", "back": "/welcome", "anchor": "welcome-continue"},
                    follow_redirects=False)
    assert r.headers["location"].endswith("#welcome-continue"), r.headers["location"]

    r = client.post("/models/provider",
                    data={"provider": "claude_cli", "back": "/models", "anchor": "models-choose"},
                    follow_redirects=False)
    assert r.headers["location"].endswith("#models-choose"), r.headers["location"]


def test_checking_a_program_returns_to_its_own_card(client, profile, monkeypatch):
    """"Check again" is asked about one particular program — and that is where a
    person should stay, not be carried to the top of the page."""
    available_are(monkeypatch, claude_cli=True)
    r = client.post("/provider/recheck",
                    data={"provider": "ollama", "back": "/welcome", "anchor": "provider-ollama"},
                    follow_redirects=False)
    assert r.headers["location"].endswith("#provider-ollama"), r.headers["location"]


@pytest.mark.parametrize("path,anchor", [("/welcome", "welcome-continue"),
                                         ("/welcome?step=model", "welcome-step2"),
                                         ("/models", "models-choose")])
def test_the_anchor_is_on_the_page_itself(client, profile, monkeypatch, path, anchor):
    """An anchor in the address with none on the page is just a scroll to the top."""
    available_are(monkeypatch, claude_cli=True)
    assert f'id="{anchor}"' in client.get(path).text, f"{path}: nowhere to return to"


def test_with_no_anchor_the_redirect_is_as_before(client, profile):
    r = client.post("/models/select", data={"model": "x", "back": "/models"}, follow_redirects=False)
    assert r.status_code == 303 and "#" not in r.headers["location"]


# --- The order of the settings page, and what it says about itself -------------

def test_the_cv_comes_before_the_fields_it_fills(client, profile):
    """The CV used to sit at the very bottom of the page, below "Save settings",
    while "Fill empty fields from CV" waited halfway up and did nothing at all
    until a CV was there — with the box to upload one two screens further down."""
    html = client.get("/").text
    assert html.index('id="cv"') < html.index("/save?then=profile_from_cv"), \
        "the CV is still below the button that needs it"


def test_the_upload_box_is_not_swallowed_by_the_settings_form(client, profile):
    """The CV goes up in a form of its own — a file field inside the settings form
    would not be sent at all: forms do not nest."""
    html = client.get("/").text
    assert html.index('action="/upload_cv"') < html.index('action="/save"'), \
        "the upload box ended up inside the settings form"


def test_the_model_list_says_what_the_power_number_means(in_english):
    """"power 74/100" on every row, and not a word about whose scale it is, what
    it is measured against, or what it changes for the person choosing."""
    from jobsearch import i18n
    assert i18n.t("en", "models_power_hint") in in_english.get("/models").text


def test_the_cv_check_goes_to_the_background_and_does_not_hold_the_page(client, profile, monkeypatch):
    """This used to be a link that thought silently for minutes. Now the page comes
    back at once and the work goes on in the background."""
    import time as _t
    from jobsearch import config, cvcheck

    config.save_cv("cv.txt", ("Viktor Lavrov, front-end engineer. " * 8).encode("utf-8"))
    monkeypatch.setattr(cvcheck, "analyze", lambda cfg: _t.sleep(1) or {})

    started_at = _t.monotonic()
    r = client.post("/cv/check/run", follow_redirects=False)
    assert r.status_code == 303
    assert _t.monotonic() - started_at < 0.5, "the page waited for the check to finish"

    assert client.get("/cv/check/status").json()["running"] is True
    assert "cvcheck_running" not in client.get("/cv/check").text  # the key is translated, not shown raw


def test_a_failed_cv_check_shows_the_reason(client, profile, monkeypatch):
    from jobsearch import config, cvcheck
    config.save_cv("cv.txt", ("Viktor Lavrov, front-end engineer. " * 8).encode("utf-8"))

    def blow_up(cfg):
        raise RuntimeError("the model did not answer")

    monkeypatch.setattr(cvcheck, "analyze", blow_up)
    client.post("/cv/check/run", follow_redirects=False)
    for _ in range(50):
        if not client.get("/cv/check/status").json()["running"]:
            break
    status = client.get("/cv/check/status").json()
    assert "the model did not answer" in status["error"]
    assert "the model did not answer" in client.get("/cv/check").text, "the reason is not shown to the person"


def test_a_cv_that_would_not_build_explains_rather_than_returning_502(client, profile, monkeypatch):
    """A blank tab with "502" tells a person nothing."""
    from jobsearch import config, db, scoring
    from conftest import job as sample_job

    config.save_cv("cv.txt", ("Viktor Lavrov, front-end engineer. " * 8).encode("utf-8"))
    db.save_job(sample_job("k1"), run_id=1)
    job_id = db.matched_jobs(min_score=0)[0]["id"]

    def blow_up(*_a, **_kw):
        raise RuntimeError("the model returned prose instead of JSON")

    monkeypatch.setattr(scoring, "generate_cv", blow_up)
    r = client.get(f"/cv/{job_id}")
    assert r.status_code == 200, "the person got a bare error instead of an explanation"
    assert "JSON" in r.text or "model" in r.text.lower()


# --- Russian words on English pages --------------------------------------------

CYRILLIC = re.compile(r"[А-Яа-яЁё]")
# Not every piece of Cyrillic on an English page is trouble. "Русский" in the list
# of languages, and a profile name the person wrote themselves, belong there.
OWN_NAMES = re.compile(r'<select name="(ui_lang|output_lang|slug)".*?</select>', re.S)
LANGUAGE_TITLE = 'title="Язык / Language"'


def human_text(html: str) -> str:
    """The page without what a person does not read: comments in scripts and lists
    of languages. The double slash in "https://" is not taken for a comment."""
    html = OWN_NAMES.sub("", html).replace(LANGUAGE_TITLE, "")
    html = re.sub(r"/\*.*?\*/", "", html, flags=re.S)
    return re.sub(r"""(?<![:"'])//.*$""", "", html, flags=re.M)


@pytest.fixture
def in_english(client, profile):
    """An English-speaking person. The profile is renamed in Latin script: a name
    the person wrote themselves is not the program's text, and the check must not
    confuse the two."""
    from jobsearch import config, profiles
    profiles.rename(profile, "Alex")
    cfg = config.load()
    cfg["ui"].update(lang="en", output_lang="en")
    # its name is dropped into the page text; the model has to match the provider,
    # or the pages are not shown at all and there is nothing here to read
    cfg["llm"].update(provider="ollama", triage_model="llama3.1:8b")
    config.save(cfg)
    return client


@pytest.mark.parametrize("path", PAGES)
def test_in_english_there_are_no_russian_words(in_english, path):
    """Every key is translated, but text arrived round them too: the provider's
    name, the model notes, the mail services, the default profile name."""
    text = human_text(in_english.get(path).text)
    found = [ln.strip() for ln in text.splitlines() if CYRILLIC.search(ln)]
    assert not found, f"{path}: " + " | ".join(found[:4])


def test_in_english_there_are_no_russian_words_with_data_too(in_english):
    from jobsearch import db
    db.save_job(job("k1", score=88, verified=True, reason="strong match",
                    advice='{"cv_changes":["one"],"linkedin_changes":[],"cover_hint":"",'
                           '"salary_estimate":"60k","company_insights":[],"sources":[]}'),
                run_id=1)
    for path in ("/results", "/coverage"):
        text = human_text(in_english.get(path).text)
        found = [ln.strip() for ln in text.splitlines() if CYRILLIC.search(ln)]
        assert not found, f"{path}: " + " | ".join(found[:4])


def test_old_coverage_with_a_russian_source_kind_is_translated(in_english):
    """The source kind is kept in the database: runs written before the translation
    still hold "агрегатор" there — and it still has to be shown in English."""
    import json as _json
    from jobsearch import db
    run_id = db.start_run()
    db.finish_run(run_id, found=1, fresh=1, matched=1, status="ok", log="",
                  coverage=_json.dumps([{"name": "Remotive", "url": "https://remotive.com",
                                         "kind": "агрегатор", "count": 3, "error": None}]))
    text = in_english.get("/coverage").text
    assert "aggregator" in text, "the old value was not translated"
    assert "агрегатор" not in human_text(text)


# --- The first screen: what the buttons say about the state --------------------

PROVIDERS = {
    "claude_cli": {"ready": False, "web_search": True, "kind": "cloud", "install_url": ""},
    "cursor_cli": {"ready": False, "web_search": False, "kind": "cloud", "install_url": ""},
    "codex_cli": {"ready": False, "web_search": False, "kind": "cloud", "install_url": ""},
    "copilot_cli": {"ready": False, "web_search": False, "kind": "cloud", "install_url": ""},
    "goose_cli": {"ready": False, "web_search": False, "kind": "cloud", "install_url": ""},
    "qwen_cli": {"ready": False, "web_search": False, "kind": "cloud", "install_url": ""},
    "ollama": {"ready": False, "web_search": False, "kind": "local", "install_url": ""},
    "openai_api": {"ready": False, "web_search": False, "kind": "custom", "install_url": ""},
}


def available_are(monkeypatch, **ready_flags):
    """Stands in for the provider list: the test decides what is installed, not the
    machine."""
    import app as app_module
    from jobsearch import providers
    avail = {k: dict(v) for k, v in PROVIDERS.items()}
    for key, ready in ready_flags.items():
        avail[key]["ready"] = ready
    # The commands come from the real source rather than being copied into the
    # double: copies would drift from it silently, and the test would go on
    # insisting the command is there.
    for key, p in avail.items():
        p["install_cmd"] = providers.install_cmd(key)
        p["verify_cmd"] = providers.verify_cmd(key)
    # the second argument is the settings block: your own endpoint needs it, since
    # its readiness is decided by the address filled in rather than a file on disk
    monkeypatch.setattr(providers, "available",
                        lambda claude_bin="claude", llm=None: avail)
    monkeypatch.setattr(app_module.providers, "available", providers.available)


def row_button(html: str, model_id: str) -> str:
    """The last button in a model's row — the one that chooses it."""
    anchor = "model-" + re.sub(r"[:/.]", "-", model_id)
    row = html[html.index(f'id="{anchor}"'):]
    row = row[:row.index("</form>", row.index("/models/select"))]
    return re.findall(r"<button[^>]*>\s*([^<\n]+)", row)[-1].strip()


def test_the_chosen_model_is_named_on_the_button(client, profile, monkeypatch):
    """Every row used to offer "Use" — including the one already in use, so there was
    no telling from the action where you currently were."""
    from jobsearch import config, i18n
    available_are(monkeypatch, claude_cli=True)
    cfg = config.load()
    cfg["ui"]["lang"] = "en"
    cfg["llm"].update(provider="claude_cli", triage_model="sonnet")
    config.save(cfg)

    html = client.get("/models").text

    assert row_button(html, "sonnet") == i18n.t("en", "models_in_use")
    assert row_button(html, "opus") == i18n.t("en", "models_use")


def test_the_introduction_does_not_let_you_past_without_the_program(client, profile, monkeypatch):
    from jobsearch import config, i18n
    available_are(monkeypatch)                      # nothing installed
    cfg = config.load()
    cfg["ui"]["lang"] = "en"
    config.save(cfg)

    html = client.get("/welcome").text
    tail = html[html.index('id="welcome-continue"'):]

    assert "disabled" in tail.split("</button>")[0], "the Continue button was left clickable"
    assert i18n.t("en", "welcome_blocked") in tail


def test_the_introduction_tells_no_program_from_no_model(client, profile, monkeypatch):
    """Ollama is running and no model has been downloaded for it. People used to be
    sent off to install what was working perfectly well."""
    from jobsearch import config, i18n, providers
    available_are(monkeypatch, ollama=True)
    monkeypatch.setattr(providers, "ollama_installed_models", lambda: set())
    cfg = config.load()
    cfg["ui"]["lang"] = "en"
    cfg["llm"].update(provider="ollama", triage_model="llama3.3:70b")
    config.save(cfg)

    tail = client.get("/welcome?step=model").text
    tail = tail[tail.index('id="welcome-continue"'):]

    assert "disabled" in tail.split("</button>")[0]
    assert i18n.t("en", "welcome_blocked_model") in tail
    assert i18n.t("en", "welcome_blocked") not in tail, "the reason is named wrongly"


def test_your_own_endpoint_is_not_told_to_press_download(client, profile, monkeypatch):
    """A complaint (issue #5): "it asks me to download a model, and there is no
    download button".

    With your own endpoint there is nothing to download — the model is on somebody
    else's server — but the address and the model name can be missing, and then
    "Continue" is locked. Locked correctly; it was the words that were somebody
    else's: the Ollama text told them to press "Download" in the list above. There
    is neither list nor button on that page: there are fields instead. The person
    was left in front of a locked button, advised to press something that does not
    exist.
    """
    from jobsearch import config, i18n
    # Your own endpoint is always ready: nothing to install, it is an address.
    available_are(monkeypatch, openai_api=True)
    cfg = config.load()
    cfg["ui"]["lang"] = "en"
    cfg["llm"].update(provider="openai_api", api_base="", api_model="")
    config.save(cfg)

    page = client.get("/welcome?step=model").text
    tail = page[page.index('id="welcome-continue"'):]

    assert "disabled" in tail.split("</button>")[0]
    assert i18n.t("en", "welcome_blocked_endpoint") in tail
    assert i18n.t("en", "welcome_blocked_model") not in tail, "advice meant for Ollama"
    assert "Download" not in tail, "there is no button by that name on the page"


def test_the_card_names_the_install_command(client, profile, monkeypatch):
    """A person's complaint: "it does not see claude code on my mac".

    We were sending them to claude.com/claude-code, where the first thing offered
    is the desktop application — and that is what got installed. It is beside the
    point: what is needed is the claude program in a terminal. The card now says so
    outright and shows the command.
    """
    from jobsearch import config, i18n, providers
    available_are(monkeypatch)                      # nothing installed
    cfg = config.load()
    cfg["ui"]["lang"] = "en"
    config.save(cfg)

    page = client.get("/welcome").text

    assert providers.install_cmd("claude_cli") in page, "the install command is missing"
    assert "claude --version" in page, "nothing to check the install with"
    assert i18n.t("en", "install_cmd_label") in page
    # And the main thing — it says this is not the desktop application
    assert "desktop app" in i18n.t("en", "prov_claude_cli_hint")


@pytest.mark.parametrize("address", ["/", "/simple"])
def test_the_one_thing_left_screen_gives_the_command(client, profile, monkeypatch, address):
    """The very screen it all started from: a person sees "one thing left to do" and
    three steps. The first step sent them to claude.com/claude-code, where the
    desktop application and the command line sit side by side under one name — and
    that is where the wrong thing came from. The step now speaks of a terminal and
    shows the command.

    Both pages at once: the block is shared, and it used to stand in each of them
    as its own list, so an edit to one quietly drifted from the other.
    """
    from jobsearch import appstate, config, i18n, providers
    available_are(monkeypatch)                      # claude is not installed
    # The introduction is done: otherwise the middleware takes us from these pages
    # to /welcome, and the "one thing left to do" screen is shown to exactly the
    # person who finished theirs and whose program later disappeared.
    monkeypatch.setattr(appstate, "needs_setup", lambda: False)
    cfg = config.load()
    cfg["ui"]["lang"] = "en"
    cfg["llm"]["provider"] = "claude_cli"
    config.save(cfg)

    page = client.get(address).text

    assert i18n.t("en", "setup_needed") in page, "the screen is not there — the test is about nothing"
    assert providers.install_cmd("claude_cli") in page, "the install command is missing"
    assert "claude --version" in page, "nothing to check the install with"
    assert "desktop app" in page, "nothing is said about the desktop application"


def test_an_update_can_be_asked_about_from_any_page(client, profile, monkeypatch):
    """There was one button and it lay in the settings, below the middle of the page
    — that is, somewhere nobody goes for this. Meanwhile the version number stands
    in the header on every page, and "am I out of date" is asked while looking at
    it.

    The return goes to the same page. The handler used to take everyone to the
    front page: that was fine while the button was on the front page and there was
    one of it.
    """
    from jobsearch import update as update_mod
    monkeypatch.setattr(update_mod, "check", lambda force=False: {})

    for address in ("/results", "/coverage", "/models"):
        assert 'action="/app/update/check"' in client.get(address).text, f"no button on {address}"

    response = client.post("/app/update/check", data={"back": "/results"},
                        follow_redirects=False)

    assert response.status_code == 303
    assert response.headers["location"].startswith("/results"), response.headers["location"]


def test_when_an_update_is_found_we_lead_to_the_button(client, profile, monkeypatch):
    """Found by Viktor on the live program, and it was a real breakage.

    The message says "you can install it below". There is one install button in the
    whole program and it lives in the settings, while an update can be asked about
    from the header — that is, from any page. From "Quick search" a person got that
    "below" — and below there was nothing.

    This is exactly what we fixed for the own-endpoint case (issue #5): the text
    told them to press what is not on the page. I brought it back here by starting
    to return to the page the question came from.
    """
    import app as app_module
    from jobsearch import update as update_mod
    found = {"version": "9.9.9", "notes_url": "",
               "asset": {"url": "https://example.invalid/app.dmg"}}
    monkeypatch.setattr(update_mod, "check", lambda force=False: found)
    # The page draws the button from what the previous check wrote down, not from
    # the answer of check(): it never waits on the network. That has to be stubbed
    # too.
    monkeypatch.setattr(update_mod, "state", lambda: found)
    monkeypatch.setattr(app_module.update_mod, "state", update_mod.state)

    response = client.post("/app/update/check", data={"back": "/simple"},
                        follow_redirects=False)
    where = response.headers["location"]

    assert not where.startswith("/simple"), f"we led them where the button is not: {where}"
    assert where.endswith("#update"), f"we brought them to the page but not to the button: {where}"
    # And the button really is there — otherwise the test would guard emptiness
    assert 'action="/app/update/install"' in client.get("/").text
    # And on "Quick search" it is not — which is what this was all for
    assert 'action="/app/update/install"' not in client.get("/simple").text


def test_the_return_after_a_check_stays_inside_the_program(client, profile, monkeypatch):
    """back comes from the page, and a foreign address can be put into it. A person
    takes their own window to be their own program, and on a foreign page opened
    from it they behave trustingly."""
    from jobsearch import update as update_mod
    monkeypatch.setattr(update_mod, "check", lambda force=False: {})

    response = client.post("/app/update/check", data={"back": "https://example.com/"},
                        follow_redirects=False)

    assert not response.headers["location"].startswith("http"), response.headers["location"]


def test_the_command_is_visible_for_an_installed_one_too(client, profile, monkeypatch):
    """Viktor's question: "why does it not say how to install claude code?"

    It does — but the whole block hung under "not installed yet", and for anyone who
    had Claude Code the card came out empty: its neighbours stretched it and there
    was nothing inside. We were hiding it from exactly the person who had got to
    the end and wanted to check.

    The commands are always visible now, and they also answer "is that even the
    program you found on my machine": the verify command names precisely what we
    look for.
    """
    from jobsearch import config, i18n, providers
    available_are(monkeypatch, claude_cli=True)     # installed
    cfg = config.load()
    cfg["ui"]["lang"] = "en"
    config.save(cfg)

    page = client.get("/models").text
    card = page[page.index('id="provider-claude_cli"'):]
    card = card[:card.index("provider-cursor_cli")]

    assert i18n.t("en", "models_ready") in card, "the test is about the wrong thing: it is not installed"
    assert providers.install_cmd("claude_cli") in card, "the install command is missing"
    assert "claude --version" in card, "nothing to check against"
    # The hint about the desktop application, though, goes only to someone who
    # does not have it yet: it is about the choice they are about to make.
    assert i18n.t("en", "prov_claude_cli_hint") not in card


def test_the_ollama_hint_does_not_lie_when_it_is_running(client, profile, monkeypatch):
    """Its hint says to download from ollama.com and run it. Showing that to someone
    whose Ollama is already running would be lying to their face — which is why the
    hint stayed under the condition when the commands came out from under it."""
    from jobsearch import config, i18n
    available_are(monkeypatch, ollama=True)
    cfg = config.load()
    cfg["ui"]["lang"] = "en"
    config.save(cfg)

    page = client.get("/models").text
    card = page[page.index('id="provider-ollama"'):]

    assert i18n.t("en", "prov_ollama_hint") not in card


def test_the_install_command_suits_this_system(monkeypatch):
    """On Windows curl … | bash will not run, and on a Mac PowerShell will not. What
    should be shown is the one a person can actually type."""
    from jobsearch import providers
    monkeypatch.setattr(providers.os, "name", "posix")
    assert providers.install_cmd("claude_cli").startswith("curl -fsSL https://claude.ai")
    monkeypatch.setattr(providers.os, "name", "nt")
    assert providers.install_cmd("claude_cli").startswith("irm https://claude.ai")
    # Where there is no command it is empty, and the card stays silent rather than lying
    assert providers.install_cmd("goose_cli") == ""
    assert providers.install_cmd("ollama") == ""


def test_the_introduction_lets_you_past_when_the_model_is_there(client, profile, monkeypatch):
    from jobsearch import config, providers
    available_are(monkeypatch, ollama=True)
    monkeypatch.setattr(providers, "ollama_installed_models", lambda: {"llama3.3:70b"})
    cfg = config.load()
    cfg["ui"]["lang"] = "en"
    cfg["llm"].update(provider="ollama", triage_model="llama3.3:70b")
    config.save(cfg)

    tail = client.get("/welcome?step=model").text
    tail = tail[tail.index('id="welcome-continue"'):]

    assert "disabled" not in tail.split("</button>")[0], "going further is not allowed"


# --- The exports --------------------------------------------------------------

def test_the_report_opens_rather_than_downloads(client):
    """The hint beside the button promises "open and print to PDF", while the report
    was handed over as a file to download. In the program's own window nothing
    happened at all: pywebview does not allow downloads, and the person pressed the
    button for nothing. Printing to PDF is done from an open page."""
    response = client.get("/export/report")
    assert response.status_code == 200
    assert "attachment" not in response.headers.get("content-disposition", ""), \
        "the report is still handed over as a download"
    assert response.headers["content-type"].startswith("text/html")


def test_the_spreadsheet_is_still_a_file(client):
    """The other side: there is no point looking at a CSV, it gets opened elsewhere."""
    response = client.get("/export/csv")
    assert "attachment" in response.headers.get("content-disposition", "")


def test_the_report_link_opens_a_new_tab(client):
    # The exports appear only when there is something to export
    db.save_job(job("k1", score=88), run_id=1)
    page = client.get("/results?min=0").text
    i = page.index("/export/report")
    chunk = page[i - 60:i + 400]
    assert 'target="_blank"' in chunk, "the report will open over the list of jobs"


FORMATS = [
    ("/export/report", "text/html", False),   # opens: printing to PDF is done from an open page
    ("/export/csv", "text/csv", True),
    ("/export/md", "text/markdown", True),
    ("/export/json", "application/json", True),
]


@pytest.mark.parametrize("address,mime,as_file", FORMATS)
def test_every_format_exports(client, address, mime, as_file):
    db.save_job(job("k1", score=88), run_id=1)
    response = client.get(address + "?min=0")
    assert response.status_code == 200
    assert response.headers["content-type"].startswith(mime)
    is_attachment = "attachment" in response.headers.get("content-disposition", "")
    assert is_attachment is as_file


def test_every_format_is_on_the_page(client):
    db.save_job(job("k1", score=88), run_id=1)
    page = client.get("/results?min=0").text
    for address, *_ in FORMATS:
        assert address in page, f"there is no button for {address}"


def test_the_report_has_a_print_button(client):
    """Advice to go hunting for print in the browser menu is not the same as a
    button. The PDF comes out of an open page, and "save as PDF" is in that same
    dialog."""
    db.save_job(job("k1", score=88), run_id=1)
    report = client.get("/export/report?min=0").text
    assert "window.print()" in report
    assert "noprint" in report, "the button will end up on paper"


def test_json_unpacks_the_advice(client):
    """In the database the advice sits as a string with JSON inside. Handing out text
    that has to be parsed a second time would be shifting our work onto the
    person."""
    import json as _json
    db.save_job(job("k1", score=88, advice=_json.dumps(
        {"cv_changes": ["Move SAP to the front"], "salary_estimate": "70k"},
        ensure_ascii=False)), run_id=1)

    d = _json.loads(client.get("/export/json?min=0").text)

    assert d["count"] == 1
    assert d["jobs"][0]["cv_changes"] == ["Move SAP to the front"]
    assert d["jobs"][0]["salary_estimate"] == "70k"
