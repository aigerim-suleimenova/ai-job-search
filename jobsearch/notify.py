"""Отправка дайджеста в Telegram."""
import html
import json

import requests

TIMEOUT = 30


def _api(token: str, method: str) -> str:
    return f"https://api.telegram.org/bot{token}/{method}"


def send_message(cfg: dict, text: str) -> None:
    tg = cfg["telegram"]
    token, chat_id = tg.get("bot_token", ""), tg.get("chat_id", "")
    if not token:
        raise RuntimeError("Не задан bot token")
    if not chat_id:
        raise RuntimeError("Не задан chat id — напишите боту сообщение и нажмите «Сохранить и определить chat id»")
    # телеграм ограничивает сообщение 4096 символами — режем по абзацам
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
        r = requests.post(
            _api(token, "sendMessage"),
            json={"chat_id": chat_id, "text": chunk, "parse_mode": "HTML",
                  "disable_web_page_preview": True},
            timeout=TIMEOUT,
        )
        data = r.json()
        if not data.get("ok"):
            raise RuntimeError(f"Telegram: {data.get('description', r.text[:200])}")


def detect_chat_id(token: str) -> str:
    """Ищет chat_id в последних сообщениях боту (getUpdates)."""
    r = requests.get(_api(token, "getUpdates"), timeout=TIMEOUT)
    data = r.json()
    if not data.get("ok"):
        raise RuntimeError(f"Telegram: {data.get('description', r.text[:200])}")
    for upd in reversed(data.get("result", [])):
        msg = upd.get("message") or upd.get("edited_message") or {}
        chat = msg.get("chat") or {}
        if chat.get("id"):
            return str(chat["id"])
    raise RuntimeError("Обновлений нет. Напишите вашему боту любое сообщение и попробуйте снова.")


def _esc(s: str) -> str:
    return html.escape(str(s or ""))


def format_digest(jobs: list, threshold: int, base_url: str = "", demoted: list = ()) -> str:
    if not jobs and not demoted:
        return f"🔍 Новых вакансий с совпадением ≥ {threshold}% не найдено."
    if not jobs:
        lines = [f"🔍 Выше порога {threshold}% ничего, но близкие варианты были (точная оценка ниже порога):"]
        for j in demoted:
            lines.append(f"\n{j.get('score')}% — {_esc(j.get('title'))} @ {_esc(j.get('company'))}\n{_esc(j.get('url'))}")
        if base_url:
            lines.append(f"\nДетали: {base_url}/results")
        return "\n".join(lines)
    direct = [j for j in jobs if j.get("is_direct") and not j.get("is_agency")]
    rest = [j for j in jobs if j not in direct]
    lines = [f"🎯 <b>Новые вакансии: {len(jobs)}</b> (порог {threshold}%)"]

    def block(title, items):
        if not items:
            return
        lines.append(f"\n<b>{title}</b>")
        for j in items:
            lines.append(
                f"\n<b>{j.get('score', '?')}%</b> — {_esc(j.get('title'))} @ {_esc(j.get('company'))}"
                f" ({_esc(j.get('location') or 'локация не указана')})\n"
                f"{_esc(j.get('url'))}"
            )
            if j.get("reason"):
                lines.append(f"💬 {_esc(j['reason'])}")
            advice = j.get("advice")
            if advice:
                try:
                    adv = json.loads(advice)
                    changes = (adv.get("cv_changes") or [])[:3]
                    if changes:
                        lines.append("📝 CV: " + " • ".join(_esc(c) for c in changes))
                except (json.JSONDecodeError, TypeError):
                    pass

    block("🏢 Напрямую от компаний", direct)
    block("🤝 Агрегаторы и агентства", rest)
    if demoted:
        lines.append(f"\n🔻 <b>Близко, но точная оценка ниже порога:</b>")
        for j in demoted:
            lines.append(f"{j.get('score')}% — {_esc(j.get('title'))} @ {_esc(j.get('company'))}")
    if base_url:
        lines.append(f"\nПодробности и правки LinkedIn: {base_url}/results")
    return "\n".join(lines)
