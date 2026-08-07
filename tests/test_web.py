"""How the program behaves on other people's sites.

Checked without the network: requests.get is stood in for, and a prepared text is
handed back instead of robots.txt. Otherwise the test would depend on the mood of
somebody else's server.
"""
import time

import pytest

from jobsearch.collectors import web


@pytest.fixture(autouse=True)
def чистый_кэш(monkeypatch):
    web._robots.clear()
    web._last_hit.clear()
    # Проверка адреса спрашивает у системы, куда ведёт имя. Тесты в сеть не
    # ходят — ни за страницами, ни за именами, — поэтому отвечаем сами, обычным
    # адресом из интернета. Сама проверка при этом остаётся в работе: подменять
    # её целиком значило бы проверять код, из которого её вынули.
    monkeypatch.setattr(web.socket, "getaddrinfo",
                        lambda host, port, **kw: [(2, 1, 6, "", ("93.184.216.34", port))])
    yield
    web._robots.clear()
    web._last_hit.clear()


class Ответ:
    def __init__(self, text="", status=200, headers=None):
        self.text = text
        self.status_code = status
        self.url = "https://example.com/"
        self.headers = headers or {}

    def raise_for_status(self):
        pass

    def close(self):
        pass


def подменить(monkeypatch, robots="", **kw):
    """Hands back the given robots.txt, and an empty page for every other address."""
    вызовы = []

    def fake_get(url, **rest):
        вызовы.append(url)
        if url.endswith("/robots.txt"):
            return Ответ(robots, kw.get("robots_status", 200))
        return Ответ("<html>ok</html>")

    monkeypatch.setattr(web.requests, "get", fake_get)
    return вызовы


def test_представляется_честно():
    ua = web.UA["User-Agent"]
    assert "Mozilla" not in ua, "маскировка под браузер вернулась"
    assert "ai-job-search" in ua
    assert "github.com" in ua, "по имени должно быть понятно, куда писать"


def test_запрет_в_robots_соблюдается(monkeypatch):
    подменить(monkeypatch, robots="User-agent: *\nDisallow: /careers\n")
    assert web.allowed("https://example.com/about") is True
    assert web.allowed("https://example.com/careers/all") is False


def test_запрещённая_страница_не_запрашивается(monkeypatch):
    вызовы = подменить(monkeypatch, robots="User-agent: *\nDisallow: /\n")
    assert web.get("https://example.com/careers", respect_robots=True) is None
    assert not [u for u in вызовы if not u.endswith("robots.txt")], "запрос всё-таки ушёл"


def test_без_robots_ходим_как_обычно(monkeypatch):
    подменить(monkeypatch, robots="", robots_status=404)
    assert web.allowed("https://example.com/careers") is True
    assert web.get("https://example.com/careers", respect_robots=True) is not None


def test_битый_robots_не_роняет(monkeypatch):
    подменить(monkeypatch, robots="\x00\x01 мусор ][")
    assert web.allowed("https://example.com/careers") is True


def test_пауза_между_запросами_к_одному_хосту(monkeypatch):
    подменить(monkeypatch)
    monkeypatch.setattr(web, "DELAY", 0.2)
    начало = time.monotonic()
    for _ in range(3):
        web.get("https://example.com/page")
    прошло = time.monotonic() - начало
    assert прошло >= 0.4, f"три запроса уложились в {прошло:.2f} c — паузы нет"


def test_разные_хосты_друг_друга_не_ждут(monkeypatch):
    подменить(monkeypatch)
    monkeypatch.setattr(web, "DELAY", 0.3)
    начало = time.monotonic()
    web.get("https://a.example/page")
    web.get("https://b.example/page")
    assert time.monotonic() - начало < 0.3, "хосты не должны стоять в общей очереди"


def test_robots_запрашивается_один_раз_на_хост(monkeypatch):
    вызовы = подменить(monkeypatch, robots="User-agent: *\nDisallow: /nope\n")
    monkeypatch.setattr(web, "DELAY", 0)
    for i in range(3):
        web.get(f"https://example.com/page{i}", respect_robots=True)
    assert len([u for u in вызовы if u.endswith("robots.txt")]) == 1


