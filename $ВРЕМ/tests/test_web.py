"""How the program behaves on other people's sites.

Checked without the network: requests.get is stood in for, and a prepared text is
handed back instead of robots.txt. Otherwise the test would depend on the mood of
somebody else's server.
"""
import time

import pytest

from jobsearch.collectors import web


@pytest.fixture(autouse=True)
def чистый_кэш(monkeypatch):
    web._robots.clear()
    web._last_hit.clear()
    # Проверка адреса спрашивает у системы, куда ведёт имя. Тесты в сеть не
    # ходят — ни за страницами, ни за именами, — поэтому отвечаем сами, обычным
    # адресом из интернета. Сама проверка при этом остаётся в работе: подменять
    # её целиком значило бы проверять код, из которого её вынули.
    monkeypatch.setattr(web.socket, "getaddrinfo",
                        lambda host, port, **kw: [(2, 1, 6, "", ("93.184.216.34", port))])
    yield
    web._robots.clear()
    web._last_hit.clear()


class Ответ:
    def __init__(self, text="", status=200, headers=None):
        self.text = text
        self.status_code = status
        self.url = "https://example.com/"
        self.headers = headers or {}

    def raise_for_status(self):
        pass

    def close(self):
        pass


def подменить(monkeypatch, robots="", **kw):
    """Hands back the given robots.txt, and an empty page for every other address."""
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
    """The standard parser understands whole-number delays only — it ignores
    fractional ones silently, which is why we check a whole one."""
    подменить(monkeypatch, robots="User-agent: *\nCrawl-delay: 1\n")
    monkeypatch.setattr(web, "DELAY", 0.01)
    web.get("https://example.com/a", respect_robots=True)
    начало = time.monotonic()
    web.get("https://example.com/b", respect_robots=True)
    assert time.monotonic() - начало >= 0.9, "сайт просил ждать дольше — надо ждать"


# --- Куда мы не пойдём ---------------------------------------------------------
#
# Работодатели попадают в список по ссылкам внутри уже собранных вакансий, а
# вакансии пишет кто угодно. Ссылка на 127.0.0.1 — это просьба сходить внутрь
# машины и принести оттуда то, до чего снаружи не дотянуться.

ВНУТРЕННИЕ = [
    ("http://127.0.0.1:11434/api/tags", "127.0.0.1", "петля: там наша же Ollama"),
    ("http://localhost:8765/settings", "127.0.0.1", "петля по имени"),
    ("http://169.254.169.254/latest/meta-data/", "169.254.169.254", "ключи облачной учётки"),
    ("http://192.168.1.1/", "192.168.1.1", "чужой роутер в домашней сети"),
    ("http://10.0.0.5/admin", "10.0.0.5", "частная сеть"),
    ("http://[::1]/", "::1", "петля по IPv6"),
]


def адреса(monkeypatch, ip: str):
    семейство = 30 if ":" in ip else 2
    monkeypatch.setattr(web.socket, "getaddrinfo",
                        lambda host, port, **kw: [(семейство, 1, 6, "", (ip, port))])


@pytest.mark.parametrize("url,ip,почему", ВНУТРЕННИЕ)
def test_внутрь_машины_и_сети_не_ходим(monkeypatch, url, ip, почему):
    адреса(monkeypatch, ip)
    with pytest.raises(web.Refused):
        web.check(url)


@pytest.mark.parametrize("схема", ["file", "ftp", "gopher", "data", ""])
def test_чужие_схемы_отсекаются(схема):
    адрес = f"{схема}://example.com/x" if схема else "example.com/x"
    with pytest.raises(web.Refused):
        web.check(адрес)


def test_обычный_сайт_пропускается(monkeypatch):
    """Обратная сторона: закрыть внутреннее нельзя ценой того, что перестанет
    работать сбор вакансий."""
    адреса(monkeypatch, "93.184.216.34")
    web.check("https://boards-api.greenhouse.io/v1/boards/acme/jobs")


def test_имя_ведущее_внутрь_не_обманет(monkeypatch):
    """Имя может быть каким угодно, а вести на петлю: смотрим не на имя, а на то,
    куда оно разрешается."""
    адреса(monkeypatch, "127.0.0.1")
    with pytest.raises(web.Refused):
        web.check("https://careers.example.com/jobs")


def test_проверяется_каждый_переход(monkeypatch):
    """Иначе проверка стоила бы ничего: сайт отвечает «идите на 127.0.0.1»,
    requests послушно идёт, и проверенный первый адрес оказывается ни при чём."""
    куда = {"https://jobs.example.com/": ("93.184.216.34", 302, "http://127.0.0.1:11434/api/tags"),
            "http://127.0.0.1:11434/api/tags": ("127.0.0.1", 200, "")}
    сходили = []

    def getaddrinfo(host, port, **kw):
        ip = "127.0.0.1" if host in ("127.0.0.1", "localhost") else "93.184.216.34"
        return [(2, 1, 6, "", (ip, port))]

    def fake_get(url, **kw):
        сходили.append(url)
        _, статус, location = куда[url]
        return Ответ(status=статус, headers={"location": location} if location else {})

    monkeypatch.setattr(web.socket, "getaddrinfo", getaddrinfo)
    monkeypatch.setattr(web.requests, "get", fake_get)

    with pytest.raises(web.Refused):
        web.get("https://jobs.example.com/")

    assert сходили == ["https://jobs.example.com/"], "по переходу всё-таки сходили"


def test_переход_на_обычный_сайт_проходится(monkeypatch):
    """И снова обратная сторона: переезды сайтов — обычное дело."""
    def fake_get(url, **kw):
        if url == "https://jobs.example.com/":
            return Ответ(status=301, headers={"location": "/careers/"})
        return Ответ("<html>вакансии</html>")

    monkeypatch.setattr(web.requests, "get", fake_get)
    r = web.get("https://jobs.example.com/")
    assert "вакансии" in r.text


def test_кольцо_переходов_обрывается(monkeypatch):
    monkeypatch.setattr(web.requests, "get",
                        lambda url, **kw: Ответ(status=302, headers={"location": "/снова"}))
    monkeypatch.setattr(web, "DELAY", 0)
    with pytest.raises(web.Refused):
        web.get("https://example.com/")


def test_robots_у_внутреннего_адреса_тоже_не_спрашиваем(monkeypatch):
    """Спросить «а можно к вам?» у 127.0.0.1 — это уже сходить к 127.0.0.1."""
    адреса(monkeypatch, "127.0.0.1")
    сходили = []
    monkeypatch.setattr(web.requests, "get",
                        lambda url, **kw: (сходили.append(url), Ответ(""))[1])

    web.allowed("http://127.0.0.1:11434/robots-ok")

    assert сходили == [], "за robots.txt всё-таки сходили внутрь машины"
