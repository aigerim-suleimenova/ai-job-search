"""Application-level settings — shared by everyone inside it.

The CLI and the model belong to the computer rather than to a person: they are
chosen once, on first launch. After that they are the default for every new
profile, so adding a second person does not send the settings back to "whatever
happens".
"""
import json
import threading

from . import profiles

_lock = threading.Lock()


def path():
    return profiles.DATA_ROOT / "app.json"


def load() -> dict:
    p = path()
    with _lock:
        if p.exists():
            try:
                return json.loads(p.read_text(encoding="utf-8"))
            except (json.JSONDecodeError, OSError):
                pass
    return {}


def save(state: dict) -> None:
    with _lock:
        profiles.DATA_ROOT.mkdir(parents=True, exist_ok=True)
        path().write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")


def setup_done() -> bool:
    """Has the introduction already been through?

    There is no reason to show it to people who upgraded from an earlier
    version: they already have a configured profile, and the introduction would
    look like their data had been lost.
    """
    if load().get("setup_done"):
        return True
    return any((profiles.PROFILES_DIR / p["slug"] / "config.json").exists()
               for p in profiles.list_profiles())


def mark_setup_done(llm: dict) -> None:
    state = load()
    state["setup_done"] = True
    state["llm"] = {k: llm.get(k, "") for k in ("provider", "claude_bin", "triage_model", "deep_model")}
    save(state)


THEMES = ("auto", "light", "dark")


def theme() -> str:
    """The theme belongs to the computer rather than a person: one for the whole app."""
    value = load().get("theme", "auto")
    return value if value in THEMES else "auto"


def set_theme(value: str) -> None:
    state = load()
    state["theme"] = value if value in THEMES else "auto"
    save(state)


def default_llm() -> dict:
    """What a new profile thinks with: the same thing that was chosen on first launch."""
    return {k: v for k, v in (load().get("llm") or {}).items() if v}
