"""Загрузка и сохранение настроек (data/config.json) и текста CV."""
import copy
import json
import threading
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent
DATA_DIR = BASE_DIR / "data"
CONFIG_PATH = DATA_DIR / "config.json"
CV_TEXT_PATH = DATA_DIR / "cv.txt"
CV_META_PATH = DATA_DIR / "cv_meta.json"

DEFAULTS = {
    "profile": {
        "summary": "",
        "roles": "",
        "seniority": "",
        "salary": "",
        "work_format": "any",       # remote | hybrid | onsite | any
        "languages": "",
        "visa_required": False,
        "visa_note": "",
        "email": "",
        "telegram": "",
        "linkedin": "",
    },
    "search": {
        "locations": "EU, USA",
        "threshold": 70,
        "keywords_include": "",
        "keywords_exclude": "",
        "include_remote": True,
        "triage_limit": 400,         # верхний предел вакансий на LLM-триаж за прогон
        "deep_top_n": 15,            # для скольких лучших делать разбор CV
        "discover_per_run": 5,       # сколько новых компаний искать веб-поиском за прогон
        "parallelism": 5,            # сколько LLM-вызовов выполнять параллельно
    },
    "sources": {
        "companies": [],             # [{"name": ..., "url": ...}]
        "use_remotive": True,
        "use_arbeitnow": True,
        "use_hnhiring": True,
        "adzuna_app_id": "",
        "adzuna_app_key": "",
        "adzuna_countries": "de,nl,gb,fr,es,it,pl,at,us",
        "jooble_key": "",
    },
    "llm": {
        "claude_bin": "claude",
        "triage_model": "haiku",
        "deep_model": "",            # пусто = модель по умолчанию из настроек claude
    },
    "telegram": {
        "bot_token": "",
        "chat_id": "",
    },
    "schedule": {
        "enabled": False,
        "every_value": 1,
        "every_unit": "days",        # hours | days | weeks
    },
}

_lock = threading.Lock()


def _merge(base: dict, override: dict) -> dict:
    out = copy.deepcopy(base)
    for k, v in (override or {}).items():
        if isinstance(v, dict) and isinstance(out.get(k), dict):
            out[k] = _merge(out[k], v)
        else:
            out[k] = v
    return out


def load() -> dict:
    with _lock:
        if CONFIG_PATH.exists():
            try:
                stored = json.loads(CONFIG_PATH.read_text())
            except (json.JSONDecodeError, OSError):
                stored = {}
        else:
            stored = {}
    return _merge(DEFAULTS, stored)


def save(cfg: dict) -> None:
    with _lock:
        DATA_DIR.mkdir(exist_ok=True)
        CONFIG_PATH.write_text(json.dumps(cfg, ensure_ascii=False, indent=2))


def cv_text() -> str:
    if CV_TEXT_PATH.exists():
        return CV_TEXT_PATH.read_text(errors="ignore")
    return ""


def cv_meta() -> dict:
    if CV_META_PATH.exists():
        try:
            return json.loads(CV_META_PATH.read_text())
        except (json.JSONDecodeError, OSError):
            return {}
    return {}


def save_cv(filename: str, raw: bytes) -> str:
    """Сохраняет CV, извлекает текст. Возвращает извлечённый текст."""
    DATA_DIR.mkdir(exist_ok=True)
    ext = Path(filename).suffix.lower()
    stored = DATA_DIR / f"cv{ext}"
    stored.write_bytes(raw)

    if ext == ".pdf":
        from pypdf import PdfReader
        reader = PdfReader(str(stored))
        text = "\n".join((page.extract_text() or "") for page in reader.pages)
    else:
        text = raw.decode("utf-8", errors="ignore")

    text = text.strip()
    CV_TEXT_PATH.write_text(text)
    CV_META_PATH.write_text(json.dumps({"filename": filename, "chars": len(text)}, ensure_ascii=False))
    return text
