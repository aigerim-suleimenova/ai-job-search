"""Командные строки — настоящим запуском, а не подменой subprocess.

Соседний test_clis.py подменяет subprocess.run и проверяет, чем мы собирались
позвать программу. Здесь программа зовётся по-настоящему: на PATH кладётся
исполняемый файл с её именем, он записывает, чем его позвали и что пришло на
вход, и отвечает.

Разница не отвлечённая. Подмена не покажет, находится ли имя на PATH вообще, не
споткнётся ли запуск о Windows (где исполняемым считается .cmd, а не файл с
решёткой в первой строке) и дойдёт ли задание через трубу целиком — с
переносами, кавычками и кириллицей, на которых кодировки и ломаются.

Чего и это не покажет: примут ли настоящие Codex, Copilot, Goose и Qwen те
ключи, что мы им передаём. Это видно только с ними самими; ключи сверены с их
документацией, и сверка — не запуск.
"""
import json
import os
import stat
import sys
from pathlib import Path

import pytest

from jobsearch import providers

# Задание нарочно длинное и нарочно не на латинице: на длине ломается передача
# аргументом, на кириллице — кодировка трубы.
ЗАПРОС = ("Оцени вакансию «Senior Frontend Engineer».\n"
          "Строка с «кавычками», переносом и запятой, вот.\n" * 40)

ВЫЗОВЫ = [
    ("codex", lambda: providers.call_codex(ЗАПРОС, "gpt-5-codex", timeout=60), "gpt-5-codex"),
    ("copilot", lambda: providers.call_copilot(ЗАПРОС, "claude-sonnet-4.5", timeout=60),
     "claude-sonnet-4.5"),
    ("goose", lambda: providers.call_goose(ЗАПРОС, "gpt-4o", timeout=60), "gpt-4o"),
    ("qwen", lambda: providers.call_qwen(ЗАПРОС, "qwen3-coder-plus", timeout=60),
     "qwen3-coder-plus"),
]

ЛОВУШКА = """\
import json, os, sys
рядом = os.path.dirname(os.path.abspath(__file__))
имя = os.environ.get("AIJS_TRAP_NAME", "нет")
задание = sys.stdin.read()
with open(os.path.join(рядом, имя + ".json"), "w", encoding="utf-8") as f:
    json.dump({"argv": sys.argv[1:], "stdin": задание}, f, ensure_ascii=False)
sys.stdout.buffer.write(("ответ от " + имя).encode("utf-8"))
"""


@pytest.fixture
def двор(monkeypatch):
    """Двор с подставными программами на PATH.

    Не tmp_path: pytest складывает его по имени теста, а имена тут русские — и
    путь получается с кириллицей. На Windows он попадает внутрь .cmd, а тот
    читается в кодировке консоли, и запуск обрывался бы ещё до дела. Системный
    временный каталог — латиница.
    """
    import shutil
    import tempfile
    место = Path(tempfile.mkdtemp(prefix="aijs-cli-"))
    ловушка = место / "trap.py"
    ловушка.write_text(ЛОВУШКА, encoding="utf-8")
    for имя, _, _ in ВЫЗОВЫ:
        if os.name == "nt":
            (место / f"{имя}.cmd").write_text(
                f'@echo off\r\nset AIJS_TRAP_NAME={имя}\r\n'
                f'"{sys.executable}" "{ловушка}" %*\r\n', encoding="ascii")
        else:
            файл = место / имя
            файл.write_text(
                f'#!/bin/sh\nAIJS_TRAP_NAME={имя} exec "{sys.executable}" "{ловушка}" "$@"\n',
                encoding="utf-8")
            файл.chmod(файл.stat().st_mode | stat.S_IEXEC | stat.S_IXGRP | stat.S_IXOTH)
    monkeypatch.setenv("PATH", str(место) + os.pathsep + os.environ["PATH"])
    providers.forget_binaries()
    providers.login_env.cache_clear()
    yield место
    providers.forget_binaries()
    providers.login_env.cache_clear()
    shutil.rmtree(место, ignore_errors=True)


@pytest.mark.parametrize("имя,вызов,модель", ВЫЗОВЫ)
def test_программа_зовётся_и_задание_доходит(двор, имя, вызов, модель):
    ответ = вызов()

    assert ответ == f"ответ от {имя}", "ответ программы не разобран"
    поймано = json.loads((двор / f"{имя}.json").read_text(encoding="utf-8"))
    assert поймано["stdin"] == ЗАПРОС, "задание дошло через трубу искажённым"
    assert any(модель in a for a in поймано["argv"]), "имя модели не передано"
    # Задание аргументом не уходит и уйти не должно: длина командной строки
    # ограничена, и как раз на таких запросах она и кончается.
    assert not any(ЗАПРОС[:40] in a for a in поймано["argv"])


