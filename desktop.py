"""Launching as an ordinary program: its own window, the server inside, no terminal.

Two ways of closing the window (switched in the settings):
- ordinary: close the window and the app exits;
- background: close the window and the search carries on, while starting the
  program again simply opens a window onto the process already running.

The second one is what makes an uninterrupted search possible: keeping a window
open for days is awkward, and cutting the search short on closing is a shame.
"""
import json
import os
import socket
import sys
import threading
import time
import urllib.request
import webbrowser
from contextlib import closing
from pathlib import Path

import traceback
from datetime import datetime

import uvicorn
import webview

def _make_output_safe(*streams) -> None:
    """The Windows console is not UTF-8 by default: a single non-Latin word in the
    output brings the program down with an encoding error. A message about
    trouble must not become the trouble — we move the streams to UTF-8 and
    replace whatever will not fit."""
    for stream in streams:
        if stream is None or not hasattr(stream, "reconfigure"):
            continue
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except (OSError, ValueError):
            pass


_make_output_safe(sys.stdout, sys.stderr)

APP_NAME = "AI Job Search"
_server_error = ""
_server = None
_window = None
_window_shown = False


def _state_dir() -> Path:
    from jobsearch import profiles
    d = profiles.DATA_ROOT
    d.mkdir(parents=True, exist_ok=True)
    return d


def _lock_path() -> Path:
    return _state_dir() / "running.json"


