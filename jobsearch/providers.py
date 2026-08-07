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

from . import hardware, net, profiles

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

# Codex's model names change more often than our versions come out: gpt-5.4 gives
# way to the gpt-5.6 family. So "Auto" comes first and is the default — with it we
# do not name a model at all, and Codex takes the one configured on its own side.
# A stale name in the list will break a search; "Auto" will not.
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

# Goose and Qwen Code are not tied to a single model: which one is available is
# decided by the key the person gave them. Listing somebody else's line-up here
# would be dishonest — "Auto" means "do not name a model", and then whatever the
# program itself is set up with gets used.
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

# And node version managers keep what npm installed inside their own tree, one
# directory per node version, adding it to PATH from .zshrc. Someone who ran
# "npm install -g @anthropic-ai/claude-code" under nvm has claude in none of the
# directories above — and .zshrc is not read by the shell we ask below either.
# Patterns rather than paths: the version in the middle changes by itself.
_MANAGER_GLOBS = [
    ".nvm/versions/node/*/bin",                                          # nvm
    "Library/Application Support/fnm/node-versions/*/installation/bin",  # fnm
    ".volta/bin",                                                        # Volta
    ".bun/bin",                                                          # bun
    ".npm-global/bin", ".npm-packages/bin",   # npm prefix set by hand
    ".asdf/shims",                            # asdf
    ".local/share/mise/shims",                # mise
]


def _search_dirs():
    """Where to look besides PATH.

    The globs are expanded on every call rather than once at import: a person
    who installs node while the app is open should not have to restart it.
    """
    yield from _EXTRA_DIRS
    home = Path.home()
    for pattern in _MANAGER_GLOBS:
        try:
            # Reversed so that of several node versions the newer is tried
            # first. Sorting is by name, so it only mostly agrees with the
            # order of the versions — but any of them with the CLI inside will
            # do, and the loop goes on until one has it.
            yield from sorted(home.glob(pattern), reverse=True)
        except OSError:            # an unreadable directory is not a reason to stop
            continue


def _ask_login_shell(command: str) -> str:
    """Runs a command in this person's shell and returns what it printed.

    Interactive (-i) as well as login (-l). zsh reads .zshrc only for an
    interactive shell, and .zshrc is exactly where nvm sets itself up and where
    everyone is told to write export PATH. Without -i a CLI installed by npm
    under nvm is invisible: it is in neither PATH, nor the directories above,
    nor the answer of a quiet shell.

    An interactive shell is also the riskier one: someone's .zshrc waits for a
    keypress or takes its time. So stdin is closed and the wait is bounded, and
    if it still goes badly we ask again the quiet way — an answer without .zshrc
    beats no answer at all.
    """
    shell = os.environ.get("SHELL") or "/bin/zsh"
    if not os.path.exists(shell):
        return ""
    for flags in ("-ilc", "-lc"):
        try:
            # The encoding is given explicitly. Without it the system encoding is
            # used — cp1252 on Windows, ascii under the C locale — and the first
            # non-Latin letter in somebody's environment variable wrecks the
            # parsing. And there is one for everyone whose name is not in Latin
            # script: it sits in HOME and in PATH. What it failed with was not a
            # comprehensible exception but an AttributeError: the error happened
            # in the reading thread, and stdout silently came back None. And every
            # command-line tool stopped working at once.
            out = subprocess.run([shell, flags, command], capture_output=True,
                                 text=True, encoding="utf-8", errors="replace",
                                 timeout=10, cwd=work_dir(),
                                 stdin=subprocess.DEVNULL)
        except subprocess.TimeoutExpired:
            continue                       # .zshrc hung — try without it
        except (OSError, subprocess.SubprocessError):
            return ""
        if out.stdout:
            return out.stdout
    return ""


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
    for pair in _ask_login_shell("env -0").split("\0"):
        key, sep, value = pair.partition("=")
        # A talkative .zshrc prints its own before env gets to run, and that
        # lands glued to the very first variable. It ends with a newline, and
        # the name of a variable never contains one — so the glue cuts here.
        key = key.rpartition("\n")[2]
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
    for d in _search_dirs():
        candidate = d / name
        if candidate.exists() and os.access(candidate, os.X_OK):
            return str(candidate)
    # last chance: the PATH as this person's terminal sees it — with .zshrc and
    # everything it sets up. Taken from login_env rather than by asking the
    # shell here: the setup page checks seven CLIs at once, and seven
    # conversations with a shell that loads oh-my-zsh would be felt. login_env
    # is asked once and remembered; forget_binaries() forgets it along with us,
    # so "install it and press again" still works.
    path = login_env().get("PATH", "")
    if path:
        found = shutil.which(name, path=path)
        if found and os.access(found, os.X_OK):
            return found
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
_TAGS_TIMEOUT = (0.4, 3)          # (how long we wait to connect, how long for the answer)


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


