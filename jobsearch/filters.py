"""Hard filters: location, stop words, and a heuristic for recruiting agencies."""
import hashlib
import re

EU_MARKERS = [
    "europe", "european union", "eu", "emea",
    # the broad regions a location is often described by
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
    # local spellings (German postings often write the city or country in German)
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
    "united states", "usa", "u.s.", "us", "america",
    "new york", "san francisco", "bay area", "seattle", "austin", "boston",
    "los angeles", "chicago", "denver", "miami", "atlanta", "washington",
    # State abbreviations. They used to be written with a comma — ", ca" — and
    # searched for as a plain substring, so that "Calgary, Canada" matched
    # California and Canadian jobs went into a search for the US. Twenty-six of
    # them in a single run. Now the match is on a whole word, and ", canada" is
    # no longer confused with anything.
    "ny", "ca", "tx", "wa", "ma", "co", "fl", "il",
]


REMOTE_MARKERS = ["remote", "anywhere", "worldwide", "удал", "distributed", "work from home", "wfh"]

AGENCY_MARKERS = [
    "recruit", "staffing", "headhunt", "talent acquisition", "talent partners",
    "personalberatung", "personaldienstleist", "executive search", "hr solutions",
    "workforce", "outstaff", "outsourc", "agency", "agentur", "humancapital",
    "human capital", "manpower", "randstad", "adecco", "hays", "kelly services",
    "robert half", "michael page", "experis", "gulp", "akkodis",
]


def _has(loc: str, markers) -> bool:
    """Whether the string carries any of the markers — as a whole word, not a piece.

    A plain substring test let us down everywhere alike: a short marker settled
    inside a longer word. "ca", the abbreviation for California, was found in
    "Calgary, Canada", and Canadian jobs went into a search for the US:
    twenty-six of them in a single run, for a person who had not asked for
    Canada. The shorter the marker, the more often this happens — and short
    markers are exactly the ones we need most: state codes and country names.
    """
    return any(re.search(rf"(?<!\w){re.escape(m)}(?!\w)", loc) for m in markers)


def job_key(company: str, title: str) -> str:
    norm = re.sub(r"\s+", " ", f"{(company or '').lower().strip()}|{(title or '').lower().strip()}")
    return hashlib.sha1(norm.encode()).hexdigest()


def parse_locations(raw: str) -> list:
    return [t.strip().lower() for t in re.split(r"[,;]", raw or "") if t.strip()]


