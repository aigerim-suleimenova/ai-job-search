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


def cp1252_stream():
    """A strict stream in the Windows encoding — the same as the console there."""
    return io.TextIOWrapper(io.BytesIO(), encoding="cp1252", errors="strict")


def test_the_stream_is_converted_to_utf8():
    stream = cp1252_stream()
    assert stream.encoding == "cp1252"
    desktop._make_output_safe(stream)
    assert stream.encoding.lower().replace("-", "") == "utf8"


def test_after_conversion_cyrillic_prints():
    """Exactly the case that brought the program down for someone on Windows."""
    stream = cp1252_stream()
    desktop._make_output_safe(stream)
    print("Внутренний сервер не запустился", file=stream)   # this used to fall over
    stream.flush()


def test_cyrillic_in_cp1252_really_does_fall_over():
    """Checking the check: without the conversion the stream must raise — otherwise
    the test above proves nothing."""
    stream = cp1252_stream()
    with pytest.raises(UnicodeEncodeError):
        print("Внутренний сервер не запустился", file=stream)
        stream.flush()


def test_a_missing_stream_breaks_nothing():
    """In a windowed PyInstaller build stdout can be None."""
    desktop._make_output_safe(None, object())


def test_the_reason_reaches_the_log(profile, tmp_path, monkeypatch):
    monkeypatch.setattr(desktop, "_state_dir", lambda: tmp_path)
    desktop._log_crash("A check", "Traceback (most recent call last): ...")
    log_path = tmp_path / "errors.log"
    assert log_path.exists(), "the log was not created"
    text = log_path.read_text(encoding="utf-8")
    assert "A check" in text
    assert "Traceback" in text


def test_the_log_is_appended_to_not_overwritten(profile, tmp_path, monkeypatch):
    monkeypatch.setattr(desktop, "_state_dir", lambda: tmp_path)
    desktop._log_crash("First", "one")
    desktop._log_crash("Second", "two")
    text = (tmp_path / "errors.log").read_text(encoding="utf-8")
    assert "First" in text and "Second" in text, "the previous entry was wiped"


def test_a_failed_server_leaves_the_reason_behind(profile, tmp_path, monkeypatch):
    """An exception in the server thread used to vanish without a trace."""
    monkeypatch.setattr(desktop, "_state_dir", lambda: tmp_path)
    monkeypatch.setattr(desktop, "_server_error", "")

    def blow_up(*_a, **_kw):
        raise RuntimeError("the port is taken by an antivirus")

    monkeypatch.setattr(desktop.uvicorn, "Config", blow_up)
    desktop._serve(12345)

    assert "the port is taken by an antivirus" in desktop._server_error, "the reason was lost"
    assert "the port is taken by an antivirus" in (tmp_path / "errors.log").read_text(encoding="utf-8")


# --- The window that would not open --------------------------------------------
#
# The portable Windows build died on opening its window: libraries unpacked from a
# downloaded archive are marked as "from the internet", and .NET refused to load
# Python.Runtime.dll, without which pywebview cannot draw a window at all.


class _Event(list):
    """A pywebview subscription: window.events.shown += handler."""

    def __iadd__(self, handler):
        self.append(handler)
        return self


class _Window:
    def __init__(self):
        self.events = types.SimpleNamespace(shown=_Event(), closing=_Event())


def _refuse_a_window(*_a, **_kw):
    raise RuntimeError("Failed to resolve Python.Runtime.Loader.Initialize")


def _refuse_to_write(*_a, **_kw):
    raise PermissionError("the install folder is read-only")


