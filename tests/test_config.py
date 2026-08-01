"""Settings: saved and read back round the loop.

Every new checkbox is added in three places — DEFAULTS, the template and the
/save handler. Missing any one of them looks exactly the same: a person ticks the
box, the page reloads, the box is clear, and there is no error anywhere.
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
    """Booleans break more often than anything else: False is indistinguishable
    from "not set" if there is an `or` somewhere along the way."""
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
    """A person upgraded the program — their config.json has none of the new
    fields. Those must appear from the defaults, and their own values stay."""
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
