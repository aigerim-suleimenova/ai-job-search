"""Searching the web with the application's own hands.

Only Claude Code can search the web: the other model programs do not have it,
and we cannot hand it to them — it is their own capability, not ours. So with
everyone else, scouting for new companies and collecting salary ranges were
switched off, and the page carried a notice saying the model cannot search the
web.

This takes another route: the application searches, and the model gets what was
found, as text. Its work does not change — it never searched by itself anyway,
it read the results — but searching stops depending on which program the person
thinks with.

As a side effect this is also sounder for safety: the model is handed no network
tools whatsoever, and what gets downloaded is our decision, not an instruction
hidden in somebody else's text.

A key is needed, but all three services have a free tier with room to spare for
a few searches per run. Without a key nothing changes: scouting is skipped as
before, and the notice says so honestly.
"""
import requests

from . import net

TIMEOUT = 20

# Services that each speak their own way. The answer is brought to one shape:
# [{"title", "url", "snippet"}] — from there it goes into the prompt as text.
PROVIDERS = ("brave", "tavily", "serper")


class SearchError(RuntimeError):
    """Carries a translation key: the text gets shown to a person."""

    def __init__(self, key: str, **fmt):
        self.key, self.fmt = key, fmt
        super().__init__(key)


def _hide(text: str, key: str) -> str:
    """The key must not ride into the run log along with the error text."""
    return str(text).replace(key, "***") if key else str(text)


def configured(cfg: dict) -> bool:
    s = cfg.get("sources", {})
    return bool((s.get("web_search_provider") or "").strip()
                and (s.get("web_search_key") or "").strip())


def _brave(query: str, key: str, n: int) -> list:
    r = net.get("https://api.search.brave.com/res/v1/web/search",
                     params={"q": query, "count": n},
                     headers={"Accept": "application/json", "X-Subscription-Token": key},
                     timeout=TIMEOUT)
    r.raise_for_status()
    out = []
    for item in (r.json().get("web") or {}).get("results", [])[:n]:
        out.append({"title": str(item.get("title", "")),
                    "url": str(item.get("url", "")),
                    "snippet": str(item.get("description", ""))})
    return out


def _tavily(query: str, key: str, n: int) -> list:
    r = net.post("https://api.tavily.com/search",
                      json={"api_key": key, "query": query, "max_results": n},
                      timeout=TIMEOUT)
    r.raise_for_status()
    return [{"title": str(i.get("title", "")), "url": str(i.get("url", "")),
             "snippet": str(i.get("content", ""))}
            for i in r.json().get("results", [])[:n]]


def _serper(query: str, key: str, n: int) -> list:
    r = net.post("https://google.serper.dev/search",
                      json={"q": query, "num": n},
                      headers={"X-API-KEY": key, "Content-Type": "application/json"},
                      timeout=TIMEOUT)
    r.raise_for_status()
    return [{"title": str(i.get("title", "")), "url": str(i.get("link", "")),
             "snippet": str(i.get("snippet", ""))}
            for i in r.json().get("organic", [])[:n]]


_ADAPTERS = {"brave": _brave, "tavily": _tavily, "serper": _serper}


def search(cfg: dict, query: str, n: int = 8) -> list:
    """Searches and returns [{title, url, snippet}]. An empty list is no disaster."""
    s = cfg.get("sources", {})
    name = (s.get("web_search_provider") or "").strip()
    key = (s.get("web_search_key") or "").strip()
    adapter = _ADAPTERS.get(name)
    if not adapter or not key:
        raise SearchError("search_err_not_set")
    try:
        return [r for r in adapter(query, key, n) if r["url"].startswith(("http://", "https://"))]
    except requests.RequestException as e:
        raise SearchError("search_err_failed", error=_hide(e, key)) from e
    except (ValueError, KeyError, TypeError) as e:
        raise SearchError("search_err_failed", error=_hide(e, key)) from e


def as_text(results: list, limit: int = 8) -> str:
    """Results in a shape fit for dropping into a prompt."""
    lines = []
    for i, r in enumerate(results[:limit], 1):
        snippet = (r.get("snippet") or "").strip().replace("\n", " ")[:400]
        lines.append(f"{i}. {r.get('title', '')}\n   {r.get('url', '')}\n   {snippet}")
    return "\n".join(lines)
