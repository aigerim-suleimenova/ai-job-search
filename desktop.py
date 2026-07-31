"""Запуск как обычной программы: своё окно, сервер внутри, без терминала.

Два режима закрытия окна (переключается в настройках):
- обычный: закрыли окно — приложение завершилось;
- фоновый: закрыли окно — поиск продолжается, а повторный запуск программы
  просто открывает окно к уже работающему процессу.

Второй режим нужен для непрерывного поиска: держать окно открытым сутками
неудобно, а прерывать поиск при закрытии — обидно.
"""
import json
import os
import socket
import sys
import threading
import time
from contextlib import closing
from pathlib import Path

import traceback
from datetime import datetime

import uvicorn
import webview

def _make_output_safe(*streams) -> None:
    """Консоль Windows по умолчанию не в UTF-8: любое русское слово в выводе
    роняет программу кодировочной ошибкой. Сообщение о беде не должно
    становиться бедой — переводим потоки в UTF-8, а непереводимое заменяем."""
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


def _state_dir() -> Path:
    from jobsearch import profiles
    d = profiles.DATA_ROOT
    d.mkdir(parents=True, exist_ok=True)
    return d


def _lock_path() -> Path:
    return _state_dir() / "running.json"


def _read_lock() -> dict:
    try:
        return json.loads(_lock_path().read_text())
    except (OSError, ValueError):
        return {}


def _alive(port: int) -> bool:
    if not port:
        return False
    with closing(socket.socket(socket.AF_INET, socket.SOCK_STREAM)) as s:
        s.settimeout(0.6)
        return s.connect_ex(("127.0.0.1", port)) == 0


def _free_port() -> int:
    """Свободный порт: 8765, если не занят (привычный адрес), иначе любой."""
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
    """Пишет причину в файл рядом с данными: у оконной программы нет консоли,
    и без этого человеку нечего показать тому, кто будет разбираться."""
    try:
        path = _state_dir() / "errors.log"
        with path.open("a", encoding="utf-8") as f:
            f.write(f"\n=== {datetime.now():%Y-%m-%d %H:%M:%S} — {title}\n{details}\n")
    except OSError:
        pass


def _serve(port: int) -> None:
    """Сервер живёт в фоновом потоке. Раньше исключение здесь означало тихую
    смерть потока: окно не открывалось, а почему — не знал никто."""
    global _server, _server_error
    try:
        from app import app as fastapi_app

        config = uvicorn.Config(fastapi_app, host="127.0.0.1", port=port,
                                log_level="warning", access_log=False)
        _server = uvicorn.Server(config)
        _server.run()
    except BaseException:                     # noqa: BLE001 — важна любая причина
        _server_error = traceback.format_exc()
        _log_crash("Внутренний сервер не запустился", _server_error)


def _show_failure(reason: str) -> None:
    """Показывает причину в окне: без этого человек видит только то, что
    программа не открылась."""
    log = _state_dir() / "errors.log"
    safe = (reason or "").replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
    html = f"""<!doctype html><meta charset="utf-8">
<body style="font:14px -apple-system,'Segoe UI',sans-serif;padding:28px;color:#1d1d1f">
<h2 style="margin:0 0 10px">{APP_NAME} не смог запуститься</h2>
<p style="color:#6e6e73;margin:0 0 16px">Это сбой программы, а не ваших данных — они на месте.
Подробности записаны в файл:<br><code>{log}</code></p>
<pre style="background:#f2f2f7;border-radius:8px;padding:14px;font-size:11.5px;
white-space:pre-wrap;max-height:320px;overflow:auto">{safe[-4000:]}</pre>
</body>"""
    try:
        webview.create_window(APP_NAME, html=html, width=760, height=520)
        webview.start()
    except Exception:                         # noqa: BLE001 — окна может не быть вовсе
        print(reason, file=sys.stderr)


def _keep_running_in_background() -> bool:
    """Настройка профиля по умолчанию: продолжать ли поиск после закрытия окна."""
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
    except Exception:  # noqa: BLE001 — при выходе доводим молча
        pass
    try:
        _lock_path().unlink(missing_ok=True)
    except OSError:
        pass
    if _server is not None:
        _server.should_exit = True


def _on_closing() -> bool:
    """True — окно закроется и процесс завершится, False — остаёмся работать в фоне."""
    if _keep_running_in_background():
        return True     # окно закрывается, процесс живёт: сервер и расписание работают
    _shutdown()
    return True


def _open_window(port: int, own_server: bool) -> None:
    global _window
    _window = webview.create_window(APP_NAME, f"http://127.0.0.1:{port}/simple",
                                    width=1180, height=860, min_size=(900, 600))
    if own_server:
        _window.events.closing += _on_closing
    webview.start()


def main() -> int:
    # Уже запущено? Тогда это «второй клик по иконке» — просто показываем окно.
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
    _lock_path().write_text(json.dumps({"port": port, "pid": os.getpid()}))

    _open_window(port, own_server=True)

    # Окно закрыто. В фоновом режиме держим процесс живым, пока идёт работа.
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
