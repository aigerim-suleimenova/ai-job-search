"""The chosen provider has to reach every call to the model.

Triage is the one stage that calls the model in batches, and it was the one that
forgot to pass provider along. A person picked a local model and the app went
quietly to claude CLI: on Windows that fell over on the encoding of a Russian
prompt, and the log said "charmap codec can't encode" instead of "the local
model is not answering".
"""
import subprocess
import sys

import pytest

from jobsearch import llm, providers, scoring
from jobsearch.collectors import crawler


@pytest.fixture
def локальный(cfg):
    cfg["llm"]["provider"] = "ollama"
    cfg["llm"]["triage_model"] = "qwen2.5:7b"
    return cfg


def _перехват(monkeypatch) -> list:
    """Stands in for providers.call and remembers what it was called with."""
    вызовы = []

    # llm — весь блок настроек: своему адресу нужны не только имя модели,
    # но и адрес с ключом, и они доезжают именно этим аргументом
    # schema — описание ответа для малых моделей. Заглушка о нём знать обязана:
    # без этого она падает на неизвестном аргументе, и падает во всех тестах разом.
    def fake(prompt, provider, model, timeout=600, allowed_tools=None,
             claude_bin="claude", llm=None, schema=None):
        вызовы.append({"provider": provider, "model": model, "prompt": prompt,
                       "llm": llm, "schema": schema})
        return "[]"

    monkeypatch.setattr(providers, "call", fake)
    return вызовы


def _claude_запрещён(monkeypatch) -> None:
    """Any trip to claude CLI is a failed test, not a trip to the network."""
    def взрыв(*a, **kw):
        raise AssertionError("вызван claude CLI, хотя выбрана локальная модель")

    monkeypatch.setattr(subprocess, "run", взрыв)


def test_триаж_идёт_в_выбранного_провайдера(локальный, monkeypatch):
    вызовы = _перехват(monkeypatch)
    _claude_запрещён(monkeypatch)

    scoring.triage([{"title": "Frontend Engineer", "company": "Northwind",
                     "description": "React"}], локальный, log=lambda *_: None)

    assert [в["provider"] for в in вызовы] == ["ollama"]
    assert вызовы[0]["model"] == "qwen2.5:7b"


def test_краулер_идёт_в_выбранного_провайдера(локальный, monkeypatch):
    вызовы = _перехват(monkeypatch)
    _claude_запрещён(monkeypatch)

    class Страница:
        url = "https://example.com/careers"
        text = ("<html><body>" + "Open positions at Northwind. " * 20 +
                "<a href='/jobs/1'>Frontend Engineer</a></body></html>")

        def raise_for_status(self):
            pass

    monkeypatch.setattr(crawler.web, "get", lambda *a, **kw: Страница())

    crawler.crawl_company("Northwind", "https://example.com", локальный, log=lambda *_: None)

    assert вызовы, "краулер не позвал модель"
    assert all(в["provider"] == "ollama" for в in вызовы)


def test_локальная_модель_не_трогает_кодировку_системы(локальный, monkeypatch):
    """A Russian prompt reaches Ollama whole: the HTTP path goes round cp1252.

    We check that the Cyrillic arrived, not which word the prompt starts with: a
    language banner may stand in front of it when the answer's language differs
    from the prompt's own. It was on those first Cyrillic letters that cp1252
    used to fall over.
    """
    вызовы = _перехват(monkeypatch)
    scoring.triage([{"title": "Frontend Engineer", "company": "Northwind"}],
                   локальный, log=lambda *_: None)
    промпт = вызовы[0]["prompt"]
    assert "Ты" in промпт, "русский текст промпта не дошёл до модели"
    assert any("А" <= c <= "я" for c in промпт)


# --- Encoding when calling the CLI -----------------------------------------

@pytest.mark.skipif(sys.platform != "win32", reason="cp1252 по умолчанию только на Windows")
def test_системная_кодировка_действительно_ронялa_бы():
    """Checking the check: without encoding= a Russian prompt would not survive stdin."""
    with pytest.raises(UnicodeEncodeError):
        subprocess.run([sys.executable, "-c", "import sys; sys.stdin.read()"],
                       input=scoring.TRIAGE_PROMPT, capture_output=True,
                       text=True, timeout=30)


