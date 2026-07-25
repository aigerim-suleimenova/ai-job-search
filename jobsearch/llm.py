"""Вызовы Claude Code CLI в headless-режиме (claude -p)."""
import concurrent.futures as _cf
import contextvars
import json
import re
import shutil
import subprocess
import time


class ClaudeError(RuntimeError):
    pass


# временные ошибки, при которых имеет смысл повторить вызов
_TRANSIENT = (
    "connection closed", "api error", "overloaded", "rate limit", "429", "529",
    "timeout", "timed out", "closed mid-response", "internal server", "5xx",
    "не ответил за", "без вывода",
)


def _is_transient(msg: str) -> bool:
    m = (msg or "").lower()
    return any(t in m for t in _TRANSIENT)


def pmap(fn, items: list, workers: int = 5) -> list:
    """Параллельно применяет fn к каждому элементу, сохраняя порядок.
    Исключения не роняют весь пул — возвращаются как объект-исключение."""
    items = list(items)
    if not items:
        return []
    workers = max(1, min(workers, len(items)))
    if workers == 1:
        out = []
        for it in items:
            try:
                out.append(fn(it))
            except Exception as e:  # noqa: BLE001
                out.append(e)
        return out
    # копируем contextvars в каждый воркер: без этого потоки теряют активный
    # профиль (profiles._active) и пишут в базу профиля по умолчанию
    with _cf.ThreadPoolExecutor(max_workers=workers) as ex:
        futures = [ex.submit(contextvars.copy_context().run, fn, it) for it in items]
        out = []
        for f in futures:
            try:
                out.append(f.result())
            except Exception as e:  # noqa: BLE001
                out.append(e)
        return out


def _ask_once(prompt: str, model: str, claude_bin: str, timeout: int, allowed_tools) -> str:
    # Абсолютный путь + close_fds=False заставляют CPython использовать posix_spawn
    # вместо fork+exec. Это критично на macOS: fork() из многопоточного процесса,
    # уже трогавшего системные сетевые фреймворки, роняет ребёнка SIGSEGV в
    # atfork-обработчиках (Network.framework) ещё до exec — claude «завершается
    # с кодом -11 без вывода». posix_spawn обходит fork целиком.
    exe = shutil.which(claude_bin or "claude") or (claude_bin or "claude")
    cmd = [exe, "-p", "--output-format", "json"]
    if model:
        cmd += ["--model", model]
    if allowed_tools:
        cmd += ["--allowedTools", ",".join(allowed_tools)]
    try:
        proc = subprocess.run(cmd, input=prompt, capture_output=True, text=True,
                              timeout=timeout, close_fds=False)
    except FileNotFoundError:
        raise ClaudeError(f"claude CLI не найден: {claude_bin!r}. Укажите путь в настройках LLM.")
    except subprocess.TimeoutExpired:
        raise ClaudeError(f"claude не ответил за {timeout} с")
    if proc.returncode != 0:
        detail = (proc.stderr or proc.stdout or "").strip()
        if not detail:
            # пустой вывод при ненулевом коде; отрицательный код = убит сигналом
            # (например, системой при нехватке памяти)
            detail = (f"claude завершился с кодом {proc.returncode} без вывода"
                      + (" (убит сигналом)" if proc.returncode < 0 else ""))
        raise ClaudeError(detail[:800])
    try:
        data = json.loads(proc.stdout)
    except json.JSONDecodeError:
        return proc.stdout.strip()
    if isinstance(data, dict):
        if data.get("is_error"):
            raise ClaudeError(str(data.get("result", ""))[:800])
        return str(data.get("result", "")).strip()
    return proc.stdout.strip()


def ask(prompt: str, model: str = "", claude_bin: str = "claude", timeout: int = 600,
        allowed_tools: list = None, retries: int = 2) -> str:
    """Вызов claude с повтором при временных ошибках (connection closed, overload, 429/5xx)."""
    last = None
    for attempt in range(retries + 1):
        try:
            return _ask_once(prompt, model, claude_bin, timeout, allowed_tools)
        except ClaudeError as e:
            last = e
            if attempt < retries and _is_transient(str(e)):
                time.sleep(2 * (attempt + 1))  # 2с, 4с
                continue
            raise
    raise last


def ask_json(prompt: str, model: str = "", claude_bin: str = "claude", timeout: int = 600,
             allowed_tools: list = None, retries: int = 2):
    """Как ask, но требует JSON в ответе. Если модель вернула прозу — повторяет."""
    last = None
    for attempt in range(retries + 1):
        text = ask(prompt, model=model, claude_bin=claude_bin, timeout=timeout,
                   allowed_tools=allowed_tools, retries=retries)
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
    """Достаёт первый JSON-объект/массив из ответа модели (терпимо к ```-обёрткам и прозе)."""
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
    raise ClaudeError("В ответе модели нет JSON: " + text[:300])
