"""Работодатели из ссылок, которые уже скачаны.

Проверка идёт без сети: собранные адреса не запрашиваются, а только строятся
и опознаются обратно. Именно круг «собрал → опознал» здесь и важен: без него
одна и та же доска добавлялась бы каждый прогон заново.
"""
from jobsearch import harvest
from jobsearch.collectors import ats


def вакансия(url, company="Компания"):
    return {"url": url, "company": company}


def test_доска_из_ссылки_на_вакансию():
    новые = harvest.find_new([вакансия("https://boards.greenhouse.io/mercury/jobs/4012", "Mercury")], [])
    assert новые == [{"name": "Mercury", "url": "https://boards.greenhouse.io/mercury"}]


def test_собранный_адрес_опознаётся_обратно():
    """Иначе доска добавится второй раз на следующем прогоне."""
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
    """Человек добавил доску руками — пусть и в другом написании."""
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
    """Список компаний пополняется сам — человек должен и знать об этом,
    и уметь запретить, не читая исходников."""
    import pytest
    pytest.importorskip("httpx", reason="TestClient требует httpx")
    from fastapi.testclient import TestClient

    import app as app_module
    from jobsearch import appstate, config, i18n

    appstate.mark_setup_done(config.load()["llm"])   # иначе первый запуск уводит на знакомство
    with TestClient(app_module.app) as c:
        c.cookies.set("profile", profile)
        страница = c.get("/").text
        assert 'name="harvest_boards"' in страница
        язык = config.load()["ui"]["lang"]
        assert i18n.t(язык, "harvest_boards") in страница
        assert i18n.t(язык, "harvest_boards_hint")[:40] in страница

        # галочка снята — в форме поля просто нет
        c.post("/save", data={"companies": "", "use_remotive": "on"}, follow_redirects=False)
        assert config.load()["sources"]["harvest_boards"] is False

        c.post("/save", data={"companies": "", "harvest_boards": "on"}, follow_redirects=False)
        assert config.load()["sources"]["harvest_boards"] is True
