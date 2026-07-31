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


def test_проверка_cv_уходит_в_фон_и_не_держит_страницу(client, profile, monkeypatch):
    """Раньше это была ссылка, которая молча считала минутами. Теперь страница
    возвращается сразу, а работа идёт в фоне."""
    import time as _t
    from jobsearch import config, cvcheck

    config.save_cv("резюме.txt", ("Виктор Лавров, фронтенд-инженер. " * 8).encode("utf-8"))
    monkeypatch.setattr(cvcheck, "analyze", lambda cfg: _t.sleep(1) or {})

    начало = _t.monotonic()
    r = client.post("/cv/check/run", follow_redirects=False)
    assert r.status_code == 303
    assert _t.monotonic() - начало < 0.5, "страница ждала окончания проверки"

    assert client.get("/cv/check/status").json()["running"] is True
    assert "cvcheck_running" not in client.get("/cv/check").text  # ключ переведён, а не показан сырым


def test_неудачная_проверка_cv_показывает_причину(client, profile, monkeypatch):
    from jobsearch import config, cvcheck
    config.save_cv("резюме.txt", ("Виктор Лавров, фронтенд-инженер. " * 8).encode("utf-8"))

    def взорваться(cfg):
        raise RuntimeError("модель не ответила")

    monkeypatch.setattr(cvcheck, "analyze", взорваться)
    client.post("/cv/check/run", follow_redirects=False)
    for _ in range(50):
        if not client.get("/cv/check/status").json()["running"]:
            break
    статус = client.get("/cv/check/status").json()
    assert "модель не ответила" in статус["error"]
    assert "модель не ответила" in client.get("/cv/check").text, "причина не показана человеку"


def test_несобравшееся_cv_объясняет_а_не_отдаёт_502(client, profile, monkeypatch):
    """Пустая вкладка с «502» человеку ничего не говорит."""
    from jobsearch import config, db, scoring
    from conftest import job as образец

    config.save_cv("резюме.txt", ("Виктор Лавров, фронтенд-инженер. " * 8).encode("utf-8"))
    db.save_job(образец("k1"), run_id=1)
    job_id = db.matched_jobs(min_score=0)[0]["id"]

    def взорваться(*_a, **_kw):
        raise RuntimeError("модель вернула прозу вместо JSON")

    monkeypatch.setattr(scoring, "generate_cv", взорваться)
    r = client.get(f"/cv/{job_id}")
    assert r.status_code == 200, "человек получил голую ошибку вместо объяснения"
    assert "JSON" in r.text or "модель" in r.text.lower()