# Countries, one line each: the code is what a source calls it, the name is what
# we write, then how people write it, then its cities.
#
# The code was needed because of EURES: it hands back a location as a two-letter
# code and nothing else — "BE", "SE", "FR". We put that code into the location
# field as it was, and the filter compared it against the word «нидерланды» — and
# threw everything out. Two hundred and twenty-seven jobs per run, every single
# one, every time. And this from EURES, our one source that knows every
# occupation and every country in the EU: the whole thing was started for its
# sake, and it never yielded a single job.
#
# The names were needed in their own right. Countries other than Italy and
# Germany were not listed here at all, and «Франция» was checked by plain
# substring: the location "Paris, France" did not suit a person who had written
# «Франция». Searching in Russian worked for two countries out of thirty-one.
#
# The cities, because a posting often carries only the city: "Milano",
# "München", and the country has to be worked out from it.
#
#     code: (name, other spellings, cities)
COUNTRIES = {
    "AT": ("Austria", ["österreich", "oesterreich", "австрия"],
           ["vienna", "wien", "graz", "linz", "salzburg", "innsbruck"]),
    "BE": ("Belgium", ["belgië", "belgie", "belgique", "belgien", "бельгия"],
           ["brussels", "bruxelles", "brussel", "antwerp", "antwerpen", "gent",
            "ghent", "leuven", "liège", "charleroi"]),
    "BG": ("Bulgaria", ["българия", "болгария"],
           ["sofia", "plovdiv", "varna", "burgas"]),
    "CH": ("Switzerland", ["schweiz", "suisse", "svizzera", "швейцария"],
           ["zurich", "zürich", "geneva", "genève", "basel", "bern", "lausanne", "zug"]),
    "CY": ("Cyprus", ["κύπρος", "кипр"], ["nicosia", "limassol", "larnaca"]),
    "CZ": ("Czechia", ["czech republic", "česko", "cesko", "чехия"],
           ["prague", "praha", "brno", "ostrava", "plzeň", "plzen"]),
    "DE": ("Germany", ["deutschland", "германия"],
           ["berlin", "munich", "münchen", "muenchen", "hamburg", "frankfurt",
            "cologne", "köln", "koeln", "düsseldorf", "duesseldorf", "stuttgart",
            "leipzig", "dresden", "hannover", "bremen", "nürnberg", "nuernberg",
            "bayern", "bavaria", "hessen", "sachsen", "nordrhein",
            "baden-württemberg", "baden-wuerttemberg"]),
    "DK": ("Denmark", ["danmark", "дания"],
           ["copenhagen", "københavn", "kobenhavn", "aarhus", "odense", "aalborg"]),
    "EE": ("Estonia", ["eesti", "эстония"], ["tallinn", "tartu"]),
    "ES": ("Spain", ["españa", "espana", "spanien", "испания"],
           ["madrid", "barcelona", "valencia", "sevilla", "seville", "bilbao",
            "málaga", "malaga", "zaragoza", "cataluña", "catalunya", "catalonia"]),
    "FI": ("Finland", ["suomi", "финляндия"],
           ["helsinki", "espoo", "tampere", "turku", "oulu"]),
    "FR": ("France", ["франция"],
           ["paris", "lyon", "marseille", "toulouse", "lille", "bordeaux",
            "nantes", "nice", "strasbourg", "montpellier", "rennes", "grenoble"]),
    "GR": ("Greece", ["ελλάδα", "hellas", "греция"],
           ["athens", "athina", "thessaloniki", "patras"]),
    "HR": ("Croatia", ["hrvatska", "хорватия"], ["zagreb", "split", "rijeka"]),
    "HU": ("Hungary", ["magyarország", "magyarorszag", "венгрия"],
           ["budapest", "debrecen", "szeged"]),
    "IE": ("Ireland", ["éire", "eire", "ирландия"],
           ["dublin", "cork", "galway", "limerick"]),
    "IS": ("Iceland", ["ísland", "island", "исландия"], ["reykjavik", "reykjavík"]),
    "IT": ("Italy", ["italia", "italien", "италия"],
           ["milan", "milano", "rome", "roma", "turin", "torino", "bologna",
            "naples", "napoli", "florence", "firenze", "padova", "padua", "verona",
            "genova", "genoa", "bergamo", "brescia", "trieste", "trento", "modena",
            "parma", "emilia", "lombardia", "lombardy", "lazio", "piemonte",
            "veneto", "toscana", "tuscany"]),
    "LI": ("Liechtenstein", ["лихтенштейн"], ["vaduz"]),
    "LT": ("Lithuania", ["lietuva", "литва"], ["vilnius", "kaunas", "klaipėda"]),
    "LU": ("Luxembourg", ["luxemburg", "люксембург"], []),
    "LV": ("Latvia", ["latvija", "латвия"], ["riga", "rīga", "daugavpils"]),
    "MT": ("Malta", ["мальта"], ["valletta", "sliema"]),
    "NL": ("Netherlands", ["nederland", "holland", "niederlande", "нидерланды",
                           "голландия"],
           ["amsterdam", "rotterdam", "the hague", "den haag", "utrecht",
            "eindhoven", "groningen", "tilburg", "lijnden"]),
    "NO": ("Norway", ["norge", "noreg", "норвегия"],
           ["oslo", "bergen", "trondheim", "stavanger"]),
    "PL": ("Poland", ["polska", "polen", "польша"],
           ["warsaw", "warszawa", "kraków", "krakow", "cracow", "wrocław",
            "wroclaw", "poznań", "poznan", "gdańsk", "gdansk", "łódź", "lodz"]),
    "PT": ("Portugal", ["португалия"],
           ["lisbon", "lisboa", "porto", "braga", "coimbra"]),
    "RO": ("Romania", ["românia", "romania", "румыния"],
           ["bucharest", "bucurești", "bucuresti", "cluj", "timișoara", "timisoara"]),
    "SE": ("Sweden", ["sverige", "швеция"],
           ["stockholm", "gothenburg", "göteborg", "goteborg", "malmö", "malmo",
            "uppsala", "lund", "linköping", "linkoping", "solna", "södertälje"]),
    "SI": ("Slovenia", ["slovenija", "словения"], ["ljubljana", "maribor"]),
    "SK": ("Slovakia", ["slovensko", "словакия"], ["bratislava", "košice", "kosice"]),
}

