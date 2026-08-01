"""Employers found in links we had already downloaded.

Aggregators hand back a job together with its address, and most of those
addresses lead straight to the company's own ATS board — boards.greenhouse.io/
<someone>, jobs.lever.co/<someone> and so on. In one person's database that came
to 342 links out of 392, standing for twenty-seven companies.

The program used to recognise an ATS only for companies added by hand and threw
these addresses away. Now it remembers them: from the next run the board is read
in full, instead of through the one or two jobs that reached the aggregator.

No web search, no model, not a single extra request — the links are already in
hand. So the reach grows just the same with Claude Code, with Cursor, with a
local model, and with no model at all.
"""
from urllib.parse import urlparse

from .collectors import ats

# The board address per kind of ATS. An assembled address has to be recognised
# back by the same detect() — otherwise the board is added again on the next run.
# The round-trip check sits below in _board_url(), and it is not belt-and-braces:
# for personio the slug is already a complete host, and the template
# "{slug}.jobs.personio.de" glued it into
# «digacon-software.jobs.personio.com.jobs.personio.de».
BOARD_URL = {
    "greenhouse": "https://boards.greenhouse.io/{slug}",
    "lever": "https://jobs.lever.co/{slug}",
    "ashby": "https://jobs.ashbyhq.com/{slug}",
    "workable": "https://apply.workable.com/{slug}",
    "recruitee": "https://{slug}.recruitee.com",
    "smartrecruiters": "https://careers.smartrecruiters.com/{slug}",
    "personio": "https://{slug}",          # here the slug is already a complete host
}


def _board_url(kind: str, slug: str) -> str:
    """A board address that is certain to be recognised back. An empty string means
    we cannot assemble this kind of ATS, and skipping beats collecting rubbish."""
    шаблон = BOARD_URL.get(kind)
    if not шаблон or not slug:
        return ""
    url = шаблон.format(slug=slug)
    return url if ats.detect(url) == (kind, slug) else ""


def _known(companies: list) -> set:
    """What is already watched — compared by the kind of ATS and the board's name
    rather than by the address string: the same employer turns up under different
    links (with www, with a trailing slash, pointing at one particular job)."""
    известно = set()
    for c in companies:
        got = ats.detect(c.get("url", ""))
        if got:
            известно.add(got)
        else:
            известно.add(("url", (c.get("url") or "").rstrip("/").lower()))
    return известно


def find_new(jobs: list, companies: list, limit: int = 10) -> list:
    """Boards not yet watched. Returns entries of the form {"name": …, "url": …} —
    the same ones a person adds by hand."""
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
