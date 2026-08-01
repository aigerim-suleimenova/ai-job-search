"""Profiles: searching for several people inside one app.

Every profile is its own directory, data/profiles/<slug>/, with its own
config.json, CV and jobs.db. The active profile is kept in a contextvar (set from
the cookie on every web request, and explicitly on every pipeline or schedule run).

The base data directory is chosen like this: the AIJS_DATA_DIR variable,
otherwise data/ next to the sources (development mode), otherwise the standard
application directory of this OS (a packaged app cannot write next to itself).
"""
import contextvars
import json
import os
import platform
import re
import shutil
import sys
import threading
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent


def _os_data_dir() -> Path:
    """Where the operating system lets an app write its data."""
    system = platform.system()
    if system == "Darwin":
        return Path.home() / "Library" / "Application Support" / "AI Job Search"
    if system == "Windows":
        base = os.environ.get("APPDATA") or (Path.home() / "AppData" / "Roaming")
        return Path(base) / "AI Job Search"
    return Path(os.environ.get("XDG_DATA_HOME") or Path.home() / ".local" / "share") / "ai-job-search"


def _default_data_root() -> Path:
    if getattr(sys, "frozen", False):        # a packaged app
        return _os_data_dir()
    return BASE_DIR / "data"                 # running from source


DATA_ROOT = Path(os.environ.get("AIJS_DATA_DIR") or _default_data_root())
PROFILES_DIR = DATA_ROOT / "profiles"
REGISTRY_PATH = DATA_ROOT / "profiles.json"

_active = contextvars.ContextVar("active_profile", default=None)
_lock = threading.Lock()


def _slugify(name: str, taken: set) -> str:
    translit = {
        "а": "a", "б": "b", "в": "v", "г": "g", "д": "d", "е": "e", "ё": "e",
        "ж": "zh", "з": "z", "и": "i", "й": "y", "к": "k", "л": "l", "м": "m",
        "н": "n", "о": "o", "п": "p", "р": "r", "с": "s", "т": "t", "у": "u",
        "ф": "f", "х": "h", "ц": "c", "ч": "ch", "ш": "sh", "щ": "sch",
        "ъ": "", "ы": "y", "ь": "", "э": "e", "ю": "yu", "я": "ya",
    }
    low = "".join(translit.get(ch, ch) for ch in name.lower())
    slug = re.sub(r"[^a-z0-9]+", "-", low).strip("-")[:32] or "person"
    base, i = slug, 2
    while slug in taken:
        slug = f"{base}-{i}"
        i += 1
    return slug


def _read_registry() -> dict:
    if REGISTRY_PATH.exists():
        try:
            return json.loads(REGISTRY_PATH.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            pass
    return {"profiles": [], "default": ""}


def _write_registry(reg: dict) -> None:
    DATA_ROOT.mkdir(parents=True, exist_ok=True)
    REGISTRY_PATH.write_text(json.dumps(reg, ensure_ascii=False, indent=2), encoding="utf-8")


def _default_name() -> str:
    """What to call a profile the program creates itself.

    The name is visible in the picker on every page, so it has to be in the
    person's language. There are no settings yet at this moment — we take the
    system language, the same one first launch picks for the interface.
    """
    from . import i18n
    return i18n.t(i18n.system_lang(), "profile_default_name")


def ensure_migrated() -> None:
    """A one-off migration: the old flat data/ → data/profiles/<slug>/."""
    with _lock:
        reg = _read_registry()
        if reg.get("profiles"):
            return  # already migrated
        PROFILES_DIR.mkdir(parents=True, exist_ok=True)
        profiles, taken = [], set()

        # existing flat data → a profile of one's own
        legacy_files = ["config.json", "cv.txt", "cv_meta.json"] + \
                       [p.name for p in DATA_ROOT.glob("cv.*")] + \
                       (["jobs.db"] if (DATA_ROOT / "jobs.db").exists() else [])
        if (DATA_ROOT / "config.json").exists():
            slug = _slugify("me", taken)
            taken.add(slug)
            dst = PROFILES_DIR / slug
            dst.mkdir(parents=True, exist_ok=True)
            for fn in set(legacy_files):
                src = DATA_ROOT / fn
                if src.exists() and src.is_file():
                    shutil.move(str(src), str(dst / fn))
            profiles.append({"slug": slug, "name": _default_name()})

        # if there was nothing — create an empty profile
        if not profiles:
            slug = _slugify("me", taken)
            (PROFILES_DIR / slug).mkdir(parents=True, exist_ok=True)
            profiles.append({"slug": slug, "name": _default_name()})

        _write_registry({"profiles": profiles, "default": profiles[0]["slug"]})


def list_profiles() -> list:
    return _read_registry().get("profiles", [])


def exists(slug: str) -> bool:
    return any(p["slug"] == slug for p in list_profiles())


def default_slug() -> str:
    reg = _read_registry()
    d = reg.get("default")
    if d and exists(d):
        return d
    profs = reg.get("profiles", [])
    return profs[0]["slug"] if profs else "me"


def name_of(slug: str) -> str:
    for p in list_profiles():
        if p["slug"] == slug:
            return p["name"]
    return slug


def active() -> str:
    return _active.get() or default_slug()


def set_active(slug: str) -> None:
    _active.set(slug if exists(slug) else default_slug())


def dir(slug: str = None) -> Path:
    d = PROFILES_DIR / (slug or active())
    d.mkdir(parents=True, exist_ok=True)
    return d


def create(name: str) -> str:
    with _lock:
        reg = _read_registry()
        taken = {p["slug"] for p in reg["profiles"]}
        slug = _slugify(name or "person", taken)
        (PROFILES_DIR / slug).mkdir(parents=True, exist_ok=True)
        reg["profiles"].append({"slug": slug, "name": (name or "").strip() or slug})
        if not reg.get("default"):
            reg["default"] = slug
        _write_registry(reg)
        return slug


def rename(slug: str, name: str) -> None:
    with _lock:
        reg = _read_registry()
        for p in reg["profiles"]:
            if p["slug"] == slug:
                p["name"] = name.strip() or p["name"]
        _write_registry(reg)


def delete(slug: str) -> None:
    with _lock:
        reg = _read_registry()
        reg["profiles"] = [p for p in reg["profiles"] if p["slug"] != slug]
        if reg.get("default") == slug:
            reg["default"] = reg["profiles"][0]["slug"] if reg["profiles"] else ""
        _write_registry(reg)
    shutil.rmtree(PROFILES_DIR / slug, ignore_errors=True)


def set_default(slug: str) -> None:
    with _lock:
        reg = _read_registry()
        if exists(slug):
            reg["default"] = slug
            _write_registry(reg)


# Migrating on first import guarantees the profile directories are ready BEFORE
# anything reads config or db, whichever the entry point was (server or script).
try:
    ensure_migrated()
except Exception:  # noqa: BLE001 — never break the import
    pass
