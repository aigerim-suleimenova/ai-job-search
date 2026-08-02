"""Избавиться от программы там, где избавляться нечем.

На Windows есть установщик: он закрывает программу, стирает её файлы, убирает
автозапуск и спрашивает про данные. На macOS и Linux установщика нет вовсе —
образ перетаскивают в корзину, архив стирают. Уходит только сама программа, а
за ней навсегда и молча остаются данные и запись автозапуска: найти их человек
не может, а убрать способна только сама программа — до того, как её удалят.
"""
import pytest

pytest.importorskip("httpx", reason="TestClient требует httpx")

from fastapi.testclient import TestClient  # noqa: E402

from jobsearch import appstate, autostart, config, profiles, removal  # noqa: E402


@pytest.fixture
def client(profile):
    import app as app_module
    with TestClient(app_module.app, base_url="http://127.0.0.1:8765") as c:
        c.cookies.set("profile", profile)
        yield c


# --- Что программа знает о себе -------------------------------------------------

def test_на_windows_удаление_есть_а_на_остальных_нет(monkeypatch):
    """Разница не косметическая: там, где установщик есть, вмешиваться незачем."""
    monkeypatch.setattr(removal.platform, "system", lambda: "Windows")
    assert removal.has_uninstaller() is True
    for система in ("Darwin", "Linux"):
        monkeypatch.setattr(removal.platform, "system", lambda: система)
        assert removal.has_uninstaller() is False, система


def test_на_macos_называется_пакет_а_не_файл_внутри(monkeypatch):
    """В корзину перетаскивают .app целиком. Путь к исполняемому файлу внутри
    него человеку ничего не даёт — он его даже не увидит."""
    monkeypatch.setattr(removal.platform, "system", lambda: "Darwin")
    monkeypatch.setattr(removal.sys, "frozen", True, raising=False)
    monkeypatch.setattr(removal.sys, "executable",
                        "/Applications/AI Job Search.app/Contents/MacOS/AI Job Search")

    assert removal.program_path() == "/Applications/AI Job Search.app"


def test_из_исходников_удалять_нечего(monkeypatch):
    """Там лежит то, что разработчик открыл у себя, а не установленная программа."""
    monkeypatch.delattr(removal.sys, "frozen", raising=False)
    assert removal.program_path() == ""


def test_в_остатках_названы_пути_а_не_общие_слова(monkeypatch):
    """«Кое-какие файлы» человеку не помогут: он не знает ни где они, ни что они есть."""
    monkeypatch.setattr(removal.platform, "system", lambda: "Linux")
    остатки = removal.leftovers()
    assert str(profiles.DATA_ROOT) in остатки, "не назвали, где лежат данные"
    assert all(str(p).strip() for p in остатки), "в списке есть пустая строка"


def test_автозапуск_попадает_в_остатки_только_когда_он_есть(monkeypatch, tmp_path):
    monkeypatch.setattr(removal.platform, "system", lambda: "Linux")
    файл = tmp_path / "ai-job-search.desktop"
    monkeypatch.setattr(autostart, "_linux_desktop_path", lambda: файл)

    assert removal.autostart_path() == "", "назвали запись, которой нет"
    файл.write_text("[Desktop Entry]", encoding="utf-8")
    assert removal.autostart_path() == str(файл)
    assert str(файл) in removal.leftovers()


# --- Что она делает по кнопке ---------------------------------------------------

def test_убирает_за_собой_всё_кроме_себя(client, profile, monkeypatch):
    выключен = []
    monkeypatch.setattr(autostart, "set_enabled", lambda on: выключен.append(on) or "")
    import app as app_module
    monkeypatch.setattr(app_module.autostart, "set_enabled", autostart.set_enabled)
    config.save(config.load())
    assert (profiles.PROFILES_DIR / profile / "config.json").exists()

    r = client.post("/app/uninstall", follow_redirects=False)

    assert r.status_code == 303
    assert выключен == [False], "автозапуск не выключили"
    assert not (profiles.PROFILES_DIR / profile).exists(), "данные человека остались"
    assert not appstate.setup_done(), "отметка о знакомстве осталась"


def test_говорит_что_осталось_убрать_руками(client, monkeypatch):
    """Съесть себя программа не может — но сказать, что именно осталось, обязана."""
    from urllib.parse import unquote
    monkeypatch.setattr(removal, "program_path", lambda: "/Applications/AI Job Search.app")
    import app as app_module
    monkeypatch.setattr(app_module.removal, "program_path", removal.program_path)
    cfg = config.load()
    cfg["ui"]["lang"] = "en"
    config.save(cfg)

    r = client.post("/app/uninstall", follow_redirects=False)

    сообщение = unquote(r.headers["location"])
    assert "/Applications/AI Job Search.app" in сообщение, \
        f"не сказали, что удалять: {сообщение}"


def test_во_время_поиска_не_убираем(client):
    """Прогон пишет в те самые файлы: часть из них молча вернулась бы обратно."""
    from jobsearch import pipeline
    config.save(config.load())
    pipeline.state["running"] = True
    try:
        r = client.post("/app/uninstall", follow_redirects=False)
    finally:
        pipeline.state["running"] = False

    assert r.headers["location"].startswith("/?"), "не объяснили, почему не убрали"
    assert profiles.list_profiles(), "всё-таки убрали посреди поиска"


# --- Что видно на странице ------------------------------------------------------

def test_где_есть_установщик_кнопку_не_показываем(client, monkeypatch):
    """Дублировать работу установщика незачем — там достаточно сказать, где он."""
    import app as app_module
    monkeypatch.setattr(app_module.removal, "has_uninstaller", lambda: True)

    html = client.get("/").text

    assert 'action="/app/uninstall"' not in html, "предложили лишнее там, где есть своё удаление"
    assert 'id="uninstall"' in html, "про удаление не сказали вовсе"


def test_где_установщика_нет_называем_пути(client, monkeypatch):
    import app as app_module
    monkeypatch.setattr(app_module.removal, "has_uninstaller", lambda: False)
    monkeypatch.setattr(app_module.removal, "program_path", lambda: "/opt/AI Job Search")

    html = client.get("/").text

    assert 'action="/app/uninstall"' in html
    assert "/opt/AI Job Search" in html, "не сказали, где сама программа"
    assert str(profiles.DATA_ROOT) in html, "не сказали, где данные"