def _read_lock() -> dict:
    try:
        return json.loads(_lock_path().read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def _alive(port: int) -> bool:
    """Отвечает ли на этом порту НАША программа, а не кто-нибудь вообще.

    Раньше здесь просто стучались в порт. Этого мало сразу по двум причинам, и
    вторая стоила работоспособности:

    — на порту может сидеть что угодно чужое, и мы открыли бы окно в него;
    — во время обновления установщик закрывает старую копию и тут же запускает
      новую. Новая заставала старую ещё живой, решала «программа уже запущена»,
      открывала окно на её порт и своего сервера не поднимала. Через мгновение
      старая дописывала и умирала — окно упиралось в «отказано в подключении».

    Поэтому спрашиваем, кто там, и сверяем версию: прицепляться имеет смысл
    только к своей ровеснице. Старая копия, которую сейчас закрывают, ответит
    чужим номером — и мы поднимем свой сервер, как и следовало.
    """
    if not port:
        return False
    try:
        with urllib.request.urlopen(f"http://127.0.0.1:{port}/ping", timeout=1.5) as r:
            answer = json.loads(r.read(400).decode("utf-8", "replace"))
    except Exception:  # noqa: BLE001 — не ответил, ответил не тем, ответил не JSON
        return False
    if answer.get("app") != "ai-job-search":
        return False
    from jobsearch import version
    return str(answer.get("version", "")) == version.current()


def _free_port() -> int:
    """A free port: 8765 if nobody has it (the familiar address), otherwise any."""
    for candidate in (8765, 0):
        with closing(socket.socket(socket.AF_INET, socket.SOCK_STREAM)) as s:
            try:
                s.bind(("127.0.0.1", candidate))
                return s.getsockname()[1]
            except OSError:
                continue
    return 0


def _wait_until_up(port: int, timeout: float = 30.0) -> bool:
    deadline = time.time() + timeout
    while time.time() < deadline:
        if _alive(port):
            return True
        time.sleep(0.2)
    return False


def _log_crash(title: str, details: str) -> None:
    """Writes the reason to a file next to the data: a windowed program has no
    console, and without this a person has nothing to show whoever will look
    into it."""
    try:
        path = _state_dir() / "errors.log"
        with path.open("a", encoding="utf-8") as f:
            f.write(f"\n=== {datetime.now():%Y-%m-%d %H:%M:%S} — {title}\n{details}\n")
    except OSError:
        pass


def _capture_server_log() -> None:
    """Uvicorn writes the reason into its own log, and in a windowed program
    nobody ever sees that log: last time all that came out was "SystemExit: 3"
    while the real error stayed invisible. We point it at a file."""
    import logging
    try:
        handler = logging.FileHandler(_state_dir() / "errors.log", encoding="utf-8")
    except OSError:
        return
    handler.setFormatter(logging.Formatter("%(asctime)s %(name)s %(levelname)s: %(message)s"))
    for name in ("uvicorn", "uvicorn.error", "uvicorn.asgi"):
        log = logging.getLogger(name)
        if not any(isinstance(h, logging.FileHandler) for h in log.handlers):
            log.addHandler(handler)
        log.setLevel(logging.INFO)


def _serve(port: int) -> None:
    """The server lives in a background thread. An exception here used to mean the
    thread died quietly: the window never opened and nobody knew why."""
    global _server, _server_error
    _capture_server_log()
    try:
        from app import app as fastapi_app

        config = uvicorn.Config(fastapi_app, host="127.0.0.1", port=port,
                                log_level="warning", access_log=False)
        _server = uvicorn.Server(config)
        _server.run()
    except BaseException:                     # noqa: BLE001 — every reason matters
        _server_error = traceback.format_exc()
        _log_crash("Внутренний сервер не запустился", _server_error)


def _ui_lang() -> str:
    """The interface language, for messages shown before the server is up.

    There may be no settings at all (first launch), or reading them may fail
    along with everything else — then the system language, and as a last resort
    English: more people understand it than Russian.
    """
    try:
        from jobsearch import config, i18n, profiles
        profiles.set_active(profiles.default_slug())
        return config.load().get("ui", {}).get("lang") or i18n.system_lang()
    except Exception:                             # noqa: BLE001
        try:
            from jobsearch import i18n
            return i18n.system_lang()
        except Exception:                         # noqa: BLE001
            return "en"


def _say(key: str, **fmt) -> str:
    """A translated line, or an empty one if the translations cannot be reached:
    a message about trouble must not become a second helping of it."""
    try:
        from jobsearch import i18n
        text = i18n.t(_ui_lang(), key)
        return text.format(**fmt) if fmt else text
    except Exception:                             # noqa: BLE001
        return ""


def _show_failure(reason: str) -> None:
    """Shows the reason in a window: without it a person sees only that the
    program did not open."""
    log = _state_dir() / "errors.log"
    safe = (reason or "").replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
    title = _say("crash_title", app=APP_NAME) or f"{APP_NAME} could not start"
    note = _say("crash_note") or "Details are written to the file:"
    html = f"""<!doctype html><meta charset="utf-8">
<body style="font:14px -apple-system,'Segoe UI',sans-serif;padding:28px;color:#1d1d1f">
<h2 style="margin:0 0 10px">{title}</h2>
<p style="color:#6e6e73;margin:0 0 16px">{note}<br><code>{log}</code></p>
<pre style="background:#f2f2f7;border-radius:8px;padding:14px;font-size:11.5px;
white-space:pre-wrap;max-height:320px;overflow:auto">{safe[-4000:]}</pre>
</body>"""
    try:
        webview.create_window(APP_NAME, html=html, width=760, height=520)
        webview.start()
        return
    except Exception:                         # noqa: BLE001 — there may be no window at all
        pass
    # The window did not open — which makes telling about the trouble matter all
    # the more: we put the same page in a file and hand it to the browser.
    try:
        page = _state_dir() / "error.html"
        page.write_text(html, encoding="utf-8")
        webbrowser.open(page.as_uri())
    except Exception:                         # noqa: BLE001 — there may be no browser either
        print(reason, file=sys.stderr)


def _keep_running_in_background() -> bool:
    """The default profile's setting: whether to keep searching once the window closes."""
    try:
        from jobsearch import config, profiles
        profiles.set_active(profiles.default_slug())
        return bool(config.load().get("ui", {}).get("background", False))
    except Exception:  # noqa: BLE001
        return False


def _shutdown() -> None:
    try:
        from jobsearch import pipeline, scheduler
        if pipeline.state.get("running"):
            pipeline.request_stop()
        scheduler.stop()
    except Exception:  # noqa: BLE001 — on the way out we finish quietly
        pass
    try:
        _lock_path().unlink(missing_ok=True)
    except OSError:
        pass
    if _server is not None:
        _server.should_exit = True


def _on_closing() -> bool:
    """True — the window closes and the process ends; False — we stay on in the background."""
    if _keep_running_in_background():
        return True     # window closes, process lives on: server and schedule keep working
    _shutdown()
    return True


def _unblock_bundled_libraries() -> int:
    """Takes the "came from the internet" mark off the build's own libraries.

    Windows puts that mark on everything unpacked from a downloaded archive, and
    .NET refuses to load a marked library. That is exactly what the portable
    build died on: .NET draws the window, and Python.Runtime.dll inside the
    build was a stranger's file to it. Removing the mark is precisely what
    "Unblock" in the file properties does.

    Returns how many libraries the mark could not be taken off.
    """
    bundle = getattr(sys, "_MEIPASS", "")
    if not bundle:
        return 0                       # running from source: there was nothing to download
    осталось = 0
    for dll in Path(bundle).rglob("*.dll"):
        метка = f"{dll}:Zone.Identifier"
        if not os.path.exists(метка):
            continue
        try:
            os.remove(метка)
        except OSError:
            осталось += 1              # a read-only folder — the mark stays
    return осталось


def _trust_marked_libraries() -> Path:
    """Permission for .NET to trust marked libraries.

    The fallback for when the mark cannot be removed: the very same files can be
    granted trust by configuration, without touching them on disk. The file goes
    next to the program's data — that is always writable, unlike the install
    folder.
    """
    path = _state_dir() / "dotnet.config"
    path.write_text('<?xml version="1.0" encoding="utf-8"?>\n'
                    "<configuration>\n"
                    "  <runtime>\n"
                    '    <loadFromRemoteSources enabled="true" />\n'
                    "  </runtime>\n"
                    "</configuration>\n", encoding="utf-8")
    return path


def _prepare_windows_gui() -> None:
    """On Windows the window is drawn by .NET — clear away what stops it starting.

    On a clean build we do nothing: the loader setting is needed only where the
    mark is really there and really will not come off, and changing the launch
    conditions for people whose window already works is a sure way to break it.
    """
    if sys.platform != "win32":
        return
    try:
        if not _unblock_bundled_libraries():
            return
        if "PYTHONNET_NETFX_CONFIG_FILE" in os.environ:
            return                     # the person configured it — do not override
        os.environ["PYTHONNET_NETFX_CONFIG_FILE"] = str(_trust_marked_libraries())
    except Exception:                  # noqa: BLE001 — the preparation is optional,
        pass                           # if it fails, the browser is still there


def _remember_window_shown() -> None:
    global _window_shown
    _window_shown = True


def _open_in_browser(port: int, own_server: bool) -> None:
    """No window of our own — so we show the program in the browser.

    Inside it is an ordinary site on 127.0.0.1, so a tab is a full replacement
    for a window. Refusing a person the whole program over one windowing library
    is not a trade worth making.
    """
    url = f"http://127.0.0.1:{port}/simple"
    try:
        webbrowser.open(url)
    except Exception:                  # noqa: BLE001 — there may be no browser at all
        pass
    print(_say("crash_in_browser", url=url) or f"{APP_NAME}: {url}", file=sys.stderr)
    if not own_server:
        return                         # someone else's server: not ours to keep alive
    try:
        while True:
            time.sleep(3600)           # no window — hold the server while the tab is needed
    except KeyboardInterrupt:
        pass


def _open_window(port: int, own_server: bool) -> None:
    """Our own window, or a browser tab if the system will not give us one."""
    global _window
    try:
        _window = webview.create_window(APP_NAME, f"http://127.0.0.1:{port}/simple",
                                        width=1180, height=860, min_size=(900, 600))
        _window.events.shown += _remember_window_shown
        if own_server:
            _window.events.closing += _on_closing
        webview.start()
        return
    except Exception:                  # noqa: BLE001 — every reason matters
        reason = traceback.format_exc()
    _log_crash("Своё окно не открылось — показываем в браузере", reason)
    if _window_shown:
        return                         # the window was already in use: trouble on the way out
    _open_in_browser(port, own_server)


def main() -> int:
    _prepare_windows_gui()

    # Already running? Then this is a second click on the icon — just show the window.
    lock = _read_lock()
    if _alive(lock.get("port", 0)):
        _open_window(lock["port"], own_server=False)
        return 0

    port = _free_port()
    threading.Thread(target=_serve, args=(port,), daemon=True).start()
    if not _wait_until_up(port):
        reason = _server_error or "Внутренний сервер не ответил за 30 секунд."
        _log_crash("Приложение не смогло запуститься", reason)
        _show_failure(reason)
        return 1
    _lock_path().write_text(json.dumps({"port": port, "pid": os.getpid()}), encoding="utf-8")

    _open_window(port, own_server=True)

    # The window is closed. In background mode we keep the process alive while it works.
    if _keep_running_in_background():
        try:
            while True:
                time.sleep(5)
        except KeyboardInterrupt:
            pass
        finally:
            _shutdown()
    else:
        _shutdown()
    return 0


if __name__ == "__main__":
    sys.exit(main())
