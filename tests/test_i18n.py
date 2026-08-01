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

ЯЗЫКИ_МОДУЛЕЙ = sorted(set(i18n.UI_LANGS) - {"ru", "en", "it", "de"})
ПОДСТАНОВКА = re.compile(r"\{(\w+)\}")


def test_все_языки_интерфейса_имеют_модуль_или_место_в_TR():
    for lang in i18n.UI_LANGS:
        assert i18n.t(lang, "nav_results") != "nav_results", f"{lang}: нет даже базовых строк"


@pytest.mark.parametrize("lang", ЯЗЫКИ_МОДУЛЕЙ)
def test_в_языке_есть_все_ключи(lang):
    свои = set(i18n._locale(lang))
    нет = sorted(set(i18n.TR) - свои)
    assert not нет, f"{lang}: не переведено {len(нет)} ключей, например {нет[:5]}"


@pytest.mark.parametrize("lang", sorted(i18n.UI_LANGS))
def test_подстановки_совпадают_с_английскими(lang):
    расхождения = []
    for key, entry in i18n.TR.items():
        эталон = ПОДСТАНОВКА.findall(entry.get("en") or "")
        перевод = i18n.t(lang, key)
        if sorted(ПОДСТАНОВКА.findall(перевод)) != sorted(эталон):
            расхождения.append(f"{key}: ожидались {эталон}, в переводе {ПОДСТАНОВКА.findall(перевод)}")
    assert not расхождения, f"{lang}: " + "; ".join(расхождения[:5])


def test_этапы_прогона_переведены():
    """The stage keys are shown in the live status line — with a key missing, a
    person sees "stage_collect" instead of a human sentence."""
    from jobsearch import pipeline
    for key in pipeline.STAGE_ORDER:
        assert i18n.t("en", key) != key, f"этап {key} без перевода"


def test_язык_справа_налево_известен():
    assert "ar" in i18n.RTL_LANGS
    assert i18n.RTL_LANGS <= set(i18n.UI_LANGS)


def test_составной_язык_вывода_берёт_первый():
    assert i18n.t("it-en", "nav_results") == i18n.t("it", "nav_results")


def test_неизвестный_язык_откатывается_на_английский():
    assert i18n.t("xx", "nav_results") == i18n.TR["nav_results"]["en"]


# --- Russian text that went round the translations -----------------------------
#
# Every key is translated, but some of the text never went through them at all:
# the provider's name, the notes in the model catalogue, the names of the mail
# services, the default profile name. All of it reached the page in Russian, in
# every language.

КИРИЛЛИЦА = re.compile(r"[А-Яа-яЁё]")


@pytest.mark.parametrize("provider", ["claude_cli", "cursor_cli", "ollama"])
def test_каталог_моделей_без_русского(provider):
    from jobsearch import providers
    for m in providers.models_for(provider, installed=set(), lang="en"):
        for поле in ("name", "note", "origin"):
            значение = m.get(поле) or ""
            assert not КИРИЛЛИЦА.search(значение), f"{m['id']}.{поле}: {значение}"


def test_каталог_моделей_переводится_а_не_обезличивается():
    """A Russian reader needs the notes too — they were moved into the translations,
    not thrown away."""
    from jobsearch import providers
    ru = {m["id"]: m for m in providers.models_for("ollama", installed=set(), lang="ru")}
    assert ru["deepseek-r1:70b"]["note"] == "рассуждающая"
    assert ru["llama3.3:70b"]["origin"] == "Meta (США)"
    en = {m["id"]: m for m in providers.models_for("ollama", installed=set(), lang="en")}
    assert en["llama3.3:70b"]["origin"] == "Meta (USA)"


def test_почтовые_службы_без_русского():
    from jobsearch import mailer
    имена = [p["name"] for p in mailer.presets("en").values()]
    assert not any(КИРИЛЛИЦА.search(n) for n in имена), имена
    assert mailer.presets("ru")["custom"]["name"] == "Другая (укажу вручную)"


def test_имя_профиля_по_умолчанию_на_языке_системы(monkeypatch):
    from jobsearch import profiles
    monkeypatch.setattr(i18n, "system_lang", lambda default="en": "en")
    assert profiles._default_name() == "Me"
    monkeypatch.setattr(i18n, "system_lang", lambda default="en": "ru")
    assert profiles._default_name() == "Я"


def test_язык_ответа_модели_откатывается_на_английский():
    """An empty or unfamiliar setting used to make the model write in Russian."""
    assert i18n.out_lang({"ui": {}}) == i18n.OUTPUT_INSTRUCTION["en"]
    assert i18n.out_lang({"ui": {"output_lang": "xx"}}) == i18n.OUTPUT_INSTRUCTION["en"]
    assert i18n.out_lang({"ui": {"output_lang": "", "lang": "de"}}) \
        == i18n.OUTPUT_INSTRUCTION["de"]
    assert i18n.out_lang({"ui": {"output_lang": "ru"}}) == i18n.OUTPUT_INSTRUCTION["ru"]


def test_ошибка_с_ключом_переводится():
    from jobsearch import llm, notify, providers
    assert i18n.err("en", providers.ProviderError(key="prov_err_ollama_down")) \
        .startswith("Ollama is not answering")
    assert i18n.err("en", llm.ClaudeError(key="prov_err_timeout", tool="claude", seconds=5)) \
        == "claude did not answer within 5s"
    assert i18n.err("ru", notify.NotifyError("tg_err_no_token")) == "Не задан bot token"


def test_чужой_текст_ошибки_остаётся_как_есть():
    """There is nothing to translate an outside program's output with — it goes as it came."""
    from jobsearch import providers
    assert i18n.err("en", providers.ProviderError("credit balance is too low")) \
        == "credit balance is too low"
    assert i18n.err("en", RuntimeError("boom")) == "boom"


def test_повтор_вызова_не_зависит_от_языка_сообщения():
    """"Should we retry" used to be decided by looking for Russian words in the
    error text: once translated, the retries would simply have stopped happening."""
    from jobsearch import llm
    таймаут = llm.ClaudeError(key="prov_err_timeout", transient=True, tool="claude", seconds=5)
    assert llm._is_transient(таймаут)
    assert not llm._is_transient(llm.ClaudeError(key="prov_err_no_claude", path="claude"))
    assert llm._is_transient(llm.ClaudeError("API Error: overloaded"))


@pytest.mark.parametrize("lang", sorted(i18n.UI_LANGS))
def test_подсказка_без_адреса_обходится_без_пустых_скобок(lang):
    """В подсказку подставляется адрес, которого на первом заходе ещё нет, и
    посреди предложения повисало «(—)». Проверяем на всех языках разом: по-японски
    и по-китайски скобки полноширинные, и обычный поиск их не находит."""
    from app import _drop_empty_parens
    готово = _drop_empty_parens(i18n.t(lang, "api_model_hint").format(base=""))
    for скобки in ("()", "（）", "( )", "（ ）"):
        assert скобки not in готово, f"{lang}: остались пустые скобки"
    assert "  " not in готово, f"{lang}: остался двойной пробел"


@pytest.mark.parametrize("lang", sorted(i18n.UI_LANGS))
def test_с_адресом_подсказка_его_называет(lang):
    """Обратная сторона: убрать лишнее нельзя ценой того, что нужное тоже пропадёт."""
    from app import _drop_empty_parens
    готово = _drop_empty_parens(
        i18n.t(lang, "api_model_hint").format(base="https://openrouter.ai/api/v1"))
    assert "https://openrouter.ai/api/v1" in готово, f"{lang}: адрес пропал"
