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

import uvicorn
import webview

APP_NAME = "AI Job Search"
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


def _serve(port: int) -> None:
    global _server
    from app import app as fastapi_app

    config = uvicorn.Config(fastapi_app, host="127.0.0.1", port=port,
                            log_level="warning", access_log=False)
    _server = uvicorn.Server(config)
    _server.run()


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
        print("Не удалось запустить внутренний сервер", file=sys.stderr)
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
