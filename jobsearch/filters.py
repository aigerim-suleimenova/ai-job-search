"""Жёсткие фильтры: локация, стоп-слова, эвристика рекрутинговых агентств."""
import hashlib
import re

EU_MARKERS = [
    "europe", "european union", " eu", "eu ", "emea",
    # макрорегионы, которыми часто описывают локацию
    "dach", "benelux", "nordics", "baltics", "cet timezone", "cet time",
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
    # локальные написания (немецкие вакансии часто пишут город/страну по-немецки)
    "deutschland", "münchen", "muenchen", "köln", "koeln", "düsseldorf", "duesseldorf",
    "stuttgart", "nürnberg", "nuernberg", "leipzig", "dresden", "hannover",
    "karlsruhe", "heidelberg", "aachen", "bremen", "essen", "dortmund", "bonn",
    "mainz", "mannheim", "wiesbaden", "würzburg", "wuerzburg", "regensburg",
    "österreich", "oesterreich", "wien", "graz", "linz", "salzburg", "innsbruck",
    "niederlande", "nederland", "utrecht", "den haag", "the hague",
    "frankreich", "toulouse", "nantes", "bordeaux", "lille", "marseille", "nice", "grenoble",
    "spanien", "sevilla", "seville", "málaga", "malaga", "bilbao", "zaragoza",
    "italien", "torino", "turin", "bologna", "napoli", "naples", "firenze", "florence",
    "polen", "poznan", "poznań", "lodz", "łódź", "katowice", "szczecin",
    "tschechien", "brno", "ostrava", "schweden", "gothenburg", "göteborg", "malmö", "malmo",
    "dänemark", "daenemark", "aarhus", "århus", "finnland", "tampere", "espoo",
    "irland", "cork", "galway", "belgien", "ghent", "gent", "antwerp", "antwerpen", "leuven",
    "ungarn", "rumänien", "rumaenien", "cluj", "timisoara", "brasov", "iasi",
    "griechenland", "thessaloniki", "kroatien", "split", "bulgarien", "plovdiv", "varna",
    "slowakei", "kosice", "slowenien", "estland", "tartu", "lettland", "litauen", "kaunas",
    "nicosia", "limassol", "braga",
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


# отдельные страны: вакансии часто указывают только город («Milano», «München»)
# без названия страны — токен страны должен матчить и города
COUNTRY_MARKERS = {
    "italy": ["italy", "italia", "italien", "milan", "milano", "rome", "roma",
              "turin", "torino", "bologna", "naples", "napoli", "florence", "firenze",
              "padova", "padua", "verona", "genova", "genoa", "bergamo", "brescia",
              "trieste", "trento", "modena", "parma", "emilia", "lombardia", "lombardy",
              "lazio", "piemonte", "veneto", "toscana", "tuscany"],
    "germany": ["germany", "deutschland", "berlin", "munich", "münchen", "muenchen",
                "hamburg", "frankfurt", "cologne", "köln", "koeln", "düsseldorf",
                "duesseldorf", "stuttgart", "leipzig", "dresden", "hannover", "bremen",
                "nürnberg", "nuernberg", "bayern", "bavaria", "hessen", "sachsen",
                "nordrhein", "baden-württemberg", "baden-wuerttemberg"],
}
COUNTRY_MARKERS["италия"] = COUNTRY_MARKERS["italy"]
COUNTRY_MARKERS["германия"] = COUNTRY_MARKERS["germany"]


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
        elif token in COUNTRY_MARKERS:
            if any(m in loc for m in COUNTRY_MARKERS[token]):
                return True
        elif token in loc:
            return True
    return False


def has_excluded(job: dict, exclude_terms: list) -> bool:
    """Стоп-слова — по границам слов, а не подстрокой: исключение «java»
    не должно убивать JavaScript, а «go» — Google."""
    if not exclude_terms:
        return False
    text = f"{job.get('title', '')} {job.get('company', '')}".lower()
    return any(
        re.search(rf"(?<![a-zа-яё0-9]){re.escape(t)}(?![a-zа-яё0-9])", text)
        for t in exclude_terms
    )


def looks_like_agency(company: str) -> bool:
    name = (company or "").lower()
    return any(m in name for m in AGENCY_MARKERS)


# заведомо не-инженерные/не-технические роли — их можно отсеять до дорогой LLM-оценки.
# Автоадаптация: не режем, если заголовок совпадает с ролями/навыками профиля
# (напр. профиль «Recruiter» → «Technical Recruiter» не отсеётся).
OFF_TARGET_MARKERS = [
    "recruiter", "talent acquisition", "talent partner", "sourcer",
    "account executive", "account manager", "key account", "sales manager",
    "sales representative", "sales development", "business development",
    "customer service", "customer support", "customer success", "support agent",
    "call center", "receptionist", "office manager", "office assistant",
    "human resources", " hr ", "hr manager", "hr business", "people operations",
    "accountant", "bookkeeper", "payroll", "financial controller", "auditor",
    "marketing manager", "social media", "content writer", "copywriter",
    "brand manager", "community manager", "paid ads", "seo specialist",
    "legal counsel", "paralegal", "procurement", "logistics coordinator",
    "warehouse", "driver", "nurse", "teacher", "waiter", "barista",
]


def off_target(job: dict, keep_terms: set) -> bool:
    """Заведомо не та профессия? True — если заголовок явно нетехнический
    и не пересекается с ролями/навыками кандидата."""
    title = f" {(job.get('title') or '').lower()} "
    if not any(m in title for m in OFF_TARGET_MARKERS):
        return False
    # заголовок совпадает с профилем (роли/навыки) — оставляем, пусть решит LLM
    title_words = set(re.findall(r"[a-zа-яё0-9+#.]{3,}", title))
    return not (title_words & keep_terms)