def test_crawl_delay_из_robots_уважается(monkeypatch):
    """The standard parser understands whole-number delays only — it ignores
    fractional ones silently, which is why we check a whole one."""
    подменить(monkeypatch, robots="User-agent: *\nCrawl-delay: 1\n")
    monkeypatch.setattr(web, "DELAY", 0.01)
    web.get("https://example.com/a", respect_robots=True)
    начало = time.monotonic()
    web.get("https://example.com/b", respect_robots=True)
    assert time.monotonic() - начало >= 0.9, "сайт просил ждать дольше — надо ждать"


# --- Куда мы не пойдём ---------------------------------------------------------
#
# Работодатели попадают в список по ссылкам внутри уже собранных вакансий, а
# вакансии пишет кто угодно. Ссылка на 127.0.0.1 — это просьба сходить внутрь
# машины и принести оттуда то, до чего снаружи не дотянуться.

ВНУТРЕННИЕ = [
    ("http://127.0.0.1:11434/api/tags", "127.0.0.1", "петля: там наша же Ollama"),
    ("http://localhost:8765/settings", "127.0.0.1", "петля по имени"),
    ("http://169.254.169.254/latest/meta-data/", "169.254.169.254", "ключи облачной учётки"),
    ("http://192.168.1.1/", "192.168.1.1", "чужой роутер в домашней сети"),
    ("http://10.0.0.5/admin", "10.0.0.5", "частная сеть"),
    ("http://[::1]/", "::1", "петля по IPv6"),
]


def адреса(monkeypatch, ip: str):
    семейство = 30 if ":" in ip else 2
    monkeypatch.setattr(web.socket, "getaddrinfo",
                        lambda host, port, **kw: [(семейство, 1, 6, "", (ip, port))])


@pytest.mark.parametrize("url,ip,почему", ВНУТРЕННИЕ)
def test_внутрь_машины_и_сети_не_ходим(monkeypatch, url, ip, почему):
    адреса(monkeypatch, ip)
    with pytest.raises(web.Refused):
        web.check(url)


@pytest.mark.parametrize("схема", ["file", "ftp", "gopher", "data", ""])
def test_чужие_схемы_отсекаются(схема):
    адрес = f"{схема}://example.com/x" if схема else "example.com/x"
    with pytest.raises(web.Refused):
        web.check(адрес)


def test_обычный_сайт_пропускается(monkeypatch):
    """Обратная сторона: закрыть внутреннее нельзя ценой того, что перестанет
    работать сбор вакансий."""
    адреса(monkeypatch, "93.184.216.34")
    web.check("https://boards-api.greenhouse.io/v1/boards/acme/jobs")


def test_имя_ведущее_внутрь_не_обманет(monkeypatch):
    """Имя может быть каким угодно, а вести на петлю: смотрим не на имя, а на то,
    куда оно разрешается."""
    адреса(monkeypatch, "127.0.0.1")
    with pytest.raises(web.Refused):
        web.check("https://careers.example.com/jobs")


def test_проверяется_каждый_переход(monkeypatch):
    """Иначе проверка стоила бы ничего: сайт отвечает «идите на 127.0.0.1»,
    requests послушно идёт, и проверенный первый адрес оказывается ни при чём."""
    куда = {"https://jobs.example.com/": ("93.184.216.34", 302, "http://127.0.0.1:11434/api/tags"),
            "http://127.0.0.1:11434/api/tags": ("127.0.0.1", 200, "")}
    сходили = []

    def getaddrinfo(host, port, **kw):
        ip = "127.0.0.1" if host in ("127.0.0.1", "localhost") else "93.184.216.34"
        return [(2, 1, 6, "", (ip, port))]

    def fake_get(url, **kw):
        сходили.append(url)
        _, статус, location = куда[url]
        return Ответ(status=статус, headers={"location": location} if location else {})

    monkeypatch.setattr(web.socket, "getaddrinfo", getaddrinfo)
    monkeypatch.setattr(web.requests, "get", fake_get)

    with pytest.raises(web.Refused):
        web.get("https://jobs.example.com/")

    assert сходили == ["https://jobs.example.com/"], "по переходу всё-таки сходили"


