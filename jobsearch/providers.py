"""Where to get a model: Claude Code CLI, Cursor CLI or a local one via Ollama.

One difference must not be hidden from the user: only Claude Code CLI can search
the web. Without it triage and job analysis still work (the model reads the text
it is handed), but finding new companies on the internet does not.
"""
import json
import os
import shutil
import subprocess
import threading
import time
from functools import lru_cache
from pathlib import Path

import requests

from . import hardware, profiles

OLLAMA_URL = "http://127.0.0.1:11434"


# --- The model catalogue ---------------------------------------------------
# power: a rough measure of "strength" for sorting (0-100); ram_gb is how much
# memory is really needed for the model not to start swapping.
#
# Notes and origins are kept as translation keys rather than finished text: the
# catalogue is read on the "Model" page, and that page comes in any of fourteen
# languages. The maker's name ("Meta", "Alibaba") is a proper noun and is not
# translated; the country is.
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

# Названия моделей у Codex меняются чаще, чем выходят наши версии: gpt-5.4
# уступает место семейству gpt-5.6. Поэтому «Авто» стоит первым и выбрано по
# умолчанию — при нём мы вообще не называем модель, и Codex берёт ту, что
# настроена у него самого. Устаревшее имя в списке сломает поиск, «Авто» — нет.
CODEX_MODELS = [
    {"id": "auto", "name": "Auto", "name_key": "model_auto_codex", "power": 95},
    {"id": "gpt-5.6-terra", "name": "GPT-5.6 Terra", "power": 97},
    {"id": "gpt-5.6-luna", "name": "GPT-5.6 Luna", "power": 88,
     "note_key": "model_note_strong_fast"},
]

COPILOT_MODELS = [
    {"id": "auto", "name": "Auto", "name_key": "model_auto_generic", "power": 95},
    {"id": "gpt-5.2", "name": "GPT-5.2", "power": 96},
    {"id": "claude-sonnet-4.6", "name": "Claude Sonnet 4.6", "power": 92},
    {"id": "claude-haiku-4.5", "name": "Claude Haiku 4.5", "power": 70,
     "note_key": "model_note_fast_cheap"},
]

# Goose и Qwen Code не привязаны к одной модели: какая доступна, решает ключ,
# который человек им дал. Перечислять чужой список здесь нечестно — «Авто»
# означает «не называть модель», и тогда берётся настроенная у самой программы.
BYOK_MODELS = [
    {"id": "auto", "name": "Auto", "name_key": "model_auto_generic", "power": 90},
]

# Local models: the Ollama name → metadata. brand/country are there so the list
# can be filtered by origin (some people do care about it).
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


# --- Provider availability -------------------------------------------------

# An app started from Finder or Launchpad inherits not the user's PATH but the
# minimal system one (/usr/bin:/bin:/usr/sbin:/sbin). That is why claude from
# ~/.local/bin or Homebrew "cannot be found" although it works in the terminal.
# So we look wider.
_EXTRA_DIRS = [
    Path.home() / ".local" / "bin",
    Path.home() / "bin",
    Path.home() / ".claude" / "local",
    Path("/usr/local/bin"),
    Path("/opt/homebrew/bin"),
    Path("/opt/local/bin"),
]


def work_dir() -> str:
    """An empty directory the external CLIs are launched from.

    A child process inherits both the working directory and the permissions of
    the app. For an app started from Finder the working directory is "/", and a
    CLI looking around itself reached Documents, Downloads, Photos and Music:
    macOS asked for permission, and asked in the name of "AI Job Search". Here
    there is nothing to look at, and so nothing to ask about.
    """
    d = profiles.DATA_ROOT / "cli-work"
    d.mkdir(parents=True, exist_ok=True)
    return str(d)


