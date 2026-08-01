"""Как избавиться от этой программы, если избавляться нечем.

На Windows есть установщик: он закрывает программу, стирает её файлы, убирает
автозапуск и спрашивает про данные. На macOS и Linux установщика нет вовсе —
образ перетаскивают в корзину, архив стирают. Вместе с папкой уходит только
сама программа, а за ней остаются:

  — данные: CV, найденные вакансии, настройки, база;
  — запись автозапуска: LaunchAgent на macOS, .desktop на Linux.

Остаются навсегда и молча. Найти их человек не может: он не знает ни где они,
ни что они есть. А убрать их способна только сама программа — то есть до того,
как её удалят, о чём никто не догадывается.

Отсюда этот модуль: он умеет сказать, что именно останется и где, а всё, до
чего программа дотягивается, убрать сама. Последний шаг — удалить саму
программу — остаётся человеку: съесть себя целиком она не может.
"""
import platform
import sys
from pathlib import Path

from . import autostart, profiles


def has_uninstaller() -> bool:
    """Есть ли у системы своё удаление, которое всё сделает за нас."""
    return platform.system() == "Windows"


def program_path() -> str:
    """Что человеку предстоит убрать своими руками.

    На macOS это весь пакет .app, а не файл внутри него: перетаскивают в корзину
    именно пакет. Пустая строка означает запуск из исходников — удалять нечего,
    там лежит то, что разработчик открыл у себя.
    """
    if not getattr(sys, "frozen", False):
        return ""
    # Строка как есть, а не через Path: Path переворачивает разделители под ту
    # систему, где выполняется код, и проверка на «.app/Contents/MacOS» тогда
    # не срабатывает нигде, кроме самой macOS.
    raw = sys.executable
    if platform.system() == "Darwin" and ".app/Contents/MacOS" in raw:
        return raw.split(".app/Contents/MacOS")[0] + ".app"
    return str(Path(raw).parent)


def autostart_path() -> str:
    """Где лежит запись автозапуска, если она заведена."""
    system = platform.system()
    if system == "Darwin":
        path = autostart._mac_plist_path()
    elif system == "Windows":
        return autostart._WIN_KEY + "\\" + autostart.APP_NAME if autostart.enabled() else ""
    else:
        path = autostart._linux_desktop_path()
    return str(path) if path.exists() else ""


def leftovers() -> list:
    """Всё, что переживёт удаление программы, — по путям, а не общими словами."""
    out = [str(profiles.DATA_ROOT)]
    auto = autostart_path()
    if auto:
        out.append(auto)
    return out