def test_переход_на_обычный_сайт_проходится(monkeypatch):
    """И снова обратная сторона: переезды сайтов — обычное дело."""
    def fake_get(url, **kw):
        if url == "https://jobs.example.com/":
            return Ответ(status=301, headers={"location": "/careers/"})
        return Ответ("<html>вакансии</html>")

    monkeypatch.setattr(web.requests, "get", fake_get)
    r = web.get("https://jobs.example.com/")
    assert "вакансии" in r.text


def test_кольцо_переходов_обрывается(monkeypatch):
    monkeypatch.setattr(web.requests, "get",
                        lambda url, **kw: Ответ(status=302, headers={"location": "/снова"}))
    monkeypatch.setattr(web, "DELAY", 0)
    with pytest.raises(web.Refused):
        web.get("https://example.com/")


def test_robots_у_внутреннего_адреса_тоже_не_спрашиваем(monkeypatch):
    """Спросить «а можно к вам?» у 127.0.0.1 — это уже сходить к 127.0.0.1."""
    адреса(monkeypatch, "127.0.0.1")
    сходили = []
    monkeypatch.setattr(web.requests, "get",
                        lambda url, **kw: (сходили.append(url), Ответ(""))[1])

    web.allowed("http://127.0.0.1:11434/robots-ok")

    assert сходили == [], "за robots.txt всё-таки сходили внутрь машины"


# --- Сколько источников называем человеку ----------------------------------------

def test_считаем_источники_а_не_пишем_словом():
    """На странице «Модель» годами висело «собираются с девяти агрегаторов», а их
    было двенадцать: число стояло в переводе, источники — в коде, и сверить их
    было нечем."""
    from jobsearch import config
    from jobsearch.collectors import aggregators
    сейчас = aggregators.enabled(config.DEFAULTS)
    # Одиннадцать: немецкая биржа выключена — её открытый API отвечает отказом
    # на любой путь, и включённой она давала только строку с ошибкой в
    # «Покрытии» каждый прогон.
    assert len(сейчас) == 11, f"источников {len(сейчас)}, а плашка обещает другое"
    assert "arbeitsagentur" not in {и[4] for и in сейчас}


def test_список_источников_и_опрос_не_расходятся(monkeypatch):
    """enabled() и collect() должны ходить по одному списку, иначе счётчик снова
    начнёт обещать не то, что делается."""
    from jobsearch import config
    from jobsearch.collectors import aggregators
    # Сборщики подменяем: проверяем, кого позвали, а не что ответит сеть.
    for *_, сборщик in aggregators.ИСТОЧНИКИ:
        monkeypatch.setattr(aggregators, сборщик, lambda cfg, log: [])
    опрошены = []
    cfg = config.DEFAULTS
    aggregators.collect(cfg, lambda *a: None, опрошены)
    имена_опроса = {c["name"] for c in опрошены}
    имена_списка = {и[2] for и in aggregators.enabled(cfg)}
    assert имена_опроса == имена_списка


# --- Место, пришедшее кодом страны ----------------------------------------------

def test_код_страны_превращается_в_имя():
    """EURES отдаёт место одним кодом — «BE», «SE», «FR», — и мы клали его в
    поле места как есть. Фильтр сравнивал код со словом «нидерланды» и
    выбрасывал всё: 227 вакансий за прогон, до единой, каждый раз. А EURES —
    единственный источник, знающий все профессии и все страны ЕС."""
    from jobsearch import filters
    assert filters.country_name("NL") == "Netherlands"
    assert filters.country_name("fr") == "France"
    assert filters.country_name("") == ""
    assert filters.country_name("ZZ") == "ZZ", "незнакомый код не выдумываем"


def test_страна_из_кода_проходит_фильтр():
    from jobsearch import filters
    хочу = filters.parse_locations("Италия, Германия, Франция, Нидерланды")
    for код in ("IT", "DE", "FR", "NL"):
        место = filters.country_name(код)
        assert filters.location_ok(место, хочу, True), f"{код} → {место} не прошло"
    assert not filters.location_ok(filters.country_name("NO"), хочу, True)


