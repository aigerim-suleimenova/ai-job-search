"""The Linux build must not carry the system's own desktop libraries inside.

The build's copies shadow the machine's, and the window — which the system's
WebKit draws — stops opening: the program falls back to the browser, and the only
trace is one line about an undefined symbol in a library nobody has heard of.
This cannot be caught on our own machine, and in CI there is no display to open a
window on. So we check the rule itself, and the build against it.
"""
import re
import sys
from pathlib import Path

КОРЕНЬ = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(КОРЕНЬ / "packaging"))

import system_libraries  # noqa: E402


def test_библиотека_из_той_самой_поломки_признаётся_системной():
    """GLib and what it stands on: this is the pair that came apart — the system's
    libsecret asked the build's older GLib for a symbol it did not have."""
    for имя in ("libglib-2.0.so.0", "libgobject-2.0.so.0", "libgio-2.0.so.0",
                "libpcre2-8.so.0", "libsecret-1.so.0"):
        assert system_libraries.is_system_library(имя), f"{имя} останется в сборке"


def test_гтк_и_то_чем_он_рисует_тоже_системные():
    for имя in ("libgtk-3.so.0", "libgdk_pixbuf-2.0.so.0", "libcairo.so.2",
                "libpango-1.0.so.0", "libfontconfig.so.1", "libX11.so.6"):
        assert system_libraries.is_system_library(имя), f"{имя} останется в сборке"


def test_свои_библиотеки_остаются_в_сборке():
    """Not everything with lib in the name belongs to the desktop. These are ours:
    the system's GTK never reaches for them, so a copy inside shadows nothing —
    and saves the program on a machine that happens to lack one."""
    for имя in ("libpython3.12.so.1.0", "libssl.so.3", "libcrypto.so.3",
                "libsqlite3.so.0", "libz.so.1", "libffi.so.8",
                "libgirepository-1.0.so.1"):
        assert not system_libraries.is_system_library(имя), f"{имя} выбросят из сборки"


def test_путь_с_папкой_разбирается():
    """PyInstaller lists some libraries inside folders of their own."""
    assert system_libraries.is_system_library("gi/libglib-2.0.so.0")


def test_не_библиотеки_не_трогаем():
    """The check is about shared libraries. A data file whose name begins the same
    way must survive: throwing one out would break the build in a new way."""
    assert not system_libraries.is_system_library("girepository-1.0/Gtk-3.0.typelib")
    assert not system_libraries.is_system_library("libglib-2.0.txt")


def test_сборка_действительно_применяет_отбор():
    """The rule is worth nothing if the spec forgets to use it — and forgetting is
    silent: the build succeeds, and the window stops opening in somebody's hands."""
    spec = (КОРЕНЬ / "packaging" / "aijobsearch.spec").read_text(encoding="utf-8")
    assert "system_libraries.is_system_library" in spec, "спек не отбрасывает системные библиотеки"
    assert re.search(r"a\.binaries\s*=", spec), "спек не переписывает список библиотек"
