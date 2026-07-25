"""Сборщики вакансий. Каждый возвращает список словарей:
{title, company, location, url, description, source, is_direct, posted_at}
"""
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime


def iso_date(value) -> str:
    """Дата публикации из API → 'YYYY-MM-DD'. Понимает ISO-строки, RFC822 (RSS)
    и epoch в секундах или миллисекундах. Не разобрали — пустая строка."""
    if value in (None, ""):
        return ""
    try:
        if isinstance(value, (int, float)) or (isinstance(value, str) and value.strip().isdigit()):
            ts = float(value)
            if ts > 1e12:  # эпоха в миллисекундах (Lever)
                ts /= 1000.0
            return datetime.fromtimestamp(ts, tz=timezone.utc).strftime("%Y-%m-%d")
        s = str(value).strip()
        try:
            return datetime.fromisoformat(s.replace("Z", "+00:00")).strftime("%Y-%m-%d")
        except ValueError:
            return parsedate_to_datetime(s).strftime("%Y-%m-%d")
    except (ValueError, TypeError, OverflowError, OSError):
        return ""


from . import aggregators, ats, crawler  # noqa: F401,E402  (импорт после iso_date — им пользуются сборщики)
