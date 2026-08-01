"""Calling Claude Code CLI in headless mode (claude -p)."""
import concurrent.futures as _cf
import contextvars
import json
import threading
import re
import shutil
import subprocess
import time


class ClaudeError(RuntimeError):
    """An error while calling the model.

    It may carry a translation key: the text lands in the run log, and a person
    reads that log — so it cannot always be in Russian. When the message came
    from the CLI itself there is no key and the text goes through as it is.

    transient is set at the point of raising rather than guessed from the text:
    the decision "should we call again" used to be made by looking for Russian
    words in the message, and translating it would have stopped that working.
    """

    def __init__(self, message: str = "", key: str = "", transient: bool = False, **fmt):
        self.key, self.fmt, self.transient = key, fmt, transient
        super().__init__(message or key)


class AuthError(ClaudeError):
    """The model is out of reach until the person signs in. Retrying is pointless."""


_AUTH_MARKERS = ("not logged in", "/login", "invalid api key", "authentication_error",
                 "credit balance is too low", "please run /login")


def _human_error(raw: str) -> str:
    """From the CLI's answer, a sentence for a person rather than the whole JSON.

    On an error claude prints the result as that same JSON, and an 800-character
    sheet used to fly into the run log with the message lost inside it.
    """
    text = (raw or "").strip()
    if text.startswith("{"):
        try:
            data = json.loads(text)
            text = str(data.get("result") or data.get("error") or text)
        except json.JSONDecodeError:
            pass
    return text[:300]


def _classify(text: str) -> ClaudeError:
    low = text.lower()
    return AuthError(text) if any(m in low for m in _AUTH_MARKERS) else ClaudeError(text)


class Cancelled(RuntimeError):
    """The work was cancelled by the user."""

    def __init__(self, key: str = "err_cancelled", **fmt):
        self.key, self.fmt = key, fmt
        super().__init__(key)


# A long stage (triaging hundreds of jobs) has to stop quickly, so pmap checks
# this flag before every task. CLI calls already under way are seen through —
# there is no sense in cutting them off mid-sentence.
_cancel = threading.Event()
# "Stop" used to kill every call to the model in the process: while a run was
# stopping, the coverage check and CV generation failed silently. Cancellation
# now applies only to the run's own threads — the pipeline marks them, and pmap
# copies the context into the worker threads.
_in_run = contextvars.ContextVar("in_pipeline_run", default=False)


def mark_run_thread() -> None:
    """Mark the current thread as belonging to a run."""
    _in_run.set(True)


def _cancelled() -> bool:
    return _cancel.is_set() and _in_run.get()


def set_cancel() -> None:
    _cancel.set()


def clear_cancel() -> None:
    _cancel.clear()


def cancelled() -> bool:
    return _cancel.is_set()


# Signs of temporary trouble in the CLI's own output — they show a call is worth
# repeating. Our own errors are not listed here: they carry the transient flag,
# because their text is translated and looking for words in it makes no sense.
_TRANSIENT = (
    "connection closed", "api error", "overloaded", "rate limit", "429", "529",
    "timeout", "timed out", "closed mid-response", "internal server", "5xx",
)


def _is_transient(exc) -> bool:
    if getattr(exc, "transient", False):
        return True
    return any(t in str(exc).lower() for t in _TRANSIENT)


def pmap(fn, items: list, workers: int = 5) -> list:
    """Applies fn to every item in parallel, keeping the order.
    An exception does not bring the pool down — it is returned as the exception."""
    items = list(items)
    if not items:
        return []
    workers = max(1, min(workers, len(items)))
    if workers == 1:
        out = []
        for it in items:
            if _cancelled():
                out.append(Cancelled())
                continue
            try:
                out.append(fn(it))
            except Exception as e:  # noqa: BLE001
                out.append(e)
        return out
    # copy the contextvars into every worker: without this the threads lose the
    # active profile (profiles._active) and write into the default profile's database
    def _guarded(it):
        if _cancelled():
            raise Cancelled()
        return fn(it)

    with _cf.ThreadPoolExecutor(max_workers=workers) as ex:
        futures = [ex.submit(contextvars.copy_context().run, _guarded, it) for it in items]
        out = []
        for f in futures:
            try:
                out.append(f.result())
            except Exception as e:  # noqa: BLE001
                out.append(e)
        return out


