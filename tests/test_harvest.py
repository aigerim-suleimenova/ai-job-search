"""Employers from links we have already downloaded.

The check runs without the network: the assembled addresses are never requested,
only built and recognised back. It is that round trip — assemble, then recognise
— which matters here: without it the same board would be added afresh every run.
"""
import pytest

from jobsearch import harvest
from jobsearch.collectors import ats


def вакансия(url, company="Компания"):
    return {"url": url, "company": company}


def test_доска_из_ссылки_на_вакансию():
    новые = harvest.find_new([вакансия("https://boards.greenhouse.io/mercury/jobs/4012", "Mercury")], [])
    assert новые == [{"name": "Mercury", "url": "https://boards.greenhouse.io/mercury"}]


def test_собранный_адрес_опознаётся_обратно():
    """Otherwise the board is added a second time on the next run."""
    ссылки = [
        "https://boards.greenhouse.io/mercury/jobs/1",
        "https://jobs.lever.co/xsolla/abc",
        "https://jobs.ashbyhq.com/n8n/xyz",
        "https://apply.workable.com/seeq/j/123",
        "https://careers.smartrecruiters.com/ABOUTYOUGmbH/744",
        "https://payflows.recruitee.com/o/engineer",
        "https://digacon-software.jobs.personio.com/job/1",
    ]
    for c in harvest.find_new([вакансия(u) for u in ссылки], [], limit=99):
        assert ats.detect(c["url"]), f"{c['url']} не опознаётся обратно"


def test_повторный_прогон_ничего_не_добавляет():
    jobs = [вакансия("https://boards.greenhouse.io/mercury/jobs/1"),
            вакансия("https://jobs.ashbyhq.com/n8n/xyz")]
    первый = harvest.find_new(jobs, [], limit=99)
    assert len(первый) == 2
    assert harvest.find_new(jobs, первый, limit=99) == []


def test_разные_ссылки_одной_доски_дают_одну_запись():
    jobs = [вакансия("https://boards.greenhouse.io/mercury/jobs/1"),
            вакансия("https://boards.greenhouse.io/mercury/jobs/2"),
            вакансия("https://job-boards.greenhouse.io/mercury/jobs/3")]
    assert len(harvest.find_new(jobs, [], limit=99)) == 1


def test_уже_наблюдаемую_компанию_не_дублируем():
    """The person added the board by hand — even if spelled differently."""
    свои = [{"name": "Mercury", "url": "https://boards.greenhouse.io/mercury/"}]
    jobs = [вакансия("https://job-boards.greenhouse.io/mercury/jobs/9")]
    assert harvest.find_new(jobs, свои) == []


def test_предел_за_прогон_соблюдается():
    jobs = [вакансия(f"https://jobs.ashbyhq.com/company{i}/x") for i in range(30)]
    assert len(harvest.find_new(jobs, [], limit=10)) == 10
    assert harvest.find_new(jobs, [], limit=0) == []


def test_обычные_ссылки_пропускаются():
    jobs = [вакансия("https://example.com/careers/frontend"),
            вакансия("https://linkedin.com/jobs/view/123"),
            вакансия("")]
    assert harvest.find_new(jobs, []) == []


def test_без_названия_компании_берём_имя_доски():
    новые = harvest.find_new([вакансия("https://jobs.lever.co/xsolla/a", company="")], [])
    assert новые[0]["name"] == "xsolla"


def test_галочка_видна_и_выключается(profile):
    """The company list fills up by itself — a person has to know that, and be
    able to forbid it without reading the source."""
    import pytest
    pytest.importorskip("httpx", reason="TestClient требует httpx")
    from fastapi.testclient import TestClient

    import app as app_module
    from jobsearch import appstate, config, i18n

    appstate.mark_setup_done(config.load()["llm"])   # or first launch sends us to the introduction
    with TestClient(app_module.app, base_url="http://127.0.0.1:8765") as c:
        c.cookies.set("profile", profile)
        страница = c.get("/").text
        assert 'name="harvest_boards"' in страница
        язык = config.load()["ui"]["lang"]
        assert i18n.t(язык, "harvest_boards") in страница
        assert i18n.t(язык, "harvest_boards_hint")[:40] in страница

        # the box is clear — the field simply is not in the form
        c.post("/save", data={"companies": "", "use_remotive": "on"}, follow_redirects=False)
        assert config.load()["sources"]["harvest_boards"] is False

        c.post("/save", data={"companies": "", "harvest_boards": "on"}, follow_redirects=False)
        assert config.load()["sources"]["harvest_boards"] is True


