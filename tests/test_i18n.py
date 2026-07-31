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
