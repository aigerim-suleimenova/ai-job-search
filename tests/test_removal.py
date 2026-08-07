"""Getting rid of the program where there is nothing to get rid of it with.

On Windows there is an installer: it closes the program, wipes its files, removes
the autostart entry and asks about the data. On macOS and Linux there is no
installer at all — the disk image gets dragged to the bin, the archive gets
deleted. Only the program itself goes, and behind it the data and the autostart
entry remain forever and silently: a person cannot find them, and only the
program itself can clear them away — before it is deleted.
"""
import pytest

pytest.importorskip("httpx", reason="TestClient needs httpx")

from fastapi.testclient import TestClient  # noqa: E402

from jobsearch import appstate, autostart, config, profiles, removal  # noqa: E402


@pytest.fixture
def client(profile):
    import app as app_module
    with TestClient(app_module.app, base_url="http://127.0.0.1:8765") as c:
        c.cookies.set("profile", profile)
        yield c


# --- What the program knows about itself --------------------------------------

def test_windows_has_an_uninstaller_and_the_others_do_not(monkeypatch):
    """The difference is not cosmetic: where an installer exists, there is no reason
    to interfere."""
    monkeypatch.setattr(removal.platform, "system", lambda: "Windows")
    assert removal.has_uninstaller() is True
    for system in ("Darwin", "Linux"):
        monkeypatch.setattr(removal.platform, "system", lambda: system)
        assert removal.has_uninstaller() is False, system


def test_on_macos_we_name_the_bundle_not_the_file_inside(monkeypatch):
    """The whole .app is what gets dragged to the bin. The path to the executable
    inside it gives a person nothing — they will never even see it."""
    monkeypatch.setattr(removal.platform, "system", lambda: "Darwin")
    monkeypatch.setattr(removal.sys, "frozen", True, raising=False)
    monkeypatch.setattr(removal.sys, "executable",
                        "/Applications/AI Job Search.app/Contents/MacOS/AI Job Search")

    assert removal.program_path() == "/Applications/AI Job Search.app"


def test_running_from_source_there_is_nothing_to_delete(monkeypatch):
    """What lies there is what the developer checked out, not an installed program."""
    monkeypatch.delattr(removal.sys, "frozen", raising=False)
    assert removal.program_path() == ""


def test_the_leftovers_name_paths_not_vague_words(monkeypatch):
    """"Some files" will not help anyone: they know neither where they are nor that
    they exist."""
    monkeypatch.setattr(removal.platform, "system", lambda: "Linux")
    leftovers = removal.leftovers()
    assert str(profiles.DATA_ROOT) in leftovers, "we did not say where the data lives"
    assert all(str(p).strip() for p in leftovers), "the list has an empty string in it"


def test_autostart_is_listed_only_when_it_exists(monkeypatch, tmp_path):
    monkeypatch.setattr(removal.platform, "system", lambda: "Linux")
    path = tmp_path / "ai-job-search.desktop"
    monkeypatch.setattr(autostart, "_linux_desktop_path", lambda: path)

    assert removal.autostart_path() == "", "we named an entry that does not exist"
    path.write_text("[Desktop Entry]", encoding="utf-8")
    assert removal.autostart_path() == str(path)
    assert str(path) in removal.leftovers()


# --- What it does when the button is pressed ----------------------------------

def test_it_clears_up_everything_but_itself(client, profile, monkeypatch):
    switched_off = []
    monkeypatch.setattr(autostart, "set_enabled", lambda on: switched_off.append(on) or "")
    import app as app_module
    monkeypatch.setattr(app_module.autostart, "set_enabled", autostart.set_enabled)
    config.save(config.load())
    assert (profiles.PROFILES_DIR / profile / "config.json").exists()

    r = client.post("/app/uninstall", follow_redirects=False)

    assert r.status_code == 303
    assert switched_off == [False], "autostart was not switched off"
    assert not (profiles.PROFILES_DIR / profile).exists(), "the person's data is still there"
    assert not appstate.setup_done(), "the setup-done mark is still there"


def test_it_says_what_is_left_to_remove_by_hand(client, monkeypatch):
    """The program cannot eat itself — but it is obliged to say what is left."""
    from urllib.parse import unquote
    monkeypatch.setattr(removal, "program_path", lambda: "/Applications/AI Job Search.app")
    import app as app_module
    monkeypatch.setattr(app_module.removal, "program_path", removal.program_path)
    cfg = config.load()
    cfg["ui"]["lang"] = "en"
    config.save(cfg)

    r = client.post("/app/uninstall", follow_redirects=False)

    message = unquote(r.headers["location"])
    assert "/Applications/AI Job Search.app" in message, \
        f"we did not say what to delete: {message}"


def test_we_do_not_clear_up_during_a_search(client):
    """A run writes into those very files: some of them would quietly come back."""
    from jobsearch import pipeline
    config.save(config.load())
    pipeline.state["running"] = True
    try:
        r = client.post("/app/uninstall", follow_redirects=False)
    finally:
        pipeline.state["running"] = False

    assert r.headers["location"].startswith("/?"), "we did not explain why nothing was cleared"
    assert profiles.list_profiles(), "we cleared up in the middle of a search after all"


# --- What is visible on the page ----------------------------------------------

def test_where_an_uninstaller_exists_we_show_no_button(client, monkeypatch):
    """No reason to duplicate the installer's work — there it is enough to say where
    it is."""
    import app as app_module
    monkeypatch.setattr(app_module.removal, "has_uninstaller", lambda: True)

    html = client.get("/").text

    assert 'action="/app/uninstall"' not in html, "we offered more than needed where an uninstaller exists"
    assert 'id="uninstall"' in html, "we said nothing about removal at all"


def test_where_there_is_no_installer_we_name_the_paths(client, monkeypatch):
    import app as app_module
    monkeypatch.setattr(app_module.removal, "has_uninstaller", lambda: False)
    monkeypatch.setattr(app_module.removal, "program_path", lambda: "/opt/AI Job Search")

    html = client.get("/").text

    assert 'action="/app/uninstall"' in html
    assert "/opt/AI Job Search" in html, "we did not say where the program itself is"
    assert str(profiles.DATA_ROOT) in html, "we did not say where the data is"
