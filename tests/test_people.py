"""Люди: имя правится, а не превращается во второго человека.

Поле «Для кого ищем» подставляет имя нынешнего человека — и выглядит как то,
что можно поправить. Раньше правка заводила ещё одного, с пустой историей,
а прежний оставался висеть в списке. Человек хотел переименовать себя.
"""
import pytest

pytest.importorskip("httpx", reason="TestClient требует httpx")

from fastapi.testclient import TestClient  # noqa: E402

import app as app_module  # noqa: E402
from jobsearch import db, profiles  # noqa: E402


@pytest.fixture
def отдельный_дом(tmp_path, monkeypatch):
    """Свой каталог данных на тест: иначе «людей всего один» никогда не выполнится —
    профили от прошлых тестов остаются в общем каталоге."""
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
    """Тот самый случай: в списке один «Я», человек пишет туда своё имя."""
    client.post("/simple/start", data={"person": "Viktor Lavrov", "locations": "EU"},
                follow_redirects=False)
    assert имена() == ["Viktor Lavrov"], "вместо переименования появился второй человек"


def test_когда_людей_несколько_имя_заводит_нового(client):
    """С несколькими людьми поле означает «для кого ищем сейчас»."""
    profiles.create("Друг")
    client.post("/simple/start", data={"person": "Коллега", "locations": "EU"},
                follow_redirects=False)
    assert имена() == ["Друг", "Коллега", "Я"]


def test_знакомое_имя_переключает_а_не_дублирует(client):
    друг = profiles.create("Друг")
    r = client.post("/simple/start", data={"person": "друг", "locations": "EU"},
                    follow_redirects=False)
    assert имена() == ["Друг", "Я"], "имя в другом регистре создало двойника"
    # cookie ставится заголовком; httpx не кладёт его в r.cookies при 303
    поставлено = r.headers.get("set-cookie", "")
    assert f"profile={друг}" in поставлено, f"не переключились на существующего: {поставлено}"
