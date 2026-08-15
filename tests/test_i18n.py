"""Translations: a gap here does not break the program, it quietly serves English.

Keys are added by hand in batches across fourteen languages — forgetting one is
the easiest thing in the world, and only somebody reading the interface in that
language would ever notice. A separate trouble is placeholders drifting apart: a
string with {name} where the code passes {title} brings the whole page down, and
in one language only.
"""
import re

import pytest

from jobsearch import i18n

MODULE_LANGS = sorted(set(i18n.UI_LANGS) - {"ru", "en", "it", "de"})
PLACEHOLDER = re.compile(r"\{(\w+)\}")


def test_every_interface_language_has_a_module_or_a_place_in_TR():
    for lang in i18n.UI_LANGS:
        assert i18n.t(lang, "nav_results") != "nav_results", f"{lang}: not even the basic strings are there"


@pytest.mark.parametrize("lang", MODULE_LANGS)
def test_the_language_has_every_key(lang):
    own_keys = set(i18n._locale(lang))
    missing = sorted(set(i18n.TR) - own_keys)
    assert not missing, f"{lang}: {len(missing)} keys untranslated, for instance {missing[:5]}"


@pytest.mark.parametrize("lang", sorted(i18n.UI_LANGS))
def test_the_placeholders_match_the_english_ones(lang):
    mismatches = []
    for key, entry in i18n.TR.items():
        expected = PLACEHOLDER.findall(entry.get("en") or "")
        translated = i18n.t(lang, key)
        if sorted(PLACEHOLDER.findall(translated)) != sorted(expected):
            mismatches.append(f"{key}: expected {expected}, translation has {PLACEHOLDER.findall(translated)}")
    assert not mismatches, f"{lang}: " + "; ".join(mismatches[:5])


def test_the_run_stages_are_translated():
    """The stage keys are shown in the live status line — with a key missing, a
    person sees "stage_collect" instead of a human sentence."""
    from jobsearch import pipeline
    for key in pipeline.STAGE_ORDER:
        assert i18n.t("en", key) != key, f"stage {key} has no translation"


def test_the_right_to_left_language_is_known():
    assert "ar" in i18n.RTL_LANGS
    assert i18n.RTL_LANGS <= set(i18n.UI_LANGS)


def test_a_compound_output_language_takes_the_first():
    assert i18n.t("it-en", "nav_results") == i18n.t("it", "nav_results")


def test_an_unknown_language_falls_back_to_english():
    assert i18n.t("xx", "nav_results") == i18n.TR["nav_results"]["en"]


# --- Russian text that went round the translations -----------------------------
#
# Every key is translated, but some of the text never went through them at all:
# the provider's name, the notes in the model catalogue, the names of the mail
# services, the default profile name. All of it reached the page in Russian, in
# every language.

CYRILLIC = re.compile(r"[А-Яа-яЁё]")


@pytest.mark.parametrize("provider", ["claude_cli", "cursor_cli", "ollama"])
def test_the_model_catalogue_has_no_russian_in_it(provider):
    from jobsearch import providers
    for m in providers.models_for(provider, installed=set(), lang="en"):
        for field in ("name", "note", "origin"):
            value = m.get(field) or ""
            assert not CYRILLIC.search(value), f"{m['id']}.{field}: {value}"


def test_the_model_catalogue_is_translated_rather_than_stripped():
    """A Russian reader needs the notes too — they were moved into the translations,
    not thrown away."""
    from jobsearch import providers
    ru = {m["id"]: m for m in providers.models_for("ollama", installed=set(), lang="ru")}
    assert ru["deepseek-r1:70b"]["note"] == "рассуждающая"
    assert ru["llama3.3:70b"]["origin"] == "Meta (США)"
    en = {m["id"]: m for m in providers.models_for("ollama", installed=set(), lang="en")}
    assert en["llama3.3:70b"]["origin"] == "Meta (USA)"


def test_the_mail_services_have_no_russian_in_them():
    from jobsearch import mailer
    names = [p["name"] for p in mailer.presets("en").values()]
    assert not any(CYRILLIC.search(n) for n in names), names
    assert mailer.presets("ru")["custom"]["name"] == "Другая (укажу вручную)"


def test_the_default_profile_name_does_not_depend_on_the_system_language(monkeypatch):
    """The name used to be taken from the system language — the only one known when
    the profile is created, since there are no settings yet. But the system
    language and the program's language differ all the time, and someone on a
    Russian Windows who installed the English build saw «Я» sitting in the middle
    of an English interface in the person picker."""
    from jobsearch import profiles
    for lang_code in ("en", "ru", "ja", "ar"):
        monkeypatch.setattr(i18n, "system_lang", lambda default="en", code=lang_code: code)
        assert profiles._default_name() == "user", f"the system language {lang_code} still has an effect"


def test_the_models_output_language_falls_back_to_english():
    """An empty or unfamiliar setting used to make the model write in Russian."""
    assert i18n.out_lang({"ui": {}}) == i18n.OUTPUT_INSTRUCTION["en"]
    assert i18n.out_lang({"ui": {"output_lang": "xx"}}) == i18n.OUTPUT_INSTRUCTION["en"]
    assert i18n.out_lang({"ui": {"output_lang": "", "lang": "de"}}) \
        == i18n.OUTPUT_INSTRUCTION["de"]
    assert i18n.out_lang({"ui": {"output_lang": "ru"}}) == i18n.OUTPUT_INSTRUCTION["ru"]


