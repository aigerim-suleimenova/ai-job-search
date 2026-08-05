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


# --- Что модель успевает посмотреть ---------------------------------------------

def test_найденное_по_запросу_идёт_раньше_вываленного_лентой():
    """Порядок задаёт совпадение слов с профилем. EURES ищет по смыслу и
    приносит названия на родных языках: на «seamstress» — польскую «Szwaczka».
    Общих букв нет, совпадение ноль — и все 283 его вакансии уходили за черту,
    под 400 IT-вакансий, попавших туда из-за слова «Designer» в названии."""
    from jobsearch import pipeline  # noqa: F401  (проверяем сам порядок, ниже)

    свои_слова = {"lingerie", "pattern", "maker", "garment"}

    def лексика(j):
        return len(свои_слова & set(j["title"].lower().split()))

    вакансии = [
        {"title": "Szwaczka", "by_query": True},                    # 0 общих слов
        {"title": "Technical Designer Pattern", "by_query": False},  # 1 общее слово
        {"title": "Lingerie Pattern Maker", "by_query": True},       # 3 общих
    ]
    for j in вакансии:
        j["_lex"] = лексика(j)

    по_словам = lambda j: -j["_lex"]  # noqa: E731
    нашли = sorted((j for j in вакансии if j.get("by_query")), key=по_словам)
    прочие = sorted((j for j in вакансии if not j.get("by_query")), key=по_словам)
    порядок = [j["title"] for j in нашли + прочие]

    assert порядок.index("Szwaczka") < порядок.index("Technical Designer Pattern"), \
        "найденное по запросу снова уехало под случайно совпавшее"


def test_источники_помечают_найденное_по_запросу():
    """Метку ставит collect(), по одному списку — иначе она разойдётся с тем,
    кто на самом деле ищет по словам."""
    from jobsearch.collectors import aggregators
    имена = {и[4] for и in aggregators.ИСТОЧНИКИ}
    assert aggregators.ИЩУТ_ПО_СЛОВАМ <= имена, "в списке ищущих есть несуществующий сборщик"
    assert "eures" in aggregators.ИЩУТ_ПО_СЛОВАМ
    assert "arbeitnow" not in aggregators.ИЩУТ_ПО_СЛОВАМ, "он отдаёт всю ленту"


def test_метка_ставится_только_ищущим(monkeypatch):
    from jobsearch import config
    from jobsearch.collectors import aggregators
    for имя in {и[4] for и in aggregators.ИСТОЧНИКИ}:
        monkeypatch.setattr(aggregators, имя,
                            lambda cfg, log, _и=имя: [{"title": _и, "source": _и}])
    вакансии = aggregators.collect(config.DEFAULTS, lambda *a: None, [])
    помечены = {j["source"] for j in вакансии if j.get("by_query")}
    без_метки = {j["source"] for j in вакансии if not j.get("by_query")}
    assert "eures" in помечены and "workable" in помечены
    assert "arbeitnow" in без_метки and "hn_hiring" in без_метки


# --- Разбор не спорит сам с собой ------------------------------------------------

def test_вердикт_чужая_держит_балл_внизу(monkeypatch, cfg):
    """Модель писала «отвечает за разработку решений для игр, а не одежды» — и
    ставила 55. Рассуждение верное, число нет. Верим рассуждению: оно идёт
    первым, то есть придумано раньше, чем она успела подогнать цифру."""
    from jobsearch import llm, scoring
    monkeypatch.setattr(llm, "ask_json", lambda *a, **k: {
        "verdict": "чужая", "reason": "другая профессия", "match": 85,
        "cv_changes": [], "linkedin_changes": [], "cover_hint": ""})
    вакансия = {"title": "iOS Developer", "description": "x" * 400}

    scoring.deep_analyze(вакансия, cfg, "CV", lambda *a: None, research=False)

    assert вакансия["score"] <= 20, f"чужая профессия ушла на {вакансия['score']}%"
    assert вакансия["verified"] is True


