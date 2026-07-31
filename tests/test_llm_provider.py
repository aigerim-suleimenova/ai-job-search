"""Выбранный провайдер должен доезжать до каждого вызова модели.

Триаж — единственный этап, который зовёт модель пачками, и он один забывал
передать provider. Человек выбирал локальную модель, а приложение молча шло в
claude CLI: на Windows тот падал на кодировке русского промпта, и в журнале
вместо «локальная модель не отвечает» было «charmap codec can't encode».
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
    """Подменяет providers.call и запоминает, с чем его позвали."""
    вызовы = []

    def fake(prompt, provider, model, timeout=600, allowed_tools=None, claude_bin="claude"):
        вызовы.append({"provider": provider, "model": model, "prompt": prompt})
        return "[]"

    monkeypatch.setattr(providers, "call", fake)
    return вызовы


def _claude_запрещён(monkeypatch) -> None:
    """Любой уход в claude CLI — это провал теста, а не сетевой поход."""
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
    """Русский промпт уходит в Ollama целиком: HTTP-путь мимо cp1252.

    Проверяем, что кириллица дошла, а не то, с какого слова промпт начинается:
    впереди может стоять языковая шапка, если язык ответа отличается от языка
    самого промпта. Именно на первых кириллических буквах и падал cp1252.
    """
    вызовы = _перехват(monkeypatch)
    scoring.triage([{"title": "Frontend Engineer", "company": "Northwind"}],
                   локальный, log=lambda *_: None)
    промпт = вызовы[0]["prompt"]
    assert "Ты" in промпт, "русский текст промпта не дошёл до модели"
    assert any("А" <= c <= "я" for c in промпт)


# --- Кодировка при вызове CLI ---------------------------------------------

@pytest.mark.skipif(sys.platform != "win32", reason="cp1252 по умолчанию только на Windows")
def test_системная_кодировка_действительно_ронялa_бы():
    """Проверка самой проверки: без encoding= русский промпт не пережил бы stdin."""
    with pytest.raises(UnicodeEncodeError):
        subprocess.run([sys.executable, "-c", "import sys; sys.stdin.read()"],
                       input=scoring.TRIAGE_PROMPT, capture_output=True,
                       text=True, timeout=30)


def test_cli_получает_русский_промпт_в_utf8(monkeypatch):
    """Промпт должен дойти до CLI байт в байт, какой бы ни была локаль системы."""
    получено = {}

    class Готово:
        returncode = 0
        stdout = '{"result": "[]"}'
        stderr = ""

    def fake_run(cmd, **kw):
        получено.update(kw)
        # то же, что сделает настоящий subprocess: кодирует ввод указанной кодировкой
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
