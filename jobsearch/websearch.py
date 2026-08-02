"""Поиск в интернете силами самого приложения.

Веб-поиск умеет только Claude Code: у остальных программ-моделей его нет, и
дать его им нельзя — это их собственная возможность, а не наша. Поэтому со всеми
остальными отключались разведка новых компаний и сбор зарплатных вилок, а на
странице висела плашка «модель не умеет искать в интернете».

Здесь другой ход: ищет приложение, а модель получает уже найденное текстом. Её
работа от этого не меняется — она и раньше не искала сама, а разбирала выдачу, —
зато поиск перестаёт зависеть от того, какой программой человек думает.

Побочно это надёжнее и с точки зрения безопасности: модели не выдаётся никаких
сетевых инструментов вовсе, и что именно скачано, решаем мы, а не указание,
спрятанное в чужом тексте.

Ключ нужен, но у всех трёх служб есть бесплатный уровень, которого на несколько
поисков за прогон хватает с запасом. Без ключа ничего не меняется: разведка
по-прежнему пропускается, и плашка честно об этом говорит.
"""
import requests

from . import net

TIMEOUT = 20

# Службы, говорящие каждая по-своему. Ответ приводится к одному виду:
# [{"title", "url", "snippet"}] — дальше он идёт в запрос к модели текстом.
PROVIDERS = ("brave", "tavily", "serper")


class SearchError(RuntimeError):
    """Несёт ключ перевода: текст показывается человеку."""

    def __init__(self, key: str, **fmt):
        self.key, self.fmt = key, fmt
        super().__init__(key)


def _hide(text: str, key: str) -> str:
    """Ключ не должен уехать в журнал прогона вместе с текстом ошибки."""
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
    """Ищет и возвращает [{title, url, snippet}]. Пустой список — не беда."""
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
    """Выдача, пригодная для вставки в запрос к модели."""
    lines = []
    for i, r in enumerate(results[:limit], 1):
        snippet = (r.get("snippet") or "").strip().replace("\n", " ")[:400]
        lines.append(f"{i}. {r.get('title', '')}\n   {r.get('url', '')}\n   {snippet}")
    return "\n".join(lines)
