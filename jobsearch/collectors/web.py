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

from .. import net

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
    """An address we will not go to."""


MAX_HOPS = 5


def check(url: str) -> None:
    """Whether we may go to this address at all.

    We do not only go where the person asked. Employers land in the list through
    links inside jobs already collected, and those jobs are written by anyone at
    all. A link to http://127.0.0.1:11434 or to 169.254.169.254 is a request to
    step inside the machine the program runs on and fetch what cannot be reached
    from outside: the answer of a local Ollama, the keys of a cloud account. The
    program used to do it, because it went to any address given.

    Schemes other than http and https are cut off along the way: file:// is
    certainly not for collecting jobs.

    What this does not close: the name can be resolved again between our check
    and the request itself — we ask DNS for the address, and requests then asks
    a second time. Closing the gap completely means pinning the resolved address
    to the connection; until it is pinned, we must not write as though it were.
    """
    parts = urlparse(url)
    if parts.scheme not in ("http", "https"):
        raise Refused(f"scheme {parts.scheme or '—'} is not for collecting jobs")
    host = parts.hostname
    if not host:
        raise Refused("the address has no host")
    try:
        addresses = socket.getaddrinfo(host, parts.port or (443 if parts.scheme == "https" else 80),
                                       proto=socket.IPPROTO_TCP)
    except socket.gaierror as e:
        raise Refused(f"the name {host} did not resolve") from e
    for *_, sockaddr in addresses:
        ip = ipaddress.ip_address(sockaddr[0])
        # is_global cuts off, in one go, the loopback, private networks,
        # link-local (and with it 169.254.169.254, where clouds hand out keys),
        # reserved and multicast. Listing the ranges by hand would mean forgetting
        # one of them some day.
        if not ip.is_global:
            raise Refused(f"{host} leads inside the machine or the network ({ip})")


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
        # robots.txt is fetched from the same host and through the same check:
        # asking 127.0.0.1 "may we come in?" is already a trip to 127.0.0.1.
        address = f"{scheme}://{host}/robots.txt"
        check(address)
        r = net.get(address, headers=UA, timeout=ROBOTS_TIMEOUT)
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
    """A POST under the same rules as GET: the pause, the honest name, the address
    check.

    One source needs it — the Europe-wide EURES, which takes the query only in
    the body. It never redirects, so there is nothing to follow here.
    """
    check(url)
    host = _host(url)
    _, robots_delay = _robots.get(host, (None, None))
    _wait_turn(host, max(DELAY, float(robots_delay or 0)))
    headers = {**UA, **kw.pop("headers", {})}
    return net.post(url, headers=headers, timeout=kw.pop("timeout", TIMEOUT), **kw)


def get(url: str, *, respect_robots: bool = False, **kw):
    """A GET with the pause and the honest name. respect_robots=True is for ordinary
    sites; when forbidden it returns None rather than raising.

    We follow redirects ourselves, one at a time. Otherwise the address check
    would be worth exactly nothing: the site answers "go to http://127.0.0.1",
    requests obediently goes, and the first address — the only checked one —
    turns out to be beside the point.
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
        r = net.get(url, headers=headers, timeout=timeout, allow_redirects=False, **kw)
        where = r.headers.get("location", "")
        if r.status_code not in (301, 302, 303, 307, 308) or not where:
            return r
        r.close()          # we do not need the redirect body, but the connection must go back
        url = urljoin(url, where)
    raise Refused(f"more than {MAX_HOPS} redirects — this looks like a loop")
