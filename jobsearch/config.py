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
        "triage_second_vote": True, # второй голос триажа для пограничных (ловит занижения)
        "keywords_include": "",
        "keywords_exclude": "",
        "include_remote": True,
        "triage_limit": 400,         # верхний предел вакансий на LLM-триаж за прогон
        "deep_during_run": True,     # разбирать глубоко прямо в прогоне
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
        "provider": "claude_cli",     # claude_cli | cursor_cli | ollama
        "claude_bin": "claude",
        "triage_model": "haiku",
        "deep_model": "",            # пусто = модель по умолчанию из настроек claude
    },
    "telegram": {
        "bot_token": "",
        "chat_id": "",
    },
    "email": {
        "enabled": False,
        "preset": "gmail",       # ключ из mailer.PRESETS
        "host": "smtp.gmail.com",
        "port": 587,
        "tls": True,
        "username": "",          # адрес отправителя (он же логин)
        "password": "",          # для Gmail/Яндекса/Mail.ru — «пароль приложения»
        "from": "",              # пусто = username
        "to": "",                # пусто = username (себе же)
    },
    "schedule": {
        "mode": "off",               # off | interval | continuous
        "every_value": 1,
        "every_unit": "days",        # hours | days | weeks (для interval)
        "continuous_cooldown_min": 20,  # пауза между прогонами в непрерывном режиме
    },
    "ui": {
        "background": False,         # продолжать поиск после закрытия окна
        "lang": "",                  # пусто = взять язык системы при первом запуске
        "output_lang": "",           # пусто = как язык интерфейса
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
                stored = json.loads(path.read_text(encoding="utf-8"))
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
    # Язык не выбран (первый запуск) — берём системный, а не чужой по умолчанию.
    if not cfg["ui"].get("lang"):
        from . import i18n
        cfg["ui"]["lang"] = i18n.system_lang()
    if not cfg["ui"].get("output_lang"):
        cfg["ui"]["output_lang"] = cfg["ui"]["lang"]
    return cfg


def save(cfg: dict) -> None:
    with _lock:
        config_path().write_text(json.dumps(cfg, ensure_ascii=False, indent=2), encoding="utf-8")


def cv_text() -> str:
    path = cv_text_path()
    if path.exists():
        return path.read_text(encoding="utf-8", errors="ignore")
    return ""


def cv_meta() -> dict:
    path = cv_meta_path()
    if path.exists():
        try:
            return json.loads(path.read_text(encoding="utf-8"))
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


def _fix_letter_spacing(text: str) -> str:
    """Некоторые PDF (дизайнерские шаблоны, напр. Canva) отдают текст с пробелом
    после каждой буквы: «E l i s a b e t t a». Детектируем по доле односимвольных
    токенов и склеиваем: буквы внутри слова разделены одним пробелом,
    слова — двумя и более."""
    import re
    tokens = text.split()
    if not tokens or sum(1 for t in tokens if len(t) == 1) / len(tokens) < 0.6:
        return text  # обычный текст — не трогаем
    lines = []
    for line in text.splitlines():
        words = re.split(r"\s{2,}", line.strip())
        lines.append(" ".join("".join(w.split()) for w in words if w))
    return "\n".join(lines)


ALLOWED_CV_EXT = (".pdf", ".docx", ".txt", ".md", ".rtf")


class CVError(ValueError):
    """Файл не годится как резюме.

    Несёт ключ перевода, а не готовый текст: модуль не знает языка интерфейса,
    а сообщение показывается человеку — раньше оно всегда было русским.
    """

    def __init__(self, key: str, **fmt):
        self.key, self.fmt = key, fmt
        super().__init__(key)


def _extract_cv_text(path, ext: str, raw: bytes) -> str:
    if ext == ".pdf":
        from pypdf import PdfReader
        try:
            reader = PdfReader(str(path))
            text = "\n".join((page.extract_text() or "") for page in reader.pages)
        except Exception as e:  # noqa: BLE001 — библиотека бросает разное
            raise CVError("cv_err_pdf") from e
        return _fix_letter_spacing(text)
    if ext == ".docx":
        try:
            return _docx_text(path)
        except Exception as e:  # noqa: BLE001
            raise CVError("cv_err_docx") from e
    try:
        return raw.decode("utf-8")
    except UnicodeDecodeError as e:
        raise CVError("cv_err_not_text") from e


def save_cv(filename: str, raw: bytes) -> str:
    """Сохраняет CV, извлекает текст. Возвращает извлечённый текст.

    Прежнее CV перезаписывается только после того, как новый файл разобран
    успешно: иначе одна неудачная загрузка стирала бы уже работающее резюме.
    """
    import re
    from pathlib import Path

    ext = Path(filename).suffix.lower()
    if ext not in ALLOWED_CV_EXT:
        raise CVError("cv_err_format", ext=ext) if ext else CVError("cv_err_format_none")
    if not raw:
        raise CVError("cv_err_empty")

    d = _dir()
    tmp = d / f"cv_incoming{ext}"
    tmp.write_bytes(raw)
    try:
        text = re.sub(r"\n{3,}", "\n\n", _extract_cv_text(tmp, ext, raw).strip())
        if len(text) < 100:
            raise CVError("cv_err_no_text")
    except CVError:
        tmp.unlink(missing_ok=True)
        raise

    # разбор удался — заменяем прежнее резюме
    for old in d.glob("cv.*"):
        if old.suffix.lower() != ".txt" or old.name != "cv.txt":
            old.unlink(missing_ok=True)
    tmp.rename(d / f"cv{ext}")
    cv_text_path().write_text(text, encoding="utf-8")
    cv_meta_path().write_text(json.dumps({"filename": filename, "chars": len(text)}, ensure_ascii=False),
                              encoding="utf-8")
    return text
