"""Как программа ведёт себя на чужих сайтах.

Проверяется без сети: requests.get подменяется, а вместо robots.txt отдаётся
заранее заготовленный текст. Иначе тест зависел бы от настроения чужого сервера.
"""
import time

import pytest

from jobsearch.collectors import web


@pytest.fixture(autouse=True)
def чистый_кэш():
    web._robots.clear()
    web._last_hit.clear()
    yield
    web._robots.clear()
    web._last_hit.clear()


class Ответ:
    def __init__(self, text="", status=200):
        self.text = text
        self.status_code = status
        self.url = "https://example.com/"

    def raise_for_status(self):
        pass


def подменить(monkeypatch, robots="", **kw):
    """Отдаёт заданный robots.txt, остальные адреса — пустую страницу."""
    вызовы = []

    def fake_get(url, **rest):
        вызовы.append(url)
        if url.endswith("/robots.txt"):
            return Ответ(robots, kw.get("robots_status", 200))
        return Ответ("<html>ok</html>")

    monkeypatch.setattr(web.requests, "get", fake_get)
    return вызовы


def test_представляется_честно():
    ua = web.UA["User-Agent"]
    assert "Mozilla" not in ua, "маскировка под браузер вернулась"
    assert "ai-job-search" in ua
    assert "github.com" in ua, "по имени должно быть понятно, куда писать"


def test_запрет_в_robots_соблюдается(monkeypatch):
    подменить(monkeypatch, robots="User-agent: *\nDisallow: /careers\n")
    assert web.allowed("https://example.com/about") is True
    assert web.allowed("https://example.com/careers/all") is False


def test_запрещённая_страница_не_запрашивается(monkeypatch):
    вызовы = подменить(monkeypatch, robots="User-agent: *\nDisallow: /\n")
    assert web.get("https://example.com/careers", respect_robots=True) is None
    assert not [u for u in вызовы if not u.endswith("robots.txt")], "запрос всё-таки ушёл"


def test_без_robots_ходим_как_обычно(monkeypatch):
    подменить(monkeypatch, robots="", robots_status=404)
    assert web.allowed("https://example.com/careers") is True
    assert web.get("https://example.com/careers", respect_robots=True) is not None


def test_битый_robots_не_роняет(monkeypatch):
    подменить(monkeypatch, robots="\x00\x01 мусор ][")
    assert web.allowed("https://example.com/careers") is True


def test_пауза_между_запросами_к_одному_хосту(monkeypatch):
    подменить(monkeypatch)
    monkeypatch.setattr(web, "DELAY", 0.2)
    начало = time.monotonic()
    for _ in range(3):
        web.get("https://example.com/page")
    прошло = time.monotonic() - начало
    assert прошло >= 0.4, f"три запроса уложились в {прошло:.2f} c — паузы нет"


def test_разные_хосты_друг_друга_не_ждут(monkeypatch):
    подменить(monkeypatch)
    monkeypatch.setattr(web, "DELAY", 0.3)
    начало = time.monotonic()
    web.get("https://a.example/page")
    web.get("https://b.example/page")
    assert time.monotonic() - начало < 0.3, "хосты не должны стоять в общей очереди"


def test_robots_запрашивается_один_раз_на_хост(monkeypatch):
    вызовы = подменить(monkeypatch, robots="User-agent: *\nDisallow: /nope\n")
    monkeypatch.setattr(web, "DELAY", 0)
    for i in range(3):
        web.get(f"https://example.com/page{i}", respect_robots=True)
    assert len([u for u in вызовы if u.endswith("robots.txt")]) == 1


def test_crawl_delay_из_robots_уважается(monkeypatch):
    """Стандартный парсер понимает только целые задержки — дробные молча
    игнорирует, поэтому и проверяем целую."""
    подменить(monkeypatch, robots="User-agent: *\nCrawl-delay: 1\n")
    monkeypatch.setattr(web, "DELAY", 0.01)
    web.get("https://example.com/a", respect_robots=True)
    начало = time.monotonic()
    web.get("https://example.com/b", respect_robots=True)
    assert time.monotonic() - начало >= 0.9, "сайт просил ждать дольше — надо ждать"
