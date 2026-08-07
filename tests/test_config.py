"""Settings: saved and read back round the loop.

Every new checkbox is added in three places — DEFAULTS, the template and the
/save handler. Missing any one of them looks exactly the same: a person ticks the
box, the page reloads, the box is clear, and there is no error anywhere.
"""
from jobsearch import config


def test_the_defaults_are_all_there(profile):
    cfg = config.load()
    for section in ("profile", "search", "sources", "schedule", "llm", "ui", "telegram"):
        assert section in cfg, f"the {section} section is gone"


def test_saving_and_reading_back(profile):
    cfg = config.load()
    cfg["search"]["threshold"] = 82
    cfg["search"]["locations"] = "Berlin, remote"
    config.save(cfg)
    again = config.load()
    assert again["search"]["threshold"] == 82
    assert again["search"]["locations"] == "Berlin, remote"


def test_checkboxes_survive_the_round_trip(profile):
    """Booleans break more often than anything else: False is indistinguishable
    from "not set" if there is an `or` somewhere along the way."""
    cfg = config.load()
    for flag in ("deep_during_run", "research_company", "include_remote",
                 "drop_off_target", "triage_second_vote"):
        assert flag in cfg["search"], f"{flag} went missing from the defaults"
        cfg["search"][flag] = False
    config.save(cfg)
    again = config.load()
    for flag in ("deep_during_run", "research_company", "include_remote",
                 "drop_off_target", "triage_second_vote"):
        assert again["search"][flag] is False, f"{flag} came back True after saving"


def test_new_keys_are_added_to_an_old_file(profile):
    """A person upgraded the program — their config.json has none of the new
    fields. Those must appear from the defaults, and their own values stay."""
    cfg = config.load()
    cfg["search"]["threshold"] = 55
    del cfg["search"]["deep_during_run"]
    config.save(cfg)
    again = config.load()
    assert again["search"]["threshold"] == 55, "somebody elses settings were wiped"
    assert "deep_during_run" in again["search"], "the new field was not filled in"


def test_profiles_do_not_see_each_others_settings(profile):
    from jobsearch import profiles
    cfg = config.load()
    cfg["search"]["locations"] = "Berlin"
    config.save(cfg)

    second = profiles.create("Second")
    profiles.set_active(second)
    assert config.load()["search"]["locations"] != "Berlin", "settings leaked between people"

    profiles.set_active(profile)
    assert config.load()["search"]["locations"] == "Berlin"


# --- The language chosen in the installer -------------------------------------

def _installer_chose(monkeypatch, tmp_path, contents: str):
    """Puts installer.json where the installer writes it."""
    from jobsearch import profiles
    (tmp_path / "installer.json").write_text(contents, encoding="utf-8")
    monkeypatch.setattr(profiles, "DATA_ROOT", tmp_path)


def test_the_installers_language_reaches_the_program(monkeypatch, tmp_path, profile):
    """A person installs the program in English on a Russian Windows and gets it
    in Russian: they were asked for a language and it was thrown away at once, the
    system's being taken instead."""
    from jobsearch import i18n
    _installer_chose(monkeypatch, tmp_path, '{"lang": "en"}')
    monkeypatch.setattr(i18n, "system_lang", lambda default="en": "ru")

    assert config.load()["ui"]["lang"] == "en"


def test_with_no_installer_the_system_language_stands(monkeypatch, tmp_path, profile):
    """The portable build and the Mac and Linux builds are installed without our
    installer — for them the system still answers."""
    from jobsearch import i18n
    monkeypatch.setattr(i18n, "system_lang", lambda default="en": "ru")
    from jobsearch import profiles
    monkeypatch.setattr(profiles, "DATA_ROOT", tmp_path)   # no installer.json

    assert config.load()["ui"]["lang"] == "ru"


def test_your_own_choice_survives_a_reinstall(monkeypatch, tmp_path, profile):
    """The installer asks for a language every time. If the person has changed it
    in the program since, reinstalling must not put everything back."""
    from jobsearch import i18n
    cfg = config.load()
    cfg["ui"]["lang"] = "it"
    config.save(cfg)
    _installer_chose(monkeypatch, tmp_path, '{"lang": "en"}')
    monkeypatch.setattr(i18n, "system_lang", lambda default="en": "ru")

    assert config.load()["ui"]["lang"] == "it"


def test_rubbish_in_the_file_does_not_break_startup(monkeypatch, tmp_path, profile):
    """The file is written by the installer, not by us, and it is read on the very
    first run: a broken or unknown language in it must not be the last thing a
    person sees."""
    from jobsearch import i18n
    monkeypatch.setattr(i18n, "system_lang", lambda default="en": "ru")
    for rubbish in ('not json', '{}', '{"lang": ""}', '{"lang": "klingon"}', '[]'):
        _installer_chose(monkeypatch, tmp_path, rubbish)
        assert config.load()["ui"]["lang"] == "ru", rubbish
