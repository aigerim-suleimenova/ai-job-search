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
    # Сокращения штатов. Раньше писались с запятой — «, ca» — и искались простой
    # подстрокой, отчего «Calgary, Canada» совпадало с Калифорнией и канадские
    # вакансии шли в поиск по США. Двадцать шесть штук за один прогон. Теперь
    # ищется целым словом, и «, canada» больше ни с чем не путается.
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


def _есть(loc: str, маркеры) -> bool:
    """Встречается ли в строке хоть один признак — целым словом, а не куском.

    Простое вхождение подводило одинаково всюду: короткий признак садился внутрь
    длинного слова. «ca» — сокращение Калифорнии — находилось в «Calgary,
    Canada», и канадские вакансии шли в поиск по США: двадцать шесть штук за
    один прогон у человека, который Канады не просил. Чем короче признак, тем
    чаще так выходит, а короткие признаки как раз самые нужные — коды штатов и
    названия стран.
    """
    return any(re.search(rf"(?<!\w){re.escape(m)}(?!\w)", loc) for m in маркеры)


def job_key(company: str, title: str) -> str:
    norm = re.sub(r"\s+", " ", f"{(company or '').lower().strip()}|{(title or '').lower().strip()}")
    return hashlib.sha1(norm.encode()).hexdigest()


def parse_locations(raw: str) -> list:
    return [t.strip().lower() for t in re.split(r"[,;]", raw or "") if t.strip()]


