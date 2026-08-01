"""Когда модели нет, программа должна говорить об этом, а не делать вид.

Собрать вакансии можно и без модели: агрегаторы — обычные адреса в сети, и всё
до оценки проходит прекрасно. Поэтому прогон с удалённой Ollama доходил до конца,
писал «найдено 340, подошло 0» и заканчивался зелёным — и читался как «сегодня
ничего подходящего», хотя ни одну вакансию так и не посмотрели. Проверка CV
теряла посчитанное здесь же, на этом компьютере, из-за необязательной части.
А там, где причина всё-таки называлась, вместо фразы показывался служебный ключ.
"""
import pytest

from jobsearch import config, cvcheck, filters, i18n, llm, pipeline, providers, scoring


def сломанная_модель(monkeypatch):
    """Модель, которой нет: ровно то, что бывает после удаления Ollama."""
    def взорваться(*a, **kw):
        raise providers.ProviderError(key="prov_err_ollama_unreachable", error="нет связи")

    monkeypatch.setattr(llm, "ask_json", взорваться)


# --- Прогон, который ничего не оценил -------------------------------------------

def test_триаж_сообщает_о_неудачах(profile, monkeypatch):
    """Раньше triage возвращал вакансии, и узнать, отвечал ли кто-нибудь, было нельзя."""
    сломанная_модель(monkeypatch)
    jobs = [{"key": f"k{i}", "title": "Frontend", "company": "Acme",
             "description": "React"} for i in range(3)]

    неудачи = scoring.triage(jobs, config.load(), lambda _m: None, cv="CV")

    assert неудачи, "триаж промолчал о том, что ни один запрос не удался"
    assert all(j.get("score") is None for j in jobs), "оценка взялась ниоткуда"


def test_прогон_без_единой_оценки_не_считается_удачным(profile, monkeypatch):
    """Тот самый случай: «найдено 340, подошло 0» и зелёный статус."""
    сломанная_модель(monkeypatch)
    monkeypatch.setattr(pipeline.aggregators, "collect", lambda cfg, log, cov: [
        {"key": "k1", "title": "Senior Frontend Engineer", "company": "Northwind",
         "location": "Berlin", "url": "https://example.com/1", "source": "remotive",
         "is_direct": 1, "is_agency": 0, "description": "React", "posted_at": "2026-07-20"}])
    cfg = config.load()
    cfg["search"].update(discover_per_run=0, discover_ats_per_run=0, drop_off_target=False)
    cfg["profile"]["roles"] = "Frontend Engineer"
    config.save(cfg)

    pipeline.run(trigger="test", profile=profile)

    from jobsearch import db
    прогон = db.recent_runs(1)[0]
    assert прогон["status"] == "error", "прогон, не посмотревший ни одной вакансии, назвался удачным"
    assert прогон["found"] >= 1, "вакансии всё-таки собирались — это не должно потеряться"


def test_причина_в_журнале_словами_а_не_ключом(profile, monkeypatch):
    """В журнал попадало «prov_err_ollama_unreachable» — слово, которое человеку
    ничего не говорит."""
    сломанная_модель(monkeypatch)
    cfg = config.load()
    cfg["ui"]["lang"] = "ru"
    config.save(cfg)
    журнал = []

    scoring.triage([{"key": "k", "title": "T", "company": "C", "description": ""}],
                   cfg, журнал.append, cv="CV")

    строки = " ".join(журнал)
    assert "prov_err_ollama_unreachable" not in строки, "показали служебный ключ"
    assert "Ollama" in строки, "не сказали, в чём дело"


# --- Проверка CV ----------------------------------------------------------------

def test_проверка_cv_не_теряет_посчитанное_без_модели(profile, monkeypatch):
    """Технические проверки считаются здесь же и всегда получаются. Раньше они
    пропадали вместе с необязательной частью, которой нужна модель."""
    from jobsearch import db
    сломанная_модель(monkeypatch)
    config.save_cv("cv.txt", ("Виктор Лавров\nfrontend@example.com\n+49 30 1234567\n"
                              "Опыт работы\nSenior Frontend Engineer, Acme, 2020-2026.\n"
                              "Образование\nМГУ\nНавыки\nReact, TypeScript\n" * 6).encode("utf-8"))
    db.save_job({"key": "k1", "title": "Frontend", "company": "Acme", "location": "Berlin",
                 "url": "u", "source": "s", "is_direct": 1, "is_agency": 0,
                 "description": "React", "score": 80, "reason": "", "advice": "",
                 "verified": False, "posted_at": "2026-07-01"}, run_id=1)

    result = cvcheck.analyze(config.load())

    assert result["tech"]["score"] > 0, "технические проверки пропали"
    assert result["unfinished"], "о невыполненной части промолчали"
    assert "prov_err" not in result["unfinished"][0], "и там служебный ключ"
    assert cvcheck.last_result(), "результат не сохранён — при следующем заходе покажется пусто"