# --- What the programs themselves need, besides themselves -------------------
#
# Claude Code, Cursor, Goose and Ollama come with their own installers and need
# nothing. Copilot and Qwen install only through npm, and npm comes with Node.js,
# which an ordinary computer does not have. The card used to say "npm install -g
# @github/copilot" — the person went to a terminal and got "npm: command not
# found", and by then they were outside our program, where we can do nothing for
# them. What is missing has to be said here, before they leave.
#
# Codex also installs through npm, but it has a ready binary too, and its install
# page offers both roads: requiring Node.js for it would mean driving a person to
# install something they can do perfectly well without.
PREREQS = {
    "copilot_cli": "node",
    "qwen_cli": "node",
}

TOOLS = {
    "node": {
        "bin": "node",
        "version_arg": "-v",
        # The npm packages of both CLIs require Node.js 22. With an old Node the
        # install does not fail at once; it fails later and inarticulately.
        "min_major": 22,
        "url": "https://nodejs.org/en/download",
    },
}


def _major(text: str) -> int:
    """The major version out of what a program said about itself: "v22.14.0" → 22."""
    import re
    m = re.search(r"(\d+)", text or "")
    return int(m.group(1)) if m else 0


@lru_cache(maxsize=8)
def tool_state(tool: str) -> tuple:
    """(is it there, what it said about its version). A tuple, so it fits lru_cache.

    If the version did not parse, we count it as good enough: barring a person's
    way because we failed to read somebody else's output format would be the
    last thing needed.
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
    """What is missing before this provider can be installed at all. Empty — nothing.

    We ask only about what is not installed yet: if Copilot is already there, how
    it got there is none of our business, and sending the person off for Node.js
    is both too late and pointless.
    """
    tool = PREREQS.get(provider, "")
    if not tool or tool_state(tool)[0]:
        return ""
    return tool


def tool_info(tool: str) -> dict:
    """Everything the page needs to know about a tool."""
    ready, version = tool_state(tool)
    spec = TOOLS.get(tool, {})
    return {"key": tool, "ready": ready, "version": version,
            "url": spec.get("url", ""), "min_major": spec.get("min_major", 0),
            # Found but old is not the same as not found: telling someone who has
            # Node.js to "install Node.js" sends them round in a circle.
            # Different troubles, different words.
            "too_old": bool(version) and not ready}


@lru_cache(maxsize=4)
def claude_logged_in(claude_bin: str = "claude") -> bool:
    """Whether anyone is signed in to Claude Code.

    A provider's readiness used to be checked by one thing: whether the program
    is on the disk. On the "Model" page it stood there green, the person picked
    it, started a search — and the run broke off after five seconds with "Not
    logged in · Please run /login". There was no way to know beforehand.

    We ask the program itself: it answers with JSON carrying a loggedIn field. If
    it did not answer, or answered unintelligibly, we count it as signed in:
    barring the way because we failed to read somebody else's output is worse
    than letting through.
    """
    exe = resolve_bin(claude_bin or "claude")
    if not exe:
        return False
    try:
        out = subprocess.run([exe, "auth", "status"], capture_output=True, text=True,
                             encoding="utf-8", errors="replace", timeout=15,
                             cwd=work_dir(), env=login_env())
        data = json.loads(out.stdout or "{}")
    except (OSError, subprocess.SubprocessError, ValueError):
        return True
    return bool(data.get("loggedIn", True))


def forget_binaries() -> None:
    """Forget the paths found: the person may have installed the program just now."""
    resolve_bin.cache_clear()
    # And the environment along with them: resolve_bin looks for the program in
    # the PATH that came from there. An installer had just added a directory to
    # it, and remembering the PATH from before the install would mean answering
    # "still not visible" to someone who has just done everything right.
    login_env.cache_clear()
    tool_state.cache_clear()
    claude_logged_in.cache_clear()
    forget_ollama()


# How to install a CLI — as a command, not as a retelling.
#
# This came from a person: "it does not see claude code on my mac". We were
# sending them to claude.com/claude-code, and the first thing offered there is
# the desktop application — so that is what they installed. That application is
# beside the point: what we need is the command line, the claude program in a
# terminal. One line you can see and copy answers this better than any
# description.
#
# The commands do not depend on language and therefore live here rather than in
# the translations: the curl line is the same in all fourteen, and keeping it in
# fourteen places means fixing it in thirteen one day.
#
# Taken from each one's official documentation and checked against it. What could
# not be verified is not here: an empty cell is more honest than an invented
# command, and a curl … | bash line pointed at the wrong address is no longer a
# typo.
_INSTALL_CMD = {
    "claude_cli": {"posix": "curl -fsSL https://claude.ai/install.sh | bash",
                   "windows": "irm https://claude.ai/install.ps1 | iex"},
    "cursor_cli": {"posix": "curl https://cursor.com/install -fsS | bash",
                   "windows": "irm 'https://cursor.com/install?win32=true' | iex"},
    "codex_cli": {"posix": "curl -fsSL https://chatgpt.com/codex/install.sh | sh",
                  "windows": "npm install -g @openai/codex"},
    "copilot_cli": {"posix": "npm install -g @github/copilot",
                    "windows": "npm install -g @github/copilot"},
    "qwen_cli": {"posix": "npm install -g @qwen-code/qwen-code",
                 "windows": "npm install -g @qwen-code/qwen-code"},
}

# How to check that it installed. The same question every "it does not find it"
# conversation begins with: let the person ask it of themselves, before we do.
_VERIFY_CMD = {
    "claude_cli": "claude --version",
    "cursor_cli": "cursor-agent --version",
    "codex_cli": "codex --version",
    "copilot_cli": "copilot --version",
    "goose_cli": "goose --version",
    "qwen_cli": "qwen --version",
}


def install_cmd(key: str) -> str:
    """The install command for the system the application is actually running on.

    There is no point showing all three at once: the person is sitting at one
    machine, and the other two only give them a choice they did not ask for.
    """
    pair = _INSTALL_CMD.get(key)
    if not pair:
        return ""
    return pair["windows" if os.name == "nt" else "posix"]


def verify_cmd(key: str) -> str:
    """How a person can check the program installed. Empty for those with nothing
    to check: Ollama and your own endpoint have no command line at all."""
    return _VERIFY_CMD.get(key, "")


def available(claude_bin: str = "claude", llm: dict = None) -> dict:
    """Which providers are actually ready to work on this machine.

    Names and hints are not kept here: they live in the translations under the
    keys prov_<code> and prov_<code>_hint — otherwise the provider's name would
    arrive on the page in Russian in the middle of an English sentence.

    llm — the model settings. Needed by one provider only: your own endpoint has
    nothing to look for on the disk, it is ready exactly when the address is
    filled in.
    """
    llm = llm or {}
    provs = {
        "claude_cli": {
            "ready": _bin_exists(claude_bin or "claude"),
            # Installed does not yet mean ready: without a sign-in it answers
            # "Not logged in" and the run breaks off at the very first question.
            "needs_login": (_bin_exists(claude_bin or "claude")
                            and not claude_logged_in(claude_bin or "claude")),
            "web_search": True, "kind": "cloud",
            "install_url": "https://claude.com/claude-code",
        },
        "cursor_cli": {
            "ready": _bin_exists("cursor-agent"),
            "web_search": False, "kind": "cloud",
            "install_url": "https://cursor.com/cli",
        },
        "codex_cli": {
            # Codex can reach the network, but by default it is locked in a
            # read-only sandbox — which is exactly what we want from a run with
            # nobody watching. Scouting for new companies is skipped with it:
            # promising a web search that may not be there is worse than not
            # promising one.
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
        # Last — it is for people who know what they want: fields instead of a
        # button, and first in the list it would confuse everyone whom any of the
        # ordinary options above would suit.
        "openai_api": {
            # There is nothing to install: this is not a program but an address.
            # Hence "ready" always — it can be picked straight away, and it gets
            # configured at the second step, where there is width for the fields.
            # They did not fit in the narrow card: "Address" and "Key" lined up
            # side by side and were cut off halfway.
            "ready": True,
            "web_search": False, "kind": "custom",
            "install_url": "",
        },
    }
    # The commands are attached to everyone at once rather than written into each
    # card by hand: that way a new provider will not be left without them, and the
    # existing ones will not drift apart.
    for key, p in provs.items():
        p["install_cmd"] = install_cmd(key)
        p["verify_cmd"] = verify_cmd(key)
    return provs


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
        # The address and the model are asked for together, at the second step:
        # without either of them there is nothing to think with, and splitting
        # them across two screens would be pedantry.
        both = (llm.get("api_base") or "").strip() and (llm.get("api_model") or "").strip()
        return "" if both else "model"
    if provider == "ollama":
        model = llm.get("triage_model") or ""
        if not model or model not in ollama_installed_models():
            return "model"
    return ""


def current_model(llm: dict) -> str:
    """What we are thinking with right now, named the way the provider names it.

    For everyone except your own endpoint, the model sits in triage_model. For
    your own endpoint it is in api_model, because the name comes from somebody
    else's service and does not fit our catalogue. Until the two were told apart,
    the "Own endpoint" card said "in use: haiku": it showed triage_model, left
    over from Claude Code — the name of a model that address has nothing
    whatsoever to do with.
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
    """The Codex CLI with no interaction.

    `codex exec -` reads the task from stdin and prints only the agent's final
    answer to stdout — exactly what we need; the working-out goes to stderr and is
    none of our concern. The sandbox is read-only by default, so a run with nobody
    watching touches nothing on the disk.

    "auto" means "do not name a model": then Codex takes the one configured on its
    own side. Its model names change faster than our versions do, and this is the
    one choice that will not go stale.
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
    """Runs a command-line tool, handing it the task through stdin.

    Through stdin rather than as an argument, and that is not pedantry: our
    prompts run to thousands of characters, with newlines, quotes and Cyrillic in
    them. The length of a command line is limited (about two megabytes on Linux),
    and prompts like these are exactly where it runs out. The same reason governs
    the choice of programs: those that can only read an argument will not do here.
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
    """The GitHub Copilot CLI, on a subscription many people already have.

    The task goes through a pipe rather than through -p: the documentation says
    plainly that with -p whatever is fed to stdin is simply ignored. -s strips the
    decoration and leaves one answer in the output.
    """
    exe = resolve_bin("copilot")
    if not exe:
        raise ProviderError(key="prov_err_no_copilot")
    cmd = [exe, "-s", "--no-ask-user", "--allow-all-tools"]
    if model and model != "auto":
        cmd += [f"--model={model}"]
    return _run_cli(cmd, prompt, timeout, "copilot")


