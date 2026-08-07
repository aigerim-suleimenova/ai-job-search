"""Starting the app must not hang on steps that are optional.

On Windows a person saw a window with `SystemExit: 3` — uvicorn's way of saying
the startup handler raised. What broke was the scheduler; what failed to open was
the whole program: no window, no reason, nothing to use.
"""
import pytest

pytest.importorskip("httpx", reason="TestClient требует httpx")

from fastapi.testclient import TestClient  # noqa: E402

import app as app_module  # noqa: E402


def test_упавшее_расписание_не_мешает_открыться(profile, monkeypatch, tmp_path):
    """Exactly the Windows case: the scheduler would not start."""
    monkeypatch.setattr(app_module, "LOG_PATH", tmp_path / "errors.log")

    def взорваться():
        raise RuntimeError("планировщик не завёлся")

    monkeypatch.setattr(app_module.scheduler, "start", взорваться)

    with TestClient(app_module.app, base_url="http://127.0.0.1:8765") as client:      # runs the startup handler
        client.cookies.set("profile", profile)
        assert client.get("/").status_code == 200, "приложение не открылось из-за расписания"

    журнал = (tmp_path / "errors.log").read_text(encoding="utf-8")
    assert "starting the scheduler" in журнал, "причина не записана"
    assert "планировщик не завёлся" in журнал


def test_упавший_перенос_профилей_не_мешает(profile, monkeypatch, tmp_path):
    monkeypatch.setattr(app_module, "LOG_PATH", tmp_path / "errors.log")
    monkeypatch.setattr(app_module.profiles, "ensure_migrated",
                        lambda: (_ for _ in ()).throw(OSError("диск только для чтения")))
    with TestClient(app_module.app, base_url="http://127.0.0.1:8765") as client:
        client.cookies.set("profile", profile)
        assert client.get("/simple").status_code == 200
    assert "migrating profiles" in (tmp_path / "errors.log").read_text(encoding="utf-8")


def test_все_шаги_падают_а_программа_живёт(profile, monkeypatch, tmp_path):
    """The extreme case: nothing optional works at all."""
    monkeypatch.setattr(app_module, "LOG_PATH", tmp_path / "errors.log")
    for имя in ("ensure_migrated",):
        monkeypatch.setattr(app_module.profiles, имя,
                            lambda: (_ for _ in ()).throw(RuntimeError("нет")))
    for имя in ("start", "reschedule_all"):
        monkeypatch.setattr(app_module.scheduler, имя,
                            lambda: (_ for _ in ()).throw(RuntimeError("нет")))
    with TestClient(app_module.app, base_url="http://127.0.0.1:8765") as client:
        client.cookies.set("profile", profile)
        assert client.get("/").status_code == 200

    журнал = (tmp_path / "errors.log").read_text(encoding="utf-8")
    for шаг in ("migrating profiles", "starting the scheduler", "restoring the schedules"):
        assert шаг in журнал, f"шаг «{шаг}» не отчитался о падении"


def test_версия_видна_на_странице(profile, monkeypatch):
    """Someone whose program misbehaves needs a way to name their build."""
    from jobsearch import version
    monkeypatch.setattr(version, "current", lambda: "9.9.9")
    with TestClient(app_module.app, base_url="http://127.0.0.1:8765") as client:
        client.cookies.set("profile", profile)
        html = client.get("/simple").text
    assert "9.9.9" in html, "версия не показана в интерфейсе"


def test_без_файла_версии_не_падает(profile):
    """Running from source: the file is missing, but the page still has to open."""
    from jobsearch import version
    version.current.cache_clear()
    assert version.current()          # a non-empty string, even if only "dev"
    with TestClient(app_module.app, base_url="http://127.0.0.1:8765") as client:
        client.cookies.set("profile", profile)
        assert client.get("/").status_code == 200
