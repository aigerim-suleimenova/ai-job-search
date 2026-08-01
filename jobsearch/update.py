"""Learning that a newer version exists, and installing it.

Until now there was no way to find out. The program never said a word about new
versions, and getting one meant remembering to look at the site, downloading the
installer by hand and uninstalling the old copy first — which nobody does, so
people stayed on whatever they first installed for good.

Where the files come from is not taken on trust. GitHub answers with a list of
assets and their addresses, but an address in an answer is only a suggestion:
before anything is downloaded it is checked to lead into the releases of this
very repository over https. An answer that has been tampered with on the way can
then name a file, but not a place to fetch it from.
"""
import os
import platform
import re
import subprocess
import tempfile
import threading
import time
from pathlib import Path

import requests

from . import appstate, version

REPO = "mrWD/ai-job-search"
API = f"https://api.github.com/repos/{REPO}/releases/latest"
RELEASES = f"https://github.com/{REPO}/releases"
# Everything downloaded must start with this and nothing else.
DOWNLOAD_PREFIX = f"https://github.com/{REPO}/releases/download/"

CHECK_EVERY = 24 * 3600      # чаще незачем: релизы выходят не по часам


def _numbers(text: str) -> tuple:
    """"v0.8.16" → (0, 8, 16). Anything unparseable sorts lowest."""
    return tuple(int(n) for n in re.findall(r"\d+", text or "")[:4]) or (0,)


def is_newer(candidate: str, installed: str) -> bool:
    """Is the candidate worth offering?

    A build run from source calls itself "dev" and has no number. Offering it an
    update would be wrong: what is running is whatever the developer has checked
    out, and it is not behind anything.
    """
    if not candidate or not installed or installed == version.FALLBACK:
        return False
    return _numbers(candidate) > _numbers(installed)


def _asset_for_this_os(assets: list) -> dict:
    """The file this computer can actually install.

    On Windows that is the installer: it puts the new version over the old one,
    with no uninstalling first. Elsewhere there is nothing to run unattended — a
    disk image has to be dragged across, an archive unpacked — so those are left
    to the release page and a pair of human hands.
    """
    if platform.system() != "Windows":
        return {}
    for a in assets:
        name = str(a.get("name", ""))
        url = str(a.get("browser_download_url", ""))
        if name.lower().endswith("setup.exe") and url.startswith(DOWNLOAD_PREFIX):
            return {"name": name, "url": url, "size": int(a.get("size") or 0)}
    return {}


def fetch_latest() -> dict:
    """Asks GitHub about the newest release. {} if it cannot be reached."""
    try:
        r = requests.get(API, timeout=(3, 10),
                         headers={"Accept": "application/vnd.github+json"})
        r.raise_for_status()
        data = r.json()
    except (requests.RequestException, ValueError):
        return {}
    if not isinstance(data, dict) or data.get("draft") or data.get("prerelease"):
        return {}
    tag = str(data.get("tag_name") or "")
    if not tag:
        return {}
    return {"version": tag.lstrip("vV"),
            "notes_url": RELEASES + "/tag/" + tag,
            "asset": _asset_for_this_os(data.get("assets") or [])}


def state() -> dict:
    """What the last check found — read by the pages, which never wait on the network."""
    return appstate.load().get("update") or {}


def check(force: bool = False) -> dict:
    """Checks and remembers the answer. Returns the same shape as state()."""
    known = state()
    if not force and known.get("checked_at") and \
            time.time() - known["checked_at"] < CHECK_EVERY:
        return known
    latest = fetch_latest()
    found = {"checked_at": time.time(), "version": "", "notes_url": "", "asset": {}}
    if latest and is_newer(latest["version"], version.current()):
        found.update(version=latest["version"], notes_url=latest["notes_url"],
                     asset=latest.get("asset") or {})
    saved = appstate.load()
    saved["update"] = found
    appstate.save(saved)
    return found


def check_in_background() -> None:
    """A check must never hold up the program starting, nor a page being drawn."""
    threading.Thread(target=lambda: _quietly(check), daemon=True).start()


def _quietly(fn) -> None:
    try:
        fn()
    except Exception:  # noqa: BLE001 — не узнать про обновление не беда
        pass


class UpdateError(RuntimeError):
    """Carries a translation key: the text is shown to a person."""

    def __init__(self, key: str, **fmt):
        self.key, self.fmt = key, fmt
        super().__init__(key)


def download(asset: dict, progress=None) -> Path:
    """Fetches the installer into a temporary directory and returns the path.

    The address is checked again here rather than only where it was chosen: this
    function is what actually reaches out to the network, and it must not depend
    on somebody else having looked first.
    """
    url = str((asset or {}).get("url") or "")
    if not url.startswith(DOWNLOAD_PREFIX):
        raise UpdateError("update_err_bad_url")
    # Имя файла тоже пришло снаружи. Берётся только последняя часть пути, и в ней
    # остаются лишь безобидные знаки: скачанное должно лечь в нашу временную папку
    # и никуда больше — что бы в ответе ни было написано.
    tail = re.split(r"[\\/]", str(asset.get("name") or ""))[-1]
    safe = re.sub(r"[^\w.-]", "_", tail).lstrip(".") or "setup.exe"
    target = Path(tempfile.mkdtemp(prefix="aijs-update-")) / safe
    try:
        with requests.get(url, stream=True, timeout=(5, 120)) as r:
            r.raise_for_status()
            total = int(r.headers.get("Content-Length") or 0)
            done = 0
            with open(target, "wb") as fh:
                for chunk in r.iter_content(chunk_size=256 * 1024):
                    fh.write(chunk)
                    done += len(chunk)
                    if progress and total:
                        progress(done * 100 // total)
    except requests.RequestException as e:
        raise UpdateError("update_err_download", error=e) from e
    return target


def install(path: Path) -> None:
    """Hands the downloaded installer over and steps aside.

    The installer cannot replace files that are open, so the program has to go.
    It is started detached and then we quit: the installer waits for the old copy
    to close on its own and opens the new one when it is done.
    """
    if platform.system() != "Windows":
        raise UpdateError("update_err_manual")
    subprocess.Popen([str(path), "/SILENT", "/NOCANCEL"],
                     creationflags=getattr(subprocess, "DETACHED_PROCESS", 0)
                     | getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0),
                     close_fds=True)


def quit_soon(seconds: float = 1.5) -> None:
    """Leaves the page time to reach the browser before the program closes."""
    def later():
        time.sleep(seconds)
        # os._exit, а не sys.exit: работа уже передана установщику, и ждать, пока
        # догорят фоновые потоки, незачем — установщик всё равно их дождаться не сможет
        os._exit(0)

    threading.Thread(target=later, daemon=True).start()