def call_goose(prompt: str, model: str, timeout: int) -> str:
    """Goose — of everything tried, this fits our task best.

    `-i -` reads the task from stdin, `-q` prints only the model's answer, and
    `--no-session` leaves no session files behind: we want one question and one
    answer, not a correspondence.
    """
    exe = resolve_bin("goose")
    if not exe:
        raise ProviderError(key="prov_err_no_goose")
    cmd = [exe, "run", "--no-session", "-q", "-i", "-"]
    if model and model != "auto":
        cmd += ["--model", model]
    return _run_cli(cmd, prompt, timeout, "goose")


def call_qwen(prompt: str, model: str, timeout: int) -> str:
    """Qwen Code. The task through a pipe, the answer as plain text."""
    exe = resolve_bin("qwen")
    if not exe:
        raise ProviderError(key="prov_err_no_qwen")
    cmd = [exe, "--yolo", "--output-format", "text"]
    if model and model != "auto":
        cmd += ["--model", model]
    return _run_cli(cmd, prompt, timeout, "qwen")


def call_openai_api(prompt: str, cfg_llm: dict, timeout: int) -> str:
    """Any service that speaks OpenAI's language: /chat/completions.

    This one branch covers as much as all the command-line tools together:
    OpenRouter with its hundreds of models, LM Studio and llama.cpp on your own
    computer, vLLM, a corporate gateway, OpenAI itself. Adding them one at a time
    would never end, and the protocol they share is one.

    The key goes in a header rather than in the address: the address ends up in
    the text of the exception when a connection drops, and from there in the run
    log, which people show to others. We have already been through this with the
    Telegram token.
    """
    base = (cfg_llm.get("api_base") or "").strip().rstrip("/")
    key = (cfg_llm.get("api_key") or "").strip()
    model = (cfg_llm.get("api_model") or "").strip()
    if not base:
        raise ProviderError(key="prov_err_no_api_base")
    if not model:
        raise ProviderError(key="prov_err_no_api_model")
    headers = {"Content-Type": "application/json"}
    if key:                      # a local service may have no key at all
        # You cannot put just anything into a request header: it is Latin script
        # and nothing else. The key does not break that — what was copied along
        # with it does. We check in advance, because otherwise requests fails with
        # a UnicodeEncodeError, which is a ValueError, and the person got "the
        # service did not answer in JSON" — about a service the request never
        # reached.
        try:
            key.encode("latin-1")
        except UnicodeEncodeError as e:
            raise ProviderError(key="prov_err_api_bad_key") from e
        headers["Authorization"] = f"Bearer {key}"
    try:
        r = net.post(f"{base}/chat/completions", headers=headers, timeout=timeout,
                          json={"model": model, "stream": False,
                                "messages": [{"role": "user", "content": prompt}]})
    except requests.RequestException as e:
        raise ProviderError(key="prov_err_api_unreachable",
                            error=_without_key(str(e), key)) from e
    if r.status_code >= 400:
        # The service answered — and in its answer said what was wrong. This used
        # to produce "the service is unreachable" with a bare "429 Client Error":
        # a person out of credit on OpenRouter read that the service could not be
        # reached, when it had answered properly and named the reason. So we show
        # the reason.
        raise ProviderError(_without_key(_api_error_text(r), key))
    try:
        data = r.json()
    except ValueError as e:
        raise ProviderError(key="prov_err_api_not_json", error=e) from e
    try:
        return str(data["choices"][0]["message"]["content"]).strip()
    except (KeyError, IndexError, TypeError) as e:
        # a service may have its own answer shape, or its own error in the body
        detail = str(data.get("error") or data)[:300] if isinstance(data, dict) else str(data)[:300]
        raise ProviderError(_without_key(detail, key)) from e


