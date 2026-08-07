"""Sending the digest to Telegram."""
import html
import json

import requests

from . import i18n, net

TIMEOUT = 30


class NotifyError(RuntimeError):
    """A Telegram error. Carries a translation key: the module does not know the
    interface language, and the text is shown to a person — it used to be in
    Russian always."""

    def __init__(self, key: str, **fmt):
        self.key, self.fmt = key, fmt
        super().__init__(key)


def _api(token: str, method: str) -> str:
    return f"https://api.telegram.org/bot{token}/{method}"


def _without_token(text: str, token: str) -> str:
    """The same text, with the token taken out.

    Telegram wants the token right in the URL, and requests puts the URL into
    the text of its own exception. That exception then went into the run log —
    the one kept in the database and shown on the results page — and into
    errors.log. So the token settled in plain text in exactly the files a person
    attaches to a bug report. And the token is full power over the bot and all
    of its correspondence.
    """
    return text.replace(token, "***") if token else text


def _call(what, token: str):
    """A call to Telegram whose failure leaks neither the token nor anything else.

    The type matters too: requests.RequestException is an OSError, not a
    RuntimeError, so it flew past every one of our `except RuntimeError` clauses
    straight into the catch-all handler — and that one writes the whole
    traceback.
    """
    try:
        return what()
    except requests.RequestException as e:
        raise NotifyError("tg_err_network", error=_without_token(str(e), token)[:300]) from e


def send_message(cfg: dict, text: str) -> None:
    tg = cfg["telegram"]
    token, chat_id = tg.get("bot_token", ""), tg.get("chat_id", "")
    if not token:
        raise NotifyError("tg_err_no_token")
    if not chat_id:
        raise NotifyError("tg_err_no_chat")
    # Telegram caps a message at 4096 characters — we cut on paragraph breaks
    chunks, current = [], ""
    for para in text.split("\n\n"):
        if len(current) + len(para) + 2 > 3900:
            if current:
                chunks.append(current)
            current = para[:3900]
        else:
            current = f"{current}\n\n{para}" if current else para
    if current:
        chunks.append(current)
    for chunk in chunks:
        r = _call(lambda: requests.post(
            _api(token, "sendMessage"),
            json={"chat_id": chat_id, "text": chunk, "parse_mode": "HTML",
                  "disable_web_page_preview": True},
            timeout=TIMEOUT,
        ), token)
        data = r.json()
        if not data.get("ok"):
            raise RuntimeError(f"Telegram: {data.get('description', r.text[:200])}")


def detect_chat_id(token: str) -> str:
    """Looks for the chat_id among the bot's latest messages (getUpdates)."""
    r = _call(lambda: net.get(_api(token, "getUpdates"), timeout=TIMEOUT), token)
    data = r.json()
    if not data.get("ok"):
        raise RuntimeError(f"Telegram: {data.get('description', r.text[:200])}")
    for upd in reversed(data.get("result", [])):
        msg = upd.get("message") or upd.get("edited_message") or {}
        chat = msg.get("chat") or {}
        if chat.get("id"):
            return str(chat["id"])
    raise NotifyError("tg_err_no_updates")


def _esc(s: str) -> str:
    return html.escape(str(s or ""))


def format_digest(jobs: list, threshold: int, base_url: str = "", demoted: list = (), lang: str = "ru") -> str:
    def tr(key):
        return i18n.t(lang, key)

    if not jobs and not demoted:
        return tr("dg_none").format(t=threshold)
    if not jobs:
        lines = [tr("dg_near_only").format(t=threshold)]
        for j in demoted:
            lines.append(f"\n{j.get('score')}% — {_esc(j.get('title'))} @ {_esc(j.get('company'))}\n{_esc(j.get('url'))}")
        if base_url:
            lines.append("\n" + tr("dg_details").format(u=base_url))
        return "\n".join(lines)
    direct = [j for j in jobs if j.get("is_direct") and not j.get("is_agency")]
    rest = [j for j in jobs if j not in direct]
    lines = [f"<b>{tr('dg_new_jobs').format(n=len(jobs), t=threshold)}</b>"]
    loc_unknown = tr("dg_loc_unknown")
    cv_label = tr("dg_cv")

    def block(title, items):
        if not items:
            return
        lines.append(f"\n<b>{title}</b>")
        for j in items:
            posted = f" · 📅 {_esc(j['posted_at'])}" if j.get("posted_at") else ""
            lines.append(
                f"\n<b>{j.get('score', '?')}%</b> — {_esc(j.get('title'))} @ {_esc(j.get('company'))}"
                f" ({_esc(j.get('location') or loc_unknown)}){posted}\n"
                f"{_esc(j.get('url'))}"
            )
            if j.get("reason"):
                lines.append(f"💬 {_esc(j['reason'])}")
            advice = j.get("advice")
            if advice:
                try:
                    adv = json.loads(advice)
                    salary = adv.get("salary_estimate")
                    if salary and "не найдено" not in salary.lower() and "not found" not in salary.lower():
                        lines.append(f"💰 {_esc(salary)}")
                    changes = (adv.get("cv_changes") or [])[:3]
                    if changes:
                        lines.append(f"{cv_label} " + " • ".join(_esc(c) for c in changes))
                except (json.JSONDecodeError, TypeError):
                    pass

    block(tr("dg_direct"), direct)
    block(tr("dg_rest"), rest)
    if demoted:
        lines.append(f"\n<b>{tr('dg_below')}</b>")
        for j in demoted:
            lines.append(f"{j.get('score')}% — {_esc(j.get('title'))} @ {_esc(j.get('company'))}")
    if base_url:
        lines.append("\n" + tr("dg_more").format(u=base_url))
    return "\n".join(lines)