def test_провайдер_считается_установленным_если_программа_на_месте(двор):
    """resolve_bin ищет по PATH, и на Windows это .cmd — то, чем npm и ставит
    свои командные строки."""
    доступны = providers.available("claude", {})
    for имя in ("codex_cli", "copilot_cli", "goose_cli", "qwen_cli"):
        assert доступны[имя]["ready"] is True, f"{имя} не найден на PATH"


def test_жалоба_программы_доходит_до_человека(двор):
    """Программа падает и говорит, почему. Проглотить это значило бы оставить
    человека с «что-то пошло не так»."""
    if os.name == "nt":
        (двор / "qwen.cmd").write_text(
            "@echo off\r\necho qwen needs an API key 1>&2\r\nexit /b 1\r\n", encoding="ascii")
    else:
        файл = двор / "qwen"
        файл.write_text('#!/bin/sh\necho "qwen needs an API key" >&2\nexit 1\n', encoding="utf-8")
        файл.chmod(файл.stat().st_mode | stat.S_IEXEC)
    providers.forget_binaries()

    with pytest.raises(providers.ProviderError) as поймано:
        providers.call_qwen("привет", "", timeout=60)

    assert "needs an API key" in str(поймано.value)


def test_молчаливое_падение_тоже_объясняется(двор):
    """А если программа не сказала ничего — сказать надо нам, иначе человек
    видит пустоту."""
    from jobsearch import i18n
    if os.name == "nt":
        (двор / "goose.cmd").write_text("@echo off\r\nexit /b 3\r\n", encoding="ascii")
    else:
        файл = двор / "goose"
        файл.write_text("#!/bin/sh\nexit 3\n", encoding="utf-8")
        файл.chmod(файл.stat().st_mode | stat.S_IEXEC)
    providers.forget_binaries()

    with pytest.raises(providers.ProviderError) as поймано:
        providers.call_goose("привет", "", timeout=60)

    текст = i18n.err("ru", поймано.value)
    assert "goose" in текст and "3" in текст, f"нечего прочесть: {текст}"


def test_кириллица_в_среде_не_ломает_запуск(monkeypatch, двор):
    """Найдено живой проверкой, и это была настоящая поломка.

    login_env() спрашивает переменные у входной оболочки и разбирал ответ
    кодировкой системы — cp1252 на Windows, ascii при локали C. Одна нелатинская
    буква в чужой переменной, и разбор рушился. А она есть у всякого, чьё имя не
    латиницей: оно лежит в HOME и в PATH.

    Падало при этом скверно: ошибка случалась в потоке чтения, stdout молча
    оказывался None, и наружу выходил AttributeError вместо внятного отказа.
    Переставали работать разом все командные строки.

    Оболочку подставляем свою. Настоящая на разных машинах разная, а где-то её
    нет вовсе — и тест, положившийся на неё, прошёл бы вхолостую, ничего не
    проверив. Здесь она отвечает ровно тем, на чём мы и споткнулись.
    """
    свалка = двор / "envdump.py"
    свалка.write_text(
        "import sys" + chr(10) +
        'sys.stdout.buffer.write("HOME=/Users/Виктор' + chr(92) + '0ИМЯ=Лаврентьев'
        + chr(92) + '0".encode("utf-8"))' + chr(10),
        encoding="utf-8")
    if os.name == "nt":
        оболочка = двор / "shell.cmd"
        оболочка.write_text("@echo off" + chr(13) + chr(10)
                            + f'"{sys.executable}" "{свалка}"' + chr(13) + chr(10),
                            encoding="ascii")
    else:
        оболочка = двор / "shell.sh"
        оболочка.write_text("#!/bin/sh" + chr(10)
                            + f'exec "{sys.executable}" "{свалка}"' + chr(10),
                            encoding="utf-8")
        оболочка.chmod(оболочка.stat().st_mode | stat.S_IEXEC)
    monkeypatch.setenv("SHELL", str(оболочка))
    providers.login_env.cache_clear()

    env = providers.login_env()

    assert env.get("HOME") == "/Users/Виктор", f"переменные не разобрались: {env.get('HOME')!r}"
    assert env.get("ИМЯ") == "Лаврентьев"
    # И сам вызов доходит до конца, а не падает по дороге
    assert providers.call_codex("привет", "", timeout=60) == "ответ от codex"
