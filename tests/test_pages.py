"""Дым: страницы должны открываться.

Сегодня эти восемь адресов проверялись руками после каждой правки — и один раз
пропущенная проверка стоила «Internal Server Error» у человека на экране.
Сеть и модель не задействованы: страницы рисуются из базы и настроек.
"""
import pytest

pytest.importorskip("httpx", reason="TestClient требует httpx")

from fastapi.testclient import TestClient  # noqa: E402

from jobsearch import db  # noqa: E402

from conftest import job  # noqa: E402

СТРАНИЦЫ = ["/", "/simple", "/results", "/coverage", "/models", "/notify", "/cv/check", "/welcome"]


@pytest.fixture
def client(profile):
    import app as app_module
    with TestClient(app_module.app) as c:
        c.cookies.set("profile", profile)
        yield c


@pytest.mark.parametrize("path", СТРАНИЦЫ)
def test_страница_открывается_на_пустой_базе(client, path):
    assert client.get(path).status_code == 200


@pytest.mark.parametrize("path", СТРАНИЦЫ)
def test_страница_открывается_с_данными(client, path, profile):
    db.save_job(job("k1", score=88, verified=True,
                    advice='{"cv_changes":["раз"],"linkedin_changes":[],"cover_hint":"",'
                           '"salary_estimate":"60k","company_insights":[],"sources":[]}'), run_id=1)
    db.save_job(job("k2", score=40, posted_at=""), run_id=1)
    assert client.get(path).status_code == 200


def test_фильтры_результатов_не_роняют_страницу(client, profile):
    db.save_job(job("k1", score=88), run_id=1)
    запросы = [
        "/results?min=70",
        "/results?sort=posted&viewed=new&source=direct",
        "/results?posted_from=2026-07-01&posted_to=2026-07-31",
        "/results?posted_from=мусор",          # человек ввёл ерунду руками
        "/results?run=999",                    # прогона с таким номером нет
        "/results?min=не-число",
    ]
    for q in запросы:
        r = client.get(q)
        assert r.status_code in (200, 422), f"{q} → {r.status_code}"


def test_все_языки_рисуют_страницы(client, profile):
    from jobsearch import config, i18n
    db.save_job(job("k1", score=88), run_id=1)
    for lang in i18n.UI_LANGS:
        cfg = config.load()
        cfg["ui"]["lang"] = lang
        config.save(cfg)
        r = client.get("/results")
        assert r.status_code == 200, f"страница результатов упала на языке {lang}"


def test_несуществующая_вакансия_не_роняет(client, profile):
    assert client.get("/cv/999999").status_code in (404, 502)


def test_выбор_модели_возвращает_к_месту_нажатия(client, profile):
    """После «Использовать» страница перезагружается — и раньше человек
    оказывался в начале длинного списка, а не там, где нажимал."""
    r = client.post("/models/select",
                    data={"model": "claude-haiku-4-5", "back": "/models", "anchor": "model-claude-haiku-4-5"},
                    follow_redirects=False)
    assert r.status_code == 303
    assert r.headers["location"].endswith("#model-claude-haiku-4-5"), r.headers["location"]


def test_на_знакомстве_ведёт_к_кнопке_продолжить(client, profile):
    r = client.post("/models/select",
                    data={"model": "claude-haiku-4-5", "back": "/welcome", "anchor": "welcome-continue"},
                    follow_redirects=False)
    assert r.status_code == 303
    assert r.headers["location"].endswith("#welcome-continue")


def test_без_якоря_перенаправление_прежнее(client, profile):
    r = client.post("/models/select", data={"model": "x", "back": "/models"}, follow_redirects=False)
    assert r.status_code == 303 and "#" not in r.headers["location"]
