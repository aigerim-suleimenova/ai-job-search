"""Sending the results by email.

Telegram is convenient, but not everyone has it; everyone has email. The letter
goes through the user's own SMTP — that way no third-party service and no keys
are needed.
"""
import smtplib
import ssl
from email.message import EmailMessage

from . import i18n

# Settings for the popular mail services: a person need only pick theirs from the
# list. Note that for Gmail/Yandex/Mail.ru an ordinary password will not do — an
# "app password" is required, issued in the mail account's security settings.
PRESETS = {
    "gmail": {"name": "Gmail", "host": "smtp.gmail.com", "port": 587, "tls": True,
              "app_password": True},
    "outlook": {"name": "Outlook / Hotmail", "host": "smtp-mail.outlook.com", "port": 587,
                "tls": True, "app_password": False},
    "yandex": {"name": "Yandex Mail", "host": "smtp.yandex.ru", "port": 465, "tls": False,
               "app_password": True},
    "mailru": {"name": "Mail.ru", "host": "smtp.mail.ru", "port": 465, "tls": False,
               "app_password": True},
    "icloud": {"name": "iCloud", "host": "smtp.mail.me.com", "port": 587, "tls": True,
               "app_password": True},
    # this one has no name of its own: it is named in the interface language
    "custom": {"name": "", "name_key": "mail_preset_custom", "host": "", "port": 587,
               "tls": True, "app_password": False},
}


def presets(lang: str = "en") -> dict:
    """The services for the drop-down, named in the interface language.

    Not all of them have a name of their own: "Other" is not a brand but a line
    of the interface, and it is translated.
    """
    out = {}
    for key, p in PRESETS.items():
        item = {k: v for k, v in p.items() if k != "name_key"}
        if p.get("name_key"):
            item["name"] = i18n.t(lang, p["name_key"])
        out[key] = item
    return out


class MailError(RuntimeError):
    """A sending error. Carries a translation key: the module does not know the
    interface language."""

    def __init__(self, key: str, **fmt):
        self.key, self.fmt = key, fmt
        super().__init__(key)

    pass


def configured(cfg: dict) -> bool:
    e = cfg.get("email", {})
    return bool(e.get("enabled") and e.get("host") and e.get("username") and e.get("to"))


def send(cfg: dict, subject: str, html: str, text: str = "") -> None:
    """Sends the letter. Raises MailError with an understandable text on failure."""
    e = cfg.get("email", {})
    host, port = e.get("host", ""), int(e.get("port", 587) or 587)
    user, password = e.get("username", ""), e.get("password", "")
    to_addr = e.get("to", "") or user
    if not (host and user and to_addr):
        raise MailError("mail_err_incomplete")

    msg = EmailMessage()
    msg["Subject"] = subject
    msg["From"] = e.get("from") or user
    msg["To"] = to_addr
    msg.set_content(text or i18n.t(cfg.get("ui", {}).get("lang", "ru"), "mail_body_fallback"))
    msg.add_alternative(html, subtype="html")

    context = ssl.create_default_context()
    try:
        if e.get("tls", True):
            with smtplib.SMTP(host, port, timeout=30) as s:
                s.starttls(context=context)
                s.login(user, password)
                s.send_message(msg)
        else:                                   # port 465 — encrypted from the first byte
            with smtplib.SMTP_SSL(host, port, timeout=30, context=context) as s:
                s.login(user, password)
                s.send_message(msg)
    except smtplib.SMTPAuthenticationError as exc:
        raise MailError("mail_err_auth") from exc
    except (smtplib.SMTPException, OSError) as exc:
        raise MailError("mail_err_send", error=exc) from exc


def send_digest(cfg: dict, jobs: list, candidate: str, when: str, threshold: int) -> None:
    """A letter with the jobs found, laid out exactly like the exported report."""
    from . import export
    if not jobs:
        return
    html = export.to_html(jobs, candidate, when, threshold)
    subject = i18n.t(cfg.get("ui", {}).get("lang", "en"), "mail_digest_subject").format(
        count=len(jobs), name=candidate)
    lines = [f"{j.get('score')}% — {j.get('title')} @ {j.get('company')}\n{j.get('url')}"
             for j in jobs[:20]]
    send(cfg, subject, html, "\n\n".join(lines))
