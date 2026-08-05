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


def забыть() -> None:
    """Для тестов: сбросить запомненное."""
    _имена.clear()
    _профессии.clear()