def test_cli_получает_русский_промпт_в_utf8(monkeypatch):
    """The prompt has to reach the CLI byte for byte, whatever the system locale is."""
    получено = {}

    class Готово:
        returncode = 0
        stdout = '{"result": "[]"}'
        stderr = ""

    def fake_run(cmd, **kw):
        получено.update(kw)
        # the same thing a real subprocess does: encodes the input with the given encoding
        kw["input"].encode(kw["encoding"], kw.get("errors", "strict"))
        return Готово()

    monkeypatch.setattr(subprocess, "run", fake_run)
    monkeypatch.setattr(providers, "resolve_bin", lambda name: "/usr/bin/claude")

    llm._ask_once("Ты — ассистент по поиску работы.", model="haiku",
                  claude_bin="claude", timeout=30, allowed_tools=None)

    assert получено["encoding"] == "utf-8"


def test_провайдерский_вызов_claude_тоже_в_utf8(monkeypatch):
    получено = {}

    class Готово:
        returncode = 0
        stdout = '{"result": "[]"}'
        stderr = ""

    def fake_run(cmd, **kw):
        получено.update(kw)
        kw["input"].encode(kw["encoding"], kw.get("errors", "strict"))
        return Готово()

    monkeypatch.setattr(subprocess, "run", fake_run)
    monkeypatch.setattr(providers, "resolve_bin", lambda name: "/usr/bin/claude")
    monkeypatch.setattr(providers, "login_env", lambda: {})

    providers.call_claude("Ты — ассистент по поиску работы.", "haiku", 30, None, "claude")

    assert получено["encoding"] == "utf-8"


# --- Как спрашивать малую модель ------------------------------------------------

def test_местной_модели_вакансии_дробятся(monkeypatch):
    """Восемь за раз малой модели не по силам, и это измерено. На восьми
    вакансиях с заранее известным ответом mistral:7b путал номера: балл,
    предназначенный одной, доставался другой — так фронтенд у SAP-интегратора и
    получал 85. На трёх — восемь верных из восьми, три прогона подряд."""
    from jobsearch import scoring
    assert scoring.batch_for("ollama") == 3
    for облачный in ("claude_cli", "openai_api", "codex_cli", "cursor_cli"):
        assert scoring.batch_for(облачный) == 8, f"{облачный}: дробить незачем и дорого"


def test_схема_уходит_в_модель(monkeypatch, profile):
    """Без неё малая модель отвечает связным текстом без нужного поля, и
    повторять запрос бесполезно — ответит так же."""
    from jobsearch import config, providers, scoring
    поймано = {}

    def fake(prompt, provider, model, timeout=600, allowed_tools=None,
             claude_bin="claude", llm=None, schema=None):
        поймано["schema"] = schema
        return "[]"

    monkeypatch.setattr(providers, "call", fake)
    cfg = config.load()
    cfg["llm"]["provider"] = "ollama"
    scoring.triage([{"title": "SAP", "description": "x", "company": "A"}],
                   cfg, log=lambda *a, **k: None, cv="CV")

    схема = поймано.get("schema")
    assert схема, "схема до провайдера не доехала"
    поля = list(схема["items"]["properties"])
    assert поля.index("reason") < поля.index("match"), \
        "число называется раньше довода — довод тогда подгоняется под него"
    assert "verdict" in схема["items"]["required"]


def test_ollama_получает_окно_и_нулевую_температуру(monkeypatch):
    """У Ollama своё окно по умолчанию, и оно меньше того, что держит модель.
    А температура выше нуля означает, что один и тот же вопрос даёт разные
    оценки — здесь не сочиняют, а судят."""
    from jobsearch import providers
    поймано = {}

    class Ответ:
        status_code = 200

        @staticmethod
        def raise_for_status():
            pass

        @staticmethod
        def json():
            return {"response": "[]"}

    def fake_post(url, json=None, timeout=None):
        поймано.update(json or {})
        return Ответ()

    monkeypatch.setattr(providers.requests, "post", fake_post)
    providers.call_ollama("привет", "mistral:7b", timeout=30, schema={"type": "object"})

    assert поймано["options"]["temperature"] == 0
    assert поймано["options"]["num_ctx"] >= 8192
    assert поймано["format"] == {"type": "object"}
