"""The job collectors. Each returns a list of dictionaries:
{title, company, location, url, description, source, is_direct, posted_at}
"""
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime


def iso_date(value) -> str:
    """A publication date from an API → 'YYYY-MM-DD'. Understands ISO strings,
    RFC822 (RSS) and an epoch in seconds or milliseconds. Unparsed — empty string."""
    if value in (None, ""):
        return ""
    try:
        if isinstance(value, (int, float)) or (isinstance(value, str) and value.strip().isdigit()):
            ts = float(value)
            if ts > 1e12:  # an epoch in milliseconds (Lever)
                ts /= 1000.0
            return datetime.fromtimestamp(ts, tz=timezone.utc).strftime("%Y-%m-%d")
        s = str(value).strip()
        try:
            return datetime.fromisoformat(s.replace("Z", "+00:00")).strftime("%Y-%m-%d")
        except ValueError:
            return parsedate_to_datetime(s).strftime("%Y-%m-%d")
    except (ValueError, TypeError, OverflowError, OSError):
        return ""


from . import aggregators, ats, crawler  # noqa: F401,E402  (imported after iso_date — the collectors use it)