@lru_cache(maxsize=1)
def login_env() -> dict:
    """The environment as this person's terminal sees it.

    An app started from Finder gets an almost empty environment, and an external
    CLI then behaves unlike it does in a terminal: a different PATH, none of the
    variables from ~/.zshrc. We ask the login shell for them once per launch.
    """
    env = dict(os.environ)
    shell = os.environ.get("SHELL") or "/bin/zsh"
    if not os.path.exists(shell):
        return env
    try:
        # encoding задана прямо. Без неё берётся кодировка системы — cp1252 на
        # Windows, ascii при локали C, — и первая же нелатинская буква в чужой
        # переменной среды рушит разбор. А она там есть у всякого, чьё имя не
        # латиницей: оно лежит в HOME и в PATH. Падало при этом не понятным
        # исключением, а AttributeError: ошибка случалась в потоке чтения, и
        # stdout молча оказывался None. И переставали работать разом все
        # командные строки.
        out = subprocess.run([shell, "-lc", "env -0"], capture_output=True,
                             text=True, encoding="utf-8", errors="replace",
                             timeout=10, cwd=work_dir())
    except (OSError, subprocess.SubprocessError):
        return env
    for pair in (out.stdout or "").split("\0"):
        key, sep, value = pair.partition("=")
        if sep and key and not key.startswith(("BASH_FUNC", "_")):
            env[key] = value
    return env


@lru_cache(maxsize=8)
def resolve_bin(name: str) -> str:
    """The full path to a program, or an empty string. Allows for the fact that a
    GUI app does not see the user's PATH."""
    if not name:
        return ""
    if os.path.sep in name:                      # already a path — check it as it is
        return name if os.access(name, os.X_OK) else ""
    found = shutil.which(name)
    if found:
        return found
    for d in _EXTRA_DIRS:
        candidate = d / name
        if candidate.exists() and os.access(candidate, os.X_OK):
            return str(candidate)
    # last chance: ask the login shell — it will read .zshrc/.bash_profile
    shell = os.environ.get("SHELL") or "/bin/zsh"
    try:
        out = subprocess.run([shell, "-lc", f"command -v {name}"], cwd=work_dir(),
                             capture_output=True, text=True, encoding="utf-8",
                             errors="replace", timeout=10)
        вывод = (out.stdout or "").strip()
        path = вывод.splitlines()[-1] if вывод else ""
        if path and os.access(path, os.X_OK):
            return path
    except (OSError, subprocess.SubprocessError, IndexError):
        pass
    return ""


def _bin_exists(name: str) -> bool:
    return bool(resolve_bin(name))


# One question to Ollama, remembered for a moment.
#
# /api/tags answers both things anyone here ever wants to know: whether Ollama is
# running at all, and what has been downloaded into it. They used to be asked
# separately and several times over a single page — by the gate deciding whether
# to show the introduction, by the provider badge, by the model list. On a
# machine without Ollama every one of those waited out its own timeout, and
# choosing a provider took six seconds.
#
# The timeout is short because this is localhost: a program that is there accepts
# the connection at once, and one that is not should not keep a person waiting to
# find that out. The longer half is for reading — an Ollama busy loading a model
# may think before it answers, and that is not a reason to call it missing.
_tags = {"asked_at": 0.0, "alive": False, "models": frozenset()}
_TAGS_TTL = 3.0
_TAGS_TIMEOUT = (0.4, 3)          # (сколько ждём соединения, сколько — ответа)


def _ollama_tags() -> dict:
    now = time.monotonic()
    if _tags["asked_at"] and now - _tags["asked_at"] < _TAGS_TTL:
        return _tags
    alive, models = False, frozenset()
    try:
        r = requests.get(f"{OLLAMA_URL}/api/tags", timeout=_TAGS_TIMEOUT)
        r.raise_for_status()
        models = frozenset(m.get("name", "") for m in r.json().get("models", []))
        alive = True
    except (requests.RequestException, ValueError):
        pass
    _tags.update(asked_at=now, alive=alive, models=models)
    return _tags


def forget_ollama() -> None:
    """Ask Ollama afresh: something has just changed on the disk or in the app."""
    _tags["asked_at"] = 0.0


def ollama_running() -> bool:
    return _ollama_tags()["alive"]


def ollama_installed_models() -> set:
    return set(_ollama_tags()["models"])


# --- Чего требуют сами программы, кроме себя ----------------------------------
#
# Claude Code, Cursor, Goose и Ollama приходят своими установщиками и не требуют
# ничего. Copilot и Qwen ставятся только через npm, а npm приходит с Node.js,
# которого на обычном компьютере нет. В карточке было написано «npm install -g
# @github/copilot» — человек шёл в терминал и получал «npm: команда не найдена»,
# причём уже вне нашей программы, где мы ему ничем не поможем. Про то, чего не
# хватает, надо сказать здесь и до того, как он уйдёт.
#
# Codex тоже ставится через npm, но у него есть и готовый бинарник, и страница
# установки предлагает оба пути: требовать для него Node.js значило бы гнать
# человека ставить то, без чего он прекрасно обойдётся.
PREREQS = {
    "copilot_cli": "node",
    "qwen_cli": "node",
}