def test_the_language_demand_reaches_the_prompt_by_the_same_rule():
    """The prompts are Russian, and what keeps the answer in another language is
    the demand at the top of them. It was asked for by reading output_lang
    directly, with "ru" for a default: with that setting never written down the
    demand vanished, and the Russian prompt was answered in Russian."""
    assert "in English" in i18n.lang_banner({"ui": {"output_lang": "en"}})
    assert "in English" in i18n.lang_banner({"ui": {"lang": "en"}}), \
        "the interface language does not stand in for an empty results language"
    assert i18n.lang_banner({"ui": {}}), "with nothing set, English is what out_lang promises"
    assert i18n.lang_banner({"ui": {"output_lang": "xx"}}), "an unknown code falls back to English"
    assert i18n.lang_banner({"ui": {"output_lang": "ru"}}) == "", \
        "a Russian prompt needs no demand to answer in Russian"


def test_an_error_carrying_a_key_gets_translated():
    from jobsearch import llm, notify, providers
    assert i18n.err("en", providers.ProviderError(key="prov_err_ollama_down")) \
        .startswith("Ollama is not answering")
    assert i18n.err("en", llm.ClaudeError(key="prov_err_timeout", tool="claude", seconds=5)) \
        == "claude did not answer within 5s"
    assert i18n.err("ru", notify.NotifyError("tg_err_no_token")) == "Не задан bot token"


def test_an_outside_error_text_is_left_as_it_came():
    """There is nothing to translate an outside program's output with — it goes as it came."""
    from jobsearch import providers
    assert i18n.err("en", providers.ProviderError("credit balance is too low")) \
        == "credit balance is too low"
    assert i18n.err("en", RuntimeError("boom")) == "boom"


def test_retrying_does_not_depend_on_the_messages_language():
    """"Should we retry" used to be decided by looking for Russian words in the
    error text: once translated, the retries would simply have stopped happening."""
    from jobsearch import llm
    timed_out = llm.ClaudeError(key="prov_err_timeout", transient=True, tool="claude", seconds=5)
    assert llm._is_transient(timed_out)
    assert not llm._is_transient(llm.ClaudeError(key="prov_err_no_claude", path="claude"))
    assert llm._is_transient(llm.ClaudeError("API Error: overloaded"))


@pytest.mark.parametrize("lang", sorted(i18n.UI_LANGS))
def test_the_hint_with_no_address_avoids_empty_brackets(lang):
    """The hint has the address substituted into it, and on the first visit there is
    no address yet, so an "(—)" hung in the middle of the sentence. We check every
    language at once: in Japanese and Chinese the brackets are full-width, and an
    ordinary search does not find them."""
    from app import _drop_empty_parens
    cleaned = _drop_empty_parens(i18n.t(lang, "api_model_hint").format(base=""))
    for brackets in ("()", "（）", "( )", "（ ）"):
        assert brackets not in cleaned, f"{lang}: empty brackets are still there"
    assert "  " not in cleaned, f"{lang}: a double space is still there"


@pytest.mark.parametrize("lang", sorted(i18n.UI_LANGS))
def test_with_an_address_the_hint_names_it(lang):
    """The other side: removing what is surplus must not cost us what is needed."""
    from app import _drop_empty_parens
    cleaned = _drop_empty_parens(
        i18n.t(lang, "api_model_hint").format(base="https://openrouter.ai/api/v1"))
    assert "https://openrouter.ai/api/v1" in cleaned, f"{lang}: the address vanished"


def test_the_program_understands_the_installers_language_codes():
    """The installer writes its language code into a file and the program reads it.
    The codes come from [Languages] in installer.iss, and the two must not drift
    apart: an unknown code the program silently discards, opening in the system
    language — exactly the trouble all of this was done for."""
    import re
    from pathlib import Path
    iss = (Path(__file__).resolve().parent.parent / "packaging" / "installer.iss")
    codes = re.findall(r'^Name: "([^"]+)"; MessagesFile', iss.read_text(encoding="utf-8"), re.M)
    assert codes, "not one language was found in installer.iss — has the format changed?"
    unknown = [c for c in codes if c not in i18n.UI_LANGS]
    assert not unknown, f"the installer speaks languages the program does not have: {unknown}"


def test_the_web_search_notice_names_the_number_everywhere():
    """The number of sources is substituted in. A language that lost its {n} will
    show the sentence without a number — and before that the number was written
    out in words and drifted from the truth: it promised nine when there were
    twelve."""
    without_number = [code for code in i18n.UI_LANGS
                      if "{n}" not in i18n.t(code, "models_websearch_warning")]
    assert without_number == [], \
        f"the number will not be substituted in these languages: {without_number}"


def test_the_web_search_notice_formats_without_error():
    """Besides {n} there must be no other curly braces in the text: .format would
    trip over them and bring the "Model" page down in that language."""
    for code in i18n.UI_LANGS:
        i18n.t(code, "models_websearch_warning").format(n=12)
