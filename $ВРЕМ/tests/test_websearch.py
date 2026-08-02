"""Поиск в интернете силами самого приложения.

Веб-поиск умеет только Claude Code: у остальных программ-моделей его нет, и дать
его им нельзя — это их возможность, а не наша. Поэтому со всеми остальными
отключались разведка новых компаний и зарплатные вилки, а на странице висела
плашка «модель не умеет искать в интернете».

Здесь ход обратный: ищет приложение, а модель получает найденное текстом. Работа
у неё та же — она и раньше не искала, а разбирала выдачу, — зато поиск больше не
зависит от того, какой программой человек думает.

И это надёжнее ещё в одном смысле: модели не выдаётся сетевых инструментов
вовсе, и что именно скачано, решаем мы, а не указание, спрятанное в чужом тексте.
"""
import pytest
import requests

from jobsearch import config, providers, websearch

КЛЮЧ = "sk-search-секрет"


def настроен(cfg: dict, служба: str = "brave") -> dict:
    cfg["sources"].update(web_search_provider=служба, web_search_key=КЛЮЧ)
    return cfg


class Ответ:
    def __init__(self, payload):
        self._payload = payload

    def raise_for_status(self):
        pass

    def json(self):
        return self._payload


ВЫДАЧА = {
    "brave": {"web": {"results": [
        {"title": "Acme careers", "url": "https://acme.example/jobs", "description": "мы нанимаем"}]}},
    "tavily": {"results": [
        {"title": "Acme careers", "url": "https://acme.example/jobs", "content": "мы нанимаем"}]},
    "serper": {"organic": [
        {"title": "Acme careers", "link": "https://acme.example/jobs", "snippet": "мы нанимаем"}]},
}


@pytest.mark.parametrize("служба", ["brave", "tavily", "serper"])
def test_разные_службы_приводятся_к_одному_виду(monkeypatch, служба, profile):
    """Дальше выдача идёт в запрос к модели текстом — форма должна быть одна."""
    monkeypatch.setattr(websearch.requests, "get", lambda *a, **kw: Ответ(ВЫДАЧА[служба]))
    monkeypatch.setattr(websearch.requests, "post", lambda *a, **kw: Ответ(ВЫДАЧА[служба]))

    найдено = websearch.search(настроен(config.load(), служба), "acme jobs")

    assert найдено == [{"title": "Acme careers", "url": "https://acme.example/jobs",
                        "snippet": "мы нанимаем"}], служба


def test_ключ_не_попадает_в_текст_ошибки(monkeypatch, profile):
    """Ошибка уходит в журнал прогона, а его человек кому-то показывает."""
    def взорваться(*a, **kw):
        raise requests.ConnectionError(f"нет связи, token={КЛЮЧ}")

    monkeypatch.setattr(websearch.requests, "get", взорваться)

    with pytest.raises(websearch.SearchError) as поймано:
        websearch.search(настроен(config.load()), "acme")

    assert КЛЮЧ not in str(поймано.value) + str(поймано.value.fmt)


def test_без_ключа_говорим_что_не_настроено(profile):
    with pytest.raises(websearch.SearchError) as поймано:
        websearch.search(config.load(), "acme")
    assert поймано.value.key == "search_err_not_set"


def test_чужие_схемы_из_выдачи_отсекаются(monkeypatch, profile):
    """Адреса из выдачи попадают в список компаний и потом скачиваются."""
    monkeypatch.setattr(websearch.requests, "get", lambda *a, **kw: Ответ({"web": {"results": [
        {"title": "плохое", "url": "javascript:alert(1)", "description": ""},
        {"title": "тоже плохое", "url": "file:///etc/passwd", "description": ""},
        {"title": "годное", "url": "https://acme.example/jobs", "description": ""}]}}))

    найдено = websearch.search(настроен(config.load()), "acme")

    assert [r["url"] for r in найдено] == ["https://acme.example/jobs"]


