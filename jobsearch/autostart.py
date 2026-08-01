"""Starting the app at login — every OS does this its own way.

macOS   — a LaunchAgent (~/Library/LaunchAgents/*.plist)
Windows — a command entry under the Run registry key
Linux   — a .desktop file in ~/.config/autostart
"""
import os
import platform
import plistlib
import subprocess
import sys
from pathlib import Path

APP_NAME = "AI Job Search"
LABEL = "com.aijobsearch.app"


def app_command() -> list:
    """What to start the app with: a packaged one by itself, source with python desktop.py."""
    if getattr(sys, "frozen", False):
        exe = Path(sys.executable)
        # inside an .app the executable sits in Contents/MacOS — the system prefers the bundle
        if platform.system() == "Darwin" and ".app/Contents/MacOS" in str(exe):
            bundle = str(exe).split(".app/Contents/MacOS")[0] + ".app"
            return ["/usr/bin/open", "-a", bundle]
        return [str(exe)]
    root = Path(__file__).resolve().parent.parent
    return [sys.executable, str(root / "desktop.py")]


# --- macOS -----------------------------------------------------------------

def _mac_plist_path() -> Path:
    return Path.home() / "Library" / "LaunchAgents" / f"{LABEL}.plist"


def _mac_enable() -> None:
    path = _mac_plist_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "wb") as fh:
        plistlib.dump({
            "Label": LABEL,
            "ProgramArguments": app_command(),
            "RunAtLoad": True,
            "KeepAlive": False,          # do not restart it if the user closed it themselves
        }, fh)
    subprocess.run(["launchctl", "unload", str(path)], capture_output=True)
    subprocess.run(["launchctl", "load", str(path)], capture_output=True)


def _mac_disable() -> None:
    path = _mac_plist_path()
    if path.exists():
        subprocess.run(["launchctl", "unload", str(path)], capture_output=True)
        path.unlink()


def _mac_enabled() -> bool:
    return _mac_plist_path().exists()


# --- Windows ---------------------------------------------------------------

_WIN_KEY = r"Software\Microsoft\Windows\CurrentVersion\Run"


def _win_enable() -> None:
    import winreg
    cmd = " ".join(f'"{p}"' for p in app_command())
    with winreg.OpenKey(winreg.HKEY_CURRENT_USER, _WIN_KEY, 0, winreg.KEY_SET_VALUE) as key:
        winreg.SetValueEx(key, APP_NAME, 0, winreg.REG_SZ, cmd)


def _win_disable() -> None:
    import winreg
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, _WIN_KEY, 0, winreg.KEY_SET_VALUE) as key:
            winreg.DeleteValue(key, APP_NAME)
    except FileNotFoundError:
        pass


def _win_enabled() -> bool:
    import winreg
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, _WIN_KEY) as key:
            winreg.QueryValueEx(key, APP_NAME)
            return True
    except (FileNotFoundError, OSError):
        return False


# --- Linux -----------------------------------------------------------------

def _linux_desktop_path() -> Path:
    base = Path(os.environ.get("XDG_CONFIG_HOME") or Path.home() / ".config")
    return base / "autostart" / "ai-job-search.desktop"


def _linux_enable() -> None:
    path = _linux_desktop_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    cmd = " ".join(app_command())
    path.write_text(
        "[Desktop Entry]\n"
        "Type=Application\n"
        f"Name={APP_NAME}\n"
        f"Exec={cmd}\n"
        "X-GNOME-Autostart-enabled=true\n"
        "Terminal=false\n",
        encoding="utf-8",
    )


def _linux_disable() -> None:
    path = _linux_desktop_path()
    if path.exists():
        path.unlink()


def _linux_enabled() -> bool:
    return _linux_desktop_path().exists()


# --- The shared interface --------------------------------------------------

_IMPL = {
    "Darwin": (_mac_enable, _mac_disable, _mac_enabled),
    "Windows": (_win_enable, _win_disable, _win_enabled),
    "Linux": (_linux_enable, _linux_disable, _linux_enabled),
}


def supported() -> bool:
    return platform.system() in _IMPL


def enabled() -> bool:
    if not supported():
        return False
    try:
        return _IMPL[platform.system()][2]()
    except Exception:  # noqa: BLE001 — checking autostart must not bring the page down
        return False


def set_enabled(on: bool) -> str:
    """Turns starting at login on or off.

    Returns an empty string, or the translation key of an error — the language is
    filled in by whoever shows the message."""
    if not supported():
        return "msg_autostart_unsupported"
    enable, disable, _ = _IMPL[platform.system()]
    try:
        enable() if on else disable()
        return ""
    except Exception as e:  # noqa: BLE001
        return f"Не удалось изменить автозапуск: {e}"
