"""Пять находок из проверки безопасности, закрытых разом.

Ни одна из них не чинит поломку — все они ограничивают ущерб, когда что-то
другое уже не удержало: чужой текст просочился в страницу, ответ прочли не тем
типом, ссылка увела наружу, файл распаковался в гигабайты.

SSRF живёт отдельно, в test_web.py: там уже есть вся оснастка вокруг похода на
чужие сайты.
"""
import io
import os
import zipfile

import pytest

from conftest import client_for
from jobsearch import config


# --- Куда нас можно увести ------------------------------------------------------
#
# Куда вернуть человека после действия, приходит со страницы, полем back. Оно
# проверялось только на «а показывается ли ещё эта страница».

ЧУЖИЕ = [
    "https://зловред.example/тут",
    "http://зловред.example/",
    "//зловред.example/",           # без схемы — браузер подставит свою
    "/\\зловред.example/",          # обратную косую браузер выправит в обычную
    "/\\/зловред.example/",
    "https:/зловред.example/",
    "javascript:alert(1)",
    "",
]

СВОИ = [
    "/results",
    "/welcome?step=model",
    "/models#provider-ollama",
    "/results?min=70&sort=score",
    "/",
]


@pytest.mark.parametrize("адрес", ЧУЖИЕ)
def test_наружу_не_уводим(адрес):
    from app import _here
    assert _here(адрес) == "/", f"{адрес!r} прошёл насквозь"


@pytest.mark.parametrize("адрес", СВОИ)
def test_свои_адреса_остаются_как_есть(адрес):
    """Обратная сторона: закрыть чужое нельзя ценой того, что перестанут
    работать возвраты по своим страницам с запросом и якорем."""
    from app import _here
    assert _here(адрес) == адрес


def test_запасной_адрес_соблюдается():
    from app import _here
    assert _here("https://зловред.example", "/results") == "/results"


def test_через_страницу_тоже_не_уводит(profile):
    """То же самое, но по-настоящему: поле back доезжает до заголовка Location."""
    import app as приложение
    ответ = client_for(приложение.app, profile).post(
        "/models/provider", data={"provider": "claude_cli", "back": "//зловред.example/"},
        follow_redirects=False)
    куда = ответ.headers["location"]
    assert "зловред" not in куда, f"увели на {куда}"


# --- Что позволено странице -----------------------------------------------------

def test_заголовки_стоят_на_каждой_странице(profile):
    import app as приложение
    ответ = client_for(приложение.app, profile).get("/results")
    for имя in ("Content-Security-Policy", "X-Content-Type-Options",
                "Referrer-Policy", "X-Frame-Options"):
        assert имя in ответ.headers, f"нет заголовка {имя}"


def test_наружу_со_страницы_не_дозвониться(profile):
    """Главное здесь. Страница знает адрес почтового пароля, токена Telegram и
    ключей от моделей: прочесть их скрипт сможет, а отправить — уже некуда."""
    import app as приложение
    csp = client_for(приложение.app, profile).get("/results").headers["Content-Security-Policy"]
    assert "connect-src 'self'" in csp
    assert "form-action 'self'" in csp
    assert "default-src 'self'" in csp
    assert "frame-ancestors 'none'" in csp


def test_заголовки_есть_и_на_отказе():
    """Отказ — тоже ответ, и он тоже показывается в окне."""
    import app as приложение
    from fastapi.testclient import TestClient
    ответ = TestClient(приложение.app, base_url="http://чужой-хост").get("/results")
    assert ответ.status_code == 403
    assert "X-Content-Type-Options" in ответ.headers


def test_статика_отдаётся_с_заголовками(profile):
    import app as приложение
    ответ = client_for(приложение.app, profile).get("/static/style.css")
    assert ответ.status_code == 200
    assert "Content-Security-Policy" in ответ.headers


# --- Настройки читает только их владелец ----------------------------------------

@pytest.mark.skipif(os.name == "nt",
                    reason="на Windows chmod правами не занимается, и делать вид, что занимается, нельзя")
