"""Starting the program: a message about trouble must not become the trouble.

On Windows the console runs in cp1252, and a Russian string in print brought the
program down with an encoding error — on top of the real reason, which the person
never got to see.
"""
import io
import os
import sys
import types
from pathlib import Path

import pytest

import desktop


def cp1252_поток():
    """A strict stream in the Windows encoding — the same as the console there."""
    return io.TextIOWrapper(io.BytesIO(), encoding="cp1252", errors="strict")


def test_поток_переводится_в_utf8():
    поток = cp1252_поток()
    assert поток.encoding == "cp1252"
    desktop._make_output_safe(поток)
    assert поток.encoding.lower().replace("-", "") == "utf8"


def test_после_перевода_кириллица_печатается():
    """Exactly the case that brought the program down for someone on Windows."""
    поток = cp1252_поток()
    desktop._make_output_safe(поток)
    print("Внутренний сервер не запустился", file=поток)   # this used to fall over
    поток.flush()


def test_кириллица_в_cp1252_действительно_роняет():
    """Checking the check: without the conversion the stream must raise — otherwise
    the test above proves nothing."""
    поток = cp1252_поток()
    with pytest.raises(UnicodeEncodeError):
        print("Внутренний сервер не запустился", file=поток)
        поток.flush()


def test_отсутствующий_поток_не_ломает():
    """In a windowed PyInstaller build stdout can be None."""
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
    """An exception in the server thread used to vanish without a trace."""
    monkeypatch.setattr(desktop, "_state_dir", lambda: tmp_path)
    monkeypatch.setattr(desktop, "_server_error", "")

    def взорваться(*_a, **_kw):
        raise RuntimeError("порт занят антивирусом")

    monkeypatch.setattr(desktop.uvicorn, "Config", взорваться)
    desktop._serve(12345)

    assert "порт занят антивирусом" in desktop._server_error, "причина потерялась"
    assert "порт занят антивирусом" in (tmp_path / "errors.log").read_text(encoding="utf-8")


# --- The window that would not open --------------------------------------------
#
# The portable Windows build died on opening its window: libraries unpacked from a
# downloaded archive are marked as "from the internet", and .NET refused to load
# Python.Runtime.dll, without which pywebview cannot draw a window at all.


class _Событие(list):
    """A pywebview subscription: window.events.shown += handler."""

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
    """The mark lives in a separate file stream — "name.dll:Zone.Identifier". On
    Linux a colon is a legal character in a name, so the same code is exercised here."""
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
    """For anyone whose window opens, nothing at all should change."""
    (tmp_path / "Python.Runtime.dll").write_bytes(b"")
    monkeypatch.setattr(desktop.sys, "_MEIPASS", str(tmp_path), raising=False)
    monkeypatch.setattr(desktop.sys, "platform", "win32")
    среда = {}
    monkeypatch.setattr(desktop.os, "environ", среда)

    desktop._prepare_windows_gui()

    assert среда == {}, "настройку .NET подложили там, где она не нужна"


def test_несъёмная_метка_разрешается_настройкой(tmp_path, monkeypatch):
    """The install folder is sometimes read-only. Then all that is left is to grant
    the marked libraries trust by configuration, without touching them on disk."""
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
    """The program used to simply die here with "Unhandled exception in script"."""
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
    """The fallback must not fire for people who do have a window."""
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


def test_экран_аварии_на_языке_интерфейса(profile, tmp_path, monkeypatch):
    """The window with the reason is shown before the server — and was in Russian
    whatever language had been chosen.

    Настраивается профиль по умолчанию, а не свой: экран рисуется до сервера,
    когда профиль ещё никем не выбран, и язык читается именно оттуда. Тест
    задавал язык своему профилю, а проверял чужой.

    А язык системы подменён нарочно, и подменён на русский: пока он совпадал с
    ожидаемым ответом, тест проходил, ни разу не заглянув в настройки — на
    английской машине он был зелёным и при коде, который читал бы только
    систему. Ровно ту беду, ради которой он написан, он и не поймал бы.
    """
    from jobsearch import config, i18n, profiles
    monkeypatch.setattr(desktop, "_state_dir", lambda: tmp_path)
    monkeypatch.setattr(profiles, "default_slug", lambda: profile)
    monkeypatch.setattr(i18n, "system_lang", lambda default="en": "ru")
    cfg = config.load()
    cfg["ui"]["lang"] = "en"
    config.save(cfg)
    monkeypatch.setattr(desktop.webview, "create_window", _не_дать_окна)
    открыто = []
    monkeypatch.setattr(desktop.webbrowser, "open", открыто.append)

    desktop._show_failure("Traceback: boom")

    страница = (tmp_path / "error.html").read_text(encoding="utf-8")
    assert "could not start" in страница, "заголовок не на языке интерфейса"
    assert "не смог запуститься" not in страница
    assert открыто, "страницу с причиной не показали"


def test_без_настроек_экран_аварии_не_ломается(tmp_path, monkeypatch):
    """There may be no settings at all — the message about trouble still has to come out."""
    monkeypatch.setattr(desktop, "_state_dir", lambda: tmp_path)
    monkeypatch.setattr(desktop, "_ui_lang", _не_дать_окна)
    assert desktop._say("crash_title", app="X") == ""