def test_своя_профессия_балл_не_теряет(monkeypatch, cfg):
    """Обратная сторона: занижать своё нельзя."""
    from jobsearch import llm, scoring
    monkeypatch.setattr(llm, "ask_json", lambda *a, **k: {
        "verdict": "своя", "reason": "та же профессия", "match": 90,
        "cv_changes": [], "linkedin_changes": [], "cover_hint": ""})
    вакансия = {"title": "Lingerie Pattern Maker", "description": "x" * 400}

    scoring.deep_analyze(вакансия, cfg, "CV", lambda *a: None, research=False)

    assert вакансия["score"] == 90


def test_разбор_думает_прежде_чем_ставить_балл():
    """Оценка стояла ПЕРВЫМ полем — модель отвечает слева направо и придумывала
    число раньше, чем успевала подумать. У триажа это давно исправлено, а до
    разбора починка не дошла."""
    from jobsearch import scoring
    поля = list(scoring.DEEP_SCHEMA["properties"])
    assert поля.index("verdict") < поля.index("match")
    assert поля.index("reason") < поля.index("match")
    тело = scoring.DEEP_PROMPT
    assert тело.index('"verdict"') < тело.index('"match"')
    assert тело.index('"reason"') < тело.index('"match"')


# --- Чего модель не прочитала, за то не ручаемся ---------------------------------

# Куски настоящих объявлений из прогона — короткие обрывки язык не определяют,
# и правильно: судить надо то, что модели и правда достаётся.
ОБЪЯВЛЕНИЯ = [
    ("Engineering Manager (Data Science). What you will do: build, mentor and lead "
     "an elite data science team as a trusted player-coach, while clearly "
     "communicating complex technical ideas to executives, customers and partners. "
     "You will personally design and train models, and you should have experience "
     "with production systems that serve millions of requests.", True),
    ("Kontroler jakości wyrobów odzieżowych. Zakres obowiązków: kontrola wyrobów "
     "gotowych, sprawdzanie zgodności wymiarów odzieży z tabelą miar oraz "
     "specyfikacją techniczną, sporządzanie raportów jakościowych, identyfikacja "
     "wad. Wymagania: wykształcenie zasadnicze zawodowe o kierunku krawiec, "
     "technolog odzieżowy lub pokrewne, znajomość technologii krawieckiej.", False),
    ("Technico-commercial itinérant. Vous recherchez un métier de découvertes et "
     "d'audace pour interagir tous les jours avec de nouvelles personnes tout en "
     "présentant des produits et services innovants ? Rejoignez l'aventure et "
     "développez votre portefeuille clients sur votre secteur géographique.", False),
    ("Wij zoeken een gemotiveerde medewerker voor onze afdeling productie en "
     "montage. Je werkt in een klein team, bedient machines en controleert de "
     "kwaliteit van de onderdelen. Ervaring in de techniek is een pluspunt, maar "
     "wij leiden je graag zelf op. Wij bieden een vast contract.", False),
    ("Konstruktor", True),          # слов почти нет — не придираемся
    # Перечень технологий: по-английски, но служебных слов нет ни одного, и язык
    # по ним не определить. Раз нельзя — не придираемся.
    ("Ruby on Rails, RSpec, PostgreSQL. " * 40, True),
]


@pytest.mark.parametrize("текст,ожидание", ОБЪЯВЛЕНИЯ)
def test_узнаём_английский(текст, ожидание):
    from jobsearch import scoring
    assert scoring._по_английски(текст) is ожидание


def test_непрочитанное_не_помечается_проверенным(monkeypatch, cfg):
    """Не поняв текста, модель не говорит «не поняла»: пересказывает профиль и
    ставит балл из примера. Конструктору белья столяр, маляр, садовник и
    кладовщик достались по 90% — все с галочкой «проверено», все на польском."""
    from jobsearch import llm, scoring
    monkeypatch.setattr(llm, "ask_json", lambda *a, **k: {
        "verdict": "своя", "reason": "кандидат имеет опыт", "match": 90,
        "cv_changes": [], "linkedin_changes": [], "cover_hint": ""})
    вакансия = {"title": "Osoba na stanowisku stolarz",
                "description": "Zakres obowiązków: wykonywanie mebli na wymiar, "
                               "obsługa maszyn stolarskich, praca w warsztacie " * 6}

    scoring.deep_analyze(вакансия, cfg, "CV", lambda *a: None, research=False)

    assert вакансия["verified"] is False, "поручились за то, чего не прочли"
    assert вакансия["score"] <= scoring.ПОТОЛОК_НЕПРОЧИТАННОГО


