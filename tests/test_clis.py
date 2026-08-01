"""Командные строки, которыми можно думать.

Главное требование к ним у нас одно и оно неочевидное: задание должно уходить
через stdin, а не аргументом командной строки. Наши запросы бывают в тысячи
знаков, с переносами, кавычками и кириллицей, а длина командной строки
ограничена — на Linux около двух мегабайт. Именно на таких запросах она и
кончается, и программа падает с «argument list too long». Поэтому те, кто умеет
читать только аргумент, сюда не годятся, и проверка на это здесь же.
"""
import pytest

from jobsearch import i18n, providers

ЗАПРОС = "Оцени вакансию.\nВторая строка с «кавычками» и переносом.\n" * 200


class Прогон:
    def __init__(self, stdout="ответ модели", code=0, stderr=""):
        self.stdout, self.returncode, self.stderr = stdout, code, stderr


def перехват(monkeypatch, **ответ):
    """Подменяет запуск программы и запоминает, чем её звали."""
    поймано = {}

    def run(cmd, **kw):
        поймано.update(cmd=cmd, stdin=kw.get("input"), cwd=kw.get("cwd"))
        return Прогон(**ответ)

    monkeypatch.setattr(providers.subprocess, "run", run)
    monkeypatch.setattr(providers, "resolve_bin", lambda name: f"/usr/bin/{name}")
    return поймано


ВЫЗОВЫ = [
    ("copilot_cli", providers.call_copilot, "copilot"),
    ("goose_cli", providers.call_goose, "goose"),
    ("qwen_cli", providers.call_qwen, "qwen"),
    ("codex_cli", providers.call_codex, "codex"),
]


@pytest.mark.parametrize("код,вызов,программа", ВЫЗОВЫ)
def test_задание_уходит_через_stdin_а_не_аргументом(monkeypatch, код, вызов, программа):
    """Тот самый предел длины командной строки. Запрос длинный нарочно."""
    поймано = перехват(monkeypatch)

    вызов(ЗАПРОС, "auto", timeout=60)

    assert поймано["stdin"] == ЗАПРОС, f"{программа}: задание не подано на вход"
    склеено = " ".join(поймано["cmd"])
    assert "Оцени вакансию" not in склеено, \
        f"{программа}: задание попало в аргументы — на длинном запросе это оборвётся"


@pytest.mark.parametrize("код,вызов,программа", ВЫЗОВЫ)
def test_зовём_ту_программу_что_обещали(monkeypatch, код, вызов, программа):
    поймано = перехват(monkeypatch)
    вызов("привет", "auto", timeout=60)
    assert поймано["cmd"][0].endswith(программа)


@pytest.mark.parametrize("код,вызов,программа", ВЫЗОВЫ)
def test_ответ_очищается_от_пробелов(monkeypatch, код, вызов, программа):
    перехват(monkeypatch, stdout="  готово  \n")
    assert вызов("привет", "auto", timeout=60) == "готово"


@pytest.mark.parametrize("код,вызов,программа", ВЫЗОВЫ)
def test_авто_не_называет_модель(monkeypatch, код, вызов, программа):
    """«Авто» значит «не называть модель»: тогда берётся настроенная у самой
    программы. Имена моделей у них меняются чаще наших версий, и устаревшее имя
    в списке сломало бы поиск, а «Авто» — нет."""
    поймано = перехват(monkeypatch)
    вызов("привет", "auto", timeout=60)
    assert not [a for a in поймано["cmd"] if a.startswith("--model")], \
        f"{программа}: при «Авто» всё-таки назвали модель"


@pytest.mark.parametrize("код,вызов,программа", ВЫЗОВЫ)
def test_названная_модель_доезжает(monkeypatch, код, вызов, программа):
    поймано = перехват(monkeypatch)
    вызов("привет", "какая-то-модель", timeout=60)
    assert "какая-то-модель" in " ".join(поймано["cmd"])


@pytest.mark.parametrize("код,вызов,программа", ВЫЗОВЫ)
def test_ошибка_программы_объясняется(monkeypatch, код, вызов, программа):
    перехват(monkeypatch, code=1, stderr="кончились деньги")
    with pytest.raises(providers.ProviderError) as поймано:
        вызов("привет", "auto", timeout=60)
    assert "кончились деньги" in str(поймано.value)


@pytest.mark.parametrize("код,вызов,программа", ВЫЗОВЫ)
def test_молчаливое_падение_тоже_объясняется(monkeypatch, код, вызов, программа):
    """Пустой вывод и ненулевой код: сказать «не получилось» всё равно надо."""
    перехват(monkeypatch, code=137, stdout="", stderr="")
    with pytest.raises(providers.ProviderError) as поймано:
        вызов("привет", "auto", timeout=60)
    assert поймано.value.key == "prov_err_exit_code"


@pytest.mark.parametrize("код,вызов,программа", ВЫЗОВЫ)
def test_ненайденная_программа_названа(monkeypatch, код, вызов, программа):
    monkeypatch.setattr(providers, "resolve_bin", lambda name: "")
    monkeypatch.setattr(providers.subprocess, "run",
                        lambda *a, **kw: pytest.fail("полезли запускать то, чего нет"))
    with pytest.raises(providers.ProviderError) as поймано:
        вызов("привет", "auto", timeout=60)
    assert программа in i18n.t("en", поймано.value.key).lower() \
        or "not found" in i18n.t("en", поймано.value.key).lower()


@pytest.mark.parametrize("код,вызов,программа", ВЫЗОВЫ)
def test_работаем_в_пустой_служебной_папке(monkeypatch, код, вызов, программа):
    """Иначе программа осматривается вокруг себя, и на macOS система начинает
    спрашивать разрешения на Документы и Фото — от нашего имени."""
    поймано = перехват(monkeypatch)
    вызов("привет", "auto", timeout=60)
    assert поймано["cwd"] and "cli-work" in str(поймано["cwd"])


# --- Как они выглядят для остальной программы -----------------------------------

@pytest.mark.parametrize("код,вызов,программа", ВЫЗОВЫ)
def test_общий_вызов_ведёт_куда_надо(monkeypatch, код, вызов, программа):
    поймано = перехват(monkeypatch)
    providers.call("привет", код, model="auto", timeout=60)
    assert поймано["cmd"][0].endswith(программа)


@pytest.mark.parametrize("код", [к for к, _, _ in ВЫЗОВЫ])
def test_у_каждого_есть_список_моделей_и_название(код):
    """Пустой список оставил бы человека на втором шаге знакомства ни с чем."""
    модели = providers.models_for(код, lang="en")
    assert модели, f"{код}: нечего выбрать"
    assert модели[0]["id"], f"{код}: у модели нет опознавателя"
    assert i18n.t("en", "prov_" + код) != "prov_" + код, f"{код}: без названия"


@pytest.mark.parametrize("код", [к for к, _, _ in ВЫЗОВЫ])
def test_веб_поиск_обещает_только_тот_кто_умеет(код):
    """Обещать веб-поиск, которого нет, хуже, чем не обещать: без него
    отключается поиск новых компаний, и человек должен знать об этом заранее."""
    assert providers.supports_web_search(код) is False


def test_все_провайдеры_названы_и_описаны():
    """Каждая карточка на первом шаге берёт три строки из переводов. Пропуск
    любой оставил бы на экране служебное имя ключа."""
    for код in providers.available("claude", {}):
        for хвост in ("", "_hint", "_about"):
            ключ = f"prov_{код}{хвост}"
            assert i18n.t("en", ключ) != ключ, f"нет строки {ключ}"
