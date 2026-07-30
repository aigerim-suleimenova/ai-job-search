"""Вызовы Claude Code CLI в headless-режиме (claude -p)."""
import concurrent.futures as _cf
import contextvars
import json
import threading
import re
import shutil
import subprocess
import time


class ClaudeError(RuntimeError):
    pass


class AuthError(ClaudeError):
    """Модель недоступна, пока человек не войдёт в неё. Повторять бессмысленно."""


_AUTH_MARKERS = ("not logged in", "/login", "invalid api key", "authentication_error",
                 "credit balance is too low", "please run /login")


def _human_error(raw: str) -> str:
    """Из ответа CLI — фраза для человека, а не весь JSON.

    При ошибке claude печатает результат тем же JSON-ом, и в журнал прогона
    улетала простыня на 800 символов, в которой сообщение терялось.
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
    """Работа отменена пользователем."""


# Останавливать длинный этап (триаж сотен вакансий) нужно быстро, поэтому pmap
# сверяется с этим флагом перед каждой задачей. Уже идущие вызовы CLI доводим
# до конца — обрывать их на полуслове смысла нет.
_cancel = threading.Event()


def set_cancel() -> None:
    _cancel.set()


def clear_cancel() -> None:
    _cancel.clear()


def cancelled() -> bool:
    return _cancel.is_set()


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
            if _cancel.is_set():
                out.append(Cancelled("отменено"))
                continue
            try:
                out.append(fn(it))
            except Exception as e:  # noqa: BLE001
                out.append(e)
        return out
    # копируем contextvars в каждый воркер: без этого потоки теряют активный
    # профиль (profiles._active) и пишут в базу профиля по умолчанию
    def _guarded(it):
        if _cancel.is_set():
            raise Cancelled("отменено")
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
              provider: str = "claude_cli") -> str:
    from . import providers
    if provider and provider != "claude_cli":
        try:
            return providers.call(prompt, provider, model, timeout, allowed_tools, claude_bin)
        except providers.ProviderError as e:
            raise ClaudeError(str(e)) from e

    # Абсолютный путь + close_fds=False заставляют CPython использовать posix_spawn
    # вместо fork+exec. Это критично на macOS: fork() из многопоточного процесса,
    # уже трогавшего системные сетевые фреймворки, роняет ребёнка SIGSEGV в
    # atfork-обработчиках (Network.framework) ещё до exec — claude «завершается
    # с кодом -11 без вывода». posix_spawn обходит fork целиком.
    from . import providers as _p
    exe = _p.resolve_bin(claude_bin or "claude") or (claude_bin or "claude")
    cmd = [exe, "-p", "--output-format", "json"]
    if model:
        cmd += ["--model", model]
    if allowed_tools:
        cmd += ["--allowedTools", ",".join(allowed_tools)]
    try:
        # cwd — пустой служебный каталог: иначе CLI осматривает то место, где
        # его запустили, и macOS начинает спрашивать доступ к папкам человека
        proc = subprocess.run(cmd, input=prompt, capture_output=True, text=True,
                              timeout=timeout, close_fds=False, cwd=_p.work_dir())
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
        allowed_tools: list = None, retries: int = 2, provider: str = "claude_cli") -> str:
    """Вызов claude с повтором при временных ошибках (connection closed, overload, 429/5xx)."""
    # Точка прерывания: этапы вроде discovery делают вызовы подряд в цикле, не через
    # pmap, и без этой проверки «Стоп» ждал бы конца всего этапа.
    if _cancel.is_set():
        raise Cancelled("поиск остановлен")
    last = None
    for attempt in range(retries + 1):
        try:
            return _ask_once(prompt, model, claude_bin, timeout, allowed_tools, provider)
        except AuthError:
            raise          # войти за человека мы не можем — повторы только тратят время
        except ClaudeError as e:
            last = e
            if attempt < retries and _is_transient(str(e)):
                time.sleep(2 * (attempt + 1))  # 2с, 4с
                continue
            raise
    raise last


def ask_json(prompt: str, model: str = "", claude_bin: str = "claude", timeout: int = 600,
             allowed_tools: list = None, retries: int = 2, provider: str = "claude_cli"):
    """Как ask, но требует JSON в ответе. Если модель вернула прозу — повторяет."""
    last = None
    for attempt in range(retries + 1):
        text = ask(prompt, model=model, claude_bin=claude_bin, timeout=timeout,
                   allowed_tools=allowed_tools, retries=retries, provider=provider)
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