def test_настройки_закрыты_от_чужого_входа(profile):
    """В config.json лежат токен Telegram, пароль от почты и ключи от моделей."""
    cfg = config.load()
    cfg["telegram"]["bot_token"] = "123:секрет"
    config.save(cfg)
    режим = config.config_path().stat().st_mode & 0o777
    assert режим == 0o600, f"права {oct(режим)} — файл читает не только владелец"


def test_права_сужаются_при_каждом_сохранении(monkeypatch, profile):
    """То же самое, но проверяется везде.

    Тест выше идёт только там, где права настоящие, — то есть не на машине
    разработки. Здесь мы притворяемся системой с правами и смотрим, о чём
    программа просит: иначе на Windows эта защита не проверялась бы вовсе и
    сломать её можно было бы незаметно.
    """
    просьбы = []
    monkeypatch.setattr(config.os, "name", "posix")
    monkeypatch.setattr(config.os, "chmod", lambda p, mode: просьбы.append(mode))

    config.save(config.load())

    assert просьбы == [0o600], f"просили {[oct(m) for m in просьбы]}"


def test_на_windows_правами_не_притворяемся(monkeypatch, profile):
    """os.chmod на Windows умеет только «только чтение» и списками доступа не
    занимается. Вызвать его там значило бы сделать вид, что мы что-то закрыли."""
    просьбы = []
    monkeypatch.setattr(config.os, "name", "nt")
    monkeypatch.setattr(config.os, "chmod", lambda p, mode: просьбы.append(mode))

    config.save(config.load())

    assert просьбы == []


def test_сохранение_не_падает_если_права_не_ставятся(monkeypatch, profile):
    """Чужая файловая система может не уметь прав. Это не повод терять настройки."""
    def отказать(*a, **kw):
        raise OSError("права тут не водятся")

    monkeypatch.setattr(config.os, "chmod", отказать)
    cfg = config.load()
    cfg["search"]["threshold"] = 63
    config.save(cfg)
    assert config.load()["search"]["threshold"] == 63


# --- Файл, который разворачивается в гигабайты ----------------------------------

def докс(размер: int) -> bytes:
    """.docx — это zip с XML внутри. Повторяющийся текст жмётся в тысячи раз."""
    буфер = io.BytesIO()
    with zipfile.ZipFile(буфер, "w", zipfile.ZIP_DEFLATED) as архив:
        архив.writestr("word/document.xml",
                       "<w:p><w:t>" + "А" * размер + "</w:t></w:p>")
    return буфер.getvalue()


def test_бомба_не_разворачивается(monkeypatch, profile):
    """Файл в несколько килобайт разворачивался в гигабайты и уводил программу в
    своп вместе со всем, что она в это время делала. Приходит он из формы
    загрузки CV — то есть от кого угодно, кто дотянулся до окна.

    Предел на время проверки сдвинут вниз: собирать в памяти настоящие сто
    мегабайт ради этого незачем, а отличить отказ по размеру от падения по
    нехватке памяти потом было бы нечем."""
    monkeypatch.setattr(config, "MAX_DOCX_XML", 50_000)
    бомба = докс(200_000)
    assert len(бомба) < 5_000, "в архиве это должно быть мало — иначе не бомба"

    with pytest.raises(config.CVError) as поймано:
        config.save_cv("бомба.docx", бомба)
    assert поймано.value.key == "cv_err_docx"


def test_подделанная_опись_не_роняет_программу(monkeypatch, profile):
    """Соврать в описи, чтобы проскочить мимо предела, нельзя: zipfile держит
    распаковку в объявленных рамках сам и ловит расхождение по контрольной сумме.

    Но ловит он его исключением BadZipFile, и без обработки оно ушло бы наружу
    из формы загрузки CV. Проверяем, что человек получает внятный отказ, а не
    страницу с трассировкой."""
    monkeypatch.setattr(config, "MAX_DOCX_XML", 50_000)
    буфер = io.BytesIO()
    with zipfile.ZipFile(буфер, "w", zipfile.ZIP_DEFLATED) as архив:
        архив.writestr("word/document.xml", "<w:t>" + "А" * 200_000 + "</w:t>")
    сырьё = буфер.getvalue()
    # Правим объявленный размер на маленький — опись перестаёт быть правдой.
    # Само число берём у zipfile, а не считаем в уме: в UTF-8 кириллица занимает
    # два байта, и посчитанное на глаз разошлось бы с записанным.
    with zipfile.ZipFile(io.BytesIO(сырьё)) as z:
        честный = z.getinfo("word/document.xml").file_size.to_bytes(4, "little")
    ложь = (1234).to_bytes(4, "little")
    assert сырьё.count(честный) >= 1, "не нашли, где записан размер"
    сырьё = сырьё.replace(честный, ложь)

    with pytest.raises(config.CVError) as поймано:
        config.save_cv("враньё.docx", сырьё)
    assert поймано.value.key.startswith("cv_err_"), "наружу вышло не наше исключение"