def test_ошибка_проверки_cv_показывается_словами(client_check, monkeypatch):
    """Страница показывала str(e) — то есть ключ перевода."""
    client, profile = client_check
    monkeypatch.setattr(cvcheck, "analyze",
                        lambda cfg: (_ for _ in ()).throw(
                            providers.ProviderError(key="prov_err_ollama_down")))
    cfg = config.load()
    cfg["ui"]["lang"] = "ru"
    config.save(cfg)
    config.save_cv("cv.txt", ("Виктор Лавров, фронтенд-инженер. " * 20).encode("utf-8"))

    client.post("/cv/check/run", follow_redirects=False)
    for _ in range(50):
        if not client.get("/cv/check/status").json()["running"]:
            break

    ошибка = client.get("/cv/check/status").json()["error"]
    assert "prov_err_ollama_down" not in ошибка, "показали служебный ключ"
    assert "Ollama" in ошибка


@pytest.fixture
def client_check(profile):
    pytest.importorskip("httpx", reason="TestClient требует httpx")
    from fastapi.testclient import TestClient
    import app as app_module
    with TestClient(app_module.app, base_url="http://127.0.0.1:8765") as c:
        c.cookies.set("profile", profile)
        yield c, profile


# --- Срок размещения ------------------------------------------------------------

@pytest.mark.parametrize("posted,since,until,ожидание", [
    ("2026-07-20", "2026-07-01", "2026-07-31", True),
    ("2026-06-20", "2026-07-01", "", False),
    ("2026-08-20", "", "2026-07-31", False),
    ("2026-07-20", "", "", True),
    ("", "2026-07-01", "2026-07-31", True),          # даты нет — не нам судить
    ("вчера", "2026-07-01", "", True),               # источник написал словами
])
def test_отбор_по_сроку(posted, since, until, ожидание):
    assert filters.posted_ok({"posted_at": posted}, since, until) is ожидание


def test_вакансии_без_даты_не_пропадают_молча():
    """Многие агрегаторы даты не дают вовсе. Если отбрасывать всё без даты, человек,
    поставивший срок, потеряет большую часть поиска и не узнает об этом."""
    без_даты = [{"posted_at": ""}, {"posted_at": None}, {}]
    assert all(filters.posted_ok(j, "2026-07-01", "2026-07-31") for j in без_даты)


def test_кривая_дата_из_браузера_не_попадает_в_настройки(client_check):
    """В поле можно вписать что угодно — в настройки должна попасть дата или ничего."""
    import app as app_module
    assert app_module._date_or_empty("2026-07-01") == "2026-07-01"
    assert app_module._date_or_empty("не дата") == ""
    assert app_module._date_or_empty("2026-13-45") == ""
    assert app_module._date_or_empty(None) == ""


def test_даты_из_быстрого_поиска_доходят_до_настроек(client_check):
    client, _ = client_check
    client.post("/simple/start",
                data={"person": "", "locations": "EU", "linkedin": "",
                      "posted_from": "2026-07-01", "posted_to": "2026-07-31"},
                follow_redirects=False)
    cfg = config.load()
    assert cfg["search"]["posted_from"] == "2026-07-01"
    assert cfg["search"]["posted_to"] == "2026-07-31"


def test_пустые_даты_снимают_ограничение(client_check):
    """Пустое поле — это ответ «без ограничения», а не отсутствие ответа: иначе
    очистить срок было бы нечем."""
    client, _ = client_check
    cfg = config.load()
    cfg["search"].update(posted_from="2026-07-01", posted_to="2026-07-31")
    config.save(cfg)

    client.post("/simple/start",
                data={"person": "", "locations": "EU", "linkedin": "",
                      "posted_from": "", "posted_to": ""},
                follow_redirects=False)

    cfg = config.load()
    assert cfg["search"]["posted_from"] == ""
    assert cfg["search"]["posted_to"] == ""
