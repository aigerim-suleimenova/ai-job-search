"""Outbound requests — made so they arrive even where connections get inspected.

Avast, AVG, Kaspersky, ESET and corporate gateways all do the same thing: they
open the secure connection themselves, read it, and re-sign it with their own
root. That root sits in the system store — otherwise the browser would not work
either. But requests does not use the system store: it carries its own bundle,
and the foreign root is not in it. Hence "certificate verify failed" — on
everything at once.

Viktor hit this twice in one day. First all nine job sources returned zero and
the run reported success. Then the program stopped noticing new versions: the
check silently came back empty, and he sat on an old build for a version and a
half without knowing newer ones were out.

It is fixed here, in one place. First we try the usual way, with our own bundle:
for anyone whose connections are not inspected, nothing changes. If that fails
over a certificate, we retry through the system store and remember the choice
until the program exits, so we do not pay for every request with a second
attempt.

This makes nothing weaker: the system store is exactly what this person's own
browser trusts, and they put that trust there themselves. We never turn
verification off.
"""
import ssl
import threading

import requests
from requests.adapters import HTTPAdapter

_lock = threading.Lock()
_via_system_store = False    # found out our own bundle will not do here
_session = None


class _SystemTrustStore(HTTPAdapter):
    """The connection is verified against what the operating system trusts."""

    def init_poolmanager(self, *args, **kwargs):
        kwargs["ssl_context"] = ssl.create_default_context()
        return super().init_poolmanager(*args, **kwargs)


def _system_session():
    global _session
    with _lock:
        if _session is None:
            _session = requests.Session()
            _session.mount("https://", _SystemTrustStore())
        return _session


def _call(method, url, **kw):
    global _via_system_store
    if _via_system_store:
        return getattr(_system_session(), method)(url, **kw)
    try:
        return getattr(requests, method)(url, **kw)
    except requests.exceptions.SSLError:
        # Maybe there is an inspecting middleman. Try its own root — and if that
        # worked, keep going that way: no need for a second attempt every time.
        response = getattr(_system_session(), method)(url, **kw)
        _via_system_store = True
        return response


def get(url, **kw):
    return _call("get", url, **kw)


def post(url, **kw):
    return _call("post", url, **kw)


def using_system_store() -> bool:
    """Whether we had to fall back to the system store. For the log and the tests."""
    return _via_system_store


def forget() -> None:
    """Ask again — the bundle may have changed (and the tests need this)."""
    global _via_system_store, _session
    with _lock:
        _via_system_store, _session = False, None