def test_обычный_docx_читается(profile):
    """Обратная сторона: предел щедрый нарочно, настоящее резюме под него не
    попадает."""
    текст = config.save_cv("резюме.docx", докс(2000))
    assert "А" * 100 in текст


# --- Чужой текст на нашей странице ----------------------------------------------

def test_текст_ошибки_экранируется(monkeypatch, profile):
    """В тексте исключения оседает то, что написали не мы: ответ модели и куски
    описания вакансии, а описание пишет кто угодно. Без экранирования <script>
    из чужой вакансии выполнялся бы на нашей странице, а она ходит по адресам
    этой программы как своя."""
    import app as приложение
    from conftest import job as вакансия
    from jobsearch import db, scoring

    db.init()
    run = db.start_run()
    db.save_job(вакансия("злая"), run)
    строка = db.get_job_by_key("злая") if hasattr(db, "get_job_by_key") else None
    ид = строка["id"] if строка else 1
    config.cv_text_path().write_text("Обычное резюме. " * 30, encoding="utf-8")

    def взорваться(*a, **kw):
        raise ValueError("модель ответила: <script>fetch('//чужой')</script>")

    monkeypatch.setattr(scoring, "generate_cv", взорваться)

    страница = client_for(приложение.app, profile).get(f"/cv/{ид}").text

    assert "<script>" not in страница, "чужой скрипт попал на страницу как есть"
    assert "&lt;script&gt;" in страница, "текст ошибки пропал совсем — человеку нечего читать"


# --- Заголовки не должны запирать дверь изнутри ---------------------------------

def test_политика_отсылок_не_обнуляет_origin(profile):
    """Ровно то, на чём обожглись.

    При Referrer-Policy: no-referrer браузер шлёт при отправке формы
    «Origin: null», и проверка «пришло ли это от нашей же страницы» отказывала
    своему окну: вместо страницы человек видел «Not from this program.».
    Заголовок был поставлен ради приличия к чужим сайтам, а стоил работы всей
    программы. same-origin даёт снаружи ровно то же — чужой сайт не узнаёт
    ничего, — и Origin при этом остаётся на месте."""
    import app as приложение
    политика = client_for(приложение.app, profile).get("/results").headers["Referrer-Policy"]
    assert политика != "no-referrer", "этот вариант обнуляет Origin у форм"
    assert политика in ("same-origin", "strict-origin-when-cross-origin",
                        "strict-origin"), f"неизвестная политика: {политика}"


def test_своё_окно_узнаётся_по_sec_fetch_site(profile):
    """Так выглядит запрос от настоящей формы в окне программы. Sec-Fetch-Site
    ставит сам браузер, и подделать его со страницы нельзя — значит он решает
    дело и без Origin, каким бы тот ни пришёл."""
    import app as приложение
    ответ = client_for(приложение.app, profile).post(
        "/models/provider", data={"provider": "claude_cli", "back": "/models"},
        headers={"Sec-Fetch-Site": "same-origin", "Origin": "null"},
        follow_redirects=False)
    assert ответ.status_code == 303, "программа отказала своему же окну"


def test_чужая_страница_по_прежнему_не_проходит(profile):
    """Обратная сторона: доверять Sec-Fetch-Site можно только потому, что чужое
    он называет чужим."""
    import app as приложение
    for site in ("cross-site", "same-site"):
        ответ = client_for(приложение.app, profile).post(
            "/models/provider", data={"provider": "ollama"},
            headers={"Sec-Fetch-Site": site, "Origin": "http://127.0.0.1:8766"},
            follow_redirects=False)
        assert ответ.status_code == 403, f"{site} прошло насквозь"
