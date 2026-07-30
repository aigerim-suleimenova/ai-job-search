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
CLOUD_MODELS = [
    {"id": "opus", "name": "Claude Opus 5", "power": 100, "note": "самая сильная, дороже"},
    {"id": "fable", "name": "Claude Fable 5", "power": 95, "note": "сильная, быстрее Opus"},
    {"id": "sonnet", "name": "Claude Sonnet 5", "power": 85, "note": "баланс цены и качества"},
    {"id": "haiku", "name": "Claude Haiku 4.5", "power": 70, "note": "быстрая и дешёвая"},
]

CURSOR_MODELS = [
    {"id": "gpt-5", "name": "GPT-5", "power": 97},
    {"id": "claude-4.5-sonnet", "name": "Claude Sonnet 4.5", "power": 88},
    {"id": "gemini-2.5-pro", "name": "Gemini 2.5 Pro", "power": 86},
    {"id": "auto", "name": "Auto (выбирает Cursor)", "power": 80},
]

# Локальные модели: имя в Ollama → метаданные. origin нужен, чтобы можно было
# отфильтровать по происхождению (часть пользователей это волнует).
LOCAL_MODELS = [
    {"id": "llama3.3:70b", "name": "Llama 3.3 70B", "params": "70B", "ram_gb": 43,
     "power": 92, "origin": "Meta (США)"},
    {"id": "qwen2.5:72b", "name": "Qwen 2.5 72B", "params": "72B", "ram_gb": 47,
     "power": 91, "origin": "Alibaba (Китай)"},
    {"id": "deepseek-r1:70b", "name": "DeepSeek R1 70B", "params": "70B", "ram_gb": 43,
     "power": 90, "origin": "DeepSeek (Китай)", "note": "рассуждающая"},
    {"id": "qwen2.5:32b", "name": "Qwen 2.5 32B", "params": "32B", "ram_gb": 20,
     "power": 84, "origin": "Alibaba (Китай)"},
    {"id": "qwq:32b", "name": "QwQ 32B", "params": "32B", "ram_gb": 20,
     "power": 83, "origin": "Alibaba (Китай)", "note": "рассуждающая"},
    {"id": "deepseek-r1:32b", "name": "DeepSeek R1 32B", "params": "32B", "ram_gb": 20,
     "power": 82, "origin": "DeepSeek (Китай)", "note": "рассуждающая"},
    {"id": "gemma2:27b", "name": "Gemma 2 27B", "params": "27B", "ram_gb": 16,
     "power": 78, "origin": "Google (США)"},
    {"id": "qwen2.5:14b", "name": "Qwen 2.5 14B", "params": "14B", "ram_gb": 9,
     "power": 74, "origin": "Alibaba (Китай)"},
    {"id": "phi4:14b", "name": "Phi-4 14B", "params": "14B", "ram_gb": 9.1,
     "power": 73, "origin": "Microsoft (США)"},
    {"id": "deepseek-r1:14b", "name": "DeepSeek R1 14B", "params": "14B", "ram_gb": 9,
     "power": 72, "origin": "DeepSeek (Китай)", "note": "рассуждающая"},
    {"id": "gemma2:9b", "name": "Gemma 2 9B", "params": "9B", "ram_gb": 5.4,
     "power": 66, "origin": "Google (США)"},
    {"id": "llama3.1:8b", "name": "Llama 3.1 8B", "params": "8B", "ram_gb": 4.7,
     "power": 64, "origin": "Meta (США)"},
    {"id": "qwen2.5:7b", "name": "Qwen 2.5 7B", "params": "7B", "ram_gb": 4.7,
     "power": 63, "origin": "Alibaba (Китай)"},
    {"id": "mistral:7b", "name": "Mistral 7B", "params": "7B", "ram_gb": 4.1,
     "power": 58, "origin": "Mistral (Франция)"},
    {"id": "llama3.2:3b", "name": "Llama 3.2 3B", "params": "3B", "ram_gb": 2.0,
     "power": 45, "origin": "Meta (США)", "note": "для слабых машин"},
    {"id": "qwen2.5:3b", "name": "Qwen 2.5 3B", "params": "3B", "ram_gb": 1.9,
     "power": 44, "origin": "Alibaba (Китай)", "note": "для слабых машин"},
    {"id": "llama3.2:1b", "name": "Llama 3.2 1B", "params": "1B", "ram_gb": 1.3,
     "power": 30, "origin": "Meta (США)", "note": "очень слабая, для проверки"},
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
    """Какие провайдеры реально готовы к работе на этой машине."""
    return {
        "claude_cli": {
            "name": "Claude Code CLI", "ready": _bin_exists(claude_bin or "claude"),
            "web_search": True, "kind": "cloud",
            "hint": "Установите с claude.com/claude-code",
            "install_url": "https://claude.com/claude-code",
        },
        "cursor_cli": {
            "name": "Cursor CLI", "ready": _bin_exists("cursor-agent"),
            "web_search": False, "kind": "cloud",
            "hint": "Установите Cursor и его CLI (cursor-agent)",
            "install_url": "https://cursor.com/cli",
        },
        "ollama": {
            "name": "Локальная модель (Ollama)", "ready": ollama_running(),
            "web_search": False, "kind": "local",
            "hint": "Скачайте с ollama.com и запустите",
            "install_url": "https://ollama.com/download",
        },
    }


def models_for(provider: str, installed: set = None) -> list:
    """Список моделей провайдера с бейджем совместимости — отсортирован по мощности."""
    if provider == "claude_cli":
        return [dict(m, fits="yes", kind="cloud") for m in CLOUD_MODELS]
    if provider == "cursor_cli":
        return [dict(m, fits="yes", kind="cloud") for m in CURSOR_MODELS]
    if provider == "ollama":
        installed = installed if installed is not None else ollama_installed_models()
        out = []
        for m in LOCAL_MODELS:
            out.append(dict(m, kind="local", fits=hardware.fits(m["ram_gb"]),
                            installed=m["id"] in installed))
        return out
    return []


# --- Вызовы ----------------------------------------------------------------

class ProviderError(RuntimeError):
    pass


def call_claude(prompt: str, model: str, timeout: int, allowed_tools, claude_bin: str) -> str:
    exe = resolve_bin(claude_bin or "claude") or (claude_bin or "claude")
    cmd = [exe, "-p", "--output-format", "json"]
    if model:
        cmd += ["--model", model]
    if allowed_tools:
        cmd += ["--allowedTools", ",".join(allowed_tools)]
    proc = subprocess.run(cmd, input=prompt, capture_output=True, text=True, cwd=work_dir(),
                          timeout=timeout, close_fds=False)
    if proc.returncode != 0:
        detail = (proc.stderr or proc.stdout or "").strip()
        raise ProviderError(detail[:800] or f"claude завершился с кодом {proc.returncode} без вывода")
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
        raise ProviderError("Cursor CLI не найден: установите cursor-agent")
    cmd = [exe, "-p", "--output-format", "text"]
    if model and model != "auto":
        cmd += ["--model", model]
    proc = subprocess.run(cmd, input=prompt, capture_output=True, text=True, cwd=work_dir(),
                          timeout=timeout, close_fds=False)
    if proc.returncode != 0:
        raise ProviderError((proc.stderr or proc.stdout or "").strip()[:800]
                            or f"cursor-agent завершился с кодом {proc.returncode}")
    return proc.stdout.strip()


def call_ollama(prompt: str, model: str, timeout: int) -> str:
    try:
        r = requests.post(f"{OLLAMA_URL}/api/generate",
                          json={"model": model, "prompt": prompt, "stream": False},
                          timeout=timeout)
        r.raise_for_status()
        return str(r.json().get("response", "")).strip()
    except requests.RequestException as e:
        raise ProviderError(f"Ollama недоступна: {e}") from e
    except ValueError as e:
        raise ProviderError(f"Ollama вернула не JSON: {e}") from e


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
_pull_state = {"model": "", "percent": 0, "status": "", "error": "", "done": False}
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
                    _set_pull(status=str(ev["status"]),
                              **({"percent": percent} if percent is not None else {}))
                    if log:
                        log(f"{ev['status']}{f': {percent}%' if percent is not None else ''}")
                if ev.get("error"):
                    raise ProviderError(str(ev["error"])[:300])
        return True
    except requests.ConnectionError as e:
        # самая частая причина: Ollama не установлена или не запущена,
        # а технический текст исключения тут только пугает
        raise ProviderError("Ollama не отвечает. Убедитесь, что программа Ollama "
                            "установлена и запущена, затем попробуйте снова.") from e
    except requests.RequestException as e:
        raise ProviderError(f"Не удалось скачать модель: {e}") from e


def pull_async(model: str) -> None:
    """Запускает скачивание в фоне: страница опрашивает pull_status()."""
    if pull_in_progress():
        return
    _set_pull(model=model, percent=0, status="начинаем", error="", done=False)

    def worker():
        try:
            pull(model)
            _set_pull(percent=100, status="готово", done=True)
        except ProviderError as e:
            _set_pull(error=str(e), done=True)

    threading.Thread(target=worker, daemon=True).start()