def test_по_русски_ищутся_не_только_италия_с_германией():
    """Страны, кроме этих двух, здесь не значились вовсе, и «Франция» сверялась
    простым вхождением: «Paris, France» человеку, написавшему «Франция», не
    годилось. По-русски искать можно было в двух странах из тридцати одной."""
    from jobsearch import filters
    случаи = [("Paris, France", "Франция"), ("Madrid", "Испания"),
              ("Amsterdam", "Нидерланды"), ("Warszawa", "Польша"),
              ("Lisboa", "Португалия"), ("Stockholm", "Швеция"),
              ("Praha", "Чехия"), ("Wien", "Австрия")]
    for место, страна in случаи:
        assert filters.location_ok(место, filters.parse_locations(страна), True), \
            f"«{место}» не нашлось по слову «{страна}»"


def test_коды_стран_для_источника():
    """Запрос к EURES не был ограничен странами, и на Италию с Германией
    приезжали Норвегия с Бельгией."""
    from jobsearch import filters
    коды = filters.country_codes(filters.parse_locations("Италия, Germany, нидерланды"))
    assert set(коды) == {"IT", "DE", "NL"}
    assert filters.country_codes(filters.parse_locations("ЕС")) == [], \
        "назвал весь союз — не сужаем"


# --- Ничейные запросы ------------------------------------------------------------

def test_ничейные_запросы_не_уходят_в_источники():
    """Регина, конструктор белья, посмотрев выдачу: «возможно, алгоритм сбивает
    название вакансии Product Developer». Так и было — мы сами его и просили, а
    источники честно приносили химиков."""
    from jobsearch.collectors.aggregators import _search_terms
    cfg = {"profile": {"roles": "Lingerie Pattern Maker, Technical Designer, "
                                "Product Developer, Garment Technologist", "skills": ""},
           "search": {}}
    вышло = _search_terms(cfg)
    assert "Product Developer" not in вышло
    assert "Technical Designer" not in вышло
    assert "Lingerie Pattern Maker" in вышло and "Garment Technologist" in вышло


def test_у_кого_все_роли_ничейные_запросы_остаются():
    """У программиста «Developer» — единственное слово, каким он себя зовёт."""
    from jobsearch.collectors.aggregators import _search_terms
    cfg = {"profile": {"roles": "Developer, Senior Engineer", "skills": ""}, "search": {}}
    assert _search_terms(cfg) == ["Developer", "Senior Engineer"]


# --- Места: то, что подводило на настоящих прогонах --------------------------------

МЕСТА = [
    # (что человек написал, что стоит в вакансии, годится ли)
    #
    # Канада приезжала в поиск по США двадцать шесть раз за прогон: «ca» —
    # сокращение Калифорнии — искалось простой подстрокой и находилось в
    # «Calgary, Canada».
    ("США, Германия, ЕС", "Calgary, Canada", False),
    ("США, Германия, ЕС", "Ottawa, Canada", False),
    ("США, Германия, ЕС", "Berkeley, CA", True),
    # А Греция и Швеция приходили правильно: человек просил ЕС, они в ЕС.
    ("США, Германия, ЕС", "Gaios, Greece", True),
    ("США, Германия, ЕС", "Umeå, Sweden", True),
    ("США, Германия, ЕС", "Missoula, United States", True),
    # России в справочнике не было вовсе, и «Москва» отсеивалась у того, кто
    # написал «Россия»: слова «россия» в строке нет, а связать их было нечем.
    ("Россия, Омск, удалённо", "Москва", True),
    ("Россия, Омск, удалённо", "Омск", True),
    ("Россия, Омск, удалённо", "Novosibirsk", True),
    # И наоборот: страна, которой нет в списках, читалась как «место не названо»,
    # то есть «работа откуда угодно», и шла в любой поиск.
    ("Россия, Омск, удалённо", "Kenya, Remote", False),
    ("Россия, Омск, удалённо", "Panama, Remote", False),
    ("Германия, ЕС", "Amman, Jordan", False),
    ("Германия, ЕС", "Morocco, Remote", False),
    # Настоящая удалёнка без страны — годится всем.
    ("Россия, Омск, удалённо", "Remote", True),
    ("Германия, Нидерланды, ЕС", "Walldorf, Germany", True),
]