# Страны, каждая одной строкой: код — каким её называет источник, имя — каким
# её пишем мы, дальше как её пишут люди и её города.
#
# Код понадобился из-за EURES: он отдаёт место двухбуквенным кодом и ничем
# больше — «BE», «SE», «FR». Мы клали этот код в поле места как есть, а фильтр
# сравнивал его со словом «нидерланды» — и выбрасывал всё. Двести двадцать семь
# вакансий за прогон, до единой, каждый раз. И это при том, что EURES —
# единственный наш источник, который знает все профессии и все страны ЕС: ради
# него всё и затевалось, а он не отдал ни одной вакансии ни разу.
#
# Имена нужны были и сами по себе. Страны, кроме Италии и Германии, здесь не
# значились вовсе, и «Франция» сверялась простым вхождением: место «Paris,
# France» человеку, написавшему «Франция», не годилось. По-русски искать можно
# было в двух странах из тридцати одной.
#
# Города — потому что в объявлении часто стоит только город: «Milano»,
# «München», и страну надо узнать по нему.
#
#     код: (имя, как ещё пишут, города)
СТРАНЫ = {
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
EURES_КОДЫ = frozenset(СТРАНЫ)

# Остальной мир. Без него выходило вот что: человек пишет «Россия, Омск», а
# «Москва» отсеивается — слова «россия» в строке нет, а справочника, который
# связал бы одно с другим, не было. И наоборот, «Kenya, Remote» проходило в
# поиск по России как «работа откуда угодно»: Кении в списках не значилось,
# значит место как бы и не названо.
#
# Города здесь скупее, чем у европейских: список нужен, чтобы узнать страну и не
# перепутать её с чужой, а не чтобы знать все её города.
СТРАНЫ.update({
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
    сведения = СТРАНЫ.get((code or "").strip().upper())
    return сведения[0] if сведения else (code or "")


def country_codes(wanted: list, только=None) -> list:
    """Коды стран, которые человек назвал, — тем источникам, что умеют по ним
    ограничивать выдачу. Названо «ЕС» или ничего — не ограничиваем.

    «только» сужает ответ до тех кодов, которые источник вообще понимает: EURES
    знает свои тридцать одну страну, и спрашивать у него про Бразилию — впустую
    потратить запрос.
    """
    коды = []
    for код, (имя, псевдонимы, города) in СТРАНЫ.items():
        if только is not None and код not in только:
            continue
        свои = {имя.lower(), *псевдонимы}
        if any(t in свои for t in wanted):
            коды.append(код)
    return коды


# individual countries: postings often name only the city ("Milano", "München")
# without the country — so a country token has to match its cities too
COUNTRY_MARKERS = {}
for _код, (_имя, _псевдонимы, _города) in СТРАНЫ.items():
    _маркеры = sorted({_имя.lower(), *_псевдонимы, *_города})
    for _как_пишут in {_имя.lower(), *_псевдонимы}:
        COUNTRY_MARKERS[_как_пишут] = _маркеры

# Все места, какие мы вообще знаем, одним списком — чтобы отвечать на вопрос
# «названо ли тут хоть что-то, кроме слова „удалённо“». Без Кении в этом списке
# «Kenya, Remote» считалось работой откуда угодно и шло в поиск по России.
ВСЕ_МЕСТА = sorted({м for _маркеры in COUNTRY_MARKERS.values() for м in _маркеры})


# Область целиком, а не отдельная страна: вакансия, названная так, подходит
# любому, кто ищет внутри этой области.
WIDE_EU_MARKERS = ["europe", "european union", "eu", "emea",
                   "dach", "benelux", "nordics", "baltics", "европ"]


# Области и страны за пределами двух наших списков. Без них «APAC, Remote»
# считалось бы работой откуда угодно и приходило бы в поиск по Европе.
OTHER_PLACE_MARKERS = [
    "apac", "latam", "emea excl", "anz", "australia", "new zealand",
    "canada", "brazil", "brasil", "argentina", "mexico", "india", "singapore",
    "japan", "china", "korea", "israel", "uae", "dubai", "south africa",
    "united kingdom", " uk ", "uk-", "london", "manchester", "edinburgh",
]


_REMOTE_TOKENS = ("remote", "удаленно", "удалённо", "anywhere", "везде")


def _only_remote(wanted: list) -> bool:
    """Человек назвал «удалённо» и больше ничего — значит и правда откуда угодно."""
    return all(t in _REMOTE_TOKENS for t in wanted)


def _names_a_place(loc: str) -> bool:
    """Названо ли в строке хоть какое-то место, кроме слова «удалённо»."""
    return (_есть(loc, US_MARKERS) or _есть(loc, EU_MARKERS)
            or _есть(loc, OTHER_PLACE_MARKERS) or _есть(loc, ВСЕ_МЕСТА))


def location_ok(location: str, wanted: list, include_remote: bool = True) -> bool:
    if not wanted:
        return True
    loc = f" {(location or '').lower()} "
    # «Удалённо» — не то же самое, что «откуда угодно». Проверка стояла первой и
    # пропускала всё, где встретилось это слово: «United States, Remote» проходило
    # в поиск по Германии как ни в чём не бывало. На настоящем прогоне по резюме
    # SAP-интегратора из тридцати двух вакансий восемь оказались из одной
    # американской конторы, и человеку из Европы не годилась ни одна. Модель это
    # даже заметила и написала «US only» — а фильтр, который для того и стоит,
    # пропустил.
    #
    # Если рядом с «удалённо» названа страна, она и решает. Если не названо
    # ничего — работа и правда откуда угодно, и её пропускаем.
    if include_remote and _есть(loc, REMOTE_MARKERS) and not _names_a_place(loc):
        return True
    if not location:
        return True  # an unknown location is not cut off — triage will decide
    for token in wanted:
        if token in ("eu", "ес", "europe", "европа", "евросоюз"):
            if _есть(loc, EU_MARKERS):
                return True
        elif token in ("us", "usa", "сша", "united states", "америка"):
            if _есть(loc, US_MARKERS):
                return True
        elif token in ("remote", "удаленно", "удалённо"):
            # То же правило, что и для галочки «удалённые тоже»: «удалённо» —
            # не «откуда угодно». Прошлая починка закрыла только галочку, а это
            # слово человек часто пишет ещё и сам, среди своих мест, — и дыра
            # открывалась снова. На прогоне для фронтендера, ищущего в России,
            # наверху списка стояли «USA, Remote» и «Sunnyvale, CA»: он написал
            # «Россия, Москва, Санкт-Петербург, удалённо», и последнее слово
            # пропускало всё подряд.
            #
            # Если человек не назвал никаких мест, кроме «удалённо», он и правда
            # готов работать откуда угодно — тогда пропускаем любую удалённую.
            # А если места названы, они и решают, куда эта удалённая годится.
            if _есть(loc, REMOTE_MARKERS) and (
                    not _names_a_place(loc) or _only_remote(wanted)):
                return True
        elif token in COUNTRY_MARKERS:
            if _есть(loc, COUNTRY_MARKERS[token]):
                return True
            # «Удалённо по Европе» человеку, который ищет в Германии, годится:
            # страна входит в названную область. Без этой оговорки отказ от
            # огульного «удалённо» выбросил бы вместе с американскими и такие.
            if _есть(loc, WIDE_EU_MARKERS):
                return True
        elif token in loc:
            return True
    return False


# Про право на работу объявления пишут словами, и почти всегда одними и теми же.
#
# Спрашивать об этом модель бесполезно: на прогоне Дмитрия Кириляка в профиле
# стояло «нужно спонсорство для США и ЕС», строка попадала в запрос — и ни в
# одном из ста девяти доводов виза не была упомянута ни разу. А для человека это
# главное: у Виктора Белоногова двадцать шесть канадских вакансий, куда без
# спонсорства не попасть, и узнать об этом надо до отклика, а не после.
#
# Порядок важен: «no visa sponsorship» содержит «visa sponsorship», поэтому
# отказы ищем первыми.
_НЕТ_СПОНСОРСТВА = [
    "no sponsorship", "no visa sponsorship", "not able to sponsor", "unable to sponsor",
    "will not sponsor", "do not sponsor", "does not sponsor", "cannot sponsor",
    "without sponsorship", "sponsorship is not", "sponsorship not available",
    "no relocation", "relocation is not", "must be authorized to work",
    "must be legally authorized", "must already have", "must have the right to work",
    "authorized to work in the united states", "us work authorization required",
    "eu work permit required", "valid work permit required", "no work permit",
    "keine visa", "kein sponsoring", "arbeitserlaubnis erforderlich",
]
_ЕСТЬ_СПОНСОРСТВО = [
    "visa sponsorship", "we sponsor", "sponsorship available", "sponsorship provided",
    "relocation package", "relocation support", "relocation assistance",
    "visa support", "work permit support", "blue card", "we help with the visa",
    "visa assistance", "umzugshilfe", "visum", "sponsoring möglich",
]


def visa_stance(job: dict) -> str:
    """Что объявление говорит про право на работу: «нет», «есть» или ничего.

    Ничего — самый частый ответ, и он честный: молчание не значит ни отказа, ни
    согласия. Догадываться за работодателя мы не станем, но если он сказал —
    человек должен это увидеть, не открывая объявления.
    """
    текст = f"{job.get('title', '')} {job.get('description', '')}".lower()
    if any(ф in текст for ф in _НЕТ_СПОНСОРСТВА):
        return "no"
    if any(ф in текст for ф in _ЕСТЬ_СПОНСОРСТВО):
        return "yes"
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
    """Was the job posted within the period asked for? Dates are ГГГГ-ММ-ДД.

    A job with no date is kept. Sources are uneven about this — many aggregators
    give no date at all — and dropping everything undated would quietly throw
    away most of the search the moment somebody set a period. Better to let
    through what we cannot judge than to hide it without saying so.
    """
    if not since and not until:
        return True
    posted = str(job.get("posted_at") or "").strip()[:10]
    if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", posted):
        return True                      # даты нет или она непонятная — не нам судить
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