# --- Как это видно остальной программе ------------------------------------------

def test_с_ключом_поиск_есть_у_любой_модели(profile):
    """Ровно то, ради чего всё затевалось: плашка исчезает не у одного Claude Code."""
    cfg = config.load()
    cfg["llm"]["provider"] = "ollama"
    assert providers.web_search_possible(cfg) is False, "предусловие: без ключа поиска нет"

    assert providers.web_search_possible(настроен(cfg)) is True


def test_у_claude_поиск_есть_и_без_ключа(profile):
    cfg = config.load()
    cfg["llm"]["provider"] = "claude_cli"
    assert providers.web_search_possible(cfg) is True


def test_возможность_модели_и_возможность_вообще_это_разные_вопросы(profile):
    """supports_web_search — про саму программу-модель, web_search_possible — про
    то, будет ли поиск. Смешать их значит снова связать возможность с выбором
    провайдера, от чего и уходили."""
    cfg = настроен(config.load())
    cfg["llm"]["provider"] = "ollama"
    assert providers.supports_web_search("ollama") is False
    assert providers.web_search_possible(cfg) is True


def test_модель_не_получает_сетевых_инструментов(monkeypatch, profile):
    """Ищет приложение — значит модели инструменты не нужны вовсе. Это и есть
    выигрыш в надёжности: что скачано, решаем мы, а не чужой текст."""
    from jobsearch import llm, scoring
    monkeypatch.setattr(websearch.requests, "get", lambda *a, **kw: Ответ(ВЫДАЧА["brave"]))
    запросы = []
    monkeypatch.setattr(llm, "ask_json",
                        lambda prompt, **kw: запросы.append(kw.get("allowed_tools")) or {})
    cfg = настроен(config.load())
    cfg["llm"].update(provider="ollama", deep_model="gemma2:9b")
    config.save(cfg)

    scoring.deep_analyze({"title": "T", "company": "Acme", "location": "Berlin",
                          "description": "x" * 400},
                         cfg, "CV", lambda _m: None, research=True)

    assert запросы, "модель не звали"
    assert all(not и for и in запросы), "модели всё-таки выдали сетевые инструменты"


def test_найденное_доезжает_до_запроса(monkeypatch, profile):
    from jobsearch import llm, scoring
    monkeypatch.setattr(websearch.requests, "get", lambda *a, **kw: Ответ(ВЫДАЧА["brave"]))
    запросы = []
    monkeypatch.setattr(llm, "ask_json", lambda prompt, **kw: запросы.append(prompt) or {})
    cfg = настроен(config.load())
    cfg["llm"].update(provider="ollama", deep_model="gemma2:9b")
    config.save(cfg)

    scoring.deep_analyze({"title": "T", "company": "Acme", "location": "",
                          "description": "x" * 400},
                         cfg, "CV", lambda _m: None, research=True)

    assert any("acme.example/jobs" in p for p in запросы), \
        "выдача не попала в запрос — модели нечего пересказывать"


def test_без_выдачи_модель_не_просят_сочинять(monkeypatch, profile):
    """Пустой поиск — не повод спрашивать: ответить она чем-то должна, и это
    будет выдумка."""
    from jobsearch import llm, scoring
    monkeypatch.setattr(websearch.requests, "get", lambda *a, **kw: Ответ({"web": {"results": []}}))
    запросы = []
    monkeypatch.setattr(llm, "ask_json", lambda prompt, **kw: запросы.append(prompt) or {})
    cfg = настроен(config.load())
    cfg["llm"].update(provider="ollama", deep_model="gemma2:9b")
    config.save(cfg)

    scoring.deep_analyze({"title": "T", "company": "Acme", "location": "",
                          "description": "x" * 400},
                         cfg, "CV", lambda _m: None, research=True)

    assert not any("Найди в интернете" in p for p in запросы), \
        "спросили о том, чего программа не нашла"