@pytest.mark.parametrize("написал,вакансия,годится", МЕСТА)
def test_места_из_настоящих_прогонов(написал, вакансия, годится):
    from jobsearch import filters
    assert filters.location_ok(вакансия, filters.parse_locations(написал), True) is годится


def test_признак_ищется_целым_словом():
    """Короткий признак садился внутрь длинного слова. Это и есть корень всех
    промахов выше: «ca» в «Canada», «us» нашлось бы в «Belarus»."""
    from jobsearch import filters
    assert filters._has(" berkeley, ca ", ["ca"]) is True
    assert filters._has(" calgary, canada ", ["ca"]) is False
    assert filters._has(" minsk, belarus ", ["us"]) is False
    assert filters._has(" austin, us ", ["us"]) is True


def test_eures_спрашиваем_только_про_свои_страны():
    """EURES знает тридцать одну страну. Спрашивать у него про Бразилию —
    впустую потратить запрос, а их всего по одному на слово."""
    from jobsearch import filters
    места = filters.parse_locations("США, Германия, Бразилия, Нидерланды")
    assert set(filters.country_codes(места, only=filters.EURES_CODES)) == {"DE", "NL"}
    assert set(filters.country_codes(места)) == {"US", "DE", "BR", "NL"}


# --- EURES спрашиваем кодами профессий, а не угаданными словами ------------------

def test_роль_отображается_в_коды_справочника(monkeypatch):
    """Поиск по словам у EURES зависит от угаданного слова, а не от смысла.
    Померено вживую, из пятидесяти вакансий по профессии оказывалось:
    «dressmaker» 28, «lingerie» 3, «Lingerie Pattern Maker» 1, «seamstress» 0.
    Швея по-английски — seamstress, и по нему не находится ничего."""
    from jobsearch.collectors import esco
    esco.forget()
    monkeypatch.setattr(esco.web, "get", lambda url, **kw: type("О", (), {
        "status_code": 200,
        "json": lambda s: {"_embedded": {"results": [
            {"uri": "http://data.europa.eu/esco/occupation/aaa", "title": "dressmaker"},
            {"uri": "http://data.europa.eu/esco/occupation/bbb", "title": "tailor"}]}},
    })())
    assert esco.occupations("seamstress") == [
        "http://data.europa.eu/esco/occupation/aaa",
        "http://data.europa.eu/esco/occupation/bbb"]


def test_справочник_молчит_роль_не_выдумываем(monkeypatch):
    from jobsearch.collectors import esco
    esco.forget()
    monkeypatch.setattr(esco.web, "get", lambda url, **kw: (_ for _ in ()).throw(
        esco.requests.RequestException("нет связи")))
    assert esco.occupations("seamstress") == []
    assert esco.occupations("") == []


def test_eures_спрашивает_и_кодами_и_словами(monkeypatch):
    """Коды дают плотность, слова — широту, и одно другое не заменяет: роли,
    выписанные из резюме, отображаются в справочнике не всегда точно."""
    from jobsearch import config
    from jobsearch.collectors import aggregators, esco
    esco.forget()
    отправленное = []

    class Ответ:
        status_code = 200

        def raise_for_status(self):
            pass

        def json(self):
            return {"jvs": []}

    monkeypatch.setattr(esco, "occupations", lambda роль, **kw: ["esco:" + роль])
    monkeypatch.setattr(aggregators.web, "post",
                        lambda url, **kw: отправленное.append(kw["json"]) or Ответ())
    cfg = dict(config.DEFAULTS)
    cfg["profile"] = {**cfg["profile"], "roles": "Швея, Портная", "skills": ""}
    cfg["search"] = {**cfg["search"], "locations": "Германия"}

    aggregators.eures(cfg, lambda *a: None)

    с_кодами = [з for з in отправленное if з["occupationUris"]]
    со_словами = [з for з in отправленное if з["keywords"]]
    assert с_кодами, "кодами не спросили"
    assert со_словами, "словами не спросили"
    assert с_кодами[0]["keywords"] == [], "коды и слова смешались в одном запросе"


