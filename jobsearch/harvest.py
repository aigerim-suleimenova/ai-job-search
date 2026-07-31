"""Работодатели из ссылок, которые мы и так скачали.

Агрегаторы отдают вакансии вместе с адресами, и большая часть этих адресов
ведёт прямо на доску компании в ATS — boards.greenhouse.io/<кто-то>,
jobs.lever.co/<кто-то> и так далее. В базе одного человека таких оказалось
342 ссылки из 392, за двадцатью семью компаниями.

Раньше программа опознавала ATS только у компаний, добавленных руками, а эти
адреса выбрасывала. Теперь запоминает: на следующем прогоне доска читается
целиком, а не теми одной-двумя вакансиями, которые попали в агрегатор.

Ни веб-поиска, ни модели, ни единого лишнего запроса — ссылки уже у нас.
Поэтому охват растёт одинаково с Claude Code, с Cursor, с локальной моделью
и вообще без модели.
"""
from urllib.parse import urlparse

from .collectors import ats

# Адрес доски по виду ATS. Собранный адрес обязан опознаваться обратно тем же
# detect() — иначе на следующем прогоне доска добавится второй раз. Проверка
# круга стоит ниже, в _board_url(), и это не перестраховка: для personio slug
# и есть готовый хост, и шаблон «{slug}.jobs.personio.de» слепил из него
# «digacon-software.jobs.personio.com.jobs.personio.de».
BOARD_URL = {
    "greenhouse": "https://boards.greenhouse.io/{slug}",
    "lever": "https://jobs.lever.co/{slug}",
    "ashby": "https://jobs.ashbyhq.com/{slug}",
    "workable": "https://apply.workable.com/{slug}",
    "recruitee": "https://{slug}.recruitee.com",
    "smartrecruiters": "https://careers.smartrecruiters.com/{slug}",
    "personio": "https://{slug}",          # slug здесь — уже полный хост
}


def _board_url(kind: str, slug: str) -> str:
    """Адрес доски, который заведомо читается обратно. Пустая строка — значит
    такой вид ATS мы собрать не умеем, и лучше пропустить, чем накопить мусор."""
    шаблон = BOARD_URL.get(kind)
    if not шаблон or not slug:
        return ""
    url = шаблон.format(slug=slug)
    return url if ats.detect(url) == (kind, slug) else ""


def _known(companies: list) -> set:
    """Что уже под наблюдением — сравниваем по виду ATS и названию доски,
    а не по строке адреса: один и тот же работодатель встречается под разными
    ссылками (с www, со слэшем, со ссылкой на конкретную вакансию)."""
    известно = set()
    for c in companies:
        got = ats.detect(c.get("url", ""))
        if got:
            известно.add(got)
        else:
            известно.add(("url", (c.get("url") or "").rstrip("/").lower()))
    return известно


def find_new(jobs: list, companies: list, limit: int = 10) -> list:
    """Доски, которых ещё нет в наблюдении. Возвращает записи вида
    {"name": ..., "url": ...} — те же, что человек добавляет руками."""
    известно = _known(companies)
    новые, взято = [], set()
    for job in jobs:
        if len(новые) >= max(0, limit):
            break
        got = ats.detect(job.get("url", ""))
        if not got or got in известно or got in взято:
            continue
        url = _board_url(*got)
        if not url:
            continue
        взято.add(got)
        новые.append({"name": job.get("company") or got[1], "url": url})
    return новые


def board_host(url: str) -> str:
    return urlparse(url).netloc.lower()
