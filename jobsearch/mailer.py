"""Отправка результатов на почту.

Telegram удобен, но не у всех есть; почта есть у всех. Письмо отправляется через
SMTP самого пользователя — так не нужен никакой сторонний сервис и ключи.
"""
import smtplib
import ssl
from email.message import EmailMessage

# Настройки популярных почтовых служб: человеку достаточно выбрать свою из списка.
# Важно: у Gmail/Yandex/Mail.ru обычный пароль не подойдёт — нужен «пароль
# приложения», который выдаётся в настройках безопасности почты.
PRESETS = {
    "gmail": {"name": "Gmail", "host": "smtp.gmail.com", "port": 587, "tls": True,
              "app_password": True},
    "outlook": {"name": "Outlook / Hotmail", "host": "smtp-mail.outlook.com", "port": 587,
                "tls": True, "app_password": False},
    "yandex": {"name": "Яндекс Почта", "host": "smtp.yandex.ru", "port": 465, "tls": False,
               "app_password": True},
    "mailru": {"name": "Mail.ru", "host": "smtp.mail.ru", "port": 465, "tls": False,
               "app_password": True},
    "icloud": {"name": "iCloud", "host": "smtp.mail.me.com", "port": 587, "tls": True,
               "app_password": True},
    "custom": {"name": "Другая (укажу вручную)", "host": "", "port": 587, "tls": True,
               "app_password": False},
}


class MailError(RuntimeError):
    pass


def configured(cfg: dict) -> bool:
    e = cfg.get("email", {})
    return bool(e.get("enabled") and e.get("host") and e.get("username") and e.get("to"))


def send(cfg: dict, subject: str, html: str, text: str = "") -> None:
    """Отправляет письмо. Бросает MailError с понятным текстом при неудаче."""
    e = cfg.get("email", {})
    host, port = e.get("host", ""), int(e.get("port", 587) or 587)
    user, password = e.get("username", ""), e.get("password", "")
    to_addr = e.get("to", "") or user
    if not (host and user and to_addr):
        raise MailError("Не заполнены сервер, логин или адрес получателя")

    msg = EmailMessage()
    msg["Subject"] = subject
    msg["From"] = e.get("from") or user
    msg["To"] = to_addr
    msg.set_content(text or "Откройте письмо в HTML-виде, чтобы увидеть вакансии.")
    msg.add_alternative(html, subtype="html")

    context = ssl.create_default_context()
    try:
        if e.get("tls", True):
            with smtplib.SMTP(host, port, timeout=30) as s:
                s.starttls(context=context)
                s.login(user, password)
                s.send_message(msg)
        else:                                   # порт 465 — шифрование с первого байта
            with smtplib.SMTP_SSL(host, port, timeout=30, context=context) as s:
                s.login(user, password)
                s.send_message(msg)
    except smtplib.SMTPAuthenticationError as exc:
        raise MailError("Почта не приняла логин или пароль. Для Gmail, Яндекса и Mail.ru "
                        "нужен «пароль приложения», а не обычный пароль от ящика.") from exc
    except (smtplib.SMTPException, OSError) as exc:
        raise MailError(f"Не удалось отправить письмо: {exc}") from exc


def send_digest(cfg: dict, jobs: list, candidate: str, when: str, threshold: int) -> None:
    """Письмо с найденными вакансиями — тем же оформлением, что и выгружаемый отчёт."""
    from . import export
    if not jobs:
        return
    html = export.to_html(jobs, candidate, when, threshold)
    subject = f"AI Job Search: {len(jobs)} вакансий для {candidate}"
    lines = [f"{j.get('score')}% — {j.get('title')} @ {j.get('company')}\n{j.get('url')}"
             for j in jobs[:20]]
    send(cfg, subject, html, "\n\n".join(lines))
