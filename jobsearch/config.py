"""Loading and saving the settings and the CV of the active profile.

Each person's data lives in its own directory, data/profiles/<slug>/ (see
profiles.py). The paths are worked out on the fly from the active profile, which
is how one server serves several people.
"""
import copy
import json
import os
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
        "skills": "",               # key skills and technologies (e.g. SAP PI/PO, Java, REST)
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
        "match_priority": "both",   # what to lean on: role | skills | both
        "drop_off_target": True,    # drop plainly irrelevant roles (sales/HR/support) before the model
        "triage_second_vote": True, # a second triage vote for borderline ones (catches underestimates)
        "keywords_include": "",
        "keywords_exclude": "",
        # За какой срок брать вакансии, ГГГГ-ММ-ДД. Пусто с обеих сторон — без
        # ограничения, как было. Отсекается до модели: платить за разбор
        # позапрошлогодней вакансии незачем, да и откликаться на неё поздно.
        "posted_from": "",
        "posted_to": "",
        "include_remote": True,
        "triage_limit": 400,         # the most jobs the model will triage in one run
        "deep_during_run": True,     # do the deep analysis during the run itself
        "deep_top_n": 15,            # how many of the best get their CV worked out
        "discover_per_run": 5,       # how many new companies to find by web search per run
        "discover_ats_per_run": 5,   # how many jobs to find by web search on the ATS domains
        "parallelism": 5,            # how many model calls to run at once
        "research_company": True,    # look up the salary and facts about the company (Glassdoor/Kununu/…)
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
        # Выключена: открытый API немецкой биржи отвечает отказом на любой путь.
        "use_arbeitsagentur": False,
        # Все профессии, а не только IT: EURES — по всему ЕС и ищет по смыслу,
        # а не по буквам (запрос по-английски приводит голландские вакансии);
        # JobTech — биржа труда Швеции, отдаёт описание сразу в выдаче.
        "use_eures": True,
        "use_jobtech": True,
        # Доски самих работодателей: у Workable есть открытый поиск по всем
        # доскам её клиентов разом. Ключа не нужно, посредника нет.
        "use_workable": True,
        # Employers found in the links of jobs already collected go onto the watch
        # list: an aggregator shows one or two of a company's jobs, while its own
        # board shows them all. Neither the model nor a web search is needed for
        # this, so the reach grows with a local model too.
        "harvest_boards": True,
        "harvest_per_run": 10,      # a soft limit, so the reach grows gradually
        # Поиск в интернете силами самого приложения. Веб-поиск умеет только
        # Claude Code; с ключом сюда разведка новых компаний и зарплатные вилки
        # работают с любой моделью, потому что ищет уже не она.
        "web_search_provider": "",   # brave | tavily | serper
        "web_search_key": "",
        "adzuna_app_id": "",
        "adzuna_app_key": "",
        "adzuna_countries": "de,nl,gb,fr,es,it,pl,at,us",
        "jooble_key": "",
    },
    "llm": {
        "provider": "claude_cli",     # см. providers.available()
        "claude_bin": "claude",
        "triage_model": "haiku",
        "deep_model": "",            # empty = claude's own default model
        # Свой адрес, говорящий на языке OpenAI. Этим одним провайдером
        # накрываются разом OpenRouter, LM Studio, vLLM, llama.cpp, корпоративные
        # шлюзы и сам OpenAI — добавлять их по одному пришлось бы бесконечно.
        "api_base": "",              # https://.../v1
        "api_key": "",
        "api_model": "",             # имя модели у выбранной службы
    },
    "telegram": {
        "bot_token": "",
        "chat_id": "",
    },
    "email": {
        "enabled": False,
        "preset": "gmail",       # a key from mailer.PRESETS
        "host": "smtp.gmail.com",
        "port": 587,
        "tls": True,
        "username": "",          # the sender's address (which is also the login)
        "password": "",          # for Gmail/Yandex/Mail.ru this is an "app password"
        "from": "",              # empty = username
        "to": "",                # empty = username (to yourself)
    },
    "schedule": {
        "mode": "off",               # off | interval | continuous
        "every_value": 1,
        "every_unit": "days",        # hours | days | weeks (for interval)
        "continuous_cooldown_min": 20,  # the pause between runs in continuous mode
    },
    "ui": {
        "background": False,         # keep searching after the window is closed
        "lang": "",                  # empty = take the system language on first launch
        "output_lang": "",           # empty = same as the interface language
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


def _installer_lang() -> str:
    """Язык, выбранный в установщике, — если он у нас спрашивался.

    Спрашивался он всегда, а вот доходил до программы нет: при первом запуске
    она брала язык системы и открывалась по-русски у того, кто в установщике
    выбрал английский. Заданный вопрос с брошенным ответом — хуже, чем
    незаданный.

    Читается только когда язык в самой программе ещё не выбран, то есть на
    первом запуске: переустановка не должна перебивать то, что человек с тех пор
    поменял руками.
    """
    from . import i18n
    try:
        код = json.loads((profiles.DATA_ROOT / "installer.json")
                         .read_text(encoding="utf-8")).get("lang", "")
    except (OSError, json.JSONDecodeError, AttributeError):
        return ""
    return код if код in i18n.UI_LANGS else ""


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
    # compatibility: the old schedule.enabled → schedule.mode
    sched = stored.get("schedule", {})
    if "mode" not in sched and "enabled" in sched:
        cfg["schedule"]["mode"] = "interval" if sched.get("enabled") else "off"
    cfg["schedule"].pop("enabled", None)
    # No language chosen (first launch) — take the system one, not a stranger's default.
    if not cfg["ui"].get("lang"):
        from . import i18n
        cfg["ui"]["lang"] = _installer_lang() or i18n.system_lang()
    if not cfg["ui"].get("output_lang"):
        cfg["ui"]["output_lang"] = cfg["ui"]["lang"]
    return cfg


def _only_owner(path) -> None:
    """Сужает права на файл до владельца.

    В config.json лежат токен Telegram, пароль от почты и ключи от моделей, а
    записывался он с обычными правами — на общей машине его читал любой другой
    вход. Особенно это заметно на Linux: домашняя папка там сплошь и рядом
    открыта на чтение всем.

    На Windows os.chmod умеет только снимать и возвращать «только чтение», а
    списками доступа не занимается, и притвориться, будто он тут что-то закрыл,
    было бы враньём. Права там наследуются от папки профиля, куда чужой вход и
    так не заходит.
    """
    if os.name == "nt":
        return
    try:
        os.chmod(path, 0o600)
    except OSError:
        pass  # чужая файловая система может не уметь прав — это не повод падать


def save(cfg: dict) -> None:
    with _lock:
        path = config_path()
        path.write_text(json.dumps(cfg, ensure_ascii=False, indent=2), encoding="utf-8")
        _only_owner(path)


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


# CV на сто мегабайт текста не бывает. Предел щедрый нарочно: смысл не в том,
# чтобы отсечь длинное резюме, а в том, чтобы отсечь файл, который в архиве
# весит килобайт, а разворачивается в гигабайты.
MAX_DOCX_XML = 100 * 1024 * 1024


def _docx_text(path) -> str:
    """Pulls the text out of a .docx with no outside dependencies (docx = a zip of XML).

    С оглядкой на размер после распаковки. Раньше содержимое читалось целиком и
    сразу: файл в несколько килобайт мог развернуться в гигабайты и увести
    программу в своп вместе со всем, что она в это время делала. Приходит он,
    между прочим, из формы загрузки CV — то есть от кого угодно, кто дотянулся
    до окна.
    """
    import re
    import zipfile
    with zipfile.ZipFile(path) as архив:
        # Сперва по описи, не читая: разжать, чтобы узнать, что разжимать не
        # стоило, — значит не проверить ничего.
        if архив.getinfo("word/document.xml").file_size > MAX_DOCX_XML:
            raise CVError("cv_err_docx")
        with архив.open("word/document.xml") as f:
            # И всё равно с ограничением. Соврать в описи, чтобы проскочить,
            # нельзя и без этого: zipfile сам держит распаковку в объявленных
            # рамках и ловит расхождение по контрольной сумме. Но тогда предел
            # держим не мы, а он, и молча — а здесь он записан.
            сырьё = f.read(MAX_DOCX_XML + 1)
        if len(сырьё) > MAX_DOCX_XML:
            raise CVError("cv_err_docx")
        xml = сырьё.decode("utf-8", errors="ignore")
    xml = re.sub(r"</w:p>", "\n", xml)          # end of paragraph → newline
    xml = re.sub(r"<w:tab/>", "\t", xml)
    text = re.sub(r"<[^>]+>", "", xml)          # strip the tags
    import html as _html
    return _html.unescape(text)


def _fix_letter_spacing(text: str) -> str:
    """Some PDFs (designer templates, Canva for instance) hand back the text with a
    space after every letter: "E l i s a b e t t a". We spot it by the share of
    one-character tokens and glue it back: inside a word the letters are separated
    by one space, words by two or more."""
    import re
    tokens = text.split()
    if not tokens or sum(1 for t in tokens if len(t) == 1) / len(tokens) < 0.6:
        return text  # ordinary text — leave it alone
    lines = []
    for line in text.splitlines():
        words = re.split(r"\s{2,}", line.strip())
        lines.append(" ".join("".join(w.split()) for w in words if w))
    return "\n".join(lines)


ALLOWED_CV_EXT = (".pdf", ".docx", ".txt", ".md", ".rtf")


class CVError(ValueError):
    """The file will not do as a CV.

    It carries a translation key rather than finished text: the module does not
    know the interface language, and the message is shown to a person — it used
    to be in Russian always.
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
        except Exception as e:  # noqa: BLE001 — the library throws all sorts
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
    """Saves the CV and extracts its text, which it returns.

    The previous CV is overwritten only once the new file has been read
    successfully: otherwise one failed upload would wipe a CV that worked.
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

    # it parsed — now replace the previous CV
    for old in d.glob("cv.*"):
        if old.suffix.lower() != ".txt" or old.name != "cv.txt":
            old.unlink(missing_ok=True)
    tmp.rename(d / f"cv{ext}")
    cv_text_path().write_text(text, encoding="utf-8")
    cv_meta_path().write_text(json.dumps({"filename": filename, "chars": len(text)}, ensure_ascii=False),
                              encoding="utf-8")
    return text
