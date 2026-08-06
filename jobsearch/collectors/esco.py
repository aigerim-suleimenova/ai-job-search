"""Название профессии по коду ESCO.

ESCO — общеевропейский справочник профессий. EURES помечает им каждое
объявление, и код этот один и тот же, на каком бы языке объявление ни было
написано: и польская «Szwaczka», и французская «Couturière» несут один код.

Понадобилось это вот зачем. Открыв EURES, мы получили вакансии по всему ЕС — и
почти все на своих языках. Местная модель их не читает. Не поняв текста, она не
говорит «не поняла»: она пересказывает профиль кандидата и ставит балл из
примера. На прогоне конструктора белья столяр, маляр, садовник, кладовщик и
монтажник водопровода получили по девяносто процентов, каждый с доводом
«кандидат имеет опыт работы как Lingerie Technical Designer, а также знания по
технологиям, необходимым для консервации систем водокана».

Переводить объявление незачем: у него уже есть машинно-читаемая профессия.
Разворачиваем код в английское название и кладём рядом с исходным — модели
больше не нужно читать по-польски, чтобы понять, чья это работа.

Ответы держим в памяти: кодов на прогон приходится десятки, а вакансий сотни.
"""
import re
import urllib.parse

import requests

from . import web

API = "https://ec.europa.eu/esco/api/resource/occupation"

_имена: dict = {}


def label(uri: str, lang: str = "en") -> str:
    """Название профессии на нужном языке. Пустая строка, если не вышло.

    Не вышло — не беда: вакансия просто останется с одним своим названием, как
    было до сих пор. Ради названия профессии прогон ронять нечего.
    """
    uri = (uri or "").strip()
    if not uri.startswith("http://data.europa.eu/esco/"):
        return ""
    ключ = (uri, lang)
    if ключ in _имена:
        return _имена[ключ]
    имя = ""
    try:
        r = web.get(f"{API}?uri={urllib.parse.quote(uri, safe='')}&language={lang}",
                    timeout=20)
        if r is not None and r.status_code == 200:
            данные = r.json()
            имя = str((данные.get("preferredLabel") or {}).get(lang)
                      or данные.get("title") or "").strip()
    except (requests.RequestException, ValueError, AttributeError):
        имя = ""
    _имена[ключ] = имя
    return имя


ПОИСК = "https://ec.europa.eu/esco/api/search"
РЕСУРС = "https://ec.europa.eu/esco/api/resource"

_профессии: dict = {}


def occupations(роль: str, сколько: int = 3, lang: str = "en") -> list:
    """Коды профессий справочника, подходящие под название роли.

    Ради этого стоило заводить: EURES ищет по словам плохо и непредсказуемо.
    Проверено на живых запросах — из пятидесяти вакансий по профессии
    оказывалось:

        «dressmaker»              28
        «lingerie»                 3
        «Lingerie Pattern Maker»   1
        «seamstress»               0
        «tailor»                   0

    То есть решает не смысл, а угаданное слово, и угадать его нельзя: «швея»
    по-английски — «seamstress», и по нему не находится ничего. А роли, которые
    программа сама выписывает из резюме, выходят длинными: «Lingerie Product
    Developer», «Parametric Pattern Making». По ним не находится тем более.

    Справочник эту угадайку снимает. «seamstress» он отображает в sewing
    machinist, dressmaker и tailor, а EURES принимает коды и ищет уже по ним: на
    том же запросе стало 29 из 50 вместо одной.
    """
    роль = (роль or "").strip()
    if not роль:
        return []
    ключ = (роль.lower(), сколько, lang)
    if ключ in _профессии:
        return _профессии[ключ]
    найдено = []
    try:
        r = web.get(f"{ПОИСК}?type=occupation&language={lang}&limit={сколько}"
                    f"&text={urllib.parse.quote(роль)}", timeout=20)
        if r is not None and r.status_code == 200:
            данные = r.json()
            записи = (данные.get("_embedded", {}) or {}).get("results") or data_results(данные)
            найдено = [str(i.get("uri", "")) for i in записи if i.get("uri")]
    except (requests.RequestException, ValueError, AttributeError):
        найдено = []
    _профессии[ключ] = найдено
    return найдено


def data_results(данные: dict) -> list:
    """У справочника два вида ответа — с _embedded и без."""
    return данные.get("results") or []


def _слова(текст: str) -> list:
    return [w for w in re.findall(r"[a-zа-яё0-9#+]+", (текст or "").lower()) if len(w) > 1]


