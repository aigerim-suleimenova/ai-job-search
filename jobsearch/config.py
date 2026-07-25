"""Загрузка и сохранение настроек и CV для активного профиля.

Данные каждого человека — в своём каталоге data/profiles/<slug>/ (см. profiles.py).
Пути вычисляются динамически по активному профилю, поэтому один сервер обслуживает
несколько людей.
"""
import copy
import json
import threading

from . import profiles


def _dir():
    return profiles.dir()


def config_path():
    return _dir() / "config.json"


def cv_text_path():
    return _dir() / "cv.txt"


def cv_meta_path():
    return _dir() / "cv_meta.json"

DEFAULTS = {
    "profile": {
        "summary": "",
        "roles": "",
        "skills": "",               # ключевые навыки/технологии (напр. SAP PI/PO, Java, REST)
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
        "match_priority": "both",   # на что опираться: role | skills | both
        "drop_off_target": True,    # отсеивать явно нерелевантные роли (продажи/HR/саппорт) до LLM
        "keywords_include": "",
        "keywords_exclude": "",
        "include_remote": True,
        "triage_limit": 400,         # верхний предел вакансий на LLM-триаж за прогон
        "deep_top_n": 15,            # для скольких лучших делать разбор CV
        "discover_per_run": 5,       # сколько новых компаний искать веб-поиском за прогон
        "discover_ats_per_run": 5,   # сколько вакансий искать веб-поиском прямо на доменах ATS
        "parallelism": 5,            # сколько LLM-вызовов выполнять параллельно
        "research_company": True,    # искать зарплату и факты о компании (Glassdoor/Kununu/...)
    },
    "sources": {
        "companies": [],             # [{"name": ..., "url": ...}]
        "use_remotive": True,
        "use_arbeitnow": True,
        "use_wwr": True,
        "use_hnhiring": True,
        "use_remoteok": True,
        "use_jobicy": True,
        "use_himalayas": True,
        "use_themuse": True,
        "use_arbeitsagentur": True,
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
        "mode": "off",               # off | interval | continuous
        "every_value": 1,
        "every_unit": "days",        # hours | days | weeks (для interval)
        "continuous_cooldown_min": 20,  # пауза между прогонами в непрерывном режиме
    },
    "ui": {
        "lang": "ru",                # язык интерфейса: ru | en
        "output_lang": "ru",         # язык результатов ИИ: ru | en | de
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
    path = config_path()
    with _lock:
        if path.exists():
            try:
                stored = json.loads(path.read_text())
            except (json.JSONDecodeError, OSError):
                stored = {}
        else:
            stored = {}
    cfg = _merge(DEFAULTS, stored)
    # совместимость: старое schedule.enabled → schedule.mode
    sched = stored.get("schedule", {})
    if "mode" not in sched and "enabled" in sched:
        cfg["schedule"]["mode"] = "interval" if sched.get("enabled") else "off"
    cfg["schedule"].pop("enabled", None)
    return cfg


def save(cfg: dict) -> None:
    with _lock:
        config_path().write_text(json.dumps(cfg, ensure_ascii=False, indent=2))


def cv_text() -> str:
    path = cv_text_path()
    if path.exists():
        return path.read_text(errors="ignore")
    return ""


def cv_meta() -> dict:
    path = cv_meta_path()
    if path.exists():
        try:
            return json.loads(path.read_text())
        except (json.JSONDecodeError, OSError):
            return {}
    return {}


def _docx_text(path) -> str:
    """Извлекает текст из .docx без внешних зависимостей (docx = zip с XML)."""
    import re
    import zipfile
    with zipfile.ZipFile(path) as z:
        xml = z.read("word/document.xml").decode("utf-8", errors="ignore")
    xml = re.sub(r"</w:p>", "\n", xml)          # конец абзаца → перенос строки
    xml = re.sub(r"<w:tab/>", "\t", xml)
    text = re.sub(r"<[^>]+>", "", xml)          # убрать теги
    import html as _html
    return _html.unescape(text)


def save_cv(filename: str, raw: bytes) -> str:
    """Сохраняет CV, извлекает текст. Возвращает извлечённый текст."""
    import re
    from pathlib import Path
    d = _dir()
    ext = Path(filename).suffix.lower()
    stored = d / f"cv{ext}"
    stored.write_bytes(raw)

    if ext == ".pdf":
        from pypdf import PdfReader
        reader = PdfReader(str(stored))
        text = "\n".join((page.extract_text() or "") for page in reader.pages)
    elif ext == ".docx":
        text = _docx_text(stored)
    else:
        text = raw.decode("utf-8", errors="ignore")

    text = re.sub(r"\n{3,}", "\n\n", text.strip())
    cv_text_path().write_text(text)
    cv_meta_path().write_text(json.dumps({"filename": filename, "chars": len(text)}, ensure_ascii=False))
    return text
