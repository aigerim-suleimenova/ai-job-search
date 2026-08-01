"""Откуда брать модель: Claude Code CLI, Cursor CLI или локальная через Ollama.

Важное различие, которое нельзя прятать от пользователя: веб-поиск умеет только
Claude Code CLI. Без него работают триаж и разбор вакансий (модель читает
переданный текст), но поиск новых компаний в интернете — нет.
"""
import json
import os
import shutil
import subprocess
import threading
from functools import lru_cache
from pathlib import Path

import requests

from . import hardware, profiles

OLLAMA_URL = "http://127.0.0.1:11434"


# --- Каталог моделей -------------------------------------------------------
# power: грубая оценка «мощности» для сортировки (0-100), нужна ram_gb — сколько
# памяти реально требуется, чтобы модель не начала свопить.
#
# Пометки и происхождение хранятся ключами перевода, а не готовым текстом:
# каталог читается на странице «Модель», а она бывает на любом из четырнадцати
# языков. Марка изготовителя («Meta», «Alibaba») — имя собственное и не
# переводится, страна — переводится.
CLOUD_MODELS = [
    {"id": "opus", "name": "Claude Opus 5", "power": 100, "note_key": "model_note_strongest"},
    {"id": "fable", "name": "Claude Fable 5", "power": 95, "note_key": "model_note_strong_fast"},
    {"id": "sonnet", "name": "Claude Sonnet 5", "power": 85, "note_key": "model_note_balanced"},
    {"id": "haiku", "name": "Claude Haiku 4.5", "power": 70, "note_key": "model_note_fast_cheap"},
]

CURSOR_MODELS = [
    {"id": "gpt-5", "name": "GPT-5", "power": 97},
    {"id": "claude-4.5-sonnet", "name": "Claude Sonnet 4.5", "power": 88},
    {"id": "gemini-2.5-pro", "name": "Gemini 2.5 Pro", "power": 86},
    {"id": "auto", "name": "Auto", "name_key": "model_auto_cursor", "power": 80},
]

# Локальные модели: имя в Ollama → метаданные. brand/country нужны, чтобы можно
# было отфильтровать по происхождению (часть пользователей это волнует).
LOCAL_MODELS = [
    {"id": "llama3.3:70b", "name": "Llama 3.3 70B", "params": "70B", "ram_gb": 43,
     "power": 92, "brand": "Meta", "country_key": "country_us"},
    {"id": "qwen2.5:72b", "name": "Qwen 2.5 72B", "params": "72B", "ram_gb": 47,
     "power": 91, "brand": "Alibaba", "country_key": "country_cn"},
    {"id": "deepseek-r1:70b", "name": "DeepSeek R1 70B", "params": "70B", "ram_gb": 43,
     "power": 90, "brand": "DeepSeek", "country_key": "country_cn",
     "note_key": "model_note_reasoning"},
    {"id": "qwen2.5:32b", "name": "Qwen 2.5 32B", "params": "32B", "ram_gb": 20,
     "power": 84, "brand": "Alibaba", "country_key": "country_cn"},
    {"id": "qwq:32b", "name": "QwQ 32B", "params": "32B", "ram_gb": 20,
     "power": 83, "brand": "Alibaba", "country_key": "country_cn",
     "note_key": "model_note_reasoning"},
    {"id": "deepseek-r1:32b", "name": "DeepSeek R1 32B", "params": "32B", "ram_gb": 20,
     "power": 82, "brand": "DeepSeek", "country_key": "country_cn",
     "note_key": "model_note_reasoning"},
    {"id": "gemma2:27b", "name": "Gemma 2 27B", "params": "27B", "ram_gb": 16,
     "power": 78, "brand": "Google", "country_key": "country_us"},
    {"id": "qwen2.5:14b", "name": "Qwen 2.5 14B", "params": "14B", "ram_gb": 9,
     "power": 74, "brand": "Alibaba", "country_key": "country_cn"},
    {"id": "phi4:14b", "name": "Phi-4 14B", "params": "14B", "ram_gb": 9.1,
     "power": 73, "brand": "Microsoft", "country_key": "country_us"},
    {"id": "deepseek-r1:14b", "name": "DeepSeek R1 14B", "params": "14B", "ram_gb": 9,
     "power": 72, "brand": "DeepSeek", "country_key": "country_cn",
     "note_key": "model_note_reasoning"},
    {"id": "gemma2:9b", "name": "Gemma 2 9B", "params": "9B", "ram_gb": 5.4,
     "power": 66, "brand": "Google", "country_key": "country_us"},
    {"id": "llama3.1:8b", "name": "Llama 3.1 8B", "params": "8B", "ram_gb": 4.7,
     "power": 64, "brand": "Meta", "country_key": "country_us"},
    {"id": "qwen2.5:7b", "name": "Qwen 2.5 7B", "params": "7B", "ram_gb": 4.7,
     "power": 63, "brand": "Alibaba", "country_key": "country_cn"},
    {"id": "mistral:7b", "name": "Mistral 7B", "params": "7B", "ram_gb": 4.1,
     "power": 58, "brand": "Mistral", "country_key": "country_fr"},
    {"id": "llama3.2:3b", "name": "Llama 3.2 3B", "params": "3B", "ram_gb": 2.0,
     "power": 45, "brand": "Meta", "country_key": "country_us",
     "note_key": "model_note_weak_machines"},
    {"id": "qwen2.5:3b", "name": "Qwen 2.5 3B", "params": "3B", "ram_gb": 1.9,
     "power": 44, "brand": "Alibaba", "country_key": "country_cn",
     "note_key": "model_note_weak_machines"},
    {"id": "llama3.2:1b", "name": "Llama 3.2 1B", "params": "1B", "ram_gb": 1.3,
     "power": 30, "brand": "Meta", "country_key": "country_us",
     "note_key": "model_note_very_weak"},
]