def test_the_downloaded_file_mark_is_removed(tmp_path, monkeypatch):
    """The mark lives in a separate file stream — "name.dll:Zone.Identifier". On
    Linux a colon is a legal character in a name, so the same code is exercised here."""
    (tmp_path / "Python.Runtime.dll").write_bytes(b"")
    mark = tmp_path / "Python.Runtime.dll:Zone.Identifier"
    mark.write_text("[ZoneTransfer]\nZoneId=3\n")
    monkeypatch.setattr(desktop.sys, "_MEIPASS", str(tmp_path), raising=False)

    assert desktop._unblock_bundled_libraries() == 0, "the mark could not be removed"
    assert not mark.exists(), "the mark is still there — .NET will refuse to load again"


def test_running_from_source_looks_for_nothing(monkeypatch):
    monkeypatch.delattr(desktop.sys, "_MEIPASS", raising=False)
    assert desktop._unblock_bundled_libraries() == 0


def test_a_clean_build_gets_nothing_slipped_under_it(tmp_path, monkeypatch):
    """For anyone whose window opens, nothing at all should change."""
    (tmp_path / "Python.Runtime.dll").write_bytes(b"")
    monkeypatch.setattr(desktop.sys, "_MEIPASS", str(tmp_path), raising=False)
    monkeypatch.setattr(desktop.sys, "platform", "win32")
    env = {}
    monkeypatch.setattr(desktop.os, "environ", env)

    desktop._prepare_windows_gui()

    assert env == {}, "the .NET configuration was slipped in where it is not needed"


def test_a_mark_that_will_not_come_off_is_settled_by_configuration(tmp_path, monkeypatch):
    """The install folder is sometimes read-only. Then all that is left is to grant
    the marked libraries trust by configuration, without touching them on disk."""
    bundle = tmp_path / "bundle"
    bundle.mkdir()
    (bundle / "Python.Runtime.dll").write_bytes(b"")
    (bundle / "Python.Runtime.dll:Zone.Identifier").write_text("[ZoneTransfer]")
    monkeypatch.setattr(desktop.sys, "_MEIPASS", str(bundle), raising=False)
    monkeypatch.setattr(desktop.sys, "platform", "win32")
    monkeypatch.setattr(desktop, "_state_dir", lambda: tmp_path)
    monkeypatch.setattr(desktop.os, "remove", _refuse_to_write)
    env = {}
    monkeypatch.setattr(desktop.os, "environ", env)

    desktop._prepare_windows_gui()

    config_path = Path(env["PYTHONNET_NETFX_CONFIG_FILE"])
    assert "loadFromRemoteSources" in config_path.read_text(encoding="utf-8")


def test_off_windows_we_do_not_interfere(tmp_path, monkeypatch):
    monkeypatch.setattr(desktop.sys, "platform", "linux")
    monkeypatch.setattr(desktop.sys, "_MEIPASS", str(tmp_path), raising=False)
    env = {}
    monkeypatch.setattr(desktop.os, "environ", env)

    desktop._prepare_windows_gui()

    assert env == {}


def test_with_no_window_the_program_opens_in_a_browser(tmp_path, monkeypatch):
    """The program used to simply die here with "Unhandled exception in script"."""
    monkeypatch.setattr(desktop, "_state_dir", lambda: tmp_path)
    monkeypatch.setattr(desktop, "_window_shown", False)
    monkeypatch.setattr(desktop.webview, "create_window", _refuse_a_window)
    opened = []
    monkeypatch.setattr(desktop.webbrowser, "open", opened.append)

    desktop._open_window(8765, own_server=False)

    assert opened == ["http://127.0.0.1:8765/simple"], "the tab was not opened"
    log_path = (tmp_path / "errors.log").read_text(encoding="utf-8")
    assert "Python.Runtime.Loader.Initialize" in log_path, "the reason was lost"


def test_a_working_window_is_left_alone(tmp_path, monkeypatch):
    """The fallback must not fire for people who do have a window."""
    monkeypatch.setattr(desktop, "_state_dir", lambda: tmp_path)
    window = _Window()
    monkeypatch.setattr(desktop.webview, "create_window", lambda *a, **kw: window)
    monkeypatch.setattr(desktop.webview, "start", lambda *a, **kw: None)
    opened = []
    monkeypatch.setattr(desktop.webbrowser, "open", opened.append)

    desktop._open_window(8765, own_server=True)

    assert opened == [], "an extra tab was opened over a working window"
    assert desktop._on_closing in window.events.closing, "the window closing is no longer tracked"
    assert not (tmp_path / "errors.log").exists(), "a working window was logged as trouble"