def test_профессия_из_справочника_снимает_вопрос(monkeypatch, cfg):
    """Название профессии приходит по-английски и одинаково для всех языков —
    по нему судить можно, даже когда объявление непрочитано."""
    from jobsearch import llm, scoring
    monkeypatch.setattr(llm, "ask_json", lambda *a, **k: {
        "verdict": "своя", "reason": "та же профессия", "match": 90,
        "cv_changes": [], "linkedin_changes": [], "cover_hint": ""})
    вакансия = {"title": "Szwaczka", "occupation": "tailor",
                "description": "Zakres obowiązków: szycie odzieży na maszynie " * 8}

    scoring.deep_analyze(вакансия, cfg, "CV", lambda *a: None, research=False)

    assert вакансия["verified"] is True
    assert вакансия["score"] == 90


def test_профессия_из_справочника_доходит_до_модели(monkeypatch, cfg):
    from jobsearch import llm, scoring
    видел = {}
    monkeypatch.setattr(llm, "ask_json",
                        lambda prompt, **k: видел.setdefault("prompt", prompt) and None
                        or {"verdict": "чужая", "reason": "—", "match": 5,
                            "cv_changes": [], "linkedin_changes": [], "cover_hint": ""})
    scoring.deep_analyze({"title": "Magazynier M/K", "occupation": "warehouse worker",
                          "description": "x" * 400},
                         cfg, "CV", lambda *a: None, research=False)
    assert "warehouse worker" in видел["prompt"], "справочник до модели не дошёл"


def test_профессия_называется_вместе_с_чьей_она(cfg):
    """Первая редакция писала просто «Профессия по справочнику: CNC machine
    operator», и слабая модель читала это как профессию КАНДИДАТА: оператор
    станка получал «своя» и 90%, тогда как без строки вовсе — «смежная» и 55.
    Подсказка делала модель не умнее, а увереннее в неправоте. Померено на
    прогоне конструктора белья."""
    from jobsearch import scoring
    cfg["profile"]["roles"] = "Lingerie Pattern Maker, Garment Technologist"
    блок = scoring._occupation_block(cfg, {"occupation": "CNC machine operator"})

    assert "CNC machine operator" in блок
    assert "Lingerie Pattern Maker" in блок, "не сказано, с чем сравнивать"
    assert "ВАКАНСИИ" in блок, "не сказано, чья это профессия"
    assert блок.index("ВАКАНСИИ") < блок.index("кандидата")


def test_без_профессии_блока_нет(cfg):
    from jobsearch import scoring
    assert scoring._occupation_block(cfg, {}) == ""
    assert scoring._occupation_block(cfg, {"occupation": "  "}) == ""


# --- Профиль из CV: ответ модели превращается в поле, а не в его пересказ -------

@pytest.mark.parametrize("ответ,ожидание", [
    (["Purchasing Manager", "Supply Chain Manager"], "Purchasing Manager, Supply Chain Manager"),
    ("Purchasing Manager, Supply Chain Manager", "Purchasing Manager, Supply Chain Manager"),
    (["  Logistics Manager  ", "", "Procurement Lead"], "Logistics Manager, Procurement Lead"),
    ("['Purchasing Manager', 'Supply Chain Manager']", "Purchasing Manager', 'Supply Chain Manager"),
    ("Middle", "Middle"),
    ("C++ (advanced)", "C++ (advanced)"),
])
def test_список_от_модели_становится_строкой(ответ, ожидание):
    """Просим «должности через запятую», слабая модель присылает список. Через
    str() он записывался в профиль как «['Purchasing Manager', ...]», дальше
    разбирался по запятой — и в источники уходил запрос «['Purchasing Manager'»,
    а модель получала такой же пример «своей» профессии."""
    from jobsearch import scoring
    assert scoring._строкой(ответ) == ожидание