# Коды, которые понимает EURES, — ровно те тридцать одна страна, что выше. Ниже
# идёт остальной мир: он нужен фильтру, но спрашивать про него EURES бесполезно.
EURES_CODES = frozenset(COUNTRIES)

# Остальной мир. Без него выходило вот что: человек пишет «Россия, Омск», а
# «Москва» отсеивается — слова «россия» в строке нет, а справочника, который
# связал бы одно с другим, не было. И наоборот, «Kenya, Remote» проходило в
# поиск по России как «работа откуда угодно»: Кении в списках не значилось,
# значит место как бы и не названо.
#
# Города здесь скупее, чем у европейских: список нужен, чтобы узнать страну и не
# перепутать её с чужой, а не чтобы знать все её города.
COUNTRIES.update({
    "US": ("United States", ["сша", "америка", "usa", "u.s."],
           ["new york", "san francisco", "bay area", "seattle", "austin", "boston",
            "los angeles", "chicago", "denver", "miami", "atlanta", "washington",
            "houston", "dallas", "phoenix", "philadelphia", "san diego", "portland"]),
    "CA": ("Canada", ["канада"],
           ["toronto", "vancouver", "montreal", "montréal", "calgary", "ottawa",
            "winnipeg", "edmonton", "quebec", "québec"]),
    "GB": ("United Kingdom", ["uk", "britain", "great britain", "england", "scotland",
                              "wales", "великобритания", "англия"],
           ["london", "manchester", "birmingham", "edinburgh", "glasgow", "bristol",
            "leeds", "cambridge", "oxford"]),
    "RU": ("Russia", ["россия", "russian federation", "рф"],
           ["moscow", "москва", "saint petersburg", "st petersburg", "санкт-петербург",
            "петербург", "novosibirsk", "новосибирск", "yekaterinburg", "екатеринбург",
            "kazan", "казань", "omsk", "омск", "samara", "самара", "ufa", "уфа",
            "krasnoyarsk", "красноярск", "nizhny novgorod", "нижний новгород",
            "chelyabinsk", "челябинск", "rostov", "ростов", "krasnodar", "краснодар",
            "voronezh", "воронеж", "perm", "пермь", "volgograd", "волгоград",
            "tyumen", "тюмень", "irkutsk", "иркутск", "tomsk", "томск"]),
    "UA": ("Ukraine", ["украина", "україна"],
           ["kyiv", "kiev", "киев", "київ", "lviv", "львов", "kharkiv", "харьков",
            "odesa", "odessa", "одесса", "dnipro", "днепр"]),
    "BY": ("Belarus", ["беларусь", "белоруссия"], ["minsk", "минск", "gomel", "гомель"]),
    "KZ": ("Kazakhstan", ["казахстан", "qazaqstan"],
           ["almaty", "алматы", "astana", "астана", "pavlodar", "павлодар",
            "shymkent", "шымкент", "karaganda", "караганда"]),
    "GE": ("Georgia", ["грузия", "sakartvelo"], ["tbilisi", "тбилиси", "batumi", "батуми"]),
    "AM": ("Armenia", ["армения"], ["yerevan", "ереван"]),
    "RS": ("Serbia", ["сербия", "srbija"], ["belgrade", "beograd", "novi sad"]),
    "MD": ("Moldova", ["молдова", "молдавия"], ["chisinau", "chișinău", "кишинёв"]),
    "TR": ("Turkey", ["türkiye", "turkiye", "турция"],
           ["istanbul", "стамбул", "ankara", "анкара", "izmir", "измир", "antalya"]),
    "IL": ("Israel", ["израиль"], ["tel aviv", "тель-авив", "jerusalem", "иерусалим", "haifa"]),
    "AE": ("United Arab Emirates", ["uae", "оаэ", "эмираты"],
           ["dubai", "дубай", "abu dhabi", "абу-даби", "sharjah"]),
    "SA": ("Saudi Arabia", ["саудовская аравия"], ["riyadh", "эр-рияд", "jeddah"]),
    "QA": ("Qatar", ["катар"], ["doha", "доха"]),
    "EG": ("Egypt", ["египет"], ["cairo", "каир", "alexandria"]),
    "MA": ("Morocco", ["марокко", "maroc"], ["casablanca", "rabat", "marrakesh"]),
    "JO": ("Jordan", ["иордания"], ["amman", "амман"]),
    "KE": ("Kenya", ["кения"], ["nairobi", "найроби", "mombasa"]),
    "NG": ("Nigeria", ["нигерия"], ["lagos", "лагос", "abuja"]),
    "ZA": ("South Africa", ["юар", "южная африка"],
           ["johannesburg", "cape town", "кейптаун", "pretoria", "durban"]),
    "MU": ("Mauritius", ["маврикий"], ["port louis", "belle rose", "ebene", "ebène"]),
    "IN": ("India", ["индия"],
           ["bangalore", "bengaluru", "бангалор", "mumbai", "мумбаи", "delhi", "дели",
            "hyderabad", "pune", "chennai", "gurgaon", "noida"]),
    "CN": ("China", ["китай"],
           ["beijing", "пекин", "shanghai", "шанхай", "shenzhen", "guangzhou", "hangzhou"]),
    "JP": ("Japan", ["япония", "nippon"], ["tokyo", "токио", "osaka", "осака", "kyoto"]),
    "KR": ("South Korea", ["korea", "корея", "южная корея"], ["seoul", "сеул", "busan"]),
    "SG": ("Singapore", ["сингапур"], []),
    "MY": ("Malaysia", ["малайзия"], ["kuala lumpur", "куала-лумпур", "penang"]),
    "ID": ("Indonesia", ["индонезия"], ["jakarta", "джакарта", "bali", "бали"]),
    "TH": ("Thailand", ["таиланд"], ["bangkok", "бангкок", "phuket"]),
    "VN": ("Vietnam", ["вьетнам"], ["hanoi", "ханой", "ho chi minh", "saigon"]),
    "PH": ("Philippines", ["филиппины"], ["manila", "манила", "cebu", "davao"]),
    "PK": ("Pakistan", ["пакистан"], ["karachi", "lahore", "islamabad"]),
    "BD": ("Bangladesh", ["бангладеш"], ["dhaka"]),
    "AU": ("Australia", ["австралия"],
           ["sydney", "сидней", "melbourne", "мельбурн", "brisbane", "perth", "canberra"]),
    "NZ": ("New Zealand", ["новая зеландия"], ["auckland", "окленд", "wellington"]),
    "BR": ("Brazil", ["brasil", "бразилия"],
           ["são paulo", "sao paulo", "сан-паулу", "rio de janeiro", "рио-де-жанейро",
            "belo horizonte", "curitiba", "porto alegre"]),
    "MX": ("Mexico", ["méxico", "мексика"],
           ["mexico city", "ciudad de méxico", "мехико", "guadalajara", "monterrey"]),
    "AR": ("Argentina", ["аргентина"], ["buenos aires", "буэнос-айрес", "córdoba"]),
    "CL": ("Chile", ["чили"], ["santiago", "сантьяго"]),
    "CO": ("Colombia", ["колумбия"], ["bogotá", "bogota", "богота", "medellín", "medellin"]),
    "PE": ("Peru", ["перу"], ["lima", "лима"]),
    "PA": ("Panama", ["панама"], ["panama city", "ciudad de panamá"]),
    "CR": ("Costa Rica", ["коста-рика"], ["san josé", "san jose"]),
})


