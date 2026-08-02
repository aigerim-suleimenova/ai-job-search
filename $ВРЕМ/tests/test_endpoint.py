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

    @property
    def text(self):
        return "" if isinstance(self._payload, Exception) else str(self._payload)

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
НАСТРОЙКИ = {"api_base": "https://openrouter.ai/api/v1", "api_key": "sk-or-v1-0a1b2c3d",
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

    assert послано["headers"]["Authorization"] == "Bearer sk-or-v1-0a1b2c3d"
    assert "sk-or-v1-0a1b2c3d" not in послано["url"]


def test_без_ключа_тоже_работает(monkeypatch):
    """У LM Studio и llama.cpp на своём компьютере ключа попросту нет."""
    послано = служба(monkeypatch, ОТВЕТ)
    свой = dict(НАСТРОЙКИ, api_base="http://127.0.0.1:1234/v1", api_key="")

    providers.call_openai_api("привет", свой, timeout=30)

    assert "Authorization" not in послано["headers"], "послали пустой ключ"


def test_ключ_не_попадает_в_текст_ошибки(monkeypatch):
    def взорваться(url, **kw):
        raise requests.ConnectionError(f"нет связи с {url}?key=sk-or-v1-0a1b2c3d")

    monkeypatch.setattr(providers.requests, "post", взорваться)

    with pytest.raises(providers.ProviderError) as поймано:
        providers.call_openai_api("привет", НАСТРОЙКИ, timeout=30)

    assert "sk-or-v1-0a1b2c3d" not in str(поймано.value) + str(поймано.value.fmt)


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


@pytest.mark.parametrize("код", [400, 401, 402, 429, 500])
def test_отказ_службы_объясняется_её_же_словами(monkeypatch, код):
    """Найдено живой проверкой, а не тут.

    Настоящие службы отвечают на беду кодом ошибки и телом с причиной. Тест
    рядом брал 200 с ошибкой в теле — так не отвечает никто, и оттого мимо
    прошло вот что: raise_for_status превращал ответ в HTTPError, и человек с
    кончившимися деньгами на OpenRouter читал «служба недоступна». Служба
    ответила исправно и назвала причину, а мы её выбрасывали."""
    служба(monkeypatch, {"error": {"message": "insufficient_quota"}}, status=код)

    with pytest.raises(providers.ProviderError) as поймано:
        providers.call_openai_api("привет", НАСТРОЙКИ, timeout=30)

    видно = str(поймано.value)
    assert "insufficient_quota" in видно, f"причина потеряна: {видно}"
    assert поймано.value.key != "prov_err_api_unreachable", "служба ответила, а мы говорим, что нет"


def test_отказ_без_внятного_тела_называет_хотя_бы_номер(monkeypatch):
    служба(monkeypatch, "<html>502 Bad Gateway</html>", status=502)
    with pytest.raises(providers.ProviderError) as поймано:
        providers.call_openai_api("привет", НАСТРОЙКИ, timeout=30)
    assert "502" in str(поймано.value)


def test_ключ_с_лишним_не_выдаёт_себя_за_беду_службы(monkeypatch):
    """Тоже находка живой проверки. В заголовок запроса можно положить только
    латиницу, и requests падает на этом UnicodeEncodeError — а это ValueError,
    и человек читал «служба ответила не в формате JSON». Про службу, до которой
    ничего не дошло."""
    служба(monkeypatch, ОТВЕТ)
    с_лишним = dict(НАСТРОЙКИ, api_key="sk-это-не-ключ")

    with pytest.raises(providers.ProviderError) as поймано:
        providers.call_openai_api("привет", с_лишним, timeout=30)

    assert поймано.value.key == "prov_err_api_bad_key"


def test_не_json_объясняется(monkeypatch):
    служба(monkeypatch, ValueError("это не json"))
    with pytest.raises(providers.ProviderError) as поймано:
        providers.call_openai_api("привет", НАСТРОЙКИ, timeout=30)
    assert поймано.value.key == "prov_err_api_not_json"


# --- Как он выглядит для остальной программы -----------------------------------

def test_ставить_нечего_поэтому_он_готов_всегда():
    """Первый шаг спрашивает, что установить. Своему адресу устанавливать нечего.

    Готовность тут значила «адрес уже вписан», и карточка до этого висела с
    красной пометкой «не установлено» — рядом с кнопкой «Установить», которой
    нечего было делать. Ставить нечего, значит готов."""
    пусто = providers.available("claude", {})["openai_api"]
    заполнено = providers.available("claude", {"api_base": "https://a.example/v1"})["openai_api"]
    assert пусто["ready"] is True
    assert заполнено["ready"] is True


def test_и_адрес_и_модель_спрашиваются_на_втором_шаге():
    """Адрес, ключ и имя модели — это один вопрос «куда ходить», и задаётся он
    целиком на втором шаге.

    Раньше адрес жил в карточке на первом шаге, где на него не было ширины: поля
    вставали в строку и обрезались на середине подсказки — от https://openrouter…
    было видно «https://ope». Пока не вписано и то и другое, второй шаг не
    пройден."""
    пусто = {"provider": "openai_api"}
    без_модели = {"provider": "openai_api", "api_base": "https://a.example/v1"}
    без_адреса = {"provider": "openai_api", "api_model": "gpt-4o"}
    целиком = dict(без_модели, api_model="gpt-4o")
    assert providers.missing_piece(пусто) == "model"
    assert providers.missing_piece(без_модели) == "model"
    assert providers.missing_piece(без_адреса) == "model"
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


def test_имя_модели_берётся_у_своего_адреса_а_не_у_соседа():
    """triage_model остаётся от прежнего провайдера, и карточка «Свой адрес»
    писала «используется: haiku» — имя модели Claude Code, к которой этот адрес
    отношения не имеет."""
    from jobsearch import providers as p
    прежнее = {"provider": "openai_api", "triage_model": "haiku"}
    assert p.current_model(прежнее) == ""
    assert p.current_model(dict(прежнее, api_model="gpt-4o")) == "gpt-4o"
    assert p.current_model({"provider": "claude_cli", "triage_model": "haiku"}) == "haiku"


def test_страница_моделей_не_называет_чужую_модель(profile):
    """Ровно то же, но через страницу: значение доезжает до шаблона."""
    from conftest import client_for
    from jobsearch import config
    import app as приложение

    cfg = config.load()
    cfg["llm"].update(provider="openai_api", triage_model="haiku", api_model="")
    config.save(cfg)

    страница = client_for(приложение.app, profile).get("/models").text
    assert "haiku" not in страница, "показано имя модели прежнего провайдера"
