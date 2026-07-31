"""Запуск приложения не должен зависеть от необязательных шагов.

На Windows человек увидел окно с `SystemExit: 3` — так uvicorn сообщает, что
обработчик запуска приложения бросил исключение. Упало расписание, а не
открылась вся программа: окна нет, причины нет, пользоваться нечем.
"""
import pytest

pytest.importorskip("httpx", reason="TestClient требует httpx")

from fastapi.testclient import TestClient  # noqa: E402

import app as app_module  # noqa: E402


def test_упавшее_расписание_не_мешает_открыться(profile, monkeypatch, tmp_path):
    """Ровно тот случай с Windows: планировщик не завёлся."""
    monkeypatch.setattr(app_module, "LOG_PATH", tmp_path / "errors.log")

    def взорваться():
        raise RuntimeError("планировщик не завёлся")

    monkeypatch.setattr(app_module.scheduler, "start", взорваться)

    with TestClient(app_module.app) as client:      # запускает startup-обработчик
        client.cookies.set("profile", profile)
        assert client.get("/").status_code == 200, "приложение не открылось из-за расписания"

    журнал = (tmp_path / "errors.log").read_text(encoding="utf-8")
    assert "запуск расписания" in журнал, "причина не записана"
    assert "планировщик не завёлся" in журнал


def test_упавший_перенос_профилей_не_мешает(profile, monkeypatch, tmp_path):
    monkeypatch.setattr(app_module, "LOG_PATH", tmp_path / "errors.log")
    monkeypatch.setattr(app_module.profiles, "ensure_migrated",
                        lambda: (_ for _ in ()).throw(OSError("диск только для чтения")))
    with TestClient(app_module.app) as client:
        client.cookies.set("profile", profile)
        assert client.get("/simple").status_code == 200
    assert "перенос профилей" in (tmp_path / "errors.log").read_text(encoding="utf-8")


def test_все_шаги_падают_а_программа_живёт(profile, monkeypatch, tmp_path):
    """Крайний случай: не работает ничего необязательное."""
    monkeypatch.setattr(app_module, "LOG_PATH", tmp_path / "errors.log")
    for имя in ("ensure_migrated",):
        monkeypatch.setattr(app_module.profiles, имя,
                            lambda: (_ for _ in ()).throw(RuntimeError("нет")))
    for имя in ("start", "reschedule_all"):
        monkeypatch.setattr(app_module.scheduler, имя,
                            lambda: (_ for _ in ()).throw(RuntimeError("нет")))
    with TestClient(app_module.app) as client:
        client.cookies.set("profile", profile)
        assert client.get("/").status_code == 200

    журнал = (tmp_path / "errors.log").read_text(encoding="utf-8")
    for шаг in ("перенос профилей", "запуск расписания", "восстановление расписаний"):
        assert шаг in журнал, f"шаг «{шаг}» не отчитался о падении"