# --- Что источник ответил, когда не дал ничего ---------------------------------

def только_remotive() -> dict:
    """Один источник включён, остальные выключены: у каждого свои требования к
    настройкам, а проверяем мы здесь не их."""
    from jobsearch import config
    cfg = config._merge(config.DEFAULTS, {})
    for ключ in list(cfg["sources"]):
        if ключ.startswith("use_"):
            cfg["sources"][ключ] = False
    cfg["sources"]["use_remotive"] = True
    return cfg


def test_отказ_источника_доезжает_до_покрытия(monkeypatch):
    """Поле error в покрытии было всегда — и всегда пустым: сборщики ловят свои
    ошибки сами и возвращают пустой список, так что снаружи отказ выглядел
    точь-в-точь как «ничего не нашлось». На странице «Покрытие» источники стояли
    с нулями и без единого слова, и человек с антивирусом, перехватывающим
    соединения, читал это как «таких вакансий нет»."""
    import requests

    from jobsearch.collectors import aggregators

    def сломанный(cfg, log):
        log("remotive: SSLError certificate verify failed")
        return []

    monkeypatch.setattr(aggregators, "remotive", сломанный)
    покрытие = []

    aggregators.collect(только_remotive(), lambda m: None, покрытие)

    свой = [c for c in покрытие if c["name"] == "Remotive"][0]
    assert свой["count"] == 0
    assert свой["error"] and "certificate" in свой["error"], "отказ потерялся по дороге"


def test_у_работающего_источника_ошибки_нет(monkeypatch):
    """Обратная сторона: сказать «ошибка» там, где её не было, не лучше молчания."""
    from jobsearch.collectors import aggregators

    monkeypatch.setattr(aggregators, "remotive",
                        lambda cfg, log: [{"title": "SAP Integration Consultant"}])
    покрытие = []

    aggregators.collect(только_remotive(), lambda m: None, покрытие)

    свой = [c for c in покрытие if c["name"] == "Remotive"][0]
    assert свой["count"] == 1 and свой["error"] is None


# --- «Удалённо» — не то же самое, что «откуда угодно» ---------------------------

СЛУЧАИ = [
    # (место в вакансии, что искали, пускать ли)
    ("United States, Remote", ["germany"], False),   # ровно то, на чём попались
    ("Remote (US only)", ["germany"], False),
    ("Remote — Europe", ["germany"], True),
    ("Remote", ["germany"], True),                   # не названо ничего — и правда откуда угодно
    ("Anywhere", ["germany"], True),
    ("United States, Remote", ["usa"], True),        # искали США — США и получили
    ("Berlin, Remote", ["germany"], True),
    ("Remote, Poland", ["eu"], True),
    ("APAC, Remote", ["germany"], False),
    ("Remote, Canada", ["eu"], False),
    ("Remote, London", ["germany"], False),
]


@pytest.mark.parametrize("место,искали,пускать", СЛУЧАИ)
def test_удалённая_работа_всё_равно_где_то_находится(место, искали, пускать):
    """Проверка на «удалённо» стояла первой и пропускала всё, где встретилось это
    слово. На настоящем прогоне по резюме SAP-интегратора из тридцати двух
    вакансий восемь оказались из одной американской конторы с пометкой
    «United States, Remote» — человеку из Европы не годилась ни одна. Модель это
    заметила и написала «US only», а фильтр, который для того и стоит, пропустил."""
    from jobsearch import filters
    assert filters.location_ok(место, искали, include_remote=True) is пускать, место


def test_без_галочки_удалённые_отсекаются_как_прежде():
    from jobsearch import filters
    assert filters.location_ok("Remote", ["germany"], include_remote=False) is False


def test_неизвестное_место_по_прежнему_не_режется():
    """Пустое место — не повод выбрасывать: разберётся триаж."""
    from jobsearch import filters
    assert filters.location_ok("", ["germany"]) is True