TOOLS = {
    "node": {
        "bin": "node",
        "version_arg": "-v",
        # npm-пакеты обеих командных строк требуют Node.js 22. Со старым Node
        # установка не падает сразу, а падает потом и невнятно.
        "min_major": 22,
        "url": "https://nodejs.org/en/download",
    },
}


def _major(text: str) -> int:
    """Старшее число версии из того, что программа сказала о себе: «v22.14.0» → 22."""
    import re
    m = re.search(r"(\d+)", text or "")
    return int(m.group(1)) if m else 0


@lru_cache(maxsize=8)
def tool_state(tool: str) -> tuple:
    """(есть ли, что ответила о версии). Кортеж — чтобы лёг в lru_cache.

    Версия не разобралась — считаем, что годится: не хватало ещё загородить
    человеку дорогу из-за того, что мы не поняли чужой формат вывода.
    """
    spec = TOOLS.get(tool)
    if not spec:
        return (True, "")
    path = resolve_bin(spec["bin"])
    if not path:
        return (False, "")
    try:
        out = subprocess.run([path, spec["version_arg"]], capture_output=True,
                             text=True, encoding="utf-8", errors="replace", timeout=5, cwd=work_dir(), env=login_env())
        version = (out.stdout or out.stderr or "").strip().splitlines()[0].strip()
    except (OSError, subprocess.SubprocessError, IndexError):
        return (True, "")
    major = _major(version)
    return (not major or major >= spec["min_major"], version)


def missing_tool(provider: str) -> str:
    """Чего не хватает, чтобы вообще установить этого провайдера. Пусто — ничего.

    Спрашивается только про то, что ещё не установлено: если Copilot уже стоит,
    как он туда попал — не наше дело, и гнать человека за Node.js поздно и незачем.
    """
    tool = PREREQS.get(provider, "")
    if not tool or tool_state(tool)[0]:
        return ""
    return tool


def tool_info(tool: str) -> dict:
    """Всё, что странице нужно знать об инструменте."""
    ready, version = tool_state(tool)
    spec = TOOLS.get(tool, {})
    return {"key": tool, "ready": ready, "version": version,
            "url": spec.get("url", ""), "min_major": spec.get("min_major", 0),
            # Нашёлся, но старый — это не то же самое, что «не нашёлся»: сказать
            # «установите Node.js» тому, у кого он есть, значит послать его по
            # кругу. Разные беды — разные слова.
            "too_old": bool(version) and not ready}


def forget_binaries() -> None:
    """Forget the paths found: the person may have installed the program just now."""
    resolve_bin.cache_clear()
    tool_state.cache_clear()
    forget_ollama()


def available(claude_bin: str = "claude", llm: dict = None) -> dict:
    """Which providers are actually ready to work on this machine.

    Names and hints are not kept here: they live in the translations under the
    keys prov_<code> and prov_<code>_hint — otherwise the provider's name would
    arrive on the page in Russian in the middle of an English sentence.

    llm — настройки модели. Нужны одному провайдеру: у своего адреса нечего
    искать на диске, он готов ровно тогда, когда адрес вписан.
    """
    llm = llm or {}
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
        "codex_cli": {
            # Codex умеет ходить в сеть, но по умолчанию заперт в песочнице
            # только на чтение — и это ровно то, чего мы хотим от запуска без
            # человека. Поиск новых компаний с ним пропускается: обещать
            # веб-поиск, которого может не оказаться, хуже, чем не обещать.
            "ready": _bin_exists("codex"),
            "web_search": False, "kind": "cloud",
            "install_url": "https://developers.openai.com/codex/cli/",
        },
        "copilot_cli": {
            "ready": _bin_exists("copilot"),
            "web_search": False, "kind": "cloud",
            "install_url": "https://docs.github.com/en/copilot/how-tos/copilot-cli/set-up-copilot-cli/install-copilot-cli",
        },
        "goose_cli": {
            "ready": _bin_exists("goose"),
            "web_search": False, "kind": "cloud",
            "install_url": "https://goose-docs.ai/docs/getting-started/installation",
        },
        "qwen_cli": {
            "ready": _bin_exists("qwen"),
            "web_search": False, "kind": "cloud",
            "install_url": "https://github.com/QwenLM/qwen-code",
        },
        "ollama": {
            "ready": ollama_running(),
            "web_search": False, "kind": "local",
            "install_url": "https://ollama.com/download",
        },
        # Последним — он для тех, кто знает, чего хочет: вместо кнопки здесь поля,
        # и первым в списке он сбивал бы с толку тех, кому подойдёт любой из
        # обычных вариантов выше.
        "openai_api": {
            # Устанавливать нечего: это не программа, а адрес. Поэтому «готов»
            # всегда — выбрать его можно сразу, а настраивается он вторым шагом,
            # там, где на поля есть ширина. В узкой карточке они не помещались:
            # «Адрес» и «Ключ» вставали в строку и обрезались на середине.
            "ready": True,
            "web_search": False, "kind": "custom",
            "install_url": "",
        },
    }