def _ask_once(prompt: str, model: str, claude_bin: str, timeout: int, allowed_tools,
              provider: str = "claude_cli", llm: dict = None) -> str:
    from . import providers
    if provider and provider != "claude_cli":
        try:
            return providers.call(prompt, provider, model, timeout, allowed_tools,
                                  claude_bin, llm)
        except providers.ProviderError as e:
            # the translation key has to be carried on, or its name lands in the log
            raise ClaudeError("" if e.key else str(e), key=e.key, **e.fmt) from e

    # An absolute path plus close_fds=False make CPython use posix_spawn instead
    # of fork+exec. That is critical on macOS: fork() from a multi-threaded
    # process that has already touched the system networking frameworks kills the
    # child with SIGSEGV in the atfork handlers (Network.framework) before exec
    # even happens — claude "exits with code -11 and no output". posix_spawn
    # sidesteps fork entirely.
    from . import providers as _p
    exe = _p.resolve_bin(claude_bin or "claude") or (claude_bin or "claude")
    cmd = [exe, "-p", "--output-format", "json"]
    if model:
        cmd += ["--model", model]
    if allowed_tools:
        cmd += ["--allowedTools", ",".join(allowed_tools)]
    try:
        # cwd is an empty service directory: otherwise the CLI looks around the
        # place it was started from, and macOS begins asking for access to the
        # person's folders.
        # encoding is set explicitly: without it text=True takes the system
        # encoding, which on Windows is cp1252, and a Russian prompt does not
        # survive even its first two letters ("Ты — ассистент…" → charmap can't
        # encode position 0-1).
        proc = subprocess.run(cmd, input=prompt, capture_output=True, text=True,
                              encoding="utf-8", errors="replace",
                              timeout=timeout, close_fds=False, cwd=_p.work_dir())
    except FileNotFoundError:
        raise ClaudeError(key="prov_err_no_claude", path=repr(claude_bin))
    except subprocess.TimeoutExpired:
        raise ClaudeError(key="prov_err_timeout", transient=True, tool="claude",
                          seconds=timeout)
    if proc.returncode != 0:
        detail = (proc.stderr or proc.stdout or "").strip()
        if not detail:
            # empty output with a non-zero code; a negative code means killed by a
            # signal (by the system when memory runs short, for instance)
            key = "prov_err_killed" if proc.returncode < 0 else "prov_err_exit_code"
            raise ClaudeError(key=key, transient=True, tool="claude",
                              code=proc.returncode)
        raise _classify(_human_error(detail))
    try:
        data = json.loads(proc.stdout)
    except json.JSONDecodeError:
        return proc.stdout.strip()
    if isinstance(data, dict):
        if data.get("is_error"):
            raise _classify(_human_error(str(data.get("result", ""))))
        return str(data.get("result", "")).strip()
    return proc.stdout.strip()


def ask(prompt: str, model: str = "", claude_bin: str = "claude", timeout: int = 600,
        allowed_tools: list = None, retries: int = 2, provider: str = "claude_cli",
        llm: dict = None) -> str:
    """Calls claude, retrying on temporary errors (connection closed, overload, 429/5xx)."""
    # A break point: stages such as discovery make their calls in a plain loop
    # rather than through pmap, and without this check "Stop" would wait for the
    # whole stage to finish.
    if _cancelled():
        raise Cancelled("err_search_stopped")
    last = None
    for attempt in range(retries + 1):
        try:
            return _ask_once(prompt, model, claude_bin, timeout, allowed_tools, provider, llm)
        except AuthError:
            raise          # we cannot sign in for them — retries only waste time
        except ClaudeError as e:
            last = e
            if attempt < retries and _is_transient(e):
                time.sleep(2 * (attempt + 1))  # 2s, 4s
                continue
            raise
    raise last


def ask_json(prompt: str, model: str = "", claude_bin: str = "claude", timeout: int = 600,
             allowed_tools: list = None, retries: int = 2, provider: str = "claude_cli",
             llm: dict = None):
    """Like ask, but requires JSON back. If the model answered in prose, it retries."""
    last = None
    for attempt in range(retries + 1):
        # retries=0: we already have our own retries, and nested ones multiplied —
        # three attempts here times three inside ask meant up to nine calls to the
        # model. With a local model at ten minutes each, that looked like a hang.
        text = ask(prompt, model=model, claude_bin=claude_bin, timeout=timeout,
                   allowed_tools=allowed_tools, retries=0, provider=provider, llm=llm)
        try:
            return extract_json(text)
        except ClaudeError as e:
            last = e
            if attempt < retries:
                time.sleep(2 * (attempt + 1))
                continue
            raise
    raise last


def extract_json(text: str):
    """Pulls the first JSON object/array out of the answer (tolerating ``` wrappers and prose)."""
    fenced = re.search(r"```(?:json)?\s*(.*?)```", text, re.S)
    candidates = [fenced.group(1)] if fenced else []
    candidates.append(text)
    decoder = json.JSONDecoder()
    for cand in candidates:
        cand = cand.strip()
        for start in [m.start() for m in re.finditer(r"[\[{]", cand)]:
            try:
                obj, _ = decoder.raw_decode(cand[start:])
                return obj
            except json.JSONDecodeError:
                continue
    raise ClaudeError(key="prov_err_no_json", text=text[:300])
