"""The name of an occupation from its ESCO code.

ESCO is the Europe-wide taxonomy of occupations. EURES stamps every posting with
it, and the code is the same whatever language the posting is written in: the
Polish «Szwaczka» and the French «Couturière» both carry one code.

Here is why this was needed. Opening EURES gave us jobs from across the EU — and
almost all of them in their own languages. The local model does not read them.
Having failed to understand the text, it does not say "I did not understand": it
retells the candidate's profile and puts down a score from the example. On a run
for a lingerie designer, a joiner, a painter, a gardener, a storekeeper and a
plumber all scored ninety percent, each with the reasoning that "the candidate
has experience as a Lingerie Technical Designer, as well as knowledge of the
technologies required for the conservation of water utility systems".

Translating the posting is unnecessary: it already carries a machine-readable
occupation. We unfold the code into an English name and put it beside the
original — the model no longer has to read Polish to work out whose job this is.

Answers are kept in memory: a run brings dozens of codes and hundreds of jobs.
"""
import re
import urllib.parse

import requests

from . import web

API = "https://ec.europa.eu/esco/api/resource/occupation"

_names: dict = {}


def label(uri: str, lang: str = "en") -> str:
    """The occupation's name in the requested language. An empty string if it did not work out.

    That is no disaster: the job simply keeps its own single title, as it did
    before. There is nothing worth failing a run over in the name of an
    occupation.
    """
    uri = (uri or "").strip()
    if not uri.startswith("http://data.europa.eu/esco/"):
        return ""
    key = (uri, lang)
    if key in _names:
        return _names[key]
    name = ""
    try:
        r = web.get(f"{API}?uri={urllib.parse.quote(uri, safe='')}&language={lang}",
                    timeout=20)
        if r is not None and r.status_code == 200:
            data = r.json()
            name = str((data.get("preferredLabel") or {}).get(lang)
                       or data.get("title") or "").strip()
    except (requests.RequestException, ValueError, AttributeError):
        name = ""
    _names[key] = name
    return name


SEARCH = "https://ec.europa.eu/esco/api/search"
RESOURCE = "https://ec.europa.eu/esco/api/resource"

_cache: dict = {}


def occupations(role: str, how_many: int = 3, lang: str = "en") -> list:
    """Taxonomy codes for occupations matching a role's name.

    This alone was worth the trouble: EURES searches by words badly and
    unpredictably. Measured on live queries — out of fifty jobs for one
    occupation, what came back was:

        "dressmaker"              28
        "lingerie"                 3
        "Lingerie Pattern Maker"   1
        "seamstress"               0
        "tailor"                   0

    So it is not the meaning that decides but the word you guessed, and guessing
    it is impossible: "швея" in English is "seamstress", and that finds nothing.
    Meanwhile the roles the program writes out of a CV come out long: "Lingerie
    Product Developer", "Parametric Pattern Making". Those find even less.

    The taxonomy takes the guessing away. It maps "seamstress" onto sewing
    machinist, dressmaker and tailor, and EURES accepts codes and searches by
    them instead: on that same query it became 29 out of 50 rather than one.
    """
    role = (role or "").strip()
    if not role:
        return []
    key = (role.lower(), how_many, lang)
    if key in _cache:
        return _cache[key]
    found = []
    try:
        r = web.get(f"{SEARCH}?type=occupation&language={lang}&limit={how_many}"
                    f"&text={urllib.parse.quote(role)}", timeout=20)
        if r is not None and r.status_code == 200:
            data = r.json()
            entries = (data.get("_embedded", {}) or {}).get("results") or data_results(data)
            found = [str(i.get("uri", "")) for i in entries if i.get("uri")]
    except (requests.RequestException, ValueError, AttributeError):
        found = []
    _cache[key] = found
    return found


def data_results(data: dict) -> list:
    """The taxonomy answers in two shapes — with _embedded and without."""
    return data.get("results") or []


def _words(text: str) -> list:
    return [w for w in re.findall(r"[a-zа-яё0-9#+]+", (text or "").lower()) if len(w) > 1]


