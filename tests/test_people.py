"""People: a name gets edited rather than turning into a second person.

The "Who are we searching for" field is filled with the current person's name —
and looks like something you may correct. Editing it used to create another one,
with an empty history, while the first stayed hanging in the list. The person had
meant to rename themselves.
"""
import pytest

pytest.importorskip("httpx", reason="TestClient needs httpx")

from fastapi.testclient import TestClient  # noqa: E402

import app as app_module  # noqa: E402
from jobsearch import db, profiles  # noqa: E402


@pytest.fixture
def own_home(tmp_path, monkeypatch):
    """Its own data directory per test: otherwise "there is only one person" would
    never hold — profiles from earlier tests stay in the shared directory."""
    monkeypatch.setattr(profiles, "DATA_ROOT", tmp_path)
    monkeypatch.setattr(profiles, "PROFILES_DIR", tmp_path / "profiles")
    monkeypatch.setattr(profiles, "REGISTRY_PATH", tmp_path / "profiles.json")
    slug = profiles.create("Я")
    profiles.set_active(slug)
    db.init()
    return slug


@pytest.fixture
def client(own_home):
    with TestClient(app_module.app, base_url="http://127.0.0.1:8765") as c:
        c.cookies.set("profile", own_home)
        yield c


def names():
    return sorted(p["name"] for p in profiles.list_profiles())


def test_the_header_button_renames(client, own_home):
    client.post("/profile/rename", data={"name": "Viktor Lavrov"}, follow_redirects=False)
    assert names() == ["Viktor Lavrov"]


def test_an_empty_name_does_not_wipe_the_old_one(client, own_home):
    client.post("/profile/rename", data={"name": "   "}, follow_redirects=False)
    assert names() == ["Я"]


def test_the_only_person_is_renamed_rather_than_duplicated(client):
    """The very case: one person in the list, and they type their own name into it."""
    client.post("/simple/start", data={"person": "Viktor Lavrov", "locations": "EU"},
                follow_redirects=False)
    assert names() == ["Viktor Lavrov"], "a second person appeared instead of a rename"


def test_with_several_people_a_name_creates_a_new_one(client):
    """With several people the field means "who are we searching for now"."""
    profiles.create("Друг")
    client.post("/simple/start", data={"person": "Коллега", "locations": "EU"},
                follow_redirects=False)
    assert names() == ["Друг", "Коллега", "Я"]


def test_a_familiar_name_switches_rather_than_duplicates(client):
    friend = profiles.create("Друг")
    # deliberately lower-case: this is the case-insensitivity being checked
    r = client.post("/simple/start", data={"person": "друг", "locations": "EU"},
                    follow_redirects=False)
    assert names() == ["Друг", "Я"], "the same name in another case created a twin"
    # the cookie is set by a header; httpx does not put it into r.cookies on a 303
    cookie_set = r.headers.get("set-cookie", "")
    assert f"profile={friend}" in cookie_set, f"we did not switch to the existing one: {cookie_set}"


# --- Changing the default name ------------------------------------------------

def registry(monkeypatch, tmp_path, rows: list):
    import json
    monkeypatch.setattr(profiles, "DATA_ROOT", tmp_path)
    monkeypatch.setattr(profiles, "PROFILES_DIR", tmp_path / "profiles")
    monkeypatch.setattr(profiles, "REGISTRY_PATH", tmp_path / "profiles.json")
    (tmp_path / "profiles.json").write_text(
        json.dumps({"profiles": rows, "default": rows[0]["slug"]}, ensure_ascii=False),
        encoding="utf-8")
    return lambda: {p["slug"]: p["name"] for p in profiles.list_profiles()}


@pytest.mark.parametrize("old_name", ["Я", "Me", "Ich", "我", "أنا"])
def test_a_name_we_handed_out_gets_renamed(monkeypatch, tmp_path, old_name):
    """Without this, changing the default is invisible to everyone except people
    installing for the first time: the name is written into profiles.json on the
    first run."""
    names_now = registry(monkeypatch, tmp_path, [{"slug": "me", "name": old_name}])

    profiles.rename_auto_defaults()

    assert names_now() == {"me": "user"}


def test_a_name_of_their_own_is_left_alone(monkeypatch, tmp_path):
    names_now = registry(monkeypatch, tmp_path, [{"slug": "me", "name": "Виктор"}])
    profiles.rename_auto_defaults()
    assert names_now() == {"me": "Виктор"}


def test_another_profile_called_ya_stays(monkeypatch, tmp_path):
    """The name alone is not enough to go on: the person could have typed «Я»
    themselves. The program creates exactly one profile, with the slug "me", and
    that is what we confine ourselves to."""
    names_now = registry(monkeypatch, tmp_path,
                    [{"slug": "me", "name": "Я"}, {"slug": "ya", "name": "Я"}])

    profiles.rename_auto_defaults()

    assert names_now() == {"me": "user", "ya": "Я"}


def test_a_second_startup_rewrites_nothing(monkeypatch, tmp_path):
    """The renaming runs on every startup — so it has to stay silent when there is
    nothing to rename."""
    registry(monkeypatch, tmp_path, [{"slug": "me", "name": "user"}])
    profiles.rename_auto_defaults()
    before = profiles.REGISTRY_PATH.stat().st_mtime_ns

    profiles.rename_auto_defaults()

    assert profiles.REGISTRY_PATH.stat().st_mtime_ns == before, "the file was rewritten for nothing"


def test_a_person_can_be_added_from_the_header(client):
    """The option existed, but it lived on the settings page, in a "Managing
    people" card: to add a second one you first had to think of leaving the very
    page where you choose people, for another."""
    from jobsearch import appstate, config
    appstate.mark_setup_done(config.load()["llm"])   # otherwise we get taken to the introduction
    page = client.get("/").text
    header = page[page.index("<header"):page.index("</header>")]
    assert 'action="/profile/create"' in header, "a person cannot be added from the header"
    assert "addPerson" in page, "there is a button, but nothing to ask the name with"


def test_the_header_button_really_does_add_one(client, own_home):
    client.post("/profile/create", data={"name": "Пётр"}, follow_redirects=False)
    assert "Пётр" in names()
