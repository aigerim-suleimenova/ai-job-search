"""Learning that a newer version exists, and installing it.

Until now there was no way to find out. The program never said a word about new
versions, and getting one meant remembering to look at the site, downloading the
installer by hand and uninstalling the old copy first — which nobody does, so
people stayed on whatever they first installed for good.

Where the file comes from is not taken on trust, and neither is the file. The
answer names an address and a SHA-256; the address is checked to lead into the
releases of this repository, and what arrives is weighed and hashed before it is
allowed to run. A truncated download, a redirect that ends up somewhere else, an
asset swapped after the answer was written — all three come out as a mismatch.

Honest about what this does NOT cover: the address and the hash arrive over the
same connection, so anyone able to forge both — GitHub itself, or somebody
holding the maintainer's account — is trusted by this. Only a signature made
with a key that never touches GitHub would close that, and the Windows installer
is not signed at all today.
"""
import hashlib
import os
import platform
import posixpath
import re
import subprocess
import tempfile
import threading
import time
from pathlib import Path
from urllib.parse import unquote, urlparse

import requests

from . import appstate, net, removal, version

REPO = "mrWD/ai-job-search"
API = f"https://api.github.com/repos/{REPO}/releases/latest"
RELEASES = f"https://github.com/{REPO}/releases"
DOWNLOAD_PREFIX = f"https://github.com/{REPO}/releases/download/"
_DOWNLOAD_PATH = f"/{REPO}/releases/download/"
# No file we hand out is ever bigger, and there is nothing to fill a disk with.
MAX_DOWNLOAD = 400 * 1024 * 1024


def _is_our_download(url: str) -> bool:
    """Whether the address leads into the releases of this repository and no other.

    Comparing by the start of the string will not do, and that was a real bug
    rather than caution: requests collapses ".." only AFTER such a check, so
    .../ai-job-search/releases/download/../../../evilcorp/evil/... passed the
    prefix test while the request went off to somebody else's repository. The
    address is first brought to the form the network will see, and only then
    compared — and in parts rather than in letters: the host separately, the path
    separately.
    """
    parsed = urlparse(url)
    if parsed.scheme != "https" or (parsed.hostname or "").lower() != "github.com":
        return False
    path = posixpath.normpath(unquote(parsed.path))
    return path.startswith(_DOWNLOAD_PATH) and len(path) > len(_DOWNLOAD_PATH)

