"""Запуск как обычной программы: своё окно, сервер внутри, без терминала.

Пользователь открывает приложение, жмёт «Запустить поиск», закрывает окно и
возвращается позже. Поиск продолжается, пока приложение открыто; при закрытии
окна текущий прогон корректно останавливается.
"""
import socket
import sys
import threading
import time
from contextlib import closing

import uvicorn
import webview

APP_NAME = "AI Job Search"
_server = None


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
        with closing(socket.socket(socket.AF_INET, socket.SOCK_STREAM)) as s:
            s.settimeout(0.5)
            if s.connect_ex(("127.0.0.1", port)) == 0:
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


def _on_closing() -> None:
    """Окно закрывают — останавливаем прогон, чтобы процесс не завис в памяти."""
    try:
        from jobsearch import pipeline, scheduler
        if pipeline.state.get("running"):
            pipeline.request_stop()
        scheduler.stop()
    except Exception:  # noqa: BLE001 — при закрытии молча доводим до конца
        pass
    if _server is not None:
        _server.should_exit = True


def main() -> int:
    port = _free_port()
    threading.Thread(target=_serve, args=(port,), daemon=True).start()
    if not _wait_until_up(port):
        print("Не удалось запустить внутренний сервер", file=sys.stderr)
        return 1

    window = webview.create_window(APP_NAME, f"http://127.0.0.1:{port}/simple",
                                   width=1180, height=860, min_size=(900, 600))
    window.events.closing += _on_closing
    webview.start()
    return 0


if __name__ == "__main__":
    sys.exit(main())
