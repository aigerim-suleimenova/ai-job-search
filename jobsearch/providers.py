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
    """Forget the paths found: the person may have installed the program just now."""
    resolve_bin.cache_clear()


def available(claude_bin: str = "claude") -> dict:
    """Which providers are actually ready to work on this machine.

    Names and hints are not kept here: they live in the translations under the
    keys prov_<code> and prov_<code>_hint — otherwise the provider's name would
    arrive on the page in Russian in the middle of an English sentence.
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
    """The single place a model is called. allowed_tools matters only to Claude Code CLI."""
    if provider == "cursor_cli":
        return call_cursor(prompt, model, timeout)
    if provider == "ollama":
        return call_ollama(prompt, model, timeout)
    return call_claude(prompt, model, timeout, allowed_tools, claude_bin)


def supports_web_search(provider: str) -> bool:
    return provider in ("", "claude_cli")


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


def pull_async(model: str) -> None:
    """Starts the download in the background: the page polls pull_status()."""
    if pull_in_progress():
        return
    _set_pull(model=model, percent=0, status="", status_key="pull_starting",
              error="", error_key="", error_fmt={}, done=False)

    def worker():
        try:
            pull(model)
            _set_pull(percent=100, status="", status_key="pull_done", done=True)
        except ProviderError as e:
            # the values become strings: this state leaves as JSON for the page,
            # and fmt may be holding an exception
            _set_pull(error=str(e) if not e.key else "", error_key=e.key,
                      error_fmt={k: str(v) for k, v in e.fmt.items()}, done=True)

    threading.Thread(target=worker, daemon=True).start()