def country_name(code: str) -> str:
    """Имя страны по коду — тем источникам, что отдают только код."""
    сведения = COUNTRIES.get((code or "").strip().upper())
    return сведения[0] if сведения else (code or "")


def country_codes(wanted: list, только=None) -> list:
    """Коды стран, которые человек назвал, — тем источникам, что умеют по ним
    ограничивать выдачу. Названо «ЕС» или ничего — не ограничиваем.

    «только» сужает ответ до тех кодов, которые источник вообще понимает: EURES
    знает свои тридцать одну страну, и спрашивать у него про Бразилию — впустую
    потратить запрос.
    """
    коды = []
    for код, (имя, псевдонимы, города) in COUNTRIES.items():
        if только is not None and код not in только:
            continue
        свои = {имя.lower(), *псевдонимы}
        if any(t in свои for t in wanted):
            коды.append(код)
    return коды


# individual countries: postings often name only the city ("Milano", "München")
# without the country — so a country token has to match its cities too
COUNTRY_MARKERS = {}
for _code, (_name, _aliases, _cities) in COUNTRIES.items():
    _markers = sorted({_name.lower(), *_aliases, *_cities})
    for _spelling in {_name.lower(), *_aliases}:
        COUNTRY_MARKERS[_spelling] = _markers

# Every place we know at all, as one list — so we can answer the question "is
# anything named here besides the word 'remote'". Without Kenya on this list,
# "Kenya, Remote" counted as work-from-anywhere and went into a search for Russia.
ALL_PLACES = sorted({m for _markers in COUNTRY_MARKERS.values() for m in _markers})


