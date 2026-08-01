"""Запуск программы: сообщение о беде не должно становиться бедой.

На Windows консоль работает в cp1252, и русская строка в print роняла программу
кодировочной ошибкой — поверх настоящей причины, которую человек так и не видел.
"""
import io
import os
import sys
import types
from pathlib import Path

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


# --- Окно, которое не открылось ------------------------------------------------
#
# Переносимая сборка под Windows падала при открытии окна: распакованные из
# скачанного архива библиотеки помечены как «из интернета», и .NET отказывался
# грузить Python.Runtime.dll, без которого pywebview не умеет рисовать окно.


class _Событие(list):
    """Подписка pywebview: window.events.shown += обработчик."""

    def __iadd__(self, обработчик):
        self.append(обработчик)
        return self


class _Окно:
    def __init__(self):
        self.events = types.SimpleNamespace(shown=_Событие(), closing=_Событие())


def _не_дать_окна(*_a, **_kw):
    raise RuntimeError("Failed to resolve Python.Runtime.Loader.Initialize")


def _отказать_в_записи(*_a, **_kw):
    raise PermissionError("папка установки только для чтения")


def test_метка_скачанного_файла_снимается(tmp_path, monkeypatch):
    """Метка живёт отдельным потоком файла — «имя.dll:Zone.Identifier». На Linux
    двоеточие в имени законно, поэтому тот же код проверяется и здесь."""
    (tmp_path / "Python.Runtime.dll").write_bytes(b"")
    метка = tmp_path / "Python.Runtime.dll:Zone.Identifier"
    метка.write_text("[ZoneTransfer]\nZoneId=3\n")
    monkeypatch.setattr(desktop.sys, "_MEIPASS", str(tmp_path), raising=False)

    assert desktop._unblock_bundled_libraries() == 0, "метку не удалось снять"
    assert not метка.exists(), "метка осталась — .NET снова откажется грузить"


def test_запуск_из_исходников_ничего_не_ищет(monkeypatch):
    monkeypatch.delattr(desktop.sys, "_MEIPASS", raising=False)
    assert desktop._unblock_bundled_libraries() == 0


def test_чистой_сборке_ничего_не_подкладываем(tmp_path, monkeypatch):
    """У кого окно открывается — у того ничего не должно измениться."""
    (tmp_path / "Python.Runtime.dll").write_bytes(b"")
    monkeypatch.setattr(desktop.sys, "_MEIPASS", str(tmp_path), raising=False)
    monkeypatch.setattr(desktop.sys, "platform", "win32")
    среда = {}
    monkeypatch.setattr(desktop.os, "environ", среда)

    desktop._prepare_windows_gui()

    assert среда == {}, "настройку .NET подложили там, где она не нужна"


def test_несъёмная_метка_разрешается_настройкой(tmp_path, monkeypatch):
    """Папка установки бывает только для чтения. Тогда остаётся выдать
    помеченным библиотекам доверие настройкой, не трогая их на диске."""
    сборка = tmp_path / "bundle"
    сборка.mkdir()
    (сборка / "Python.Runtime.dll").write_bytes(b"")
    (сборка / "Python.Runtime.dll:Zone.Identifier").write_text("[ZoneTransfer]")
    monkeypatch.setattr(desktop.sys, "_MEIPASS", str(сборка), raising=False)
    monkeypatch.setattr(desktop.sys, "platform", "win32")
    monkeypatch.setattr(desktop, "_state_dir", lambda: tmp_path)
    monkeypatch.setattr(desktop.os, "remove", _отказать_в_записи)
    среда = {}
    monkeypatch.setattr(desktop.os, "environ", среда)

    desktop._prepare_windows_gui()

    конфиг = Path(среда["PYTHONNET_NETFX_CONFIG_FILE"])
    assert "loadFromRemoteSources" in конфиг.read_text(encoding="utf-8")


def test_не_на_windows_не_вмешиваемся(tmp_path, monkeypatch):
    monkeypatch.setattr(desktop.sys, "platform", "linux")
    monkeypatch.setattr(desktop.sys, "_MEIPASS", str(tmp_path), raising=False)
    среда = {}
    monkeypatch.setattr(desktop.os, "environ", среда)

    desktop._prepare_windows_gui()

    assert среда == {}


def test_без_окна_программа_открывается_в_браузере(tmp_path, monkeypatch):
    """Раньше здесь программа просто падала с «Unhandled exception in script»."""
    monkeypatch.setattr(desktop, "_state_dir", lambda: tmp_path)
    monkeypatch.setattr(desktop, "_window_shown", False)
    monkeypatch.setattr(desktop.webview, "create_window", _не_дать_окна)
    открыто = []
    monkeypatch.setattr(desktop.webbrowser, "open", открыто.append)

    desktop._open_window(8765, own_server=False)

    assert открыто == ["http://127.0.0.1:8765/simple"], "вкладку не открыли"
    журнал = (tmp_path / "errors.log").read_text(encoding="utf-8")
    assert "Python.Runtime.Loader.Initialize" in журнал, "причина потерялась"


def test_рабочее_окно_браузер_не_трогает(tmp_path, monkeypatch):
    """Запасной ход не должен срабатывать у тех, у кого окно есть."""
    monkeypatch.setattr(desktop, "_state_dir", lambda: tmp_path)
    окно = _Окно()
    monkeypatch.setattr(desktop.webview, "create_window", lambda *a, **kw: окно)
    monkeypatch.setattr(desktop.webview, "start", lambda *a, **kw: None)
    открыто = []
    monkeypatch.setattr(desktop.webbrowser, "open", открыто.append)

    desktop._open_window(8765, own_server=True)

    assert открыто == [], "открыли лишнюю вкладку поверх работающего окна"
    assert desktop._on_closing in окно.events.closing, "закрытие окна перестало отслеживаться"
    assert not (tmp_path / "errors.log").exists(), "рабочее окно записалось как беда"
