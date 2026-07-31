"""Файлы читаются и пишутся в UTF-8 явно, а не «как в системе».

На macOS и Linux кодировка по умолчанию и так UTF-8, поэтому ошибка здесь
невидима до тех пор, пока кто-нибудь не запустит программу на Windows: там
по умолчанию cp1252, и первая же стрелка «→» в резюме роняет сохранение.
Проверять это на своей машине бесполезно — поэтому проверяем сам код.
"""
import ast
from pathlib import Path

import pytest

КОРЕНЬ = Path(__file__).resolve().parent.parent
ПАПКИ = ["jobsearch", "packaging"]
ФАЙЛЫ = ["app.py", "desktop.py"]

# Эти вызовы работают с текстом, и без encoding= берут кодировку системы
ТЕКСТОВЫЕ = {"read_text", "write_text", "open"}
# ...но open() есть не только у файлов: webbrowser.open открывает браузер
НЕ_ФАЙЛЫ = {"webbrowser", "os", "subprocess", "urllib", "request"}


def исходники():
    for имя in ФАЙЛЫ:
        yield КОРЕНЬ / имя
    for папка in ПАПКИ:
        yield from sorted((КОРЕНЬ / папка).rglob("*.py"))


def двоичный_режим(call: ast.Call) -> bool:
    """open(..., 'rb') и подобное — байты, кодировка им не нужна."""
    режимы = [a for a in call.args[1:2] if isinstance(a, ast.Constant)]
    режимы += [k.value for k in call.keywords
               if k.arg == "mode" and isinstance(k.value, ast.Constant)]
    return any(isinstance(m, ast.Constant) and "b" in str(m.value) for m in режимы)


def нарушения(путь: Path):
    дерево = ast.parse(путь.read_text(encoding="utf-8"))
    for node in ast.walk(дерево):
        if not isinstance(node, ast.Call):
            continue
        имя = (node.func.attr if isinstance(node.func, ast.Attribute)
               else getattr(node.func, "id", ""))
        if имя not in ТЕКСТОВЫЕ:
            continue
        if isinstance(node.func, ast.Attribute):
            получатель = getattr(node.func.value, "id", "")
            if получатель in НЕ_ФАЙЛЫ:
                continue
        if двоичный_режим(node):
            continue
        if any(k.arg == "encoding" for k in node.keywords):
            continue
        yield node.lineno, имя


@pytest.mark.parametrize("путь", list(исходники()), ids=lambda p: p.name)
def test_кодировка_задана_явно(путь):
    найдено = list(нарушения(путь))
    подробно = ", ".join(f"строка {n}: {имя}()" for n, имя in найдено)
    assert not найдено, (
        f"{путь.relative_to(КОРЕНЬ)} — без encoding=\"utf-8\": {подробно}. "
        "На Windows это возьмёт cp1252 и упадёт на первом же не-латинском символе.")


def test_резюме_со_стрелками_и_кириллицей_переживает_запись(profile):
    """Тот самый случай: в резюме «→», и сохранение падало на Windows.

    Текст длиннее сотни символов — короче программа считает разбор неудачным
    и отказывается сохранять."""
    from jobsearch import config
    текст = ("Виктор Лавров — фронтенд-инженер.\n"
             "Опыт: React → TypeScript → Python. Дизайн-системы, доступность.\n"
             "Языки: русский, английский C1, немецкий B1. Стрелки → тире — эмодзи 🎯\n"
             "Работал над переносом библиотеки компонентов на тридцать команд.")
    assert len(текст) > 100
    config.save_cv("резюме.txt", текст.encode("utf-8"))
    assert "→" in config.cv_text(), "стрелка не пережила сохранение"
    assert "🎯" in config.cv_text()
    assert config.cv_meta()["filename"] == "резюме.txt"
