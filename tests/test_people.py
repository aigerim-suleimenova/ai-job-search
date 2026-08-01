"""People: a name gets edited rather than turning into a second person.

The "Who are we searching for" field is filled with the current person's name —
and looks like something you may correct. Editing it used to create another one,
with an empty history, while the first stayed hanging in the list. The person had
meant to rename themselves.
"""
import pytest

pytest.importorskip("httpx", reason="TestClient требует httpx")

from fastapi.testclient import TestClient  # noqa: E402

import app as app_module  # noqa: E402
from jobsearch import db, profiles  # noqa: E402


@pytest.fixture
def отдельный_дом(tmp_path, monkeypatch):
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
def client(отдельный_дом):
    with TestClient(app_module.app) as c:
        c.cookies.set("profile", отдельный_дом)
        yield c


def имена():
    return sorted(p["name"] for p in profiles.list_profiles())


def test_кнопка_в_шапке_переименовывает(client, отдельный_дом):
    client.post("/profile/rename", data={"name": "Viktor Lavrov"}, follow_redirects=False)
    assert имена() == ["Viktor Lavrov"]


def test_пустое_имя_не_стирает_прежнее(client, отдельный_дом):
    client.post("/profile/rename", data={"name": "   "}, follow_redirects=False)
    assert имена() == ["Я"]


def test_единственный_человек_переименовывается_а_не_удваивается(client):
    """The very case: one person in the list, and they type their own name into it."""
    client.post("/simple/start", data={"person": "Viktor Lavrov", "locations": "EU"},
                follow_redirects=False)
    assert имена() == ["Viktor Lavrov"], "вместо переименования появился второй человек"


def test_когда_людей_несколько_имя_заводит_нового(client):
    """With several people the field means "who are we searching for now"."""
    profiles.create("Друг")
    client.post("/simple/start", data={"person": "Коллега", "locations": "EU"},
                follow_redirects=False)
    assert имена() == ["Друг", "Коллега", "Я"]


def test_знакомое_имя_переключает_а_не_дублирует(client):
    друг = profiles.create("Друг")
    r = client.post("/simple/start", data={"person": "друг", "locations": "EU"},
                    follow_redirects=False)
    assert имена() == ["Друг", "Я"], "имя в другом регистре создало двойника"
    # the cookie is set by a header; httpx does not put it into r.cookies on a 303
    поставлено = r.headers.get("set-cookie", "")
    assert f"profile={друг}" in поставлено, f"не переключились на существующего: {поставлено}"
