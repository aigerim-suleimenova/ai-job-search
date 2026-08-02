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
# Больше файла всё равно не бывает, а место на диске занимать нечем.
MAX_DOWNLOAD = 400 * 1024 * 1024


def _is_our_download(url: str) -> bool:
    """Ведёт ли адрес в выпуски именно этого репозитория.

    Сравнивать началом строки нельзя, и это была настоящая ошибка, а не
    осторожность: requests схлопывает «..» уже ПОСЛЕ такой проверки, поэтому
    .../ai-job-search/releases/download/../../../evilcorp/evil/... начало
    проходило, а запрос уходил в чужой репозиторий. Адрес сначала приводится к
    тому виду, в котором его увидит сеть, и лишь потом сверяется — и по частям,
    а не по буквам: имя узла отдельно, путь отдельно.
    """
    parsed = urlparse(url)
    if parsed.scheme != "https" or (parsed.hostname or "").lower() != "github.com":
        return False
    path = posixpath.normpath(unquote(parsed.path))
    return path.startswith(_DOWNLOAD_PATH) and len(path) > len(_DOWNLOAD_PATH)

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
    with no uninstalling first. On macOS — the disk image: внутри лежит готовый
    пакет .app, и подставить его вместо старого мы умеем сами (см. install).

    Linux остаётся за страницей выпуска: там архив, который человек распаковал
    туда, куда захотел, и угадывать это место — значит однажды не угадать и
    затереть чужое.
    """
    system = platform.system()
    хвост = {"Windows": "setup.exe", "Darwin": ".dmg"}.get(system)
    if not хвост:
        return {}
    for a in assets:
        name = str(a.get("name", ""))
        url = str(a.get("browser_download_url", ""))
        if name.lower().endswith(хвост) and _is_our_download(url):
            return {"name": name, "url": url, "size": int(a.get("size") or 0),
                    # чем сверить скачанное; GitHub присылает "sha256:<hex>"
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

    Сверяется с установленной версией при каждом чтении, а не только в час
    опроса. Иначе выходит так: программа нашла новую версию и записала это,
    человек её поставил — а запись пережила установку (данные лежат отдельно) и
    ещё сутки предлагала обновиться до того, что уже стоит. «Вышла версия
    0.8.20 — у вас 0.8.20», и кнопка «Обновить» рядом.
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
    """Пора ли спрашивать заново."""
    было = (appstate.load().get("update") or {}).get("checked_at")
    return not было or time.time() - было >= CHECK_EVERY


def check_in_background() -> None:
    """A check must never hold up the program starting, nor a page being drawn.

    Спрашиваем не только при запуске. Раньше — только при нём, и выходило вот
    что: человек открыл программу утром, днём вышла новая версия, а он о ней не
    узнал, потому что программа больше не спрашивала. А закрывать её незачем —
    у неё фоновый режим и расписание, она и должна стоять открытой сутками.

    Отдельного расписания для этого не нужно: сама check() спрашивает не чаще
    раза в сутки, а здесь мы лишь не заводим поток впустую, когда срок ещё не
    вышел. Иначе на каждую отрисовку страницы приходился бы свой.
    """
    if not due():
        return
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
    # Имя файла тоже пришло снаружи. Берётся только последняя часть пути, и в ней
    # остаются лишь безобидные знаки: скачанное должно лечь в нашу временную папку
    # и никуда больше — что бы в ответе ни было написано.
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
        target.unlink(missing_ok=True)      # обрыв связи оставлял огрызок, и он запускался
        raise UpdateError("update_err_broken")
    if expected_hash.startswith("sha256:") and sha.hexdigest() != expected_hash[7:].lower():
        target.unlink(missing_ok=True)
        raise UpdateError("update_err_broken")
    return target


# Подставляет новую копию вместо старой. Отдельным сценарием, а не из самой
# программы, потому что программа в это время закрывается: заменить пакет, пока
# он открыт, нельзя, а дождаться собственного закрытия изнутри — тем более.
_MAC_SWAP = r"""#!/bin/sh
set -u
dmg="$1"; target="$2"; pid="$3"

# Ждём, пока старая копия уйдёт. Не дождались за двадцать секунд — не трогаем
# ничего: подменить работающую программу под собой хуже, чем не обновиться.
n=0
while kill -0 "$pid" 2>/dev/null; do
  n=$((n + 1))
  [ "$n" -gt 200 ] && exit 1
  sleep 0.1
done

mnt="$(mktemp -d)"
hdiutil attach -nobrowse -readonly -quiet -mountpoint "$mnt" "$dmg" || exit 1

# Имя пакета внутри образа не угадываем — берём тот, что там лежит.
src=""
for p in "$mnt"/*.app; do [ -d "$p" ] && src="$p"; done
if [ -z "$src" ]; then hdiutil detach "$mnt" -quiet -force; exit 1; fi

# Сначала уводим старую копию в сторону и только потом кладём новую. Оборвись
# копирование на середине — старую вернём, и человек останется с работающей
# программой, а не с половиной пакета, которая не запускается.
old="$(dirname "$target")/.$(basename "$target").old"
rm -rf "$old"
if mv "$target" "$old"; then
  # ditto, а не cp: он один переносит пакет как есть — права, ссылки, подпись.
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
    first. Что запускается вместо неё, зависит от системы, но порядок один:
    открепиться, дождаться нашего ухода, заменить, открыть новую копию.

    Windows: /RELAUNCH is our own flag and the installer looks for it. A silent
    install started by somebody's script should not throw a window on the
    screen, but an update the person asked for must give them their program
    back. Without it they would press "Update" and be left with nothing.

    macOS: обновления не было вовсе — там показывали «ставится вручную», хотя
    внутри образа лежит готовый пакет и подставить его несложно. Право на запись
    проверяем здесь, до закрытия программы: иначе она просто исчезла бы с
    экрана, а обновление молча не случилось бы.
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
        raise UpdateError("update_err_manual")      # запуск из исходников
    # Менять надо и сам пакет, и папку вокруг него: подстановка идёт через mv.
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
        # os._exit, а не sys.exit: работа уже передана установщику, и ждать, пока
        # догорят фоновые потоки, незачем — установщик всё равно их дождаться не сможет
        os._exit(0)

    threading.Thread(target=later, daemon=True).start()
