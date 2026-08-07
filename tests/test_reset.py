"""Starting over: the introduction that insists, erasing the data, removing a model.

All three come from one complaint. Somebody deleted the program and Ollama with
it, installed the new version, and was met by their old profile, their old
settings, and a model marked "in use" that was no longer anywhere on the disk.
Uninstalling does not touch the data directory — so a reinstall is not a fresh
start, and the app went on believing the introduction was over and the model was
there. There was no way from inside to say otherwise: not to erase the data, not
to remove a model, and not even to reach the one screen that could have fixed it.
"""
import re
from urllib.parse import unquote

import pytest

pytest.importorskip("httpx", reason="TestClient needs httpx")

from fastapi.testclient import TestClient  # noqa: E402

from jobsearch import appstate, config, db, profiles, providers  # noqa: E402

from conftest import job  # noqa: E402


@pytest.fixture
def client(profile):
    import app as app_module
    with TestClient(app_module.app, base_url="http://127.0.0.1:8765") as c:
        c.cookies.set("profile", profile)
        yield c


def model_row(html: str, model_id: str) -> str:
    """The piece of the page belonging to one model."""
    anchor = "model-" + re.sub(r"[:/.]", "-", model_id)
    chunks = html.split('<div class="model-row')
    return next(k for k in chunks if f'id="{anchor}"' in k)


def without_a_model(monkeypatch):
    """Ollama is chosen but not on this machine — exactly as after a reinstall."""
    monkeypatch.setattr(providers, "ollama_running", lambda: False)
    monkeypatch.setattr(providers, "ollama_installed_models", lambda: set())
    cfg = config.load()
    cfg["llm"].update(provider="ollama", triage_model="gemma2:9b")
    config.save(cfg)


# --- The introduction insists ---------------------------------------------------

PAGES = ["/", "/simple", "/results", "/coverage", "/models", "/notify", "/cv/check"]


@pytest.mark.parametrize("path", PAGES)
def test_with_no_model_only_the_introduction_shows(client, monkeypatch, path):
    """This is what people saw: the settings opened, and there was nothing to start
    a search with."""
    without_a_model(monkeypatch)
    r = client.get(path, follow_redirects=False)
    assert r.status_code == 303, f"{path} opened with no model"
    assert r.headers["location"] == "/welcome"


def test_a_finished_introduction_does_not_save_a_vanished_model(client, monkeypatch):
    """The "introduction done" mark outlives uninstalling the program — and it used
    to be enough on its own to let someone in with a model that does not work."""
    without_a_model(monkeypatch)
    assert appstate.setup_done(), "precondition: the mark is there"
    assert appstate.needs_setup(), "the mark was enough although there is no model"


def test_with_a_working_model_the_pages_open(client):
    """The other side: the introduction must not hold back a program already set up."""
    for path in PAGES:
        assert client.get(path, follow_redirects=False).status_code == 200, path


def test_continuing_without_a_model_fails_even_round_the_button(client, monkeypatch):
    """The button on the page is disabled, but the address can be called by hand."""
    without_a_model(monkeypatch)
    (profiles.DATA_ROOT / "app.json").unlink(missing_ok=True)

    r = client.post("/welcome/done", follow_redirects=False)

    assert r.headers["location"].startswith("/welcome"), "we let them into the program with no model"
    assert not appstate.load().get("setup_done"), "the introduction was marked done with no model"


def test_an_unlisted_model_does_not_count_as_broken(client, monkeypatch):
    """Somebody may have downloaded a model that is not on our list. No disaster."""
    monkeypatch.setattr(providers, "ollama_running", lambda: True)
    monkeypatch.setattr(providers, "ollama_installed_models", lambda: {"some-model:7b"})
    cfg = config.load()
    cfg["llm"].update(provider="ollama", triage_model="some-model:7b")
    config.save(cfg)

    assert providers.missing_piece(config.load()["llm"]) == ""


def test_the_reason_is_named_exactly(client, monkeypatch):
    """A running Ollama with no model is not the same as a missing Ollama:
    otherwise a person is sent off to reinstall what is working perfectly well."""
    monkeypatch.setattr(providers, "ollama_running", lambda: True)
    monkeypatch.setattr(providers, "ollama_installed_models", lambda: set())
    cfg = config.load()
    cfg["llm"].update(provider="ollama", triage_model="gemma2:9b")
    config.save(cfg)
    assert providers.missing_piece(config.load()["llm"]) == "model"

    monkeypatch.setattr(providers, "ollama_running", lambda: False)
    assert providers.missing_piece(config.load()["llm"]) == "provider"