def _api_error_text(r) -> str:
    """What the service said about the refusal, in its own words.

    The answer's shape differs for everyone: OpenAI and OpenRouter use
    {"error": {"message": …}}, others simply {"error": "…"} or plain text. We take
    whatever was found, and if nothing was — at least the status code: even that
    is clearer than emptiness.
    """
    try:
        body = r.json()
    except ValueError:
        body = None
    message = ""
    if isinstance(body, dict):
        error = body.get("error", body.get("message", ""))
        if isinstance(error, dict):
            message = str(error.get("message") or error)
        elif error:
            message = str(error)
    if not message:
        message = (r.text or "").strip()[:300]
    return f"{r.status_code}: {message}" if message else f"{r.status_code}"


def _without_key(text: str, key: str) -> str:
    """The key must not reach the log, even if the service echoed it in an error."""
    return text.replace(key, "***") if key else text


def call_ollama(prompt: str, model: str, timeout: int, schema: dict = None) -> str:
    """A call to the local model.

    schema — the shape of the answer. Ollama can hold its output to it, and for a
    small model that is no luxury: it answers with fluent prose and no required
    field over and over, leaving us nothing to parse. With a schema the field is
    always there.

    temperature 0, because nothing is being invented here, only judged: the same
    question must give the same answer. num_ctx is set explicitly: Ollama has a
    default window of its own, and it is smaller than what the model itself holds.
    """
    body = {"model": model, "prompt": prompt, "stream": False,
            "options": {"temperature": 0, "num_ctx": 8192}}
    if schema:
        body["format"] = schema
    try:
        r = requests.post(f"{OLLAMA_URL}/api/generate", json=body, timeout=timeout)
        # A 404 from Ollama means "no such model", not "I am not here". We used to
        # write "Ollama is unreachable" for it, and the person went off to check
        # whether it was running — and it had been running all along. You can end
        # up here by leaving the previous provider's model name in the settings:
        # Ollama was being asked for "haiku", and all twenty-five batches in a row
        # came back refused.
        if r.status_code == 404:
            raise ProviderError(key="prov_err_ollama_no_model", model=model)
        r.raise_for_status()
        return str(r.json().get("response", "")).strip()
    except requests.RequestException as e:
        raise ProviderError(key="prov_err_ollama_unreachable", error=e) from e
    except ValueError as e:
        raise ProviderError(key="prov_err_ollama_not_json", error=e) from e


