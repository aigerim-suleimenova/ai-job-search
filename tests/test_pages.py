"""Дым: страницы должны открываться.

Сегодня эти восемь адресов проверялись руками после каждой правки — и один раз
пропущенная проверка стоила «Internal Server Error» у человека на экране.
Сеть и модель не задействованы: страницы рисуются из базы и настроек.
"""
import re

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


# --- Русские слова на английских страницах -------------------------------------

КИРИЛЛИЦА = re.compile(r"[А-Яа-яЁё]")
# Не всякая кириллица на английской странице — беда. «Русский» в списке языков
# и имя профиля, которое человек написал сам, там и должны быть по-русски.
СВОИ_ИМЕНА = re.compile(r'<select name="(ui_lang|output_lang|slug)".*?</select>', re.S)
ПОДСКАЗКА_ЯЗЫКА = 'title="Язык / Language"'


def человеческий_текст(html: str) -> str:
    """Страница без того, что человек не читает: комментарии в скриптах и
    списки языков. Двойную косую в «https://» за комментарий не принимаем."""
    html = СВОИ_ИМЕНА.sub("", html).replace(ПОДСКАЗКА_ЯЗЫКА, "")
    html = re.sub(r"/\*.*?\*/", "", html, flags=re.S)
    return re.sub(r"""(?<![:"'])//.*$""", "", html, flags=re.M)


@pytest.fixture
def английский(client, profile):
    """Англоязычный человек. Профиль переименован по-латински: имя, которое
    человек написал сам, — не текст программы, и путать их в проверке нельзя."""
    from jobsearch import config, profiles
    profiles.rename(profile, "Alex")
    cfg = config.load()
    cfg["ui"].update(lang="en", output_lang="en")
    cfg["llm"]["provider"] = "ollama"      # его имя подставляется в текст страницы
    config.save(cfg)
    return client


@pytest.mark.parametrize("path", СТРАНИЦЫ)
def test_на_английском_нет_русских_слов(английский, path):
    """Ключи-то переведены, но текст приходил и мимо них: имя провайдера,
    пометки моделей, почтовые службы, имя профиля по умолчанию."""
    текст = человеческий_текст(английский.get(path).text)
    найдено = [ln.strip() for ln in текст.splitlines() if КИРИЛЛИЦА.search(ln)]
    assert not найдено, f"{path}: " + " | ".join(найдено[:4])


def test_на_английском_нет_русских_слов_с_данными(английский):
    from jobsearch import db
    db.save_job(job("k1", score=88, verified=True, reason="strong match",
                    advice='{"cv_changes":["one"],"linkedin_changes":[],"cover_hint":"",'
                           '"salary_estimate":"60k","company_insights":[],"sources":[]}'),
                run_id=1)
    for path in ("/results", "/coverage"):
        текст = человеческий_текст(английский.get(path).text)
        найдено = [ln.strip() for ln in текст.splitlines() if КИРИЛЛИЦА.search(ln)]
        assert not найдено, f"{path}: " + " | ".join(найдено[:4])


def test_старое_покрытие_с_русским_типом_источника_переводится(английский):
    """Тип источника хранится в базе: у прогонов, записанных до перевода, там
    так и лежит «агрегатор» — показать его надо всё равно по-английски."""
    import json as _json
    from jobsearch import db
    run_id = db.start_run()
    db.finish_run(run_id, found=1, fresh=1, matched=1, status="ok", log="",
                  coverage=_json.dumps([{"name": "Remotive", "url": "https://remotive.com",
                                         "kind": "агрегатор", "count": 3, "error": None}]))
    текст = английский.get("/coverage").text
    assert "aggregator" in текст, "старое значение не перевелось"
    assert "агрегатор" not in человеческий_текст(текст)