# --- Доступность провайдеров ----------------------------------------------

# Приложение, запущенное из Finder или Launchpad, наследует не пользовательский
# PATH, а минимальный системный (/usr/bin:/bin:/usr/sbin:/sbin). Поэтому claude из
# ~/.local/bin или Homebrew «не находится», хотя в терминале работает. Ищем шире.
_EXTRA_DIRS = [
    Path.home() / ".local" / "bin",
    Path.home() / "bin",
    Path.home() / ".claude" / "local",
    Path("/usr/local/bin"),
    Path("/opt/homebrew/bin"),
    Path("/opt/local/bin"),
]


def work_dir() -> str:
    """Пустой каталог, из которого запускаются внешние CLI.

    Дочерний процесс наследует от приложения и рабочий каталог, и права. У
    запущенного из Finder приложения рабочий каталог — «/», и CLI, осматриваясь
    вокруг себя, добирался до Документов, Загрузок, Фото и Музыки: macOS
    спрашивал разрешение, причём от имени «AI Job Search». Здесь смотреть не на
    что, и спрашивать не о чем.
    """
    d = profiles.DATA_ROOT / "cli-work"
    d.mkdir(parents=True, exist_ok=True)
    return str(d)


@lru_cache(maxsize=1)
def login_env() -> dict:
    """Переменные окружения, как их видит терминал этого человека.

    Запущенное из Finder приложение получает почти пустое окружение, и внешний
    CLI ведёт себя не так, как в терминале: другой PATH, нет переменных из
    ~/.zshrc. Спрашиваем их у входного shell один раз за запуск.
    """
    env = dict(os.environ)
    shell = os.environ.get("SHELL") or "/bin/zsh"
    if not os.path.exists(shell):
        return env
    try:
        out = subprocess.run([shell, "-lc", "env -0"], capture_output=True,
                             text=True, timeout=10, cwd=work_dir())
    except (OSError, subprocess.SubprocessError):
        return env
    for pair in out.stdout.split("\0"):
        key, sep, value = pair.partition("=")
        if sep and key and not key.startswith(("BASH_FUNC", "_")):
            env[key] = value
    return env


@lru_cache(maxsize=8)
def resolve_bin(name: str) -> str:
    """Полный путь к программе или пустая строка. Учитывает, что GUI-приложение
    не видит пользовательский PATH."""
    if not name:
        return ""
    if os.path.sep in name:                      # уже путь — проверяем как есть
        return name if os.access(name, os.X_OK) else ""
    found = shutil.which(name)
    if found:
        return found
    for d in _EXTRA_DIRS:
        candidate = d / name
        if candidate.exists() and os.access(candidate, os.X_OK):
            return str(candidate)
    # последний шанс: спросить login shell — он прочитает .zshrc/.bash_profile
    shell = os.environ.get("SHELL") or "/bin/zsh"
    try:
        out = subprocess.run([shell, "-lc", f"command -v {name}"], cwd=work_dir(),
                             capture_output=True, text=True, timeout=10)
        path = out.stdout.strip().splitlines()[-1] if out.stdout.strip() else ""
        if path and os.access(path, os.X_OK):
            return path
    except (OSError, subprocess.SubprocessError, IndexError):
        pass
    return ""


def _bin_exists(name: str) -> bool:
    return bool(resolve_bin(name))


def ollama_running() -> bool:
    try:
        requests.get(f"{OLLAMA_URL}/api/tags", timeout=2).raise_for_status()
        return True
    except requests.RequestException:
        return False


