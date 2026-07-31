"""Настройки: сохранение и чтение по кругу.

Каждая новая галочка добавляется в трёх местах — DEFAULTS, шаблон и обработчик
/save. Пропуск в любом из них выглядит одинаково: человек ставит галочку,
страница перезагружается, галочка снята, и никакой ошибки.
"""
from jobsearch import config


def test_умолчания_на_месте(profile):
    cfg = config.load()
    for раздел in ("profile", "search", "sources", "schedule", "llm", "ui", "telegram"):
        assert раздел in cfg, f"пропал раздел {раздел}"


def test_сохранение_и_чтение(profile):
    cfg = config.load()
    cfg["search"]["threshold"] = 82
    cfg["search"]["locations"] = "Berlin, remote"
    config.save(cfg)
    снова = config.load()
    assert снова["search"]["threshold"] == 82
    assert снова["search"]["locations"] == "Berlin, remote"


def test_галочки_переживают_круг(profile):
    """Булевы значения ломаются чаще прочих: False неотличим от «не задано»,
    если где-то по дороге стоит `or`."""
    cfg = config.load()
    for флаг in ("deep_during_run", "research_company", "include_remote",
                 "drop_off_target", "triage_second_vote"):
        assert флаг in cfg["search"], f"{флаг} потерялся в умолчаниях"
        cfg["search"][флаг] = False
    config.save(cfg)
    снова = config.load()
    for флаг in ("deep_during_run", "research_company", "include_remote",
                 "drop_off_target", "triage_second_vote"):
        assert снова["search"][флаг] is False, f"{флаг} вернулся в True после сохранения"


def test_новые_ключи_добавляются_к_старому_файлу(profile):
    """Человек обновил программу — в его config.json нет новых полей.
    Они должны появиться из умолчаний, а его значения остаться."""
    cfg = config.load()
    cfg["search"]["threshold"] = 55
    del cfg["search"]["deep_during_run"]
    config.save(cfg)
    снова = config.load()
    assert снова["search"]["threshold"] == 55, "чужие настройки затёрлись"
    assert "deep_during_run" in снова["search"], "новое поле не подставилось"


def test_профили_не_видят_чужие_настройки(profile):
    from jobsearch import profiles
    cfg = config.load()
    cfg["search"]["locations"] = "Berlin"
    config.save(cfg)

    второй = profiles.create("Второй")
    profiles.set_active(второй)
    assert config.load()["search"]["locations"] != "Berlin", "настройки протекли между людьми"

    profiles.set_active(profile)
    assert config.load()["search"]["locations"] == "Berlin"