def call(prompt: str, provider: str, model: str, timeout: int = 600,
         allowed_tools=None, claude_bin: str = "claude", llm: dict = None,
         schema: dict = None) -> str:
    """The single place a model is called. allowed_tools matters only to Claude Code CLI.

    llm — the whole block of model settings. Needed by your own endpoint: unlike
    the command-line tools, it has an address and a key besides the model's name.

    schema — the shape of the expected answer. So far only Ollama understands it,
    and it is the one that needs it most: without it a small model loses fields.
    There is nothing to pass to the others, and that is no loss — cloud models
    hold the format by themselves.
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
        return call_ollama(prompt, model, timeout, schema=schema)
    return call_claude(prompt, model, timeout, allowed_tools, claude_bin)


def supports_web_search(provider: str) -> bool:
    """Whether the model program can search ITSELF. This is about its own abilities only."""
    return provider in ("", "claude_cli")


def web_search_possible(cfg: dict) -> bool:
    """Whether there is a web search at all — never mind by whose hands.

    Either the model searches (only Claude Code can), or the application does with
    its own key and hands it the results as text. To a person there is no
    difference: scouting for new companies and salary ranges either work or they
    do not.
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
        forget_ollama()      # the list of downloaded models just changed
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
            forget_ollama()  # a model appeared — the downloaded list is stale
            _set_pull(percent=100, status="", status_key="pull_done", done=True)
        except ProviderError as e:
            # the values become strings: this state leaves as JSON for the page,
            # and fmt may be holding an exception
            _set_pull(error=str(e) if not e.key else "", error_key=e.key,
                      error_fmt={k: str(v) for k, v in e.fmt.items()}, done=True)

    threading.Thread(target=worker, daemon=True).start()
