"""Запуск программы: сообщение о беде не должно становиться бедой.

На Windows консоль работает в cp1252, и русская строка в print роняла программу
кодировочной ошибкой — поверх настоящей причины, которую человек так и не видел.
"""
import io
import sys

import pytest

import desktop


def cp1252_поток():
    """Строгий поток в кодировке Windows — такой же, как консоль там."""
    return io.TextIOWrapper(io.BytesIO(), encoding="cp1252", errors="strict")


def test_поток_переводится_в_utf8():
    поток = cp1252_поток()
    assert поток.encoding == "cp1252"
    desktop._make_output_safe(поток)
    assert поток.encoding.lower().replace("-", "") == "utf8"


def test_после_перевода_кириллица_печатается():
    """Ровно тот случай, который ронял программу у человека на Windows."""
    поток = cp1252_поток()
    desktop._make_output_safe(поток)
    print("Внутренний сервер не запустился", file=поток)   # раньше здесь падало
    поток.flush()


def test_кириллица_в_cp1252_действительно_роняет():
    """Проверка самой проверки: без перевода поток обязан выбросить ошибку —
    иначе тест выше ничего не доказывает."""
    поток = cp1252_поток()
    with pytest.raises(UnicodeEncodeError):
        print("Внутренний сервер не запустился", file=поток)
        поток.flush()


def test_отсутствующий_поток_не_ломает():
    """В оконной сборке PyInstaller stdout может быть None."""
    desktop._make_output_safe(None, object())


def test_причина_попадает_в_журнал(profile, tmp_path, monkeypatch):
    monkeypatch.setattr(desktop, "_state_dir", lambda: tmp_path)
    desktop._log_crash("Проверка", "Traceback (most recent call last): ...")
    журнал = tmp_path / "errors.log"
    assert журнал.exists(), "журнал не создан"
    текст = журнал.read_text(encoding="utf-8")
    assert "Проверка" in текст
    assert "Traceback" in текст


def test_журнал_дописывается_а_не_перезаписывается(profile, tmp_path, monkeypatch):
    monkeypatch.setattr(desktop, "_state_dir", lambda: tmp_path)
    desktop._log_crash("Первая", "раз")
    desktop._log_crash("Вторая", "два")
    текст = (tmp_path / "errors.log").read_text(encoding="utf-8")
    assert "Первая" in текст and "Вторая" in текст, "предыдущая запись затёрлась"


def test_упавший_сервер_оставляет_причину(profile, tmp_path, monkeypatch):
    """Раньше исключение в потоке сервера пропадало бесследно."""
    monkeypatch.setattr(desktop, "_state_dir", lambda: tmp_path)
    monkeypatch.setattr(desktop, "_server_error", "")

    def взорваться(*_a, **_kw):
        raise RuntimeError("порт занят антивирусом")

    monkeypatch.setattr(desktop.uvicorn, "Config", взорваться)
    desktop._serve(12345)

    assert "порт занят антивирусом" in desktop._server_error, "причина потерялась"
    assert "порт занят антивирусом" in (tmp_path / "errors.log").read_text(encoding="utf-8")