# --- Erasing everything ---------------------------------------------------------

def test_erasing_the_data_wipes_the_profiles_and_the_mark(client, profile):
    config.save(config.load())                       # the profile now has settings
    db.save_job(job("k1", score=70), run_id=1)
    assert (profiles.PROFILES_DIR / profile / "config.json").exists()

    r = client.post("/app/reset", follow_redirects=False)

    assert r.status_code == 303
    assert r.headers["location"].startswith("/welcome")
    assert not (profiles.PROFILES_DIR / profile).exists(), "the person's data is still there"
    assert not (profiles.DATA_ROOT / "app.json").exists(), "the introduction mark is still there"
    assert not appstate.setup_done(), "the program still believes it is set up"


def test_after_erasing_the_program_carries_on(client):
    """Erasing the data is not the same as breaking the program: an empty profile
    has to appear by itself, and the database has to come back up."""
    client.post("/app/reset", follow_redirects=False)

    assert profiles.list_profiles(), "not one profile is left"
    profiles.set_active(profiles.default_slug())
    db.init()
    assert db.matched_jobs(min_score=0) == [], "the database did not come back up"
    assert client.get("/welcome").status_code == 200


def test_erasing_leaves_alone_what_the_program_lives_by(client):
    """The single-instance lock and the crash log are not a person's data. Erasing
    them means knocking the ground out from under the very copy doing the erasing
    — and on Windows an open log will not delete anyway."""
    (profiles.DATA_ROOT / "running.json").write_text("{}", encoding="utf-8")
    (profiles.DATA_ROOT / "errors.log").write_text("old news", encoding="utf-8")

    client.post("/app/reset", follow_redirects=False)

    assert (profiles.DATA_ROOT / "running.json").exists(), "the single-instance lock was deleted"
    assert (profiles.DATA_ROOT / "errors.log").exists(), "the crash log was deleted"


def test_erasing_takes_what_nobody_remembered_either(client):
    """Everything but the housekeeping files is erased, rather than a list of known
    names: a file the program starts writing tomorrow would otherwise quietly
    outlive "erase everything"."""
    (profiles.DATA_ROOT / "something-new.json").write_text("{}", encoding="utf-8")

    client.post("/app/reset", follow_redirects=False)

    assert not (profiles.DATA_ROOT / "something-new.json").exists()


def test_the_data_is_not_erased_during_a_search(client):
    """A run writes into those very files: some of them would quietly come back."""
    from jobsearch import pipeline
    config.save(config.load())
    pipeline.state["running"] = True
    try:
        r = client.post("/app/reset", follow_redirects=False)
    finally:
        pipeline.state["running"] = False

    assert r.headers["location"].startswith("/?"), "we did not explain why nothing was erased"
    assert profiles.list_profiles(), "the data was erased in the middle of a search after all"


def test_a_file_that_survived_is_named_not_hidden(client, monkeypatch):
    """Something may fail to delete — staying silent about it will not do: the
    person leaves believing they started with a clean sheet."""
    import app as app_module
    monkeypatch.setattr(app_module.profiles, "wipe_all", lambda: ["jobs.db"])
    cfg = config.load()
    cfg["ui"]["lang"] = "en"
    config.save(cfg)

    r = client.post("/app/reset", follow_redirects=False)

    where = r.headers["location"]
    assert "jobs.db" in where, f"we did not name what was left: {where}"
    assert "clean+sheet" not in where and "clean%20sheet" not in where, \
        "we reported a clean sheet while leaving a file behind"


def test_the_introduction_offers_to_erase_only_when_there_is_something(client, monkeypatch):
    without_a_model(monkeypatch)
    config.save(config.load())                       # there is something already
    assert 'action="/app/reset"' in client.get("/welcome").text

    profiles.wipe_all()
    profiles.ensure_migrated()
    profiles.set_active(profiles.default_slug())
    assert 'action="/app/reset"' not in client.get("/welcome").text, \
        "a new person was offered the chance to erase what they do not have"


# --- Removing a downloaded model ------------------------------------------------

