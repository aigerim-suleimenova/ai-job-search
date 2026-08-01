"""Smoke: the pages have to open.

These eight addresses used to be checked by hand after every change — and one
skipped check cost somebody an "Internal Server Error" on their screen. Neither
the network nor the model is involved: the pages are drawn from the database and
the settings.
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
        "/results?posted_from=мусор",          # someone typed nonsense by hand
        "/results?run=999",                    # there is no run with that number
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
    """After "Use" the page reloads — and a person used to end up at the top of a
    long list rather than where they had clicked."""
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
    """This used to be a link that thought silently for minutes. Now the page comes
    back at once and the work goes on in the background."""
    import time as _t
    from jobsearch import config, cvcheck

    config.save_cv("резюме.txt", ("Виктор Лавров, фронтенд-инженер. " * 8).encode("utf-8"))
    monkeypatch.setattr(cvcheck, "analyze", lambda cfg: _t.sleep(1) or {})

    начало = _t.monotonic()
    r = client.post("/cv/check/run", follow_redirects=False)
    assert r.status_code == 303
    assert _t.monotonic() - начало < 0.5, "страница ждала окончания проверки"

    assert client.get("/cv/check/status").json()["running"] is True
    assert "cvcheck_running" not in client.get("/cv/check").text  # the key is translated, not shown raw


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
    """A blank tab with "502" tells a person nothing."""
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


# --- Russian words on English pages --------------------------------------------

КИРИЛЛИЦА = re.compile(r"[А-Яа-яЁё]")
# Not every piece of Cyrillic on an English page is trouble. "Русский" in the list
# of languages, and a profile name the person wrote themselves, belong there.
СВОИ_ИМЕНА = re.compile(r'<select name="(ui_lang|output_lang|slug)".*?</select>', re.S)
ПОДСКАЗКА_ЯЗЫКА = 'title="Язык / Language"'


def человеческий_текст(html: str) -> str:
    """The page without what a person does not read: comments in scripts and lists
    of languages. The double slash in "https://" is not taken for a comment."""
    html = СВОИ_ИМЕНА.sub("", html).replace(ПОДСКАЗКА_ЯЗЫКА, "")
    html = re.sub(r"/\*.*?\*/", "", html, flags=re.S)
    return re.sub(r"""(?<![:"'])//.*$""", "", html, flags=re.M)


@pytest.fixture
def английский(client, profile):
    """An English-speaking person. The profile is renamed in Latin script: a name
    the person wrote themselves is not the program's text, and the check must not
    confuse the two."""
    from jobsearch import config, profiles
    profiles.rename(profile, "Alex")
    cfg = config.load()
    cfg["ui"].update(lang="en", output_lang="en")
    cfg["llm"]["provider"] = "ollama"      # its name is dropped into the page text
    config.save(cfg)
    return client


@pytest.mark.parametrize("path", СТРАНИЦЫ)
def test_на_английском_нет_русских_слов(английский, path):
    """Every key is translated, but text arrived round them too: the provider's
    name, the model notes, the mail services, the default profile name."""
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
    """The source kind is kept in the database: runs written before the translation
    still hold "агрегатор" there — and it still has to be shown in English."""
    import json as _json
    from jobsearch import db
    run_id = db.start_run()
    db.finish_run(run_id, found=1, fresh=1, matched=1, status="ok", log="",
                  coverage=_json.dumps([{"name": "Remotive", "url": "https://remotive.com",
                                         "kind": "агрегатор", "count": 3, "error": None}]))
    текст = английский.get("/coverage").text
    assert "aggregator" in текст, "старое значение не перевелось"
    assert "агрегатор" not in человеческий_текст(текст)
