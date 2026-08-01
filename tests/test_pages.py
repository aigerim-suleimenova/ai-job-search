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
    with TestClient(app_module.app, base_url="http://127.0.0.1:8765") as c:
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


def test_выбор_провайдера_ведёт_к_следующему_вопросу(client, profile, monkeypatch):
    """Выбрав, через что работать, человек ещё не закончил: дальше — какой моделью,
    и это ниже на той же странице. Его бросало в самый верх, к началу всего, что
    он уже прочитал."""
    доступны(monkeypatch, claude_cli=True)
    r = client.post("/models/provider",
                    data={"provider": "claude_cli", "back": "/welcome", "anchor": "welcome-continue"},
                    follow_redirects=False)
    assert r.headers["location"].endswith("#welcome-continue"), r.headers["location"]

    r = client.post("/models/provider",
                    data={"provider": "claude_cli", "back": "/models", "anchor": "models-choose"},
                    follow_redirects=False)
    assert r.headers["location"].endswith("#models-choose"), r.headers["location"]


def test_проверка_программы_возвращает_к_её_же_карточке(client, profile, monkeypatch):
    """«Проверить снова» спрашивают про конкретную программу — и остаться надо
    возле неё, а не уехать наверх страницы."""
    доступны(monkeypatch, claude_cli=True)
    r = client.post("/provider/recheck",
                    data={"provider": "ollama", "back": "/welcome", "anchor": "provider-ollama"},
                    follow_redirects=False)
    assert r.headers["location"].endswith("#provider-ollama"), r.headers["location"]


@pytest.mark.parametrize("path,anchor", [("/welcome", "welcome-continue"),
                                         ("/welcome?step=model", "welcome-step2"),
                                         ("/models", "models-choose")])
def test_якорь_есть_на_самой_странице(client, profile, monkeypatch, path, anchor):
    """Якорь в адресе без якоря на странице — это просто прокрутка наверх."""
    доступны(monkeypatch, claude_cli=True)
    assert f'id="{anchor}"' in client.get(path).text, f"{path}: некуда возвращать"


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
    # its name is dropped into the page text; the model has to match the provider,
    # or the pages are not shown at all and there is nothing here to read
    cfg["llm"].update(provider="ollama", triage_model="llama3.1:8b")
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


# --- The first screen: what the buttons say about the state --------------------

ПРОВАЙДЕРЫ = {
    "claude_cli": {"ready": False, "web_search": True, "kind": "cloud", "install_url": ""},
    "cursor_cli": {"ready": False, "web_search": False, "kind": "cloud", "install_url": ""},
    "ollama": {"ready": False, "web_search": False, "kind": "local", "install_url": ""},
}


def доступны(monkeypatch, **готовы):
    """Подменяет список провайдеров: что установлено, решает тест, а не машина."""
    import app as app_module
    from jobsearch import providers
    avail = {k: dict(v) for k, v in ПРОВАЙДЕРЫ.items()}
    for key, ready in готовы.items():
        avail[key]["ready"] = ready
    monkeypatch.setattr(providers, "available", lambda claude_bin="claude": avail)
    monkeypatch.setattr(app_module.providers, "available", providers.available)


def кнопка_строки(html: str, model_id: str) -> str:
    """Последняя кнопка в строке модели — та, что выбирает её."""
    anchor = "model-" + re.sub(r"[:/.]", "-", model_id)
    row = html[html.index(f'id="{anchor}"'):]
    row = row[:row.index("</form>", row.index("/models/select"))]
    return re.findall(r"<button[^>]*>\s*([^<\n]+)", row)[-1].strip()


def test_выбранная_модель_названа_на_кнопке(client, profile, monkeypatch):
    """Раньше все строки предлагали «Использовать» — включая ту, что уже
    используется, и по действию нельзя было понять, где ты сейчас."""
    from jobsearch import config, i18n
    доступны(monkeypatch, claude_cli=True)
    cfg = config.load()
    cfg["ui"]["lang"] = "en"
    cfg["llm"].update(provider="claude_cli", triage_model="sonnet")
    config.save(cfg)

    html = client.get("/models").text

    assert кнопка_строки(html, "sonnet") == i18n.t("en", "models_in_use")
    assert кнопка_строки(html, "opus") == i18n.t("en", "models_use")


def test_знакомство_не_пускает_без_установленной_программы(client, profile, monkeypatch):
    from jobsearch import config, i18n
    доступны(monkeypatch)                      # ничего не установлено
    cfg = config.load()
    cfg["ui"]["lang"] = "en"
    config.save(cfg)

    html = client.get("/welcome").text
    хвост = html[html.index('id="welcome-continue"'):]

    assert "disabled" in хвост.split("</button>")[0], "кнопка «Продолжить» осталась нажимаемой"
    assert i18n.t("en", "welcome_blocked") in хвост


def test_знакомство_различает_нет_программы_и_нет_модели(client, profile, monkeypatch):
    """Ollama запущена, а модель к ней не скачана. Раньше человека отправляли
    устанавливать то, что и так работает."""
    from jobsearch import config, i18n, providers
    доступны(monkeypatch, ollama=True)
    monkeypatch.setattr(providers, "ollama_installed_models", lambda: set())
    cfg = config.load()
    cfg["ui"]["lang"] = "en"
    cfg["llm"].update(provider="ollama", triage_model="llama3.3:70b")
    config.save(cfg)

    хвост = client.get("/welcome?step=model").text
    хвост = хвост[хвост.index('id="welcome-continue"'):]

    assert "disabled" in хвост.split("</button>")[0]
    assert i18n.t("en", "welcome_blocked_model") in хвост
    assert i18n.t("en", "welcome_blocked") not in хвост, "причина названа неверно"


def test_знакомство_пускает_когда_модель_на_месте(client, profile, monkeypatch):
    from jobsearch import config, providers
    доступны(monkeypatch, ollama=True)
    monkeypatch.setattr(providers, "ollama_installed_models", lambda: {"llama3.3:70b"})
    cfg = config.load()
    cfg["ui"]["lang"] = "en"
    cfg["llm"].update(provider="ollama", triage_model="llama3.3:70b")
    config.save(cfg)

    хвост = client.get("/welcome?step=model").text
    хвост = хвост[хвост.index('id="welcome-continue"'):]

    assert "disabled" not in хвост.split("</button>")[0], "пройти дальше не дают"