def test_профиль_из_cv_не_приносит_скобок(monkeypatch, cfg):
    from jobsearch import llm, scoring
    monkeypatch.setattr(llm, "ask_json", lambda *a, **k: {
        "roles": ["Purchasing Manager", "Logistics Manager"],
        "skills": ["Procurement", "MS Excel"],
        "seniority": "Middle", "summary": ["10 лет в снабжении"],
        "languages": ["Russian (native)", "English (B1)"]})
    cfg["profile"].update(roles="", skills="", seniority="", summary="", languages="")

    получилось = scoring.profile_from_cv(cfg, "CV")["profile"]

    for поле in ("roles", "skills", "summary", "languages"):
        assert "[" not in получилось[поле] and "'" not in получилось[поле], \
            f"{поле}: {получилось[поле]}"
    assert получилось["roles"] == "Purchasing Manager, Logistics Manager"


# --- Право на работу читаем из объявления, а не спрашиваем модель ----------------

@pytest.mark.parametrize("текст,ожидание", [
    ("We are unable to sponsor visas for this position at this time.", "no"),
    ("Candidates must be authorized to work in the United States.", "no"),
    ("No visa sponsorship is available for this role.", "no"),
    ("Relocation package and visa sponsorship provided for the right candidate.", "yes"),
    ("We help with the visa and offer relocation support to Berlin.", "yes"),
    ("Join our team of engineers building payment systems.", ""),
])
def test_что_объявление_говорит_про_визу(текст, ожидание):
    """Спрашивать модель бесполезно: на прогоне Дмитрия в профиле стояло «нужно
    спонсорство», строка попадала в запрос — и ни в одном из ста девяти доводов
    виза не была упомянута ни разу."""
    from jobsearch import filters
    assert filters.visa_stance({"title": "Engineer", "description": текст}) == ожидание


def test_отказ_важнее_согласия():
    """«no visa sponsorship» содержит «visa sponsorship» — отказ должен побеждать,
    иначе объявление, которое прямо отказывает, покажется приглашающим."""
    from jobsearch import filters
    assert filters.visa_stance(
        {"description": "Visa sponsorship: no visa sponsorship for this role."}) == "no"


def test_молчание_не_считается_ни_за_что():
    from jobsearch import filters
    assert filters.visa_stance({"description": ""}) == ""


# --- Цитата из резюме: против выдумок --------------------------------------------

@pytest.mark.parametrize("цитата,есть", [
    ("React, TypeScript", True),
    ("react   typescript", True),          # пробелы и регистр не в счёт
    ("опыт в области электротехники", False),
    ("Salesforce Commerce Cloud", False),
    ("React", False),                      # одно слово — не цитата
    ("", False),
])
def test_цитата_сверяется_с_резюме(цитата, есть):
    """Модель написала конструктору белья «кандидат имеет опыт и образование в
    области электротехники» — вакансия лектора по электротехнике на 90% с
    галочкой «проверено». Никакой электротехники у человека нет."""
    from jobsearch import scoring
    cv = "Восемь лет во фронтенде. React, TypeScript, Vite. Вёл переход на монорепозиторий."
    assert scoring._цитата_настоящая(цитата, cv) is есть


def test_выдуманный_довод_не_получает_галочки(monkeypatch, cfg):
    from jobsearch import llm, scoring
    monkeypatch.setattr(llm, "ask_json", lambda *a, **k: {
        "verdict": "своя", "reason": "опыт в области электротехники",
        "quote": "опыт в области электротехники", "match": 90,
        "cv_changes": [], "linkedin_changes": [], "cover_hint": ""})
    вакансия = {"title": "Senior lecturers in Electrical Engineering",
                "occupation": "lecturer", "description": "x" * 400}

    scoring.deep_analyze(вакансия, cfg, "Конструктор белья. Valentina, лекала.",
                         lambda *a: None, research=False)

    assert вакансия["verified"] is False, "поручились за выдуманное"
    assert вакансия["score"] == 90, "балл при этом терять незачем"


