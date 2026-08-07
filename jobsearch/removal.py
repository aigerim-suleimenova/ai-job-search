"""How to get rid of this program when there is nothing to get rid of it with.

On Windows there is an installer: it closes the program, wipes its files,
removes the autostart entry and asks about the data. On macOS and Linux there is
no installer at all — the disk image gets dragged to the bin, the archive gets
deleted. Only the program itself goes with the folder, and behind it remain:

  — the data: the CV, the jobs found, the settings, the database;
  — the autostart entry: a LaunchAgent on macOS, a .desktop file on Linux.

They remain forever, and silently. A person cannot find them: they know neither
where they are nor that they exist. And only the program itself can clear them
away — that is, before it is deleted, which nobody thinks to do.

Hence this module: it can say what exactly will remain and where, and clear away
everything the program can reach. The last step — deleting the program itself —
is left to the person: it cannot eat itself whole.
"""
import platform
import sys
from pathlib import Path

from . import autostart, profiles


def has_uninstaller() -> bool:
    """Whether the system has its own uninstall that does it all for us."""
    return platform.system() == "Windows"


def program_path() -> str:
    """What the person will have to remove with their own hands.

    On macOS that is the whole .app bundle, not a file inside it: the bundle is
    what gets dragged to the bin. An empty string means running from source —
    nothing to delete, what lies there is what the developer checked out.
    """
    if not getattr(sys, "frozen", False):
        return ""
    # The raw string rather than Path: Path flips the separators to suit the
    # system the code runs on, and then the ".app/Contents/MacOS" check fires
    # nowhere except on macOS itself.
    raw = sys.executable
    if platform.system() == "Darwin" and ".app/Contents/MacOS" in raw:
        return raw.split(".app/Contents/MacOS")[0] + ".app"
    return str(Path(raw).parent)


def autostart_path() -> str:
    """Where the autostart entry lives, if one was made."""
    system = platform.system()
    if system == "Darwin":
        path = autostart._mac_plist_path()
    elif system == "Windows":
        return autostart._WIN_KEY + "\\" + autostart.APP_NAME if autostart.enabled() else ""
    else:
        path = autostart._linux_desktop_path()
    return str(path) if path.exists() else ""


def leftovers() -> list:
    """Everything that outlives the program — as paths, not as vague words."""
    out = [str(profiles.DATA_ROOT)]
    auto = autostart_path()
    if auto:
        out.append(auto)
    return out