CHECK_EVERY = 24 * 3600      # no point more often: releases do not come out hourly


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
    with no uninstalling first. On macOS — the disk image: a ready .app bundle
    sits inside it, and swapping it in for the old one is something we can do
    ourselves (see install).

    Linux stays behind the release page: there it is an archive the person
    unpacked wherever they wanted, and guessing that place means guessing wrong
    one day and overwriting somebody else's files.
    """
    system = platform.system()
    suffix = {"Windows": "setup.exe", "Darwin": ".dmg"}.get(system)
    if not suffix:
        return {}
    for a in assets:
        name = str(a.get("name", ""))
        url = str(a.get("browser_download_url", ""))
        if name.lower().endswith(suffix) and _is_our_download(url):
            return {"name": name, "url": url, "size": int(a.get("size") or 0),
                    # what to check the download against; GitHub sends "sha256:<hex>"
                    "digest": str(a.get("digest") or "")}
    return {}


def fetch_latest() -> dict:
    """Asks GitHub about the newest release. {} if it cannot be reached."""
    try:
        r = net.get(API, timeout=(3, 10),
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
    """What the last check found — read by the pages, which never wait on the network.

    Checked against the installed version on every read, not only at the hour of
    asking. Otherwise this happens: the program found a new version and wrote it
    down, the person installed it — and the note outlived the install (the data
    lives separately) and went on offering an update to what was already there
    for another day. "Version 0.8.20 is out — you have 0.8.20", with an "Update"
    button beside it.
    """
    saved = appstate.load().get("update") or {}
    if is_newer(saved.get("version", ""), version.current()):
        return saved
    return {**saved, "version": "", "notes_url": "", "asset": {}}


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


def due() -> bool:
    """Whether it is time to ask again."""
    last = (appstate.load().get("update") or {}).get("checked_at")
    return not last or time.time() - last >= CHECK_EVERY


def check_in_background() -> None:
    """A check must never hold up the program starting, nor a page being drawn.

    We ask not only at startup. It used to be only then, and this is what came of
    it: a person opened the program in the morning, a new version came out during
    the day, and they never learned of it because the program did not ask again.
    And there is no reason to close it — it has a background mode and a schedule,
    it is meant to stand open for days.

    No separate schedule is needed for this: check() itself asks no more than
    once a day, and all we do here is not start a thread for nothing while the
    time has not come. Otherwise every page draw would get one of its own.
    """
    if not due():
        return
    threading.Thread(target=lambda: _quietly(check), daemon=True).start()


def _quietly(fn) -> None:
    try:
        fn()
    except Exception:  # noqa: BLE001 — not learning about an update is no disaster
        pass


class UpdateError(RuntimeError):
    """Carries a translation key: the text is shown to a person."""

    def __init__(self, key: str, **fmt):
        self.key, self.fmt = key, fmt
        super().__init__(key)


def download(asset: dict, progress=None) -> Path:
    """Fetches the installer, checks it, and returns the path to it.

    The address is checked again here rather than only where it was chosen: this
    function is what actually reaches out to the network, and it must not depend
    on somebody else having looked first.

    Redirects are followed — a GitHub download always ends up on a storage host —
    so where the bytes finally come from is not the thing being trusted. What is
    checked is the bytes themselves: the size and the hash the answer promised.
    A file that does not match never becomes a path anybody can run; it is
    deleted before this returns.
    """
    url = str((asset or {}).get("url") or "")
    if not _is_our_download(url):
        raise UpdateError("update_err_bad_url")
    # The file name came from outside too. Only the last part of the path is
    # taken, and only harmless characters are left in it: the download must land
    # in our temporary folder and nowhere else — whatever the response says.
    tail = re.split(r"[\\/]", str(asset.get("name") or ""))[-1]
    safe = re.sub(r"[^\w.-]", "_", tail).lstrip(".") or "setup.exe"
    target = Path(tempfile.mkdtemp(prefix="aijs-update-")) / safe
    expected_size = int(asset.get("size") or 0)
    expected_hash = str(asset.get("digest") or "")
    ceiling = expected_size or MAX_DOWNLOAD
    sha = hashlib.sha256()
    done = 0
    try:
        with net.get(url, stream=True, timeout=(5, 120)) as r:
            r.raise_for_status()
            total = expected_size or int(r.headers.get("Content-Length") or 0)
            with open(target, "wb") as fh:
                for chunk in r.iter_content(chunk_size=256 * 1024):
                    done += len(chunk)
                    if done > ceiling:
                        raise UpdateError("update_err_broken")
                    fh.write(chunk)
                    sha.update(chunk)
                    if progress and total:
                        progress(min(100, done * 100 // total))
    except requests.RequestException as e:
        target.unlink(missing_ok=True)
        raise UpdateError("update_err_download", error=e) from e
    except UpdateError:
        target.unlink(missing_ok=True)
        raise

    if expected_size and done != expected_size:
        target.unlink(missing_ok=True)      # a dropped connection left a stub, and it ran
        raise UpdateError("update_err_broken")
    if expected_hash.startswith("sha256:") and sha.hexdigest() != expected_hash[7:].lower():
        target.unlink(missing_ok=True)
        raise UpdateError("update_err_broken")
    return target


# Puts the new copy in place of the old one. As a separate script rather than
# from the program itself, because the program is closing at that moment:
# replacing a bundle while it is open is impossible, and waiting out your own
# shutdown from inside even more so.
_MAC_SWAP = r"""#!/bin/sh
set -u
dmg="$1"; target="$2"; pid="$3"

