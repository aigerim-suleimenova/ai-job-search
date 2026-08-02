"""Files are read and written as UTF-8 explicitly, not "however the system does it".

On macOS and Linux the default encoding is UTF-8 anyway, so a mistake here stays
invisible until somebody runs the program on Windows: there the default is
cp1252, and the very first arrow "→" in a CV brings the save down. Checking this
on your own machine is useless — so we check the code itself.
"""
import ast
from pathlib import Path

import pytest

КОРЕНЬ = Path(__file__).resolve().parent.parent
ПАПКИ = ["jobsearch", "packaging"]
ФАЙЛЫ = ["app.py", "desktop.py"]

# These calls work with text, and without encoding= they take the system's
ТЕКСТОВЫЕ = {"read_text", "write_text", "open"}
# …but open() is not only a file thing: webbrowser.open opens a browser
НЕ_ФАЙЛЫ = {"webbrowser", "os", "subprocess", "urllib", "request"}
# А ZipFile.open отдаёт байты и параметра encoding не имеет вовсе: требовать его
# там — требовать невозможного. Разбирается по имени получателя, потому что типов
# в дереве разбора нет; отсюда и уговор называть архив архивом.
ДВОИЧНЫЕ = {"архив", "zip", "archive"}


def исходники():
    for имя in ФАЙЛЫ:
        yield КОРЕНЬ / имя
    for папка in ПАПКИ:
        yield from sorted((КОРЕНЬ / папка).rglob("*.py"))


def двоичный_режим(call: ast.Call) -> bool:
    """open(..., 'rb') and its like deal in bytes and need no encoding."""
    режимы = [a for a in call.args[1:2] if isinstance(a, ast.Constant)]
    режимы += [k.value for k in call.keywords
               if k.arg == "mode" and isinstance(k.value, ast.Constant)]
    return any(isinstance(m, ast.Constant) and "b" in str(m.value) for m in режимы)


def запуски_без_кодировки(дерево):
    """subprocess.run(..., text=True) без encoding= — та же беда, другой боком.

    Вывод чужой программы декодируется кодировкой системы: cp1252 на Windows,
    ascii при локали C. Нелатинская буква в чужой переменной среды рушила разбор,
    и рушила скверно: ошибка случалась в потоке чтения, stdout молча оказывался
    None, а падало потом и в другом месте — AttributeError вместо внятного
    UnicodeDecodeError. Разом переставали работать все командные строки.
    """
    for node in ast.walk(дерево):
        if not isinstance(node, ast.Call):
            continue
        если = getattr(node.func, "attr", "")
        получатель = getattr(getattr(node.func, "value", None), "id", "")
        if если != "run" or получатель != "subprocess":
            continue
        ключи = {k.arg for k in node.keywords}
        текстом = any(k.arg == "text" and getattr(k.value, "value", False) is True
                      for k in node.keywords) or "encoding" in ключи
        if текстом and "encoding" not in ключи:
            yield node.lineno, "subprocess.run(text=True)"


def нарушения(путь: Path):
    дерево = ast.parse(путь.read_text(encoding="utf-8"))
    yield from запуски_без_кодировки(дерево)
    for node in ast.walk(дерево):
        if not isinstance(node, ast.Call):
            continue
        имя = (node.func.attr if isinstance(node.func, ast.Attribute)
               else getattr(node.func, "id", ""))
        if имя not in ТЕКСТОВЫЕ:
            continue
        if isinstance(node.func, ast.Attribute):
            получатель = getattr(node.func.value, "id", "")
            if получатель in НЕ_ФАЙЛЫ or получатель in ДВОИЧНЫЕ:
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
    """The very case: an arrow "→" in a CV, and saving fell over on Windows.

    The text is longer than a hundred characters — anything shorter and the
    program treats the parse as failed and refuses to save."""
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