# A whole region rather than a single country: a job named this way suits anyone
# searching inside that region.
WIDE_EU_MARKERS = ["europe", "european union", "eu", "emea",
                   "dach", "benelux", "nordics", "baltics", "европ"]


# Regions and countries outside our two lists. Without them "APAC, Remote" would
# count as work-from-anywhere and turn up in a search for Europe.
OTHER_PLACE_MARKERS = [
    "apac", "latam", "emea excl", "anz", "australia", "new zealand",
    "canada", "brazil", "brasil", "argentina", "mexico", "india", "singapore",
    "japan", "china", "korea", "israel", "uae", "dubai", "south africa",
    "united kingdom", " uk ", "uk-", "london", "manchester", "edinburgh",
]


_REMOTE_TOKENS = ("remote", "удаленно", "удалённо", "anywhere", "везде")


def _only_remote(wanted: list) -> bool:
    """The person named "remote" and nothing else — then it really is from anywhere."""
    return all(t in _REMOTE_TOKENS for t in wanted)


def _names_a_place(loc: str) -> bool:
    """Whether the string names any place at all besides the word "remote"."""
    return (_has(loc, US_MARKERS) or _has(loc, EU_MARKERS)
            or _has(loc, OTHER_PLACE_MARKERS) or _has(loc, ALL_PLACES))


def location_ok(location: str, wanted: list, include_remote: bool = True) -> bool:
    if not wanted:
        return True
    loc = f" {(location or '').lower()} "
    # "Remote" is not the same as "from anywhere". This check used to come first
    # and let through everything the word appeared in: "United States, Remote"
    # sailed into a search for Germany as if nothing were wrong. On a real run
    # from a SAP integrator's CV, eight of thirty-two jobs turned out to be from
    # one American outfit, and not one of them suited a person in Europe. The
    # model even noticed and wrote "US only" — and the filter, which exists for
    # exactly this, let it through.
    #
    # If a country is named next to "remote", the country decides. If nothing is
    # named, the work really is from anywhere, and we let it through.
    if include_remote and _has(loc, REMOTE_MARKERS) and not _names_a_place(loc):
        return True
    if not location:
        return True  # an unknown location is not cut off — triage will decide
    for token in wanted:
        if token in ("eu", "ес", "europe", "европа", "евросоюз"):
            if _has(loc, EU_MARKERS):
                return True
        elif token in ("us", "usa", "сша", "united states", "америка"):
            if _has(loc, US_MARKERS):
                return True
        elif token in ("remote", "удаленно", "удалённо"):
            # The same rule as for the "remote too" checkbox: "remote" is not
            # "from anywhere". The previous fix closed only the checkbox, while
            # people often write this word themselves as well, among their own
            # places — and the hole opened again. On a run for a front-end
            # developer searching in Russia, the top of the list held "USA,
            # Remote" and "Sunnyvale, CA": they had written «Россия, Москва,
            # Санкт-Петербург, удалённо», and that last word let everything past.
            #
            # If the person named no places besides "remote", they really are
            # willing to work from anywhere — then we let any remote job through.
            # And if places are named, they decide where this remote job fits.
            if _has(loc, REMOTE_MARKERS) and (
                    not _names_a_place(loc) or _only_remote(wanted)):
                return True
        elif token in COUNTRY_MARKERS:
            if _has(loc, COUNTRY_MARKERS[token]):
                return True
            # "Remote within Europe" suits a person searching in Germany: their
            # country is inside the named region. Without this proviso, refusing
            # a blanket "remote" would have thrown these out along with the
            # American ones.
            if _has(loc, WIDE_EU_MARKERS):
                return True
        elif token in loc:
            return True
    return False