# --- Одна и та же должность в разных городах ------------------------------------

def test_сеть_заведений_схлопывается_в_одну_строку():
    """Сеть вешает одну вакансию по всем точкам, меняя город в названии. У
    Виктора Белоногова десять строк подряд принадлежали одной сети «Pollo
    Regio», и он читал их как десять разных работ."""
    from jobsearch import filters
    вакансии = [
        {"title": "Restaurant Assistant Manager (Austin) TX", "company": "Pollo Regio",
         "location": "Austin, United States", "score": 95},
        {"title": "Restaurant Assistant Manager Fort Worth", "company": "Pollo Regio",
         "location": "Fort Worth, United States", "score": 95},
        {"title": "Restaurant Assistant Manager - Kyle TX", "company": "Pollo Regio",
         "location": "Kyle, United States", "score": 95},
        {"title": "Restaurant Manager", "company": "Pollo Regio",
         "location": "Dallas, United States", "score": 90},
    ]
    вышло = filters.group_same_role(вакансии)

    assert len(вышло) == 2, [j["title"] for j in вышло]
    помощник = вышло[0]
    assert помощник["title"].startswith("Restaurant Assistant Manager")
    assert len(помощник["siblings"]) == 2, "остальные города потерялись"
    assert вышло[1]["title"] == "Restaurant Manager", "другая должность не должна слипаться"


def test_разные_работодатели_не_слипаются():
    from jobsearch import filters
    вакансии = [
        {"title": "Restaurant Manager", "company": "Carbon", "location": "Göteborg"},
        {"title": "Restaurant Manager", "company": "The June", "location": "Jacksonville"},
    ]
    assert len(filters.group_same_role(вакансии)) == 2


def test_порядок_не_ломается():
    """Первой в группе остаётся та, что была выше в списке: он уже отсортирован."""
    from jobsearch import filters
    вакансии = [
        {"title": "Cook", "company": "A", "location": "X", "score": 90},
        {"title": "Restaurant Manager Austin", "company": "B", "location": "Austin", "score": 80},
        {"title": "Restaurant Manager Dallas", "company": "B", "location": "Dallas", "score": 70},
    ]
    вышло = filters.group_same_role(вакансии)
    assert [j["title"] for j in вышло] == ["Cook", "Restaurant Manager Austin"]


# --- Регулируемые профессии ------------------------------------------------------

@pytest.mark.parametrize("роли,ожидание", [
    ("Lawyer, Legal Counsel", "law"),
    ("Avvocato civilista", "law"),
    ("Registered Nurse", "medicine"),
    ("Архитектор", "architecture"),
    ("Restaurant Manager", ""),
    ("Frontend Engineer", ""),
    ("Lingerie Pattern Maker", ""),
])
def test_узнаём_регулируемую_профессию(роли, ожидание):
    """Elisabetta Matassi — адвокат, сдавшая экзамен в Италии. Программа нашла
    ей двадцать четыре вакансии юриста: Швеция 9, Греция 7, Италия 2. В Швеции
    она не адвокат, пока не подтвердит квалификацию, и знать это надо до
    отклика."""
    from jobsearch import filters
    assert filters.regulated_profession(роли) == ожидание


def test_роли_важнее_резюме():
    """В резюме слово «lawyer» может встретиться и у того, кто юристом не
    работает: роли — то, кем человек себя называет."""
    from jobsearch import filters
    assert filters.regulated_profession(
        "Frontend Engineer", "Работал с юристами над договорами, lawyer review") == ""


# --- Навыки → профессии: кем человек мог бы работать -----------------------------