def missing_piece(llm: dict) -> str:
    """What stands between the app and a thought: "" | "provider" | "model".

    The two are different troubles and must not be told as one. Ollama can be
    running perfectly well while the model inside it was never downloaded — and
    a person sent off to "install Ollama" would be reinstalling the one part
    that already works.

    A local model is looked for in Ollama itself rather than in our catalogue:
    somebody may be using a model we never listed, and it would be strange to
    call their working setup broken.
    """
    provider = llm.get("provider") or "claude_cli"
    if not available(llm.get("claude_bin", "claude"), llm).get(provider, {}).get("ready"):
        return "provider"
    if provider == "openai_api":
        # Адрес и модель спрашиваются вместе, на втором шаге: без любого из них
        # думать нечем, и разделять их на два экрана было бы придиркой.
        есть = (llm.get("api_base") or "").strip() and (llm.get("api_model") or "").strip()
        return "" if есть else "model"
    if provider == "ollama":
        model = llm.get("triage_model") or ""
        if not model or model not in ollama_installed_models():
            return "model"
    return ""


def current_model(llm: dict) -> str:
    """Чем сейчас считаем — так, как это называет сам провайдер.

    У всех, кроме своего адреса, модель лежит в triage_model. У своего адреса —
    в api_model, потому что имя ей даёт чужая служба и в наш каталог оно не
    ложится. Пока это не различали, карточка «Свой адрес» писала «используется:
    haiku»: показывалось triage_model, оставшееся от Claude Code, то есть имя
    модели, к которой этот адрес не имеет никакого отношения.
    """
    if (llm.get("provider") or "claude_cli") == "openai_api":
        return (llm.get("api_model") or "").strip()
    return llm.get("triage_model") or "haiku"


def _localized(model: dict, lang: str) -> dict:
    """A catalogue model whose readable fields are in the interface language."""
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
    """A provider's models with a "will it fit" badge, sorted by strength."""
    if provider == "claude_cli":
        return [_localized(dict(m, fits="yes", kind="cloud"), lang) for m in CLOUD_MODELS]
    if provider == "cursor_cli":
        return [_localized(dict(m, fits="yes", kind="cloud"), lang) for m in CURSOR_MODELS]
    if provider == "codex_cli":
        return [_localized(dict(m, fits="yes", kind="cloud"), lang) for m in CODEX_MODELS]
    if provider == "copilot_cli":
        return [_localized(dict(m, fits="yes", kind="cloud"), lang) for m in COPILOT_MODELS]
    if provider in ("goose_cli", "qwen_cli"):
        return [_localized(dict(m, fits="yes", kind="cloud"), lang) for m in BYOK_MODELS]
    if provider == "ollama":
        installed = installed if installed is not None else ollama_installed_models()
        out = []
        for m in LOCAL_MODELS:
            out.append(_localized(dict(m, kind="local", fits=hardware.fits(m["ram_gb"]),
                                       installed=m["id"] in installed), lang))
        return out
    return []


# --- Calls -----------------------------------------------------------------