# Postings state the right to work in words, and almost always the same ones.
#
# Asking the model about it is useless: on Dmitry Kirilyak's run the profile said
# sponsorship was needed for the US and the EU, that line went into the prompt —
# and in none of the hundred and nine pieces of reasoning was a visa mentioned
# even once. Yet for the person it is the main thing: Viktor Belonogov had
# twenty-six Canadian jobs that cannot be entered without sponsorship, and that
# has to be known before applying, not after.
#
# The order matters: "no visa sponsorship" contains "visa sponsorship", so we
# look for the refusals first.
_NO_SPONSORSHIP = [
    "no sponsorship", "no visa sponsorship", "not able to sponsor", "unable to sponsor",
    "will not sponsor", "do not sponsor", "does not sponsor", "cannot sponsor",
    "without sponsorship", "sponsorship is not", "sponsorship not available",
    "no relocation", "relocation is not", "must be authorized to work",
    "must be legally authorized", "must already have", "must have the right to work",
    "authorized to work in the united states", "us work authorization required",
    "eu work permit required", "valid work permit required", "no work permit",
    "keine visa", "kein sponsoring", "arbeitserlaubnis erforderlich",
]
_SPONSORSHIP = [
    "visa sponsorship", "we sponsor", "sponsorship available", "sponsorship provided",
    "relocation package", "relocation support", "relocation assistance",
    "visa support", "work permit support", "blue card", "we help with the visa",
    "visa assistance", "umzugshilfe", "visum", "sponsoring möglich",
]


def visa_stance(job: dict) -> str:
    """What the posting says about the right to work: "no", "yes" or nothing.

    Nothing is the commonest answer, and an honest one: silence means neither
    refusal nor agreement. We will not guess on the employer's behalf, but if
    they have said it, the person should see it without opening the posting.
    """
    text = f"{job.get('title', '')} {job.get('description', '')}".lower()
    if any(p in text for p in _NO_SPONSORSHIP):
        return "no"
    if any(p in text for p in _SPONSORSHIP):
        return "yes"
    return ""


# US state abbreviations — they end up in job titles and get in the way of seeing
# that the position is one and the same: "Restaurant Assistant Manager (Austin) TX".
_US_STATES = set(
    "tx ca ny fl il wa ma co pa oh ga nc mi az va nj tn in mo md wi mn sc al la "
    "ky or ok ct ut ia nv ar ms ks nm ne wv id hi nh me ri mt de sd nd ak vt wy".split())


def _role(title: str, location: str) -> list:
    """The job title without the city, the state and the notes in brackets.

    A restaurant chain posts one and the same job at every one of its locations,
    changing the city in the title: "Restaurant Assistant Manager (Austin) TX",
    "… Fort Worth", "… - Kyle TX". For a person this is one job, and ten lines in
    a row read as ten different ones.
    """
    s = re.sub(r"\([^)]*\)", " ", (title or "").lower())
    s = re.split(r"\s[-–—|]\s", s)[0]
    places = set(re.findall(r"\w+", (location or "").lower()))
    return [w for w in re.findall(r"[a-zа-яё]+", s)
            if w not in places and w not in _US_STATES and len(w) > 1]


def group_same_role(jobs: list) -> list:
    """Collapses one company's jobs for one and the same position.

    Returns the same jobs, but the first of each group gains a "siblings" field
    holding the rest. Nothing is thrown away: a person may be looking for work in
    Dallas specifically, and we are not going to decide for them which city they
    need.

    Measured on Viktor Belonogov's run: of a hundred and eighteen jobs above the
    threshold, the first twenty lines belonged to four employers, and ten in a
    row to a single chain, "Pollo Regio". After collapsing there are ninety-eight
    lines, and the chain takes up one.

    The city is stripped from the title, but a short role also swallows a long
    one when it is its beginning: in "Restaurant Assistant Manager Frisco" the
    city is not in the location field (that says "Little Elm"), and there is no
    other way to remove it.
    """
    roles = [(j, _role(j.get("title", ""), j.get("location", ""))) for j in jobs]
    stems: dict = {}           # company → the stems found for it
    firsts: dict = {}          # (company, stem) → the group's first job
    order = []
    for j, words in sorted(roles, key=lambda p: len(p[1])):
        c = (j.get("company") or "").strip().lower()
        if not c or not words:
            continue
        ours = stems.setdefault(c, [])
        stem = next((s for s in ours if words[:len(s)] == s), None)
        if stem is None:
            stem = words
            ours.append(words)
        j["_group"] = (c, " ".join(stem))
    for j in jobs:
        key = j.get("_group")
        j.pop("_group", None)
        if key is None:
            order.append(j)
            continue
        if key in firsts:
            firsts[key].setdefault("siblings", []).append(j)
        else:
            firsts[key] = j
            order.append(j)
    return order