def похоже(запрос: str, ответ: str) -> bool:
    """Похож ли ответ справочника на то, что спрашивали.

    Брать первое совпадение подряд нельзя, и это померено: «SOAP» справочник
    понимает как «harden soap», «REST» — как «promote balance between rest and
    activity», «XML» — как «AJAX», «vendor management» — как «use office
    systems». Дальше по графу расходится уже мусор, и никакая мера соответствия
    его не спасёт.

    Правило вышло простое: дословное совпадение — берём; все слова запроса
    нашлись в ответе — берём; одно слово и не дословно — не берём. На шести
    настоящих резюме отсев поднял число тех, кому справочник называет их
    собственную профессию, с одного до трёх.
    """
    з, о = _слова(запрос), _слова(ответ)
    if not з or not о:
        return False
    if з == о:
        return True
    if len(з) == 1:
        return False
    # с точностью до окончания: «restaurant management» и «manage restaurant
    # service» — одно и то же, а придираться к грамматике не за что
    return all(any(w[:5] == x[:5] for x in о) for w in з)


def skill(текст: str, lang: str = "en") -> str:
    """Код навыка по его названию. Пустая строка, если справочник его не знает."""
    текст = (текст or "").strip()
    if not текст:
        return ""
    ключ = ("навык", текст.lower(), lang)
    if ключ in _профессии:
        return _профессии[ключ]
    найдено = ""
    try:
        r = web.get(f"{ПОИСК}?type=skill&language={lang}&limit=5"
                    f"&text={urllib.parse.quote(текст)}", timeout=20)
        if r is not None and r.status_code == 200:
            записи = (r.json().get("_embedded", {}) or {}).get("results") or []
            найдено = next((str(i.get("uri", "")) for i in записи
                            if похоже(текст, str(i.get("title", "")))), "")
    except (requests.RequestException, ValueError, AttributeError):
        найдено = ""
    _профессии[ключ] = найдено
    return найдено


def occupations_by_skills(навыки: list, сколько: int = 3, lang: str = "en") -> list:
    """Кем человек мог бы работать — по одним его навыкам.

    Спрашивать «кем вы хотите» бесполезно: человек не знает, на что годится, он
    для того и пришёл. Охранник, выучивший вёрстку, напишет в резюме «охранник»,
    и вакансий начинающего верстальщика не увидит никогда — программа ищет по
    тому, кем он был.

    Справочник отвечает на этот вопрос сам: у него навык знает, каким профессиям
    он нужен. Тому охраннику по трём навыкам — JavaScript, CSS, HTML — он
    называет web developer, webmaster, digital media designer. Ни одного вопроса
    человеку не задано.

    Считаем просто: сколько навыков человека профессии вообще нужны. Обязательные
    и желательные весят одинаково, и это померено на семи случаях.

    Сперва обязательные весили втрое, и вышло скверно: «JavaScript» в справочнике
    обязателен ровно для двух профессий — оператора станка с ЧПУ и оператора
    САПР, — а для web developer он лишь желательный, один из полусотни. Две
    причуды справочника перевешивали полсотни осмысленных связей, и охранник,
    выучивший вёрстку, получал станок с ЧПУ.

    Уравняв веса, из трёх верхних верными стали 14 из 21 против 11. Ни один
    случай не ухудшился, три улучшились: у охранника наверх вышли digital media
    designer и user interface developer, у фронтендера — web developer, а у
    адвоката появился «lawyer», которого до того не было вовсе.

    Долю закрытых навыков я тоже пробовал, и она оказалась хуже всех: побеждают
    профессии, у которых навыков в списке мало, и наверх лезут «movie
    distributor» и «tanning consultant».

    Работает не у всех. У конструктора белья справочник знает один навык из
    восьми, и тот привязан к обувным профессиям: граф у него неровный, обувь
    расписана подробно, а одежда бедно. Поэтому это дополнение к поиску по
    ролям, а не замена ему.
    """
    очки: dict = {}
    for н in навыки:
        uri = skill(н, lang)
        if not uri:
            continue
        try:
            r = web.get(f"{РЕСУРС}/skill?uri={urllib.parse.quote(uri, safe='')}"
                        f"&language={lang}", timeout=20)
            если = r.json().get("_links", {}) if r is not None and r.status_code == 200 else {}
        except (requests.RequestException, ValueError, AttributeError):
            continue
        for ключ in ("isEssentialForOccupation", "isOptionalForOccupation"):
            for о in (если.get(ключ) or []):
                очки[str(о.get("uri"))] = очки.get(str(о.get("uri")), 0) + 1
    лучшие = sorted(очки.items(), key=lambda x: -x[1])[:сколько]
    return [u for u, _ in лучшие if u]


def забыть() -> None:
    """Для тестов: сбросить запомненное."""
    _имена.clear()
    _профессии.clear()