class ProviderError(RuntimeError):
    """A provider error.

    It may carry a translation key, the way MailError does: the module does not
    know the interface language, and the text is shown to a person. When the
    message comes from the provider program itself (claude's stderr, Ollama's
    answer), there is no key and the text is passed through as it is: there is
    nothing to translate someone else's output with.
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
    proc = subprocess.run(cmd, input=prompt, capture_output=True, text=True, encoding="utf-8", errors="replace",
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
    proc = subprocess.run(cmd, input=prompt, capture_output=True, text=True, encoding="utf-8", errors="replace",
                          cwd=work_dir(), env=login_env(),
                          timeout=timeout, close_fds=False)
    if proc.returncode != 0:
        detail = (proc.stderr or proc.stdout or "").strip()[:800]
        if not detail:
            raise ProviderError(key="prov_err_exit_code", tool="cursor-agent",
                                code=proc.returncode)
        raise ProviderError(detail)
    return proc.stdout.strip()


def call_codex(prompt: str, model: str, timeout: int) -> str:
    """Codex CLI без интерактива.

    `codex exec -` читает задание из stdin и печатает в stdout только последний
    ответ агента — ровно то, что нам нужно; ход работы уходит в stderr и нас не
    касается. Песочница по умолчанию только на чтение, так что запуск без
    человека ничего на диске не тронет.

    «auto» означает «не называть модель»: тогда Codex берёт настроенную у себя.
    Имена моделей у него меняются быстрее наших версий, и это единственный
    выбор, который не устареет.
    """
    exe = resolve_bin("codex")
    if not exe:
        raise ProviderError(key="prov_err_no_codex")
    cmd = [exe, "exec"]
    if model and model != "auto":
        cmd += ["--model", model]
    cmd.append("-")
    proc = subprocess.run(cmd, input=prompt, capture_output=True, text=True, encoding="utf-8", errors="replace",
                          cwd=work_dir(), env=login_env(),
                          timeout=timeout, close_fds=False)
    if proc.returncode != 0:
        detail = (proc.stderr or proc.stdout or "").strip()[:800]
        if not detail:
            raise ProviderError(key="prov_err_exit_code", tool="codex",
                                code=proc.returncode)
        raise ProviderError(detail)
    return proc.stdout.strip()


def _run_cli(cmd: list, prompt: str, timeout: int, tool: str) -> str:
    """Запускает командную строку, отдавая задание через stdin.

    Через stdin, а не аргументом, — и это не придирка: наши запросы бывают в
    тысячи знаков, с переносами, кавычками и кириллицей. Длина командной строки
    ограничена (на Linux около двух мегабайт), и как раз на таких запросах она и
    кончается. Отсюда же выбор самих программ: те, что умеют читать только
    аргумент, сюда не годятся.
    """
    proc = subprocess.run(cmd, input=prompt, capture_output=True, text=True, encoding="utf-8", errors="replace",
                          cwd=work_dir(), env=login_env(),
                          timeout=timeout, close_fds=False)
    if proc.returncode != 0:
        detail = (proc.stderr or proc.stdout or "").strip()[:800]
        if not detail:
            raise ProviderError(key="prov_err_exit_code", tool=tool, code=proc.returncode)
        raise ProviderError(detail)
    return proc.stdout.strip()


def call_copilot(prompt: str, model: str, timeout: int) -> str:
    """GitHub Copilot CLI по подписке, которая у многих уже есть.

    Задание идёт трубой, а не через -p: в документации прямо сказано, что при
    -p поданное на вход просто игнорируется. -s убирает украшения и оставляет в
    выводе один ответ.
    """
    exe = resolve_bin("copilot")
    if not exe:
        raise ProviderError(key="prov_err_no_copilot")
    cmd = [exe, "-s", "--no-ask-user", "--allow-all-tools"]
    if model and model != "auto":
        cmd += [f"--model={model}"]
    return _run_cli(cmd, prompt, timeout, "copilot")


def call_goose(prompt: str, model: str, timeout: int) -> str:
    """Goose — из всех проверенных лучше всего ложится на нашу задачу.

    `-i -` читает задание из stdin, `-q` печатает только ответ модели,
    `--no-session` не оставляет за собой файлов сеанса: нам нужен один вопрос и
    один ответ, а не переписка.
    """
    exe = resolve_bin("goose")
    if not exe:
        raise ProviderError(key="prov_err_no_goose")
    cmd = [exe, "run", "--no-session", "-q", "-i", "-"]
    if model and model != "auto":
        cmd += ["--model", model]
    return _run_cli(cmd, prompt, timeout, "goose")


def call_qwen(prompt: str, model: str, timeout: int) -> str:
    """Qwen Code. Задание через трубу, ответ обычным текстом."""
    exe = resolve_bin("qwen")
    if not exe:
        raise ProviderError(key="prov_err_no_qwen")
    cmd = [exe, "--yolo", "--output-format", "text"]
    if model and model != "auto":
        cmd += ["--model", model]
    return _run_cli(cmd, prompt, timeout, "qwen")


def call_openai_api(prompt: str, cfg_llm: dict, timeout: int) -> str:
    """Любая служба, говорящая на языке OpenAI: /chat/completions.

    Одна эта веточка накрывает столько же, сколько все командные строки вместе:
    OpenRouter с сотнями моделей, LM Studio и llama.cpp на своём компьютере,
    vLLM, корпоративный шлюз, сам OpenAI. Добавлять их по одной пришлось бы
    бесконечно, а протокол у них общий.

    Ключ уходит в заголовке, а не в адресе: адрес попадает в текст исключения
    при обрыве связи, а оттуда — в журнал прогона, который человек показывает
    другим. На токене Telegram мы это уже проходили.
    """
    base = (cfg_llm.get("api_base") or "").strip().rstrip("/")
    key = (cfg_llm.get("api_key") or "").strip()
    model = (cfg_llm.get("api_model") or "").strip()
    if not base:
        raise ProviderError(key="prov_err_no_api_base")
    if not model:
        raise ProviderError(key="prov_err_no_api_model")
    headers = {"Content-Type": "application/json"}
    if key:                      # у местных служб ключа может не быть вовсе
        # В заголовок запроса нельзя положить что угодно: там латиница и только
        # она. Ключ этого не нарушает — нарушает то, что скопировалось вместе с
        # ним. Проверяем заранее, потому что иначе requests падает
        # UnicodeEncodeError, а это ValueError, и человек получал «служба
        # ответила не в формате JSON» — про службу, до которой не дошло.
        try:
            key.encode("latin-1")
        except UnicodeEncodeError as e:
            raise ProviderError(key="prov_err_api_bad_key") from e
        headers["Authorization"] = f"Bearer {key}"
    try:
        r = requests.post(f"{base}/chat/completions", headers=headers, timeout=timeout,
                          json={"model": model, "stream": False,
                                "messages": [{"role": "user", "content": prompt}]})
    except requests.RequestException as e:
        raise ProviderError(key="prov_err_api_unreachable",
                            error=_without_key(str(e), key)) from e
    if r.status_code >= 400:
        # Служба ответила — и в ответе сказала, что не так. Раньше отсюда шло
        # «служба недоступна» с голым «429 Client Error»: человек с кончившимися
        # деньгами на OpenRouter читал, что до службы не достучаться, хотя она
        # ответила исправно и назвала причину. Причину и показываем.
        raise ProviderError(_without_key(_api_error_text(r), key))
    try:
        data = r.json()
    except ValueError as e:
        raise ProviderError(key="prov_err_api_not_json", error=e) from e
    try:
        return str(data["choices"][0]["message"]["content"]).strip()
    except (KeyError, IndexError, TypeError) as e:
        # у службы может быть своя форма ответа или своя ошибка в теле
        detail = str(data.get("error") or data)[:300] if isinstance(data, dict) else str(data)[:300]
        raise ProviderError(_without_key(detail, key)) from e


def _api_error_text(r) -> str:
    """Что служба сказала об отказе, её же словами.

    Форма ответа у всех разная: у OpenAI и OpenRouter — {"error": {"message": …}},
    у иных просто {"error": "…"} или вовсе текст. Берём то, что нашлось, а если
    не нашлось ничего — хотя бы номер отказа: он всё равно понятнее пустоты.
    """
    try:
        тело = r.json()
    except ValueError:
        тело = None
    сообщение = ""
    if isinstance(тело, dict):
        ошибка = тело.get("error", тело.get("message", ""))
        if isinstance(ошибка, dict):
            сообщение = str(ошибка.get("message") or ошибка)
        elif ошибка:
            сообщение = str(ошибка)
    if not сообщение:
        сообщение = (r.text or "").strip()[:300]
    return f"{r.status_code}: {сообщение}" if сообщение else f"{r.status_code}"


def _without_key(text: str, key: str) -> str:
    """Ключ не должен доехать до журнала, даже если служба вернула его в ошибке."""
    return text.replace(key, "***") if key else text


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
         allowed_tools=None, claude_bin: str = "claude", llm: dict = None) -> str:
    """The single place a model is called. allowed_tools matters only to Claude Code CLI.

    llm — весь блок настроек модели. Нужен своему адресу: у него, в отличие от
    командных строк, кроме имени модели есть ещё адрес и ключ.
    """
    if provider == "cursor_cli":
        return call_cursor(prompt, model, timeout)
    if provider == "codex_cli":
        return call_codex(prompt, model, timeout)
    if provider == "copilot_cli":
        return call_copilot(prompt, model, timeout)
    if provider == "goose_cli":
        return call_goose(prompt, model, timeout)
    if provider == "qwen_cli":
        return call_qwen(prompt, model, timeout)
    if provider == "openai_api":
        return call_openai_api(prompt, llm or {}, timeout)
    if provider == "ollama":
        return call_ollama(prompt, model, timeout)
    return call_claude(prompt, model, timeout, allowed_tools, claude_bin)


def supports_web_search(provider: str) -> bool:
    """Умеет ли ИСКАТЬ САМА программа-модель. Это только про её возможности."""
    return provider in ("", "claude_cli")


def web_search_possible(cfg: dict) -> bool:
    """Есть ли веб-поиск вообще — неважно, чьими силами.

    Либо ищет сама модель (так умеет только Claude Code), либо приложение своим
    ключом и отдаёт ей найденное текстом. Для человека разницы нет: разведка
    новых компаний и зарплатные вилки работают или не работают.
    """
    from . import websearch
    return supports_web_search(cfg.get("llm", {}).get("provider", "claude_cli")) \
        or websearch.configured(cfg)


# Downloading a model means gigabytes and minutes, so it runs in the background
# while the page polls for progress. Otherwise the app window would simply freeze
# until the download finished.
# status_key/error_key are our own steps and errors; they are translated on the
# way out. status carries Ollama's own messages ("pulling manifest") — there is
# nothing to translate those with, so they go through as they are.
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
    """Downloads a local model, reporting progress through log()."""
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
        # the commonest cause is that Ollama is not installed or not running,
        # and the technical text of the exception only frightens people here
        raise ProviderError(key="prov_err_ollama_down") from e
    except requests.RequestException as e:
        raise ProviderError(key="prov_err_pull_failed", error=e) from e


def delete_model(model: str) -> None:
    """Removes a downloaded model from the computer — several gigabytes back.

    Ollama renamed the field of this request from "name" to "model"; builds old
    enough to want the first are still about, so both go, and whichever one the
    running Ollama does not know it ignores.
    """
    try:
        r = requests.delete(f"{OLLAMA_URL}/api/delete",
                            json={"model": model, "name": model}, timeout=120)
        r.raise_for_status()
        forget_ollama()      # список скачанного только что изменился
    except requests.ConnectionError as e:
        raise ProviderError(key="prov_err_ollama_down") from e
    except requests.RequestException as e:
        raise ProviderError(key="prov_err_delete_failed", error=e) from e


def pull_async(model: str) -> None:
    """Starts the download in the background: the page polls pull_status()."""
    if pull_in_progress():
        return
    _set_pull(model=model, percent=0, status="", status_key="pull_starting",
              error="", error_key="", error_fmt={}, done=False)

    def worker():
        try:
            pull(model)
            forget_ollama()  # модель появилась — список скачанного устарел
            _set_pull(percent=100, status="", status_key="pull_done", done=True)
        except ProviderError as e:
            # the values become strings: this state leaves as JSON for the page,
            # and fmt may be holding an exception
            _set_pull(error=str(e) if not e.key else "", error_key=e.key,
                      error_fmt={k: str(v) for k, v in e.fmt.items()}, done=True)

    threading.Thread(target=worker, daemon=True).start()