def test_настоящая_цитата_галочку_не_отнимает(monkeypatch, cfg):
    from jobsearch import llm, scoring
    monkeypatch.setattr(llm, "ask_json", lambda *a, **k: {
        "verdict": "своя", "reason": "работал с лекалами",
        "quote": "Valentina, лекала", "match": 90,
        "cv_changes": [], "linkedin_changes": [], "cover_hint": ""})
    вакансия = {"title": "Modéliste", "occupation": "tailor", "description": "x" * 400}

    scoring.deep_analyze(вакансия, cfg, "Конструктор белья. Valentina, лекала.",
                         lambda *a: None, research=False)

    assert вакансия["verified"] is True


# --- Второй проход: советы проверяются отдельным вопросом ------------------------

def test_совет_дописать_несуществующее_отбрасывается(monkeypatch, cfg):
    """Запрет выдумывать стоит в самом промпте разбора, и слабая модель его
    перебивает: руководителю ОМТС она советовала «Добавить опыт работы с
    Salesforce Commerce Cloud»."""
    from jobsearch import llm, scoring
    monkeypatch.setattr(llm, "ask_json", lambda *a, **k: {"ok": [0]})
    советы = ["Вынести дизайн-систему в первую строку",
              "Добавить опыт работы с Salesforce Commerce Cloud"]

    осталось = scoring.keep_honest_advice(советы, "CV: дизайн-система, React", {}, lambda *a: None)

    assert осталось == ["Вынести дизайн-систему в первую строку"]


def test_если_забраковали_всё_ничего_не_выбрасываем(monkeypatch, cfg):
    """Скорее всего модель не поняла вопроса, а не все советы плохи."""
    from jobsearch import llm, scoring
    monkeypatch.setattr(llm, "ask_json", lambda *a, **k: {"ok": []})
    советы = ["Первый", "Второй"]
    assert scoring.keep_honest_advice(советы, "CV", {}, lambda *a: None) == советы


def test_проверить_не_вышло_советы_остаются(monkeypatch, cfg):
    from jobsearch import llm, scoring
    monkeypatch.setattr(llm, "ask_json",
                        lambda *a, **k: (_ for _ in ()).throw(llm.ClaudeError("нет модели")))
    советы = ["Первый"]
    assert scoring.keep_honest_advice(советы, "CV", {}, lambda *a: None) == советы


# --- Перевод названия ------------------------------------------------------------

def test_английское_название_не_переводим(cfg):
    from jobsearch import scoring
    assert scoring.translate_title({"title": "Senior Frontend Engineer"}, cfg, {},
                                   по_английски=True) == ""


def test_с_профессией_из_справочника_перевод_не_нужен(cfg):
    from jobsearch import scoring
    assert scoring.translate_title({"title": "Szwaczka", "occupation": "tailor"}, cfg, {},
                                   по_английски=False) == ""


def test_перевод_не_вышел_прогон_не_встаёт(monkeypatch, cfg):
    from jobsearch import llm, scoring
    monkeypatch.setattr(llm, "ask",
                        lambda *a, **k: (_ for _ in ()).throw(llm.ClaudeError("нет модели")))
    assert scoring.translate_title({"title": "Modéliste"}, cfg, {}, по_английски=False) == ""


def test_объяснение_вместо_перевода_отбрасывается(monkeypatch, cfg):
    """Модель иногда отвечает рассуждением. Название вакансии длиннее полутора
    сотен знаков не бывает."""
    from jobsearch import llm, scoring
    monkeypatch.setattr(llm, "ask", lambda *a, **k: "Это название означает " + "х" * 200)
    assert scoring.translate_title({"title": "Modéliste"}, cfg, {}, по_английски=False) == ""