def test_the_crash_screen_is_in_the_interface_language(profile, tmp_path, monkeypatch):
    """The window with the reason is shown before the server — and was in Russian
    whatever language had been chosen.

    It is the default profile that gets configured, not one of our own: the screen
    is drawn before the server, while no profile has been chosen by anyone, and
    that is where the language is read from. The test used to set the language on
    its own profile and check somebody else's.

    And the system language is stubbed on purpose, and stubbed to Russian: while
    it matched the expected answer, the test passed without once looking at the
    settings — on an English machine it stayed green even for code that read only
    the system. It would have missed the very trouble it was written for.
    """
    from jobsearch import config, i18n, profiles
    monkeypatch.setattr(desktop, "_state_dir", lambda: tmp_path)
    monkeypatch.setattr(profiles, "default_slug", lambda: profile)
    monkeypatch.setattr(i18n, "system_lang", lambda default="en": "ru")
    cfg = config.load()
    cfg["ui"]["lang"] = "en"
    config.save(cfg)
    monkeypatch.setattr(desktop.webview, "create_window", _refuse_a_window)
    opened = []
    monkeypatch.setattr(desktop.webbrowser, "open", opened.append)

    desktop._show_failure("Traceback: boom")

    page = (tmp_path / "error.html").read_text(encoding="utf-8")
    assert "could not start" in page, "the heading is not in the interface language"
    assert "не смог запуститься" not in page
    assert opened, "the page with the reason was not shown"


def test_with_no_settings_the_crash_screen_still_works(tmp_path, monkeypatch):
    """There may be no settings at all — the message about trouble still has to come out."""
    monkeypatch.setattr(desktop, "_state_dir", lambda: tmp_path)
    monkeypatch.setattr(desktop, "_ui_lang", _refuse_a_window)
    assert desktop._say("crash_title", app="X") == ""


# --- "The program is already running" — but is it ours, and is it alive? ------

def test_we_attach_only_to_a_copy_of_our_own_age(monkeypatch):
    """That very failure: the window opened onto "connection refused".

    The installer closes the old copy and starts the new one right away. The new
    one found the old still alive, decided "the program is already running", opened
    a window onto its port and never brought up its own server. The old one
    finished up and died — and the window ran into emptiness. The check looked only
    at whether anybody was listening on the port.
    """
    from jobsearch import version
    monkeypatch.setattr(version, "current", lambda: "0.8.23")

    def answering(version):
        import io, json as _json
        class R:
            def read(self, n=None):
                return _json.dumps({"app": "ai-job-search", "version": version}).encode()
            def __enter__(self): return self
            def __exit__(self, *a): return False
        return lambda url, timeout=None: R()

    # the old copy, the one being closed now
    monkeypatch.setattr(desktop.urllib.request, "urlopen", answering("0.8.22"))
    assert desktop._alive(8765) is False, "we attached to a copy that is being closed"

    # a copy of our own age — attaching to that one is right
    monkeypatch.setattr(desktop.urllib.request, "urlopen", answering("0.8.23"))
    assert desktop._alive(8765) is True, "we did not recognise our own running copy"


def test_another_program_on_the_port_does_not_count_as_ours(monkeypatch):
    """Anything at all may be sitting on 8765 — the window would open straight into it."""
    class R:
        def read(self, n=None):
            return b'{"hello": "i am something else"}'
        def __enter__(self): return self
        def __exit__(self, *a): return False

    monkeypatch.setattr(desktop.urllib.request, "urlopen", lambda url, timeout=None: R())
    assert desktop._alive(8765) is False


