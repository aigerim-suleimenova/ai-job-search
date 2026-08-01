"""Переводы: пропуск здесь не ломает программу, а тихо подсовывает английский.

Ключи добавляются пачками по четырнадцати языкам вручную — забыть один проще
простого, и заметить это может только тот, кто читает интерфейс на этом языке.
Отдельная беда — расходящиеся подстановки: строка с {name} там, где код передаёт
{title}, роняет страницу целиком, и только на одном языке.
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
    """Ключи этапов показываются в живой строке состояния — если ключа нет,
    человек увидит «stage_collect» вместо человеческой фразы."""
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


# --- Русский текст мимо переводов ----------------------------------------------
#
# Ключи-то переведены все, но часть текста никогда через них не проходила: имя
# провайдера, пометки в каталоге моделей, названия почтовых служб, имя профиля
# по умолчанию. Всё это приезжало на страницу по-русски на любом языке.

КИРИЛЛИЦА = re.compile(r"[А-Яа-яЁё]")


@pytest.mark.parametrize("provider", ["claude_cli", "cursor_cli", "ollama"])
def test_каталог_моделей_без_русского(provider):
    from jobsearch import providers
    for m in providers.models_for(provider, installed=set(), lang="en"):
        for поле in ("name", "note", "origin"):
            значение = m.get(поле) or ""
            assert not КИРИЛЛИЦА.search(значение), f"{m['id']}.{поле}: {значение}"


def test_каталог_моделей_переводится_а_не_обезличивается():
    """Русскому пометки тоже нужны — их не выкинули, а перенесли в переводы."""
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
    """Пустая или незнакомая настройка заставляла модель писать по-русски."""
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
    """Вывод внешней программы переводить нечем — он идёт как пришёл."""
    from jobsearch import providers
    assert i18n.err("en", providers.ProviderError("credit balance is too low")) \
        == "credit balance is too low"
    assert i18n.err("en", RuntimeError("boom")) == "boom"


def test_повтор_вызова_не_зависит_от_языка_сообщения():
    """Раньше «повторить ли» решалось поиском русских слов в тексте ошибки:
    с переводом повторы просто перестали бы включаться."""
    from jobsearch import llm
    таймаут = llm.ClaudeError(key="prov_err_timeout", transient=True, tool="claude", seconds=5)
    assert llm._is_transient(таймаут)
    assert not llm._is_transient(llm.ClaudeError(key="prov_err_no_claude", path="claude"))
    assert llm._is_transient(llm.ClaudeError("API Error: overloaded"))
