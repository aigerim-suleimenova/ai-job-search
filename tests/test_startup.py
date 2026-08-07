"""Starting the app must not hang on steps that are optional.

On Windows a person saw a window with `SystemExit: 3` — uvicorn's way of saying
the startup handler raised. What broke was the scheduler; what failed to open was
the whole program: no window, no reason, nothing to use.
"""
import pytest

pytest.importorskip("httpx", reason="TestClient needs httpx")

from fastapi.testclient import TestClient  # noqa: E402

import app as app_module  # noqa: E402


def test_a_failed_scheduler_does_not_stop_the_app_opening(profile, monkeypatch, tmp_path):
    """Exactly the Windows case: the scheduler would not start."""
    monkeypatch.setattr(app_module, "LOG_PATH", tmp_path / "errors.log")

    def blow_up():
        raise RuntimeError("the scheduler would not start")

    monkeypatch.setattr(app_module.scheduler, "start", blow_up)

    with TestClient(app_module.app, base_url="http://127.0.0.1:8765") as client:      # runs the startup handler
        client.cookies.set("profile", profile)
        assert client.get("/").status_code == 200, "the app did not open because of the scheduler"

    log_text = (tmp_path / "errors.log").read_text(encoding="utf-8")
    assert "starting the scheduler" in log_text, "the reason was not written down"
    assert "the scheduler would not start" in log_text


def test_a_failed_profile_migration_does_not_get_in_the_way(profile, monkeypatch, tmp_path):
    monkeypatch.setattr(app_module, "LOG_PATH", tmp_path / "errors.log")
    monkeypatch.setattr(app_module.profiles, "ensure_migrated",
                        lambda: (_ for _ in ()).throw(OSError("read-only disk")))
    with TestClient(app_module.app, base_url="http://127.0.0.1:8765") as client:
        client.cookies.set("profile", profile)
        assert client.get("/simple").status_code == 200
    assert "migrating profiles" in (tmp_path / "errors.log").read_text(encoding="utf-8")


def test_every_step_fails_and_the_program_lives(profile, monkeypatch, tmp_path):
    """The extreme case: nothing optional works at all."""
    monkeypatch.setattr(app_module, "LOG_PATH", tmp_path / "errors.log")
    for name in ("ensure_migrated",):
        monkeypatch.setattr(app_module.profiles, name,
                            lambda: (_ for _ in ()).throw(RuntimeError("no")))
    for name in ("start", "reschedule_all"):
        monkeypatch.setattr(app_module.scheduler, name,
                            lambda: (_ for _ in ()).throw(RuntimeError("no")))
    with TestClient(app_module.app, base_url="http://127.0.0.1:8765") as client:
        client.cookies.set("profile", profile)
        assert client.get("/").status_code == 200

    log_text = (tmp_path / "errors.log").read_text(encoding="utf-8")
    for step in ("migrating profiles", "starting the scheduler", "restoring the schedules"):
        assert step in log_text, f'step "{step}" did not report its failure'


def test_the_version_is_visible_on_the_page(profile, monkeypatch):
    """Someone whose program misbehaves needs a way to name their build."""
    from jobsearch import version
    monkeypatch.setattr(version, "current", lambda: "9.9.9")
    with TestClient(app_module.app, base_url="http://127.0.0.1:8765") as client:
        client.cookies.set("profile", profile)
        html = client.get("/simple").text
    assert "9.9.9" in html, "the version is not shown in the interface"


def test_no_version_file_and_it_still_does_not_fall_over(profile):
    """Running from source: the file is missing, but the page still has to open."""
    from jobsearch import version
    version.current.cache_clear()
    assert version.current()          # a non-empty string, even if only "dev"
    with TestClient(app_module.app, base_url="http://127.0.0.1:8765") as client:
        client.cookies.set("profile", profile)
        assert client.get("/").status_code == 200