def test_a_dead_lock_does_not_stop_the_start(monkeypatch):
    """The lock is left by a copy that died badly. It used to be simply wrong; now it
    is harmless."""
    def refuse(url, timeout=None):
        raise OSError("connection refused")

    monkeypatch.setattr(desktop.urllib.request, "urlopen", refuse)
    assert desktop._alive(8765) is False
    assert desktop._alive(0) is False


def test_a_non_json_answer_does_not_fall_over(monkeypatch):
    class R:
        def read(self, n=None):
            return b"<html>404</html>"
        def __enter__(self): return self
        def __exit__(self, *a): return False

    monkeypatch.setattr(desktop.urllib.request, "urlopen", lambda url, timeout=None: R())
    assert desktop._alive(8765) is False


# --- Closing the program ------------------------------------------------------
#
# "It does not close on Ctrl+C, I had to kill it by hand" — and so it was. Without
# a window of its own the program waits in a sleep, and catching the interrupt
# there meant not "close" but "come out of this sleep": it then went off to wait in
# the next one, and to all appearances nothing happened.


def _interrupt(*_a, **_kw):
    """Ctrl+C at exactly the point where the program waits."""
    raise KeyboardInterrupt


def test_ctrl_c_with_no_window_closes_the_program(tmp_path, monkeypatch):
    """That very case: no window, the program living as a browser tab."""
    monkeypatch.setattr(desktop, "_state_dir", lambda: tmp_path)
    monkeypatch.setattr(desktop.webbrowser, "open", lambda url: None)
    monkeypatch.setattr(desktop.time, "sleep", _interrupt)
    wound_down = []
    monkeypatch.setattr(desktop, "_shutdown", lambda: wound_down.append(True))

    with pytest.raises(SystemExit):
        desktop._open_in_browser(8765, own_server=True)

    assert wound_down, "the program did not wind down — Ctrl+C went to waste again"


def test_the_stop_signals_are_caught(monkeypatch):
    """Ctrl+C and kill mean the same as a closed window."""
    caught = []
    monkeypatch.setattr(desktop.signal, "signal",
                        lambda sig, handler: caught.append(sig))

    desktop._install_stop_signals()

    assert desktop.signal.SIGINT in caught, "Ctrl+C was left without a handler"
    assert desktop.signal.SIGTERM in caught, "kill was left without a handler"


def test_a_refused_signal_subscription_does_not_stop_the_start(monkeypatch):
    """signal.signal complains if called from anywhere but the main thread, and not
    every system has every signal. Closing nicely is no reason not to open."""
    def refuse_signal(*_a):
        raise ValueError("signal only works in main thread")

    monkeypatch.setattr(desktop.signal, "signal", refuse_signal)
    desktop._install_stop_signals()


def test_the_shutdown_has_a_safety_net(tmp_path, monkeypatch):
    """All our threads are daemons, and leaving still does not depend on us alone:
    the server, the scheduler pool and the web framework's own threads are all
    entitled to hold the process up."""
    monkeypatch.setattr(desktop, "_state_dir", lambda: tmp_path)
    armed = []
    monkeypatch.setattr(desktop, "_force_exit_later", lambda: armed.append(True))

    desktop._shutdown()

    assert armed, "the exit has no safety net — the program can hang again"


def test_the_exit_watchdog_does_not_hold_the_program_itself(monkeypatch):
    """A non-daemon watchdog would be the very trouble it is there to prevent."""
    started = []

    class _Timer:
        def __init__(self, seconds, what):
            self.seconds, self.what, self.daemon = seconds, what, False

        def start(self):
            started.append(self)

    monkeypatch.setattr(desktop.threading, "Timer", _Timer)

    desktop._force_exit_later()

    assert started, "the watchdog was not started"
    assert started[0].daemon, "the watchdog will hold the exit itself"
    assert started[0].seconds <= 10, "nobody will wait that long"
