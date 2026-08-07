"""Files are read and written as UTF-8 explicitly, not "however the system does it".

On macOS and Linux the default encoding is UTF-8 anyway, so a mistake here stays
invisible until somebody runs the program on Windows: there the default is
cp1252, and the very first arrow "→" in a CV brings the save down. Checking this
on your own machine is useless — so we check the code itself.
"""
import ast
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
PACKAGES = ["jobsearch", "packaging"]
FILES = ["app.py", "desktop.py"]

# These calls work with text, and without encoding= they take the system's
TEXT_CALLS = {"read_text", "write_text", "open"}
# …but open() is not only a file thing: webbrowser.open opens a browser
NOT_FILES = {"webbrowser", "os", "subprocess", "urllib", "request"}
# And ZipFile.open hands back bytes and has no encoding parameter at all:
# demanding one there is demanding the impossible. This is worked out from the
# receiver's name, because the parse tree carries no types; hence the convention
# of calling an archive an archive.
BINARY_RECEIVERS = {"zip", "archive"}


def sources():
    for name in FILES:
        yield ROOT / name
    for package in PACKAGES:
        yield from sorted((ROOT / package).rglob("*.py"))


def binary_mode(call: ast.Call) -> bool:
    """open(..., 'rb') and its like deal in bytes and need no encoding."""
    modes = [a for a in call.args[1:2] if isinstance(a, ast.Constant)]
    modes += [k.value for k in call.keywords
               if k.arg == "mode" and isinstance(k.value, ast.Constant)]
    return any(isinstance(m, ast.Constant) and "b" in str(m.value) for m in modes)


def runs_without_encoding(tree):
    """subprocess.run(..., text=True) without encoding= is the same trouble from
    another side.

    Another program's output is decoded with the system's encoding: cp1252 on
    Windows, ascii under the C locale. A non-Latin letter in somebody's
    environment variable wrecked the parsing, and wrecked it badly: the error
    happened in the reading thread, stdout silently came back None, and the fall
    came later and somewhere else — an AttributeError instead of a comprehensible
    UnicodeDecodeError. Every command-line tool stopped working at once.
    """
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        attr = getattr(node.func, "attr", "")
        receiver = getattr(getattr(node.func, "value", None), "id", "")
        if attr != "run" or receiver != "subprocess":
            continue
        kwargs = {k.arg for k in node.keywords}
        as_text = any(k.arg == "text" and getattr(k.value, "value", False) is True
                      for k in node.keywords) or "encoding" in kwargs
        if as_text and "encoding" not in kwargs:
            yield node.lineno, "subprocess.run(text=True)"


def offences(path: Path):
    tree = ast.parse(path.read_text(encoding="utf-8"))
    yield from runs_without_encoding(tree)
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        name = (node.func.attr if isinstance(node.func, ast.Attribute)
               else getattr(node.func, "id", ""))
        if name not in TEXT_CALLS:
            continue
        if isinstance(node.func, ast.Attribute):
            receiver = getattr(node.func.value, "id", "")
            if receiver in NOT_FILES or receiver in BINARY_RECEIVERS:
                continue
        if binary_mode(node):
            continue
        if any(k.arg == "encoding" for k in node.keywords):
            continue
        yield node.lineno, name


@pytest.mark.parametrize("path", list(sources()), ids=lambda p: p.name)
def test_the_encoding_is_given_explicitly(path):
    found = list(offences(path))
    detail = ", ".join(f"line {n}: {name}()" for n, name in found)
    assert not found, (
        f"{path.relative_to(ROOT)} — no encoding=\"utf-8\": {detail}. "
        "On Windows that takes cp1252 and falls over on the first non-Latin character.")


def test_a_cv_with_arrows_and_cyrillic_survives_being_written(profile):
    """The very case: an arrow "→" in a CV, and saving fell over on Windows.

    The text is longer than a hundred characters — anything shorter and the
    program treats the parse as failed and refuses to save."""
    from jobsearch import config
    text = ("Виктор Лавров — фронтенд-инженер.\n"
             "Опыт: React → TypeScript → Python. Дизайн-системы, доступность.\n"
             "Языки: русский, английский C1, немецкий B1. Стрелки → тире — эмодзи 🎯\n"
             "Работал над переносом библиотеки компонентов на тридцать команд.")
    assert len(text) > 100
    config.save_cv("резюме.txt", text.encode("utf-8"))
    assert "→" in config.cv_text(), "the arrow did not survive being saved"
    assert "🎯" in config.cv_text()
    assert config.cv_meta()["filename"] == "резюме.txt"