def looks_like(query: str, answer: str) -> bool:
    """Whether the taxonomy's answer looks like what was asked about.

    Taking the first match in order will not do, and that is measured: the
    taxonomy reads "SOAP" as "harden soap", "REST" as "promote balance between
    rest and activity", "XML" as "AJAX", "vendor management" as "use office
    systems". Further along the graph it spreads into rubbish, and no measure of
    similarity will save it.

    The rule came out simple: an exact match — take it; every word of the query
    found in the answer — take it; a single word and not exact — leave it. Across
    six real CVs this filter raised the number of people whose own occupation the
    taxonomy names from one to three.
    """
    q, a = _words(query), _words(answer)
    if not q or not a:
        return False
    if q == a:
        return True
    if len(q) == 1:
        return False
    # up to the ending: "restaurant management" and "manage restaurant service"
    # are the same thing, and there is nothing to pick at in the grammar
    return all(any(w[:5] == x[:5] for x in a) for w in q)


def skill(text: str, lang: str = "en") -> str:
    """The code for a skill from its name. An empty string if the taxonomy does not know it."""
    text = (text or "").strip()
    if not text:
        return ""
    key = ("skill", text.lower(), lang)
    if key in _cache:
        return _cache[key]
    found = ""
    try:
        r = web.get(f"{SEARCH}?type=skill&language={lang}&limit=5"
                    f"&text={urllib.parse.quote(text)}", timeout=20)
        if r is not None and r.status_code == 200:
            entries = (r.json().get("_embedded", {}) or {}).get("results") or []
            found = next((str(i.get("uri", "")) for i in entries
                          if looks_like(text, str(i.get("title", "")))), "")
    except (requests.RequestException, ValueError, AttributeError):
        found = ""
    _cache[key] = found
    return found


def occupations_by_skills(skills: list, how_many: int = 3, lang: str = "en") -> list:
    """What a person could work as, going by their skills alone.

    Asking "what would you like to be" is useless: a person does not know what
    they are fit for, that is what they came here for. A security guard who has
    learned front-end will write "security guard" on their CV, and will never see
    a junior front-end job — the program searches for who they used to be.

    The taxonomy answers this question by itself: in it, a skill knows which
    occupations need it. For that guard, from three skills — JavaScript, CSS,
    HTML — it names web developer, webmaster, digital media designer. Not one
    question was put to the person.

    The counting is plain: how many of the person's skills an occupation needs at
    all. Essential and optional weigh the same, and that is measured across seven
    cases.

    At first essential weighed triple, and it came out badly: in the taxonomy
    "JavaScript" is essential for exactly two occupations — CNC machine operator
    and CAD operator — while for web developer it is merely optional, one of some
    fifty. Two quirks of the taxonomy outweighed fifty sensible links, and the
    security guard who had learned front-end got a CNC machine.

    With the weights levelled, 14 of the top three were right instead of 11. Not
    one case got worse, three got better: for the guard, digital media designer
    and user interface developer rose to the top; for the front-end developer, web
    developer; and the lawyer gained a "lawyer", which had not been there at all.

    I tried the fraction of skills covered as well, and it turned out worst of the
    lot: occupations with few skills in their list win, and "movie distributor"
    and "tanning consultant" climb to the top.

    It does not work for everyone. For the lingerie designer the taxonomy knows
    one skill out of eight, and that one is tied to footwear occupations: its
    graph is uneven, footwear written out in detail and clothing sparsely. So
    this is an addition to searching by roles, not a replacement for it.
    """
    points: dict = {}
    for s in skills:
        uri = skill(s, lang)
        if not uri:
            continue
        try:
            r = web.get(f"{RESOURCE}/skill?uri={urllib.parse.quote(uri, safe='')}"
                        f"&language={lang}", timeout=20)
            links = r.json().get("_links", {}) if r is not None and r.status_code == 200 else {}
        except (requests.RequestException, ValueError, AttributeError):
            continue
        for key in ("isEssentialForOccupation", "isOptionalForOccupation"):
            for o in (links.get(key) or []):
                points[str(o.get("uri"))] = points.get(str(o.get("uri")), 0) + 1
    best = sorted(points.items(), key=lambda x: -x[1])[:how_many]
    return [u for u, _ in best if u]


def forget() -> None:
    """For the tests: drop everything remembered."""
    _names.clear()
    _cache.clear()