def test_the_model_is_removed_from_ollama(client, monkeypatch):
    sent = {}

    def stand_in(model):
        sent["model"] = model

    monkeypatch.setattr(providers, "delete_model", stand_in)
    r = client.post("/models/delete", data={"model": "gemma2:9b", "back": "/models"},
                    follow_redirects=False)

    assert sent == {"model": "gemma2:9b"}, "the delete request never went out"
    assert r.status_code == 303


def test_removing_the_model_in_use_leads_to_the_introduction(client, monkeypatch):
    """Removing the one in use is allowed — but then there is nothing left to think
    with, and that has to be said at once rather than dropping the person on a
    page that does not work."""
    cfg = config.load()
    cfg["llm"].update(provider="ollama", triage_model="gemma2:9b")
    config.save(cfg)
    monkeypatch.setattr(providers, "delete_model", lambda model: None)
    monkeypatch.setattr(providers, "ollama_installed_models", lambda: set())

    r = client.post("/models/delete", data={"model": "gemma2:9b", "back": "/models"},
                    follow_redirects=False)

    assert r.headers["location"].startswith("/welcome"), r.headers["location"]
    assert "gemma2:9b" in unquote(r.headers["location"]), "we did not say what exactly was removed"


def test_we_do_not_delete_during_a_download(client, monkeypatch):
    monkeypatch.setattr(providers, "pull_in_progress", lambda: True)
    touched = []
    monkeypatch.setattr(providers, "delete_model", lambda model: touched.append(model))

    client.post("/models/delete", data={"model": "gemma2:9b"}, follow_redirects=False)

    assert touched == [], "a model was deleted while a download is running"


def test_the_explanation_is_not_lost_on_the_way(client, monkeypatch):
    """The page they came from may have just closed — the model is gone. Sending
    them back to it means the gate takes them to the introduction, and the
    explanation falls away with the address: an unfamiliar screen and not a word
    about why they are on it."""
    without_a_model(monkeypatch)

    def blow_up(model):
        raise providers.ProviderError(key="prov_err_ollama_down")

    monkeypatch.setattr(providers, "delete_model", blow_up)

    r = client.post("/models/delete", data={"model": "gemma2:9b", "back": "/models"},
                    follow_redirects=False)

    where = r.headers["location"]
    assert where.startswith("/welcome?"), f"the explanation was lost on the way: {where}"
    assert "Ollama" in unquote(where), "we did not say what actually happened"


def test_a_deletion_error_is_explained_to_the_person(client, monkeypatch):
    def blow_up(model):
        raise providers.ProviderError(key="prov_err_ollama_down")

    monkeypatch.setattr(providers, "delete_model", blow_up)
    cfg = config.load()
    cfg["ui"]["lang"] = "en"
    config.save(cfg)

    r = client.post("/models/delete", data={"model": "gemma2:9b", "back": "/models"},
                    follow_redirects=False)

    assert "Ollama" in r.headers["location"], r.headers["location"]


def test_the_delete_button_is_on_the_downloaded_one_and_not_the_rest(client, monkeypatch):
    from jobsearch import i18n
    monkeypatch.setattr(providers, "ollama_installed_models", lambda: {"gemma2:9b"})
    cfg = config.load()
    cfg["ui"]["lang"] = "en"
    cfg["llm"].update(provider="ollama", triage_model="gemma2:9b")
    config.save(cfg)

    html = client.get("/models").text

    downloaded = model_row(html, "gemma2:9b")
    not_downloaded = model_row(html, "llama3.1:8b")

    assert "/models/delete" in downloaded, "the downloaded model has no delete button"
    assert i18n.t("en", "models_delete") in downloaded
    assert "/models/delete" not in not_downloaded, "we offered to delete something that is not downloaded"


def test_the_confirmation_does_not_break_on_an_apostrophe(client):
    """The confirmation text travels into confirm(). It used to stand right in
    onsubmit, and an apostrophe — unavoidable in French — broke the string: the
    button quietly stopped both asking and deleting."""
    from jobsearch import config as cfg_mod, i18n
    for lang in i18n.UI_LANGS:
        cfg = cfg_mod.load()
        cfg["ui"]["lang"] = lang
        cfg_mod.save(cfg)

        html = client.get("/").text

        assert "data-confirm=" in html, f"{lang}: no confirmations are left on the page at all"
        assert "confirm('" not in html, \
            f"{lang}: the confirmation text sits inside the code as a string again"
