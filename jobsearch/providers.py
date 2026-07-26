"""Откуда брать модель: Claude Code CLI, Cursor CLI или локальная через Ollama.

Важное различие, которое нельзя прятать от пользователя: веб-поиск умеет только
Claude Code CLI. Без него работают триаж и разбор вакансий (модель читает
переданный текст), но поиск новых компаний в интернете — нет.
"""
import json
import shutil
import subprocess
from functools import lru_cache

import requests

from . import hardware

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

def _bin_exists(name: str) -> bool:
    return bool(shutil.which(name))


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


def available(claude_bin: str = "claude") -> dict:
    """Какие провайдеры реально готовы к работе на этой машине."""
    return {
        "claude_cli": {
            "name": "Claude Code CLI", "ready": _bin_exists(claude_bin or "claude"),
            "web_search": True, "kind": "cloud",
            "hint": "Установите с claude.com/claude-code",
        },
        "cursor_cli": {
            "name": "Cursor CLI", "ready": _bin_exists("cursor-agent"),
            "web_search": False, "kind": "cloud",
            "hint": "Установите Cursor и его CLI (cursor-agent)",
        },
        "ollama": {
            "name": "Локальная модель (Ollama)", "ready": ollama_running(),
            "web_search": False, "kind": "local",
            "hint": "Скачайте с ollama.com и запустите",
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
    exe = shutil.which(claude_bin or "claude") or (claude_bin or "claude")
    cmd = [exe, "-p", "--output-format", "json"]
    if model:
        cmd += ["--model", model]
    if allowed_tools:
        cmd += ["--allowedTools", ",".join(allowed_tools)]
    proc = subprocess.run(cmd, input=prompt, capture_output=True, text=True,
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
    exe = shutil.which("cursor-agent")
    if not exe:
        raise ProviderError("Cursor CLI не найден: установите cursor-agent")
    cmd = [exe, "-p", "--output-format", "text"]
    if model and model != "auto":
        cmd += ["--model", model]
    proc = subprocess.run(cmd, input=prompt, capture_output=True, text=True,
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


@lru_cache(maxsize=1)
def _pull_state() -> dict:
    return {}


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
                if log and ev.get("status"):
                    total, done = ev.get("total"), ev.get("completed")
                    if total and done:
                        log(f"{ev['status']}: {done * 100 // total}%")
                    else:
                        log(str(ev["status"]))
                if ev.get("error"):
                    raise ProviderError(str(ev["error"])[:300])
        return True
    except requests.RequestException as e:
        raise ProviderError(f"Не удалось скачать модель: {e}") from e
