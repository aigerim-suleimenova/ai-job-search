"""С этим сервером вправе разговаривать только сама программа.

Он слушает петлю, из сети до него не дотянуться — но «только с этого компьютера»
не то же самое, что «только из этой программы». Любая страница, открытая в
обычном браузере, живёт на том же компьютере, и до сих пор ей никто не мешал:
три десятка действий, меняющих состояние, не спрашивали, кто просит. Одной формы
на чужом сайте хватало, чтобы перенаправить сводки в чужой Telegram, подменить
путь к программе-модели или стереть все данные разом.

А проверка Host закрывает подмену DNS: имя, которое минуту назад вело на чужой
сервер, а теперь на 127.0.0.1, делает чужой сценарий «своим» для браузера — и
тогда он может уже не только слать, но и читать.
"""
import pytest

pytest.importorskip("httpx", reason="TestClient требует httpx")

from fastapi.testclient import TestClient  # noqa: E402

import app as app_module  # noqa: E402


@pytest.fixture
def client(profile):
    with TestClient(app_module.app, base_url="http://127.0.0.1:8765") as c:
        c.cookies.set("profile", profile)
        yield c


# --- Чужое имя ------------------------------------------------------------------

@pytest.mark.parametrize("host", [
    "evil.example",
    "attacker.tld:8765",
    "rebind.example:8766",
    "ai-job-search.attacker.test",
])
def test_на_чужое_имя_не_отзываемся(client, host):
    """Ровно то, чем пользуется подмена DNS."""
    r = client.get("/simple", headers={"Host": host})
    assert r.status_code == 403, f"ответили на Host: {host}"


@pytest.mark.parametrize("host", [
    "127.0.0.1:8765", "127.0.0.1", "localhost:8765", "localhost", "[::1]:8765",
])
def test_на_свои_имена_отзываемся(client, host):
    """Обратная сторона: собственное окно программы стучится по всем этим адресам."""
    r = client.get("/simple", headers={"Host": host}, follow_redirects=True)
    assert r.status_code == 200, f"не ответили на своё же имя: {host}"


def test_чужое_имя_закрывает_и_статику(client):
    """Проверка стоит раньше всего остального, а не только перед страницами."""
    assert client.get("/static/style.css", headers={"Host": "evil.example"}).status_code == 403


# --- Чужое происхождение --------------------------------------------------------

ДЕЙСТВИЯ = [
    ("/save", {"telegram_token": "чужой", "telegram_chat": "1"}),
    ("/app/reset", {}),
    ("/run", {}),
    ("/models/select", {"model": "haiku"}),
    ("/notify/telegram", {"bot_token": "чужой", "chat_id": "1"}),
    ("/set_theme", {"theme": "dark"}),
    ("/profile/delete", {"slug": "me"}),
    ("/app/update/install", {}),
]


@pytest.mark.parametrize("path,data", ДЕЙСТВИЯ)
def test_форма_с_чужого_сайта_не_срабатывает(client, path, data):
    r = client.post(path, data=data,
                    headers={"Origin": "https://evil.example",
                             "Referer": "https://evil.example/page"},
                    follow_redirects=False)
    assert r.status_code == 403, f"{path} выполнился по чужой просьбе"


@pytest.mark.parametrize("path,data", ДЕЙСТВИЯ)
def test_браузер_прямо_называет_чужое_происхождение(client, path, data):
    """Sec-Fetch-Site ставит сам браузер, со страницы его не подделать."""
    r = client.post(path, data=data, headers={"Sec-Fetch-Site": "cross-site"},
                    follow_redirects=False)
    assert r.status_code == 403, f"{path} выполнился при cross-site"


def test_свои_действия_проходят(client):
    """Главное, чего нельзя сломать: собственные формы программы."""
    for заголовки in ({"Origin": "http://127.0.0.1:8765", "Sec-Fetch-Site": "same-origin"},
                      {"Sec-Fetch-Site": "none"},          # адрес набрали руками
                      {}):                                  # встроенный браузер молчит
        r = client.post("/set_theme", data={"theme": "auto"},
                        headers=заголовки, follow_redirects=False)
        assert r.status_code == 303, f"своя же форма отклонена при {заголовки}"


def test_чтение_чужим_не_запрещаем_если_имя_наше(client):
    """Ограничение только на действия: обычный переход по ссылке из самой
    программы приходит без Origin, и мешать ему нечего."""
    assert client.get("/simple", headers={"Sec-Fetch-Site": "cross-site"},
                      follow_redirects=True).status_code == 200


def test_подмена_dns_не_даёт_прочитать(client):
    """Соединение двух приёмов: после переразрешения имени страница злоумышленника
    становится «своей» для браузера — но не для нас."""
    r = client.get("/results", headers={"Host": "evil.example",
                                        "Origin": "http://evil.example",
                                        "Sec-Fetch-Site": "same-origin"})
    assert r.status_code == 403
