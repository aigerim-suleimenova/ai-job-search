"""Общие правила хождения по чужим сайтам.

Раньше каждый сборщик ходил в сеть сам: представлялся браузером Mozilla,
не спрашивал robots.txt и стучал без пауз. Для владельца сайта это выглядит
как назойливый робот, который вдобавок скрывает, кто он такой.

Теперь одно место на всех:
  * честное имя с адресом проекта — по нему видно, кто пришёл и куда писать;
  * пауза между запросами к одному хосту, чтобы не создавать нагрузку;
  * robots.txt для обычных сайтов (allowed()).

Документированные API (Greenhouse, Lever, Adzuna и прочие) существуют ровно
для того, чтобы к ним обращались программы, — их robots.txt не касается, но
паузу и честное имя они получают наравне со всеми.
"""
import threading
import time
from urllib.parse import urlparse
from urllib.robotparser import RobotFileParser

import requests

VERSION = "0.8"
PROJECT_URL = "https://github.com/mrWD/ai-job-search"
UA_STRING = f"ai-job-search/{VERSION} (+{PROJECT_URL})"
UA = {"User-Agent": UA_STRING}

TIMEOUT = 30
DELAY = 1.5          # секунды между запросами к одному хосту
ROBOTS_TIMEOUT = 10

_lock = threading.Lock()
_last_hit: dict = {}     # хост → когда стучались в последний раз
_robots: dict = {}       # хост → (парсер | None, задержка из robots.txt)


def _host(url: str) -> str:
    return urlparse(url).netloc.lower()


def _wait_turn(host: str, delay: float) -> None:
    """Держит паузу перед обращением к тому же хосту. Считаем под замком:
    запросы идут из нескольких потоков, и без него пауза ничего не значит."""
    while True:
        with _lock:
            now = time.monotonic()
            left = delay - (now - _last_hit.get(host, 0.0))
            if left <= 0:
                _last_hit[host] = now
                return
        time.sleep(min(left, delay))


def _robots_for(host: str, scheme: str = "https"):
    """robots.txt хоста. Недоступен или не читается — считаем, что можно:
    так же ведут себя поисковые роботы."""
    if host in _robots:
        return _robots[host]
    parser = None
    delay = None
    try:
        r = requests.get(f"{scheme}://{host}/robots.txt", headers=UA, timeout=ROBOTS_TIMEOUT)
        if r.status_code == 200 and len(r.text) < 500_000:
            parser = RobotFileParser()
            parser.parse(r.text.splitlines())
            # crawl_delay() в стандартной библиотеке начинается с проверки
            # mtime() и без неё всегда отдаёт None — отмечаем, что прочитали.
            # Дробные задержки (Crawl-delay: 0.5) парсер игнорирует: он берёт
            # только целые. Наша собственная пауза всё равно не даёт частить.
            parser.modified()
            try:
                delay = parser.crawl_delay(UA_STRING)
            except (AttributeError, ValueError):
                delay = None
    except (requests.RequestException, ValueError, UnicodeDecodeError):
        parser = None
    with _lock:
        _robots[host] = (parser, delay)
    return _robots[host]


def allowed(url: str) -> bool:
    """Разрешает ли сайт роботам читать эту страницу."""
    host = _host(url)
    if not host:
        return False
    parser, _ = _robots_for(host, urlparse(url).scheme or "https")
    if parser is None:
        return True
    try:
        return parser.can_fetch(UA_STRING, url)
    except Exception:  # noqa: BLE001 — кривой robots.txt не повод падать
        return True


def get(url: str, *, respect_robots: bool = False, **kw):
    """GET с паузой и честным именем. respect_robots=True — для обычных сайтов;
    при запрете возвращает None, а не бросает исключение."""
    if respect_robots and not allowed(url):
        return None
    host = _host(url)
    _, robots_delay = _robots.get(host, (None, None))
    _wait_turn(host, max(DELAY, float(robots_delay or 0)))
    headers = {**UA, **kw.pop("headers", {})}
    return requests.get(url, headers=headers, timeout=kw.pop("timeout", TIMEOUT), **kw)