def ollama_installed_models() -> set:
    try:
        r = requests.get(f"{OLLAMA_URL}/api/tags", timeout=5)
        r.raise_for_status()
        return {m.get("name", "") for m in r.json().get("models", [])}
    except (requests.RequestException, ValueError):
        return set()


def forget_binaries() -> None:
    """Забыть найденные пути: человек мог поставить программу только что."""
    resolve_bin.cache_clear()


def available(claude_bin: str = "claude") -> dict:
    """Какие провайдеры реально готовы к работе на этой машине.

    Названия и подсказки здесь не хранятся: они лежат в переводах под ключами
    prov_<код> и prov_<код>_hint — иначе имя провайдера приезжало бы на
    страницу по-русски посреди английской фразы.
    """
    return {
        "claude_cli": {
            "ready": _bin_exists(claude_bin or "claude"),
            "web_search": True, "kind": "cloud",
            "install_url": "https://claude.com/claude-code",
        },
        "cursor_cli": {
            "ready": _bin_exists("cursor-agent"),
            "web_search": False, "kind": "cloud",
            "install_url": "https://cursor.com/cli",
        },
        "ollama": {
            "ready": ollama_running(),
            "web_search": False, "kind": "local",
            "install_url": "https://ollama.com/download",
        },
    }


def _localized(model: dict, lang: str) -> dict:
    """Модель каталога с человекочитаемыми полями на языке интерфейса."""
    from . import i18n
    out = dict(model)
    if model.get("name_key"):
        out["name"] = i18n.t(lang, model["name_key"])
    if model.get("note_key"):
        out["note"] = i18n.t(lang, model["note_key"])
    if model.get("brand"):
        country = i18n.t(lang, model["country_key"]) if model.get("country_key") else ""
        out["origin"] = f"{model['brand']} ({country})" if country else model["brand"]
    return out


def models_for(provider: str, installed: set = None, lang: str = "en") -> list:
    """Список моделей провайдера с бейджем совместимости — отсортирован по мощности."""
    if provider == "claude_cli":
        return [_localized(dict(m, fits="yes", kind="cloud"), lang) for m in CLOUD_MODELS]
    if provider == "cursor_cli":
        return [_localized(dict(m, fits="yes", kind="cloud"), lang) for m in CURSOR_MODELS]
    if provider == "ollama":
        installed = installed if installed is not None else ollama_installed_models()
        out = []
        for m in LOCAL_MODELS:
            out.append(_localized(dict(m, kind="local", fits=hardware.fits(m["ram_gb"]),
                                       installed=m["id"] in installed), lang))
        return out
    return []


# --- Вызовы ----------------------------------------------------------------

class ProviderError(RuntimeError):
    """Ошибка провайдера.

    Может нести ключ перевода — как MailError: модуль не знает языка
    интерфейса, а текст показывается человеку. Когда сообщение приходит от
    самой программы-провайдера (stderr claude, ответ Ollama), ключа нет и
    текст передаётся как есть: переводить чужой вывод нечем.
    """

    def __init__(self, message: str = "", key: str = "", **fmt):
        self.key, self.fmt = key, fmt
        super().__init__(message or key)

    def text(self, lang: str) -> str:
        from . import i18n
        if not self.key:
            return str(self)
        return i18n.t(lang, self.key).format(**self.fmt)


def call_claude(prompt: str, model: str, timeout: int, allowed_tools, claude_bin: str) -> str:
    exe = resolve_bin(claude_bin or "claude") or (claude_bin or "claude")
    cmd = [exe, "-p", "--output-format", "json"]
    if model:
        cmd += ["--model", model]
    if allowed_tools:
        cmd += ["--allowedTools", ",".join(allowed_tools)]
    proc = subprocess.run(cmd, input=prompt, capture_output=True, text=True,
                          encoding="utf-8", errors="replace",
                          cwd=work_dir(), env=login_env(),
                          timeout=timeout, close_fds=False)
    if proc.returncode != 0:
        detail = (proc.stderr or proc.stdout or "").strip()
        if not detail:
            raise ProviderError(key="prov_err_exit_code", tool="claude", code=proc.returncode)
        raise ProviderError(detail[:800])
    try:
        data = json.loads(proc.stdout)
    except json.JSONDecodeError:
        return proc.stdout.strip()
    if isinstance(data, dict):
        if data.get("is_error"):
            raise ProviderError(str(data.get("result", ""))[:800])
        return str(data.get("result", "")).strip()
    return proc.stdout.strip()


