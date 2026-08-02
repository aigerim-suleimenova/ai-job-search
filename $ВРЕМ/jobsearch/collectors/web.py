"""The shared rules for visiting other people's sites.

Every collector used to go to the network on its own: introducing itself as a
Mozilla browser, never asking robots.txt, and knocking without pause. To the
owner of a site that looks like a pushy robot which, on top of it, hides who it is.

Now there is one place for all of them:
  * an honest name with the project's address — it shows who came and where to write;
  * a pause between requests to the same host, so as not to create load;
  * robots.txt for ordinary sites (allowed()).

Documented APIs (Greenhouse, Lever, Adzuna and the rest) exist precisely so that
programs will call them — robots.txt does not concern them, but they get the
pause and the honest name along with everyone else.
"""
import ipaddress
import socket
import threading
import time
from urllib.parse import urljoin, urlparse
from urllib.robotparser import RobotFileParser

import requests

VERSION = "0.8"
PROJECT_URL = "https://github.com/mrWD/ai-job-search"
UA_STRING = f"ai-job-search/{VERSION} (+{PROJECT_URL})"
UA = {"User-Agent": UA_STRING}

TIMEOUT = 30
DELAY = 1.5          # seconds between requests to the same host
ROBOTS_TIMEOUT = 10

_lock = threading.Lock()
_last_hit: dict = {}     # host → when we last knocked
_robots: dict = {}       # host → (parser | None, the delay from robots.txt)


def _host(url: str) -> str:
    return urlparse(url).netloc.lower()


class Refused(requests.RequestException):
    """Адрес, по которому мы не пойдём."""


MAX_HOPS = 5


def check(url: str) -> None:
    """Пускать ли нас по этому адресу вообще.

    Ходим мы не только туда, куда человек попросил. Работодатели попадают в
    список по ссылкам внутри уже собранных вакансий, а вакансии эти пишет кто
    угодно. Ссылка на http://127.0.0.1:11434 или на 169.254.169.254 — это
    просьба сходить внутрь машины, где стоит программа, и принести оттуда то,
    до чего снаружи не дотянуться: ответ локальной Ollama, ключи облачной
    учётки. Программа это делала, потому что ходила по любому адресу.

    Схемы, кроме http и https, отсекаются заодно: file:// и подавно не для
    сбора вакансий.

    Чего это не закрывает: имя можно перерешить между нашей проверкой и самим
    запросом — мы спрашиваем адрес у DNS, а requests потом спрашивает ещё раз.
    Закрыть щель до конца можно, только приколотив найденный адрес к соединению;
    пока не приколочено, и писать, будто закрыто, нельзя.
    """
    parts = urlparse(url)
    if parts.scheme not in ("http", "https"):
        raise Refused(f"схема {parts.scheme or '—'} не для сбора вакансий")
    host = parts.hostname
    if not host:
        raise Refused("в адресе нет хоста")
    try:
        адреса = socket.getaddrinfo(host, parts.port or (443 if parts.scheme == "https" else 80),
                                    proto=socket.IPPROTO_TCP)
    except socket.gaierror as e:
        raise Refused(f"имя {host} не нашлось") from e
    for *_, sockaddr in адреса:
        ip = ipaddress.ip_address(sockaddr[0])
        # is_global разом отсекает петлю, частные сети, link-local (а с ним и
        # 169.254.169.254, откуда облака отдают ключи), зарезервированное и
        # многоадресное. Перечислять диапазоны руками значило бы однажды забыть один.
        if not ip.is_global:
            raise Refused(f"{host} ведёт внутрь машины или сети ({ip})")


def _wait_turn(host: str, delay: float) -> None:
    """Holds the pause before touching the same host again. Counted under a lock:
    the requests come from several threads, and without it the pause means nothing."""
    while True:
        with _lock:
            now = time.monotonic()
            left = delay - (now - _last_hit.get(host, 0.0))
            if left <= 0:
                _last_hit[host] = now
                return
        time.sleep(min(left, delay))


def _robots_for(host: str, scheme: str = "https"):
    """A host's robots.txt. Unreachable or unreadable — we take it as permitted:
    search engine robots behave the same way."""
    if host in _robots:
        return _robots[host]
    parser = None
    delay = None
    try:
        # robots.txt берётся у того же хоста и той же проверкой: спросить «а можно
        # к вам?» у 127.0.0.1 — это уже сходить к 127.0.0.1.
        адрес = f"{scheme}://{host}/robots.txt"
        check(адрес)
        r = requests.get(адрес, headers=UA, timeout=ROBOTS_TIMEOUT)
        if r.status_code == 200 and len(r.text) < 500_000:
            parser = RobotFileParser()
            parser.parse(r.text.splitlines())
            # crawl_delay() in the standard library starts by checking mtime()
            # and without it always returns None — so we mark it as read.
            # Fractional delays (Crawl-delay: 0.5) the parser ignores: it takes
            # whole numbers only. Our own pause keeps us from hurrying anyway.
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
    """Does the site allow robots to read this page."""
    host = _host(url)
    if not host:
        return False
    parser, _ = _robots_for(host, urlparse(url).scheme or "https")
    if parser is None:
        return True
    try:
        return parser.can_fetch(UA_STRING, url)
    except Exception:  # noqa: BLE001 — a malformed robots.txt is no reason to fall over
        return True


def post(url: str, **kw):
    """POST с теми же правилами, что и GET: пауза, честное имя, проверка адреса.

    Нужен одному источнику — общеевропейскому EURES, который принимает запрос
    только телом. Переходов у него не бывает, поэтому и вести их тут нечего.
    """
    check(url)
    host = _host(url)
    _, robots_delay = _robots.get(host, (None, None))
    _wait_turn(host, max(DELAY, float(robots_delay or 0)))
    headers = {**UA, **kw.pop("headers", {})}
    return requests.post(url, headers=headers, timeout=kw.pop("timeout", TIMEOUT), **kw)


def get(url: str, *, respect_robots: bool = False, **kw):
    """A GET with the pause and the honest name. respect_robots=True is for ordinary
    sites; when forbidden it returns None rather than raising.

    Переходы ведём сами, по одному. Иначе проверка адреса стоила бы ровно
    ничего: сайт отвечает «идите на http://127.0.0.1», requests послушно идёт, и
    первый — единственный проверенный — адрес оказывается ни при чём.
    """
    if respect_robots and not allowed(url):
        return None
    kw.pop("allow_redirects", None)
    headers = {**UA, **kw.pop("headers", {})}
    timeout = kw.pop("timeout", TIMEOUT)
    for _ in range(MAX_HOPS + 1):
        check(url)
        host = _host(url)
        _, robots_delay = _robots.get(host, (None, None))
        _wait_turn(host, max(DELAY, float(robots_delay or 0)))
        r = requests.get(url, headers=headers, timeout=timeout, allow_redirects=False, **kw)
        куда = r.headers.get("location", "")
        if r.status_code not in (301, 302, 303, 307, 308) or not куда:
            return r
        r.close()          # тело перехода нам не нужно, а соединение освободить надо
        url = urljoin(url, куда)
    raise Refused(f"переходов больше {MAX_HOPS} — похоже на кольцо")
