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


# --- Без веб-поиска не просим о том, чего модель не может -------------------------

def test_без_веб_поиска_не_просим_изучать_компанию(profile, monkeypatch):
    """Изучение компании — это поиск в интернете. Просить о нём модель, которой
    в сеть не выйти, значит потратить вызов впустую и подтолкнуть её сочинять:
    ответить-то она чем-то должна.

    Флаг в настройках говорит «хочу», а может ли — решает провайдер."""
    from jobsearch import db, llm, pipeline
    вызовы = []
    monkeypatch.setattr(llm, "ask_json",
                        lambda prompt, **kw: вызовы.append(kw.get("allowed_tools")) or {})
    monkeypatch.setattr(pipeline.aggregators, "collect", lambda cfg, log, cov: [
        {"key": "k1", "title": "Frontend", "company": "Acme", "location": "Berlin",
         "url": "https://example.com/1", "source": "remotive", "is_direct": 1,
         "is_agency": 0, "description": "React " * 100, "posted_at": "2026-07-20"}])
    cfg = config.load()
    # локальная модель: в сеть не ходит
    cfg["llm"].update(provider="ollama", triage_model="gemma2:9b", deep_model="gemma2:9b")
    cfg["search"].update(discover_per_run=0, discover_ats_per_run=0,
                         drop_off_target=False, research_company=True, threshold=0)
    cfg["profile"]["roles"] = "Frontend"
    config.save(cfg)

    pipeline.run(trigger="test", profile=profile)

    assert вызовы, "модель не звали вовсе"
    assert all(not и for и in вызовы), \
        "просили поискать в интернете модель, которая туда не ходит"


def test_с_веб_поиском_изучение_компании_остаётся(profile, monkeypatch):
    """Обратная сторона: у кого поиск есть, тот его и получает."""
    from jobsearch import llm, scoring
    инструменты = []
    monkeypatch.setattr(llm, "ask_json",
                        lambda prompt, **kw: инструменты.append(kw.get("allowed_tools")) or {})
    cfg = config.load()
    cfg["llm"].update(provider="claude_cli")
    config.save(cfg)

    scoring.deep_analyze({"title": "T", "company": "C", "location": "",
                          "description": "x" * 400},
                         cfg, "CV", lambda _m: None, research=True)

    assert any(и for и in инструменты), "лишили веб-поиска того, кто его умеет"


def test_отказ_всех_источников_не_выдаётся_за_успех(monkeypatch, profile):
    """Найдено настоящим прогоном: антивирус на машине перехватывал защищённые
    соединения, все девять источников отвалились с ошибкой сертификата — а
    прогон отчитался «Готово», status ok, ноль вакансий.

    Ноль от рынка и ноль от того, что до рынка не дошло, — разные вещи, и
    человек не должен путать одно с другим. Тем более что причина у такого
    обычно общая и снаружи: антивирус, рабочий шлюз, нет сети.
    """
    from jobsearch import config, db, llm, pipeline
    from jobsearch.collectors import aggregators

    monkeypatch.setattr(llm, "ask_json", lambda prompt, **kw: {})
    cfg = config.load()
    cfg["llm"].update(provider="ollama", triage_model="gemma2:9b", deep_model="gemma2:9b")
    cfg["search"].update(discover_per_run=0, discover_ats_per_run=0)
    config.save(cfg)

    def всё_упало(cfg, log, coverage=None):
        for имя in ("Remotive", "Arbeitnow", "RemoteOK"):
            coverage.append({"name": имя, "url": "https://x.example", "kind": "aggregator",
                             "count": 0, "error": "SSLError certificate verify failed"})
        return []

    monkeypatch.setattr(aggregators, "collect", всё_упало)

    pipeline.run(trigger="test", profile=profile)

    with db.conn() as c:
        прогон = dict(c.execute("SELECT * FROM runs ORDER BY id DESC LIMIT 1").fetchone())
    assert прогон["status"] != "ok", "прогон, где не ответил никто, назван успешным"
    assert "источник" in прогон["log"] or "sources" in прогон["log"], \
        "в журнале не сказано, что молчали все"


# --- Галочка «проверено» --------------------------------------------------------

def глубокий_ответ(monkeypatch, ответ):
    from jobsearch import scoring
    monkeypatch.setattr(scoring.llm, "ask_json", lambda prompt, **kw: ответ)


def вакансия_на_разбор() -> dict:
    return {"title": "Senior Ruby on Rails Developer", "company": "Proxify AB",
            "location": "Remote", "url": "https://example.com/1",
            "description": "Ruby on Rails, RSpec, PostgreSQL. " * 40,
            "score": 90, "verified": False}


def test_без_оценки_разбора_галочки_не_ставим(monkeypatch, profile):
    """Пометка ставилась по факту ответа, а оценка бралась, только если в ответе
    было поле match. Слабые модели сплошь и рядом отвечают связным текстом без
    него — и на карточке оставался балл быстрого триажа с галочкой «проверено».

    Триаж видит семьсот знаков описания и восемь вакансий за раз, он для того и
    быстрый. Галочка говорила, что балл подтверждён вторым, внимательным
    проходом, — а он его не подтверждал. Так вакансия на Ruby on Rails и стояла
    у SAP-интегратора с «90 % ✓ проверено».
    """
    from jobsearch import config, scoring
    глубокий_ответ(monkeypatch, {"reason": "Ключевые навыки все на месте.",
                                 "cv_changes": ["Подчеркнуть SAP PO/PI"]})
    j = вакансия_на_разбор()

    scoring.deep_analyze(j, config.load(), cv="SAP Integration Consultant",
                         log=lambda *a, **k: None, research=False)

    assert j["verified"] is False, "проверенным назван балл, которого разбор не давал"
    assert j["score"] == 90, "балл триажа при этом терять незачем"


def test_с_оценкой_разбора_галочка_ставится(monkeypatch, profile):
    """Обратная сторона: разбор ответил оценкой — значит подтвердил."""
    from jobsearch import config, scoring
    глубокий_ответ(monkeypatch, {"match": 20, "reason": "Другое направление."})
    j = вакансия_на_разбор()

    scoring.deep_analyze(j, config.load(), cv="SAP Integration Consultant",
                         log=lambda *a, **k: None, research=False)

    assert j["verified"] is True
    assert j["score"] == 20, "оценку разбора не взяли"


def test_совет_не_подсказывает_соврать():
    """Модель предлагала «упомянуть опыт с PostgreSQL» тому, у кого его нет.
    Это совет соврать, и на собеседовании он обернётся против человека. В запросе
    на адаптацию CV такой запрет был, а в запросе на разбор — нет."""
    from jobsearch import scoring
    запрос = scoring.DEEP_PROMPT.lower()
    assert "не предлагай упоминать навыки" in запрос
    assert "которых у кандидата нет" in запрос
