"""Свой адрес, говорящий на языке OpenAI.

Одним провайдером накрывается столько же, сколько всеми командными строками
вместе: OpenRouter с сотнями моделей, LM Studio и llama.cpp на своём компьютере,
vLLM, корпоративный шлюз, сам OpenAI. Протокол у них общий, и добавлять их по
одному пришлось бы бесконечно.
"""
import pytest
import requests

from jobsearch import providers


class Ответ:
    def __init__(self, payload, status=200):
        self._payload, self.status_code = payload, status

    def raise_for_status(self):
        if self.status_code >= 400:
            raise requests.HTTPError(f"{self.status_code} для {self._payload}")

    def json(self):
        if isinstance(self._payload, Exception):
            raise self._payload
        return self._payload


def служба(monkeypatch, payload, status=200):
    """Подменяет сеть и запоминает, что именно мы послали."""
    послано = {}

    def post(url, headers=None, json=None, timeout=None):
        послано.update(url=url, headers=headers or {}, body=json)
        return Ответ(payload, status)

    monkeypatch.setattr(providers.requests, "post", post)
    return послано


ОТВЕТ = {"choices": [{"message": {"content": "  готовый ответ  "}}]}
НАСТРОЙКИ = {"api_base": "https://openrouter.ai/api/v1", "api_key": "sk-секрет",
             "api_model": "anthropic/claude-sonnet-5"}


def test_запрос_уходит_куда_надо(monkeypatch):
    послано = служба(monkeypatch, ОТВЕТ)

    ответ = providers.call_openai_api("привет", НАСТРОЙКИ, timeout=30)

    assert ответ == "готовый ответ", "ответ не разобран или не обрезан"
    assert послано["url"] == "https://openrouter.ai/api/v1/chat/completions"
    assert послано["body"]["model"] == "anthropic/claude-sonnet-5"
    assert послано["body"]["messages"] == [{"role": "user", "content": "привет"}]
    assert послано["body"]["stream"] is False


def test_ключ_уходит_заголовком_а_не_в_адресе(monkeypatch):
    """На токене Telegram мы это уже проходили: адрес попадает в текст исключения,
    а оттуда — в журнал прогона, который человек кому-то показывает."""
    послано = служба(monkeypatch, ОТВЕТ)

    providers.call_openai_api("привет", НАСТРОЙКИ, timeout=30)

    assert послано["headers"]["Authorization"] == "Bearer sk-секрет"
    assert "sk-секрет" not in послано["url"]


def test_без_ключа_тоже_работает(monkeypatch):
    """У LM Studio и llama.cpp на своём компьютере ключа попросту нет."""
    послано = служба(monkeypatch, ОТВЕТ)
    свой = dict(НАСТРОЙКИ, api_base="http://127.0.0.1:1234/v1", api_key="")

    providers.call_openai_api("привет", свой, timeout=30)

    assert "Authorization" not in послано["headers"], "послали пустой ключ"


def test_ключ_не_попадает_в_текст_ошибки(monkeypatch):
    def взорваться(url, **kw):
        raise requests.ConnectionError(f"нет связи с {url}?key=sk-секрет")

    monkeypatch.setattr(providers.requests, "post", взорваться)

    with pytest.raises(providers.ProviderError) as поймано:
        providers.call_openai_api("привет", НАСТРОЙКИ, timeout=30)

    assert "sk-секрет" not in str(поймано.value) + str(поймано.value.fmt)


def test_лишний_слэш_в_адресе_не_ломает(monkeypatch):
    послано = служба(monkeypatch, ОТВЕТ)
    providers.call_openai_api("x", dict(НАСТРОЙКИ, api_base="https://a.example/v1/"), timeout=30)
    assert послано["url"] == "https://a.example/v1/chat/completions"


@pytest.mark.parametrize("не_хватает,ключ", [
    ("api_base", "prov_err_no_api_base"),
    ("api_model", "prov_err_no_api_model"),
])
def test_без_настроек_говорим_чего_не_хватает(не_хватает, ключ):
    свой = dict(НАСТРОЙКИ, **{не_хватает: ""})
    with pytest.raises(providers.ProviderError) as поймано:
        providers.call_openai_api("привет", свой, timeout=30)
    assert поймано.value.key == ключ


def test_чужая_форма_ответа_объясняется_а_не_роняет(monkeypatch):
    """У службы может быть своя ошибка в теле — человеку надо показать её, а не
    трассировку по чужому JSON."""
    служба(monkeypatch, {"error": {"message": "no credits left"}})

    with pytest.raises(providers.ProviderError) as поймано:
        providers.call_openai_api("привет", НАСТРОЙКИ, timeout=30)

    assert "no credits" in str(поймано.value)


def test_не_json_объясняется(monkeypatch):
    служба(monkeypatch, ValueError("это не json"))
    with pytest.raises(providers.ProviderError) as поймано:
        providers.call_openai_api("привет", НАСТРОЙКИ, timeout=30)
    assert поймано.value.key == "prov_err_api_not_json"


# --- Как он выглядит для остальной программы -----------------------------------

def test_готовность_определяется_адресом():
    """Искать на диске нечего: провайдер готов ровно тогда, когда адрес вписан."""
    пусто = providers.available("claude", {})["openai_api"]
    заполнено = providers.available("claude", {"api_base": "https://a.example/v1"})["openai_api"]
    assert пусто["ready"] is False
    assert заполнено["ready"] is True


def test_адрес_это_первый_шаг_а_модель_второй():
    """Ложится ровно на два шага знакомства: сперва куда ходить, потом чем думать."""
    без_адреса = {"provider": "openai_api"}
    без_модели = {"provider": "openai_api", "api_base": "https://a.example/v1"}
    целиком = dict(без_модели, api_model="gpt-4o")
    assert providers.missing_piece(без_адреса) == "provider"
    assert providers.missing_piece(без_модели) == "model"
    assert providers.missing_piece(целиком) == ""


def test_общий_вызов_доводит_настройки(monkeypatch):
    """providers.call получает весь блок настроек — иначе адрес и ключ до него
    просто не доедут."""
    послано = служба(monkeypatch, ОТВЕТ)

    providers.call("привет", "openai_api", model="не важно", timeout=30, llm=НАСТРОЙКИ)

    assert послано["body"]["model"] == "anthropic/claude-sonnet-5"


def test_веб_поиска_у_него_нет():
    """Обещать веб-поиск, которого может не быть, хуже, чем не обещать."""
    assert providers.supports_web_search("openai_api") is False
