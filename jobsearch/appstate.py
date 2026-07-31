"""Настройки уровня приложения — общие для всех людей внутри него.

CLI и модель — свойство компьютера, а не человека: их выбирают один раз при
первом запуске. Дальше это значение по умолчанию для каждого нового профиля,
чтобы добавление второго человека не возвращало настройки к «как получится».
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
    """Знакомство уже пройдено?

    Тем, кто обновился с прежней версии, показывать его незачем: у них уже
    есть настроенный профиль, и знакомство выглядело бы как потеря данных.
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
    """Оформление — свойство компьютера, а не человека: одно на всё приложение."""
    value = load().get("theme", "auto")
    return value if value in THEMES else "auto"


def set_theme(value: str) -> None:
    state = load()
    state["theme"] = value if value in THEMES else "auto"
    save(state)


def default_llm() -> dict:
    """Чем считать у нового профиля — тем же, что выбрано при первом запуске."""
    return {k: v for k, v in (load().get("llm") or {}).items() if v}
