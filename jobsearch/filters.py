"""Жёсткие фильтры: локация, стоп-слова, эвристика рекрутинговых агентств."""
import hashlib
import re

EU_MARKERS = [
    "europe", "european union", " eu", "eu ", "emea",
    "germany", "berlin", "munich", "hamburg", "frankfurt", "cologne",
    "netherlands", "amsterdam", "rotterdam", "eindhoven",
    "france", "paris", "lyon", "spain", "madrid", "barcelona", "valencia",
    "italy", "milan", "rome", "poland", "warsaw", "krakow", "wroclaw", "gdansk",
    "portugal", "lisbon", "porto", "austria", "vienna", "belgium", "brussels",
    "ireland", "dublin", "sweden", "stockholm", "denmark", "copenhagen",
    "finland", "helsinki", "czech", "prague", "estonia", "tallinn",
    "latvia", "riga", "lithuania", "vilnius", "greece", "athens",
    "hungary", "budapest", "romania", "bucharest", "bulgaria", "sofia",
    "croatia", "zagreb", "slovakia", "bratislava", "slovenia", "ljubljana",
    "luxembourg", "malta", "cyprus",
]
US_MARKERS = [
    "united states", "usa", "u.s.", " us", "us ", "america",
    "new york", "san francisco", "bay area", "seattle", "austin", "boston",
    "los angeles", "chicago", "denver", "miami", "atlanta", "washington",
    ", ny", ", ca", ", tx", ", wa", ", ma", ", co", ", fl", ", il",
]
REMOTE_MARKERS = ["remote", "anywhere", "worldwide", "удал", "distributed", "work from home", "wfh"]

AGENCY_MARKERS = [
    "recruit", "staffing", "headhunt", "talent acquisition", "talent partners",
    "personalberatung", "personaldienstleist", "executive search", "hr solutions",
    "workforce", "outstaff", "outsourc", "agency", "agentur", "humancapital",
    "human capital", "manpower", "randstad", "adecco", "hays", "kelly services",
    "robert half", "michael page", "experis", "gulp", "akkodis",
]


def job_key(company: str, title: str) -> str:
    norm = re.sub(r"\s+", " ", f"{(company or '').lower().strip()}|{(title or '').lower().strip()}")
    return hashlib.sha1(norm.encode()).hexdigest()


def parse_locations(raw: str) -> list:
    return [t.strip().lower() for t in re.split(r"[,;]", raw or "") if t.strip()]


def location_ok(location: str, wanted: list, include_remote: bool = True) -> bool:
    if not wanted:
        return True
    loc = f" {(location or '').lower()} "
    if include_remote and any(m in loc for m in REMOTE_MARKERS):
        return True
    if not location:
        return True  # неизвестную локацию не отсекаем — решит LLM-триаж
    for token in wanted:
        if token in ("eu", "ес", "europe", "европа", "евросоюз"):
            if any(m in loc for m in EU_MARKERS):
                return True
        elif token in ("us", "usa", "сша", "united states", "америка"):
            if any(m in loc for m in US_MARKERS):
                return True
        elif token in ("remote", "удаленно", "удалённо"):
            if any(m in loc for m in REMOTE_MARKERS):
                return True
        elif token in loc:
            return True
    return False


def has_excluded(job: dict, exclude_terms: list) -> bool:
    if not exclude_terms:
        return False
    text = f"{job.get('title', '')} {job.get('company', '')}".lower()
    return any(t in text for t in exclude_terms)


def looks_like_agency(company: str) -> bool:
    name = (company or "").lower()
    return any(m in name for m in AGENCY_MARKERS)
