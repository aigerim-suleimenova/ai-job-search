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


# --- Язык, выбранный в установщике ---------------------------------------------

def _установщик_выбрал(monkeypatch, tmp_path, содержимое: str):
    """Кладёт installer.json туда, куда его пишет установщик."""
    from jobsearch import profiles
    (tmp_path / "installer.json").write_text(содержимое, encoding="utf-8")
    monkeypatch.setattr(profiles, "DATA_ROOT", tmp_path)


def test_язык_установщика_доходит_до_программы(monkeypatch, tmp_path, profile):
    """Человек ставит программу по-английски на русской Windows и получает её
    по-русски: язык у него спрашивали и тут же выбрасывали, беря язык системы."""
    from jobsearch import i18n
    _установщик_выбрал(monkeypatch, tmp_path, '{"lang": "en"}')
    monkeypatch.setattr(i18n, "system_lang", lambda default="en": "ru")

    assert config.load()["ui"]["lang"] == "en"


def test_без_установщика_остаётся_язык_системы(monkeypatch, tmp_path, profile):
    """Портативная сборка и сборки для Mac и Linux ставятся без нашего
    установщика — им по-прежнему отвечает система."""
    from jobsearch import i18n
    monkeypatch.setattr(i18n, "system_lang", lambda default="en": "ru")
    from jobsearch import profiles
    monkeypatch.setattr(profiles, "DATA_ROOT", tmp_path)   # installer.json нет

    assert config.load()["ui"]["lang"] == "ru"


def test_свой_выбор_не_перебивается_переустановкой(monkeypatch, tmp_path, profile):
    """Установщик спрашивает язык каждый раз. Если человек с тех пор поменял его
    в самой программе, переустановка не должна возвращать всё назад."""
    from jobsearch import i18n
    cfg = config.load()
    cfg["ui"]["lang"] = "it"
    config.save(cfg)
    _установщик_выбрал(monkeypatch, tmp_path, '{"lang": "en"}')
    monkeypatch.setattr(i18n, "system_lang", lambda default="en": "ru")

    assert config.load()["ui"]["lang"] == "it"


def test_мусор_в_файле_не_роняет_запуск(monkeypatch, tmp_path, profile):
    """Файл пишет установщик, а не мы, и читается он на самом первом запуске:
    сломанный или чужой язык в нём не должен быть последним, что видит человек."""
    from jobsearch import i18n
    monkeypatch.setattr(i18n, "system_lang", lambda default="en": "ru")
    for мусор in ('не json', '{}', '{"lang": ""}', '{"lang": "клингонский"}', '[]'):
        _установщик_выбрал(monkeypatch, tmp_path, мусор)
        assert config.load()["ui"]["lang"] == "ru", мусор