def call_cursor(prompt: str, model: str, timeout: int) -> str:
    exe = resolve_bin("cursor-agent")
    if not exe:
        raise ProviderError(key="prov_err_no_cursor")
    cmd = [exe, "-p", "--output-format", "text"]
    if model and model != "auto":
        cmd += ["--model", model]
    proc = subprocess.run(cmd, input=prompt, capture_output=True, text=True,
                          encoding="utf-8", errors="replace",
                          cwd=work_dir(), env=login_env(),
                          timeout=timeout, close_fds=False)
    if proc.returncode != 0:
        detail = (proc.stderr or proc.stdout or "").strip()[:800]
        if not detail:
            raise ProviderError(key="prov_err_exit_code", tool="cursor-agent",
                                code=proc.returncode)
        raise ProviderError(detail)
    return proc.stdout.strip()


def call_ollama(prompt: str, model: str, timeout: int) -> str:
    try:
        r = requests.post(f"{OLLAMA_URL}/api/generate",
                          json={"model": model, "prompt": prompt, "stream": False},
                          timeout=timeout)
        r.raise_for_status()
        return str(r.json().get("response", "")).strip()
    except requests.RequestException as e:
        raise ProviderError(key="prov_err_ollama_unreachable", error=e) from e
    except ValueError as e:
        raise ProviderError(key="prov_err_ollama_not_json", error=e) from e


def call(prompt: str, provider: str, model: str, timeout: int = 600,
         allowed_tools=None, claude_bin: str = "claude") -> str:
    """Единая точка вызова модели. allowed_tools учитывается только Claude Code CLI."""
    if provider == "cursor_cli":
        return call_cursor(prompt, model, timeout)
    if provider == "ollama":
        return call_ollama(prompt, model, timeout)
    return call_claude(prompt, model, timeout, allowed_tools, claude_bin)


def supports_web_search(provider: str) -> bool:
    return provider in ("", "claude_cli")


# Скачивание модели — это гигабайты и минуты, поэтому оно идёт в фоне, а страница
# опрашивает прогресс. Иначе окно приложения просто замирало бы до конца загрузки.
# status_key/error_key — наши собственные шаги и ошибки, они переводятся при
# выдаче наружу. В status приходят сообщения самой Ollama («pulling manifest»),
# переводить их нечем, поэтому они идут как есть.
_pull_state = {"model": "", "percent": 0, "status": "", "status_key": "",
               "error": "", "error_key": "", "error_fmt": {}, "done": False}
_pull_lock = threading.Lock()


def pull_status() -> dict:
    with _pull_lock:
        return dict(_pull_state)


def pull_in_progress() -> bool:
    with _pull_lock:
        return bool(_pull_state["model"]) and not _pull_state["done"]


def _set_pull(**kw) -> None:
    with _pull_lock:
        _pull_state.update(kw)


def pull(model: str, log=None) -> bool:
    """Скачивает локальную модель, сообщая прогресс через log()."""
    try:
        with requests.post(f"{OLLAMA_URL}/api/pull", json={"model": model, "stream": True},
                           stream=True, timeout=3600) as r:
            r.raise_for_status()
            for line in r.iter_lines():
                if not line:
                    continue
                try:
                    ev = json.loads(line)
                except ValueError:
                    continue
                if ev.get("status"):
                    total, done = ev.get("total"), ev.get("completed")
                    percent = (done * 100 // total) if (total and done) else None
                    _set_pull(status=str(ev["status"]), status_key="",
                              **({"percent": percent} if percent is not None else {}))
                    if log:
                        log(f"{ev['status']}{f': {percent}%' if percent is not None else ''}")
                if ev.get("error"):
                    raise ProviderError(str(ev["error"])[:300])
        return True
    except requests.ConnectionError as e:
        # самая частая причина: Ollama не установлена или не запущена,
        # а технический текст исключения тут только пугает
        raise ProviderError(key="prov_err_ollama_down") from e
    except requests.RequestException as e:
        raise ProviderError(key="prov_err_pull_failed", error=e) from e


def pull_async(model: str) -> None:
    """Запускает скачивание в фоне: страница опрашивает pull_status()."""
    if pull_in_progress():
        return
    _set_pull(model=model, percent=0, status="", status_key="pull_starting",
              error="", error_key="", error_fmt={}, done=False)

    def worker():
        try:
            pull(model)
            _set_pull(percent=100, status="", status_key="pull_done", done=True)
        except ProviderError as e:
            # значения приводим к строкам: состояние уезжает в JSON на страницу,
            # а в fmt может лежать исключение
            _set_pull(error=str(e) if not e.key else "", error_key=e.key,
                      error_fmt={k: str(v) for k, v in e.fmt.items()}, done=True)

    threading.Thread(target=worker, daemon=True).start()