@pytest.mark.parametrize("запрос,ответ,берём", [
    ("CSS", "CSS", True),                                    # дословно
    ("pattern grading", "pattern grading", True),
    ("restaurant management", "manage restaurant service", True),   # иначе сказано, то же
    ("SOAP", "harden soap", False),                          # мыло вместо протокола
    ("REST", "promote balance between rest and activity", False),
    ("XML", "AJAX", False),
    ("vendor management", "use office systems", False),
    ("bra construction", "construction industry", False),
])
def test_ответ_справочника_сверяется_с_запросом(запрос, ответ, берём):
    """Брать первое совпадение подряд нельзя: справочник вместо «не знаю»
    отвечает ближайшим по буквам, и дальше по графу расходится мусор. Отсев
    поднял число тех, кому справочник называет их собственную профессию, с
    одного из шести до трёх."""
    from jobsearch.collectors import esco
    assert esco.looks_like(запрос, ответ) is берём


def test_профессии_по_навыкам(monkeypatch):
    """Охранник, выучивший вёрстку, напишет в резюме «охранник» и вакансий
    начинающего верстальщика не увидит никогда. Справочник по трём его навыкам
    называет web developer, ничего не спрашивая."""
    from jobsearch.collectors import esco
    esco.forget()
    ОТВЕТЫ = {
        "skill?": {"_links": {
            "isEssentialForOccupation": [{"uri": "esco:web-developer", "title": "web developer"}],
            "isOptionalForOccupation": [{"uri": "esco:webmaster", "title": "webmaster"}]}},
        "search": {"_embedded": {"results": [{"uri": "esco:css", "title": "CSS"}]}},
    }

    def подделка(url, **kw):
        какой = "skill?" if "/resource/skill" in url else "search"
        return type("О", (), {"status_code": 200, "json": lambda s: ОТВЕТЫ[какой]})()

    monkeypatch.setattr(esco.web, "get", подделка)
    вышло = esco.occupations_by_skills(["CSS"])
    assert set(вышло) == {"esco:web-developer", "esco:webmaster"}


def test_обязательные_и_желательные_связи_весят_поровну(monkeypatch):
    """Сперва обязательные весили втрое, и вышло скверно: «JavaScript» в
    справочнике обязателен ровно для двух профессий — оператора станка с ЧПУ и
    оператора САПР, — а для web developer он лишь желательный, один из
    полусотни. Две причуды перевешивали полсотни осмысленных связей, и охранник,
    выучивший вёрстку, получал станок с ЧПУ."""
    from jobsearch.collectors import esco
    esco.forget()
    СВЯЗИ = {
        "esco:js": {"isEssentialForOccupation": [{"uri": "станок", "title": "CNC"}],
                    "isOptionalForOccupation": [{"uri": "веб", "title": "web developer"}]},
        "esco:css": {"isEssentialForOccupation": [],
                     "isOptionalForOccupation": [{"uri": "веб", "title": "web developer"}]},
    }
    найдено = {"JavaScript": "esco:js", "CSS": "esco:css"}

    def подделка(url, **kw):
        if "/resource/skill" in url:
            uri = "esco:js" if "esco%3Ajs" in url or "esco:js" in url else "esco:css"
            тело = {"_links": СВЯЗИ[uri]}
        else:
            слово = "JavaScript" if "JavaScript" in url else "CSS"
            тело = {"_embedded": {"results": [{"uri": найдено[слово], "title": слово}]}}
        return type("О", (), {"status_code": 200, "json": lambda s: тело})()

    monkeypatch.setattr(esco.web, "get", подделка)
    вышло = esco.occupations_by_skills(["JavaScript", "CSS"])

    assert вышло[0] == "веб", "профессия, нужная обоим навыкам, должна быть первой"


def test_нераспознанный_навык_ничего_не_приносит(monkeypatch):
    from jobsearch.collectors import esco
    esco.forget()
    monkeypatch.setattr(esco.web, "get", lambda url, **kw: type("О", (), {
        "status_code": 200,
        "json": lambda s: {"_embedded": {"results": [{"uri": "esco:soap", "title": "harden soap"}]}},
    })())
    assert esco.skill("SOAP") == ""
    assert esco.occupations_by_skills(["SOAP"]) == []