# Professions you cannot simply take up in another country: a local degree, an
# examination or an entry in a register is required.
#
# Elisabetta Matassi is an advocate who passed the bar in Italy. The program
# found her twenty-four lawyer jobs, every one of them in her profession: Legal
# Counsel, Associate Lawyer, Corporate Legal Counsel. Only — Sweden 9, Greece 7,
# Italy 2 — and an Italian advocate is not an advocate in Sweden until the
# qualification is recognised. Neither the model nor the filters know this, and
# the person reads the list as a list of work available to them.
#
# We do not undertake to decide whether her degree will be recognised: that is a
# matter for the authorities, and the answer depends on the country, the years of
# practice and the agreements between them. But we are obliged to say that the
# question exists at all — otherwise twenty-four hopes turn out to be empty.
REGULATED = {
    "law": ["lawyer", "attorney", "avvocat", "solicitor", "barrister", "notaio",
            "notary", "rechtsanwalt", "advokat", "юрист", "адвокат", "нотариус",
            "abogado", "avocat", "prawnik", "radca prawny"],
    "medicine": ["doctor", "physician", "surgeon", "nurse", "medico", "arzt",
                 "krankenschwester", "pharmacist", "apotheker", "dentist",
                 "врач", "медсестра", "фармацевт", "стоматолог", "psychotherap"],
    "engineering": ["chartered engineer", "structural engineer", "ingegnere",
                    "geodet", "surveyor", "инженер-проектировщик"],
    "architecture": ["architect", "architetto", "architekt", "архитектор"],
    "accounting": ["auditor", "chartered accountant", "revisor", "wirtschaftsprüfer",
                   "commercialista", "аудитор"],
    "teaching": ["teacher", "lehrer", "insegnante", "nauczyciel", "учитель"],
    "veterinary": ["veterinar", "veterinär", "ветеринар"],
}


def regulated_profession(roles: str, cv: str = "") -> str:
    """Whether a profession is named that needs recognition abroad.

    We look at the roles first of all: the word "lawyer" can turn up in the CV of
    someone who does not work as one. The roles are what a person calls
    themselves.
    """
    where = (roles or "").strip().lower()
    if not where:
        # No roles at all — then the CV, and only its opening lines: that is
        # where the job title is. Further down, "lawyer" turns up for someone who
        # merely worked alongside lawyers, and the warning would go to the wrong
        # person.
        where = (cv or "")[:400].lower()
    for field, words in REGULATED.items():
        if any(w in where for w in words):
            return field
    return ""


def has_excluded(job: dict, exclude_terms: list) -> bool:
    """Stop words match on word boundaries, not as substrings: excluding "java"
    must not kill JavaScript, nor "go" kill Google."""
    if not exclude_terms:
        return False
    text = f"{job.get('title', '')} {job.get('company', '')}".lower()
    return any(
        re.search(rf"(?<![a-zа-яё0-9]){re.escape(t)}(?![a-zа-яё0-9])", text)
        for t in exclude_terms
    )


def posted_ok(job: dict, since: str = "", until: str = "") -> bool:
    """Was the job posted within the period asked for? Dates are YYYY-MM-DD.

    A job with no date is kept. Sources are uneven about this — many aggregators
    give no date at all — and dropping everything undated would quietly throw
    away most of the search the moment somebody set a period. Better to let
    through what we cannot judge than to hide it without saying so.
    """
    if not since and not until:
        return True
    posted = str(job.get("posted_at") or "").strip()[:10]
    if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", posted):
        return True                      # no date, or an unreadable one — not ours to judge
    if since and posted < since:
        return False
    if until and posted > until:
        return False
    return True


def looks_like_agency(company: str) -> bool:
    name = (company or "").lower()
    return any(m in name for m in AGENCY_MARKERS)


# plainly non-engineering, non-technical roles — they can be dropped before the
# expensive model call. Self-adjusting: we do not cut when the title matches the
# profile's roles or skills (a "Recruiter" profile keeps "Technical Recruiter").
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
    """Plainly the wrong trade? True if the title is clearly non-technical and does
    not overlap with the person's roles or skills."""
    title = f" {(job.get('title') or '').lower()} "
    if not any(m in title for m in OFF_TARGET_MARKERS):
        return False
    # the title matches the profile (roles or skills) — keep it, let the model decide
    title_words = set(re.findall(r"[a-zа-яё0-9+#.]{3,}", title))
    return not (title_words & keep_terms)