# Wait for the old copy to go. If it has not gone in twenty seconds, touch
# nothing: swapping a running program out from under itself is worse than not
# updating at all.
#
# kill -0 alone is not enough: a finished process still answers it with "alive"
# until its parent buries it. Such a process holds nothing open any more — as far
# as we are concerned it has gone, and there is no need to wait for it.
n=0
while kill -0 "$pid" 2>/dev/null; do
  case "$(ps -o stat= -p "$pid" 2>/dev/null)" in Z*) break ;; esac
  n=$((n + 1))
  [ "$n" -gt 200 ] && exit 1
  sleep 0.1
done

mnt="$(mktemp -d)"
hdiutil attach -nobrowse -readonly -quiet -mountpoint "$mnt" "$dmg" || exit 1

# We do not guess the bundle's name inside the image — we take the one lying there.
src=""
for p in "$mnt"/*.app; do [ -d "$p" ] && src="$p"; done
if [ -z "$src" ]; then hdiutil detach "$mnt" -quiet -force; exit 1; fi

# First we move the old copy aside, and only then put the new one down. Should
# the copy break off halfway, the old one comes back and the person is left with
# a working program rather than half a bundle that will not start.
old="$(dirname "$target")/.$(basename "$target").old"
rm -rf "$old"
if mv "$target" "$old"; then
  # ditto, not cp: it alone carries the bundle across as it is — permissions,
  # symlinks, signature.
  if ditto "$src" "$target"; then
    rm -rf "$old"
  else
    rm -rf "$target"
    mv "$old" "$target"
  fi
fi

hdiutil detach "$mnt" -quiet -force
rmdir "$mnt" 2>/dev/null
rm -rf "$(dirname "$dmg")"
open -n "$target"
"""


def install(path: Path) -> None:
    """Hands the downloaded update over and steps aside.

    Neither system can replace files that are open, so the program has to go
    first. What runs in its place depends on the system, but the order is always
    the same: detach, wait for us to go, replace, open the new copy.

    Windows: /RELAUNCH is our own flag and the installer looks for it. A silent
    install started by somebody's script should not throw a window on the
    screen, but an update the person asked for must give them their program
    back. Without it they would press "Update" and be left with nothing.

    macOS: there was no update at all — it said "install by hand", even though a
    ready bundle sits inside the image and swapping it in is not hard. We check
    for write permission here, before closing the program: otherwise it would
    simply vanish from the screen and the update would silently fail to happen.
    """
    system = platform.system()
    if system == "Windows":
        subprocess.Popen([str(path), "/SILENT", "/NOCANCEL", "/RELAUNCH"],
                         creationflags=getattr(subprocess, "DETACHED_PROCESS", 0)
                         | getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0),
                         close_fds=True)
        return
    if system == "Darwin":
        _install_macos(path)
        return
    raise UpdateError("update_err_manual")


def _install_macos(image: Path) -> None:
    bundle = removal.program_path()
    if not bundle.endswith(".app") or not os.path.isdir(bundle):
        raise UpdateError("update_err_manual")      # running from source
    # Both the bundle and the folder around it have to be writable: the swap goes
    # through mv.
    if not (os.access(bundle, os.W_OK) and os.access(os.path.dirname(bundle), os.W_OK)):
        raise UpdateError("update_err_readonly", where=bundle)
    script = image.parent / "swap.sh"
    script.write_text(_MAC_SWAP, encoding="utf-8")
    script.chmod(0o755)
    subprocess.Popen(["/bin/sh", str(script), str(image), bundle, str(os.getpid())],
                     start_new_session=True, close_fds=True,
                     stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


def quit_soon(seconds: float = 1.5) -> None:
    """Leaves the page time to reach the browser before the program closes."""
    def later():
        time.sleep(seconds)
        # os._exit, not sys.exit: the work has already been handed to the
        # installer, and waiting for background threads to burn out serves
        # nothing — the installer cannot wait for them anyway
        os._exit(0)

    threading.Thread(target=later, daemon=True).start()
