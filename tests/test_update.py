"""Обновление: узнать о новой версии и поставить её.

Отдельного внимания стоит то, откуда берётся файл. GitHub присылает список
файлов вместе с адресами, но адрес в ответе — это лишь предложение: ответ можно
подменить по дороге, а скачанное здесь будет запущено. Поэтому адрес проверяется
перед скачиванием, и проверяется в том самом месте, которое лезет в сеть, — а не
только там, где его выбирали.
"""
import hashlib
import os
import platform
import subprocess
import time

import pytest

from jobsearch import update, version


# --- Что считать новее ----------------------------------------------------------

@pytest.mark.parametrize("новая,своя,ожидание", [
    ("0.8.17", "0.8.16", True),
    ("0.9.0", "0.8.16", True),
    ("0.8.16", "0.8.16", False),
    ("0.8.15", "0.8.16", False),
    ("0.8.9", "0.8.10", False),          # не по алфавиту, а по числам
    ("0.10.0", "0.9.9", True),
])
def test_сравнение_версий(новая, своя, ожидание):
    assert update.is_newer(новая, своя) is ожидание


def test_запуску_из_исходников_обновление_не_предлагают():
    """Из исходников программа зовётся «dev» и ни от чего не отстаёт: там
    работает то, что разработчик у себя открыл."""
    assert update.is_newer("9.9.9", version.FALLBACK) is False
    assert update.is_newer("", "0.8.16") is False


# --- Откуда берётся файл --------------------------------------------------------

def чужой(url):
    return {"name": "AI Job Search Setup.exe", "url": url, "size": 1}


@pytest.mark.parametrize("адрес", [
    "https://example.com/evil.exe",
    "http://github.com/mrWD/ai-job-search/releases/download/v1/x.exe",   # не https
    "https://github.com/someone-else/ai-job-search/releases/download/v1/x.exe",
    "https://github.com.evil.test/mrWD/ai-job-search/releases/download/v1/x.exe",
    "https://raw.githubusercontent.com/mrWD/ai-job-search/main/x.exe",
    "",
    # Обход, который в первой редакции работал: сравнение началом строки
    # проходило, а requests схлопывал «..» уже после проверки и уходил в чужой
    # репозиторий. Проверять надо тот адрес, который увидит сеть.
    update.DOWNLOAD_PREFIX + "../../../../evilcorp/evil/releases/download/v1/x.exe",
    update.DOWNLOAD_PREFIX + "%2e%2e/%2e%2e/%2e%2e/evil/x/releases/download/v1/x.exe",
    update.DOWNLOAD_PREFIX,                       # каталог без файла
])
def test_скачиваем_только_из_своих_выпусков(адрес):
    """Ответ с подменённым адресом может назвать файл, но не место, откуда его брать."""
    with pytest.raises(update.UpdateError) as поймано:
        update.download(чужой(адрес))
    assert поймано.value.key == "update_err_bad_url"


def test_проверка_адреса_не_расходится_с_тем_куда_пойдёт_запрос():
    """Смысл проверки в том, чтобы совпадать с поведением сети, а не с догадкой
    о нём. Расхождение допустимо лишь в одну сторону — отвергнуть лишнее."""
    import requests as _r
    for url in (update.DOWNLOAD_PREFIX + "v1/setup.exe",
                update.DOWNLOAD_PREFIX + "../../../evil/x/releases/download/v1/x.exe",
                "https://evil.example/x.exe",
                "https://github.com.evil.test/x.exe"):
        пропускаем = update._is_our_download(url)
        реально_свой = _r.Request("GET", url).prepare().url.startswith(update.DOWNLOAD_PREFIX)
        assert not (пропускаем and not реально_свой), \
            f"пропустили адрес, уходящий на сторону: {url}"


def test_свой_адрес_проходит_проверку(monkeypatch, tmp_path):
    """Обратная сторона: настоящий адрес отвергать нельзя."""
    свой = update.DOWNLOAD_PREFIX + "v0.8.17/AI%20Job%20Search%20Setup.exe"
    попытки = []

    class Ответ:
        headers = {"Content-Length": "4"}

        def raise_for_status(self):
            pass

        def iter_content(self, chunk_size=0):
            yield b"data"

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

    monkeypatch.setattr(update.requests, "get",
                        lambda url, **kw: попытки.append(url) or Ответ())

    path = update.download({"name": "AI Job Search Setup.exe", "url": свой, "size": 4})

    assert попытки == [свой], "пошли не по тому адресу"
    assert path.read_bytes() == b"data"


def test_имя_файла_из_ответа_не_уводит_из_папки(monkeypatch):
    """Имя тоже приходит снаружи. Из него делается имя файла на диске, и «..» в
    нём не должно уводить запись куда-то ещё."""
    class Ответ:
        headers = {}

        def raise_for_status(self):
            pass

        def iter_content(self, chunk_size=0):
            yield b"x"

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

    monkeypatch.setattr(update.requests, "get", lambda url, **kw: Ответ())
    asset = {"name": "../../../../Windows/System32/evil.exe",
             "url": update.DOWNLOAD_PREFIX + "v1/x.exe"}

    path = update.download(asset)

    assert ".." not in path.name, f"имя увело из папки: {path}"
    assert path.name == "evil.exe", f"от имени осталось что-то странное: {path.name}"
    # главное: файл лёг именно туда, куда мы его звали, а не куда указало имя
    assert path.resolve().parent == path.parent.resolve()
    assert path.parent.name.startswith("aijs-update-")


# --- Скачанное сверяется с обещанным ---------------------------------------------

def поддельная_сеть(monkeypatch, payload: bytes):
    class Ответ:
        headers = {"Content-Length": str(len(payload))}

        def raise_for_status(self):
            pass

        def iter_content(self, chunk_size=0):
            yield payload

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

    monkeypatch.setattr(update.requests, "get", lambda url, **kw: Ответ())


def свой(**поля):
    base = {"name": "setup.exe", "url": update.DOWNLOAD_PREFIX + "v1/setup.exe"}
    base.update(поля)
    return base


def test_целый_файл_принимается(monkeypatch):
    данные = b"installer bytes"
    поддельная_сеть(monkeypatch, данные)
    asset = свой(size=len(данные), digest="sha256:" + hashlib.sha256(данные).hexdigest())

    path = update.download(asset)

    assert path.read_bytes() == данные


def test_недокачанный_файл_не_запускается(monkeypatch):
    """Обрыв связи оставлял огрызок, и он передавался установщику как есть."""
    поддельная_сеть(monkeypatch, b"half")
    asset = свой(size=999999)

    with pytest.raises(update.UpdateError) as поймано:
        update.download(asset)

    assert поймано.value.key == "update_err_broken"


def test_подменённое_содержимое_не_запускается(monkeypatch):
    """Размер сошёлся, а байты другие: адрес ведёт на хранилище, куда нас
    перенаправили, и доверять надо не ему, а самим байтам."""
    данные = b"NOT the installer"
    поддельная_сеть(monkeypatch, данные)
    asset = свой(size=len(данные),
                 digest="sha256:" + hashlib.sha256(b"the real installer").hexdigest())

    with pytest.raises(update.UpdateError) as поймано:
        update.download(asset)

    assert поймано.value.key == "update_err_broken"


def test_негодный_файл_стирается_а_не_остаётся_лежать(monkeypatch):
    поддельная_сеть(monkeypatch, b"stub")
    asset = свой(size=999999)
    оставшиеся = []
    настоящий_mkdtemp = update.tempfile.mkdtemp
    monkeypatch.setattr(update.tempfile, "mkdtemp",
                        lambda **kw: оставшиеся.append(настоящий_mkdtemp(**kw)) or оставшиеся[-1])

    with pytest.raises(update.UpdateError):
        update.download(asset)

    from pathlib import Path
    остатки = list(Path(оставшиеся[0]).glob("*"))
    assert остатки == [], f"негодный файл остался на диске: {остатки}"


def test_бесконечный_ответ_не_забьёт_диск(monkeypatch):
    class Бесконечный:
        headers = {}

        def raise_for_status(self):
            pass

        def iter_content(self, chunk_size=0):
            while True:
                yield b"x" * 1024 * 1024

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

    monkeypatch.setattr(update.requests, "get", lambda url, **kw: Бесконечный())
    monkeypatch.setattr(update, "MAX_DOWNLOAD", 4 * 1024 * 1024)

    with pytest.raises(update.UpdateError) as поймано:
        update.download(свой())

    assert поймано.value.key == "update_err_broken"


# --- Выбор файла под систему ----------------------------------------------------

def test_на_windows_берётся_установщик(monkeypatch):
    monkeypatch.setattr(update.platform, "system", lambda: "Windows")
    assets = [
        {"name": "AI Job Search.dmg", "browser_download_url": update.DOWNLOAD_PREFIX + "v1/a.dmg"},
        {"name": "AI Job Search Setup.exe", "browser_download_url": update.DOWNLOAD_PREFIX + "v1/s.exe"},
    ]
    assert update._asset_for_this_os(assets)["name"] == "AI Job Search Setup.exe"


def test_установщик_с_чужого_адреса_не_предлагается(monkeypatch):
    monkeypatch.setattr(update.platform, "system", lambda: "Windows")
    assets = [{"name": "AI Job Search Setup.exe",
               "browser_download_url": "https://example.com/setup.exe"}]
    assert update._asset_for_this_os(assets) == {}


def test_на_macos_берётся_образ(monkeypatch):
    """Кнопки «Обновить» на macOS не было вовсе — там показывали «ставится
    вручную», хотя внутри образа лежит готовый пакет."""
    monkeypatch.setattr(update.platform, "system", lambda: "Darwin")
    assets = [
        {"name": "AI Job Search Setup.exe", "browser_download_url": update.DOWNLOAD_PREFIX + "v1/s.exe"},
        {"name": "AI Job Search.dmg", "browser_download_url": update.DOWNLOAD_PREFIX + "v1/a.dmg"},
    ]
    assert update._asset_for_this_os(assets)["name"] == "AI Job Search.dmg"


def test_образ_с_чужого_адреса_не_предлагается(monkeypatch):
    monkeypatch.setattr(update.platform, "system", lambda: "Darwin")
    assets = [{"name": "AI Job Search.dmg",
               "browser_download_url": "https://example.com/a.dmg"}]
    assert update._asset_for_this_os(assets) == {}


def test_где_ставить_руками_ничего_не_предлагается(monkeypatch):
    """Linux остаётся за страницей выпуска: там архив, который человек распаковал
    туда, куда захотел, и угадывать это место — значит однажды не угадать."""
    monkeypatch.setattr(update.platform, "system", lambda: "Linux")
    assets = [{"name": "AI Job Search-linux.tar.gz",
               "browser_download_url": update.DOWNLOAD_PREFIX + "v1/l.tar.gz"}]
    assert update._asset_for_this_os(assets) == {}


# --- Ответ GitHub ---------------------------------------------------------------

def test_черновик_и_предвыпуск_не_предлагаются(monkeypatch):
    for поле in ("draft", "prerelease"):
        monkeypatch.setattr(update.requests, "get",
                            lambda *a, **kw: _ответ({"tag_name": "v9.9.9", поле: True}))
        assert update.fetch_latest() == {}


def test_недоступный_github_не_ломает_программу(monkeypatch):
    def взорваться(*a, **kw):
        raise update.requests.RequestException("нет сети")

    monkeypatch.setattr(update.requests, "get", взорваться)
    assert update.fetch_latest() == {}


def _ответ(payload):
    class R:
        def raise_for_status(self):
            pass

        def json(self):
            return payload

    return R()


# --- Запись об обновлении переживает само обновление ------------------------------

def test_после_установки_не_предлагаем_ту_же_версию(monkeypatch):
    """Так и было видно на экране: «Вышла версия 0.8.20 — у вас 0.8.20», и кнопка
    «Обновить» рядом.

    Программа нашла новую версию и записала это. Человек её поставил — а запись
    пережила установку, потому что данные лежат отдельно от программы, и
    следующий опрос GitHub только через сутки. Страница читала запись как есть.
    """
    monkeypatch.setattr(update.appstate, "load", lambda: {"update": {
        "checked_at": 1.0, "version": "0.8.20",
        "notes_url": "https://example/tag/v0.8.20",
        "asset": {"name": "setup.exe", "url": update.DOWNLOAD_PREFIX + "v0.8.20/setup.exe"}}})
    monkeypatch.setattr(update.version, "current", lambda: "0.8.20")

    сейчас = update.state()

    assert сейчас["version"] == "", "предложили обновиться до уже установленной версии"
    assert not сейчас["asset"], "кнопка «Обновить» осталась бы на экране"


def test_настоящее_обновление_по_прежнему_видно(monkeypatch):
    """Обратная сторона: заглушить всё было бы легко и неправильно."""
    monkeypatch.setattr(update.appstate, "load", lambda: {"update": {
        "checked_at": 1.0, "version": "0.9.0", "notes_url": "u",
        "asset": {"name": "setup.exe", "url": update.DOWNLOAD_PREFIX + "v0.9.0/setup.exe"}}})
    monkeypatch.setattr(update.version, "current", lambda: "0.8.20")

    сейчас = update.state()

    assert сейчас["version"] == "0.9.0"
    assert сейчас["asset"]["name"] == "setup.exe"


def test_запись_от_более_старой_версии_тоже_не_показывается(monkeypatch):
    """Откат на предыдущую сборку не должен звать «обновиться» назад."""
    monkeypatch.setattr(update.appstate, "load", lambda: {"update": {
        "checked_at": 1.0, "version": "0.8.15", "notes_url": "u", "asset": {"url": "x"}}})
    monkeypatch.setattr(update.version, "current", lambda: "0.8.20")

    assert update.state()["version"] == ""


# --- Когда соединения проверяет посредник ---------------------------------------

def test_при_перехвате_соединений_переходим_на_хранилище_системы(monkeypatch):
    """Виктор увидел это дважды за день. Сперва все девять источников вакансий
    отдали ноль, и прогон отчитался успешным. Потом программа перестала замечать
    новые версии: сидел на 0.8.28, когда вышли 0.8.29 и 0.8.30.

    Причина одна: антивирус на машине сам открывает защищённое соединение, читает
    его и подписывает своим корнем. Корень стоит в хранилище системы — иначе не
    работал бы браузер, — но requests хранилищем системы не пользуется, у него
    свой набор. Проверка молча возвращала пустоту."""
    import requests

    from jobsearch import net
    net.forget()
    пошло_через = []

    class Ответ:
        status_code = 200

        @staticmethod
        def json():
            return {"ok": True}

    def обычный(url, **kw):
        пошло_через.append("свой набор")
        raise requests.exceptions.SSLError("certificate verify failed")

    class Сессия:
        def get(self, url, **kw):
            пошло_через.append("хранилище системы")
            return Ответ()

    monkeypatch.setattr(net.requests, "get", обычный)
    monkeypatch.setattr(net, "_system_session", lambda: Сессия())

    assert net.get("https://api.github.com/x").json() == {"ok": True}
    assert пошло_через == ["свой набор", "хранилище системы"]
    assert net.using_system_store() is True

    # и дальше ходим сразу туда: второй попытки на каждый запрос не надо
    пошло_через.clear()
    net.get("https://api.github.com/y")
    assert пошло_через == ["хранилище системы"]
    net.forget()


def test_без_перехвата_ничего_не_меняется(monkeypatch):
    """У кого соединения не проверяют — для того всё как было."""
    from jobsearch import net
    net.forget()
    звали = []

    monkeypatch.setattr(net.requests, "get",
                        lambda url, **kw: звали.append(url) or "ответ")
    monkeypatch.setattr(net, "_system_session",
                        lambda: (_ for _ in ()).throw(AssertionError("зря полезли в хранилище")))

    assert net.get("https://example.com") == "ответ"
    assert net.using_system_store() is False
    net.forget()


def test_другие_беды_сети_на_хранилище_не_списываются(monkeypatch):
    """Нет связи — это нет связи, а не чужой корень. Молча подменять одно другим
    значило бы прятать настоящую причину."""
    import requests

    from jobsearch import net
    net.forget()
    monkeypatch.setattr(net.requests, "get",
                        lambda url, **kw: (_ for _ in ()).throw(
                            requests.exceptions.ConnectionError("нет связи")))

    with pytest.raises(requests.exceptions.ConnectionError):
        net.get("https://example.com")
    assert net.using_system_store() is False
    net.forget()


# --- Когда спрашиваем про новую версию -------------------------------------------
#
# Настоящую функцию берём до того, как общая заглушка её подменит: conftest
# затыкает проверку обновления во всех тестах, чтобы они не ходили в сеть.
from jobsearch.update import check_in_background as _настоящая_проверка

def test_спрашиваем_не_только_при_запуске(monkeypatch, profile):
    """Проверка шла ровно один раз, при запуске. Виктор открыл программу до
    выхода 0.8.32, она ничего не нашла — и больше не спрашивала. А закрывать её
    незачем: у неё фоновый режим и расписание, она и должна стоять открытой."""
    from jobsearch import appstate, update
    заводили = []
    monkeypatch.setattr(update.threading, "Thread",
                        lambda **kw: type("П", (), {"start": lambda s: заводили.append(1)})())

    # прошлый раз был давно
    st = appstate.load()
    st["update"] = {"checked_at": time.time() - update.CHECK_EVERY - 60, "version": ""}
    appstate.save(st)
    assert update.due() is True
    _настоящая_проверка()
    assert заводили == [1], "срок вышел, а спрашивать не пошли"


def test_чаще_срока_не_беспокоим(monkeypatch, profile):
    """Иначе на каждую отрисовку страницы приходился бы свой поток."""
    from jobsearch import appstate, update
    заводили = []
    monkeypatch.setattr(update.threading, "Thread",
                        lambda **kw: type("П", (), {"start": lambda s: заводили.append(1)})())

    st = appstate.load()
    st["update"] = {"checked_at": time.time() - 60, "version": ""}
    appstate.save(st)
    assert update.due() is False
    _настоящая_проверка()
    assert заводили == [], "спросили раньше срока"


def test_страница_заводит_проверку(monkeypatch, profile):
    """Ровно то место, где это и должно случаться: человек открыл страницу."""
    import app as приложение
    from conftest import client_for
    from jobsearch import update
    звали = []
    monkeypatch.setattr(приложение.update_mod, "check_in_background",
                        lambda: звали.append(1))

    client_for(приложение.app, profile).get("/results")

    assert звали, "страница отрисовалась, а спросить не пошли"


# --- Установка на macOS ---------------------------------------------------------
#
# Кнопки «Обновить» тут не было: программа говорила «ставится вручную». Внутри
# образа при этом лежит готовый пакет .app, и подставить его вместо старого —
# ровно то, что человек делает руками, перетаскивая пакет в «Программы».

def test_из_исходников_ставить_нечего(monkeypatch, tmp_path):
    """Пакета .app нет — значит, программа запущена у разработчика."""
    monkeypatch.setattr(update.platform, "system", lambda: "Darwin")
    monkeypatch.setattr(update.removal, "program_path", lambda: "")
    with pytest.raises(update.UpdateError) as e:
        update.install(tmp_path / "a.dmg")
    assert e.value.key == "update_err_manual"


def test_без_прав_на_запись_программу_не_закрываем(monkeypatch, tmp_path):
    """Право проверяем ДО закрытия программы. Иначе она просто исчезла бы с
    экрана, а обновление молча не случилось бы — и человек остался бы ни с чем."""
    пакет = tmp_path / "AI Job Search.app"
    пакет.mkdir()
    monkeypatch.setattr(update.platform, "system", lambda: "Darwin")
    monkeypatch.setattr(update.removal, "program_path", lambda: str(пакет))
    monkeypatch.setattr(update.os, "access", lambda p, m: False)
    запускали = []
    monkeypatch.setattr(update.subprocess, "Popen", lambda *a, **k: запускали.append(a))

    with pytest.raises(update.UpdateError) as e:
        update.install(tmp_path / "a.dmg")
    assert e.value.key == "update_err_readonly"
    assert e.value.fmt["where"] == str(пакет)
    assert запускали == [], "программа пошла бы закрываться впустую"


def test_на_linux_по_прежнему_вручную(monkeypatch, tmp_path):
    monkeypatch.setattr(update.platform, "system", lambda: "Linux")
    with pytest.raises(update.UpdateError) as e:
        update.install(tmp_path / "a.tar.gz")
    assert e.value.key == "update_err_manual"


# --- Та же подстановка, но на настоящей macOS -----------------------------------
#
# Всё, что выше, проверяет наши развилки на заглушках. А подставляет пакет
# сценарий на sh: там hdiutil, ditto и ожидание чужого процесса, и ни одно из
# этого заглушкой не проверишь. Поэтому здесь — по-настоящему: собираем образ,
# кладём «установленную» копию, запускаем сценарий и смотрим, что стало.
# В сборке это гоняется на macos-latest (см. .github/workflows/build.yml).

только_на_маке = pytest.mark.skipif(platform.system() != "Darwin",
                                    reason="подстановка пакета бывает только на macOS")


def _образ(tmp_path, что_внутри: str, имя_пакета: str = "AI Job Search.app"):
    """Собирает .dmg с пакетом внутри — как тот, что выкладывается в выпуске."""
    склад = tmp_path / "склад"
    внутри = склад / имя_пакета / "Contents" / "MacOS"
    внутри.mkdir(parents=True)
    (внутри / "AI Job Search").write_text(что_внутри)
    (внутри.parent / "Info.plist").write_text("<plist/>")
    рядом = tmp_path / "скачано"          # сценарий сносит папку целиком
    рядом.mkdir()
    образ = рядом / "AI Job Search.dmg"
    subprocess.run(["hdiutil", "create", "-volname", "AI Job Search",
                    "-srcfolder", str(склад), "-ov", "-format", "UDZO", str(образ)],
                   check=True, capture_output=True)
    return образ


def _установлено(tmp_path, что_внутри: str):
    пакет = tmp_path / "Applications" / "AI Job Search.app"
    (пакет / "Contents" / "MacOS").mkdir(parents=True)
    (пакет / "Contents" / "MacOS" / "AI Job Search").write_text(что_внутри)
    return пакет


def _прогнать(tmp_path, образ, пакет, подложить: dict = None):
    """Гоняет сценарий вместо программы, подсунув ему заглушки в PATH.

    open подменяется всегда: настоящий открыл бы окно прямо в сборке.
    """
    свои = tmp_path / "свои-команды"
    свои.mkdir(exist_ok=True)
    (свои / "open").write_text(f'#!/bin/sh\necho "$@" >> "{tmp_path}/открывали"\n')
    for имя, тело in (подложить or {}).items():
        (свои / имя).write_text(тело)
    for f in свои.iterdir():
        f.chmod(0o755)

    # Чужой процесс вместо нашей программы: сценарий ждёт, пока он уйдёт.
    вместо_программы = subprocess.Popen(["sleep", "1"])
    сценарий = tmp_path / "swap.sh"
    сценарий.write_text(update._MAC_SWAP, encoding="utf-8")
    сценарий.chmod(0o755)
    процесс = subprocess.Popen(
        ["/bin/sh", str(сценарий), str(образ), str(пакет), str(вместо_программы.pid)],
        env={**os.environ, "PATH": f"{свои}:{os.environ['PATH']}"},
        stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    # Дожидаемся и хороним. Без этого процесс остаётся незахороненным и на
    # kill -0 отвечает «жив» — сценарий честно ждал его все двадцать секунд и
    # уходил ни с чем, а тест показывал «пакет не подставился» без причины.
    вместо_программы.wait()
    out, err = процесс.communicate(timeout=180)
    return subprocess.CompletedProcess(процесс.args, процесс.returncode, out, err)


@только_на_маке
def test_пакет_подставляется(tmp_path):
    образ = _образ(tmp_path, "новая\n")
    пакет = _установлено(tmp_path, "старая\n")

    r = _прогнать(tmp_path, образ, пакет)

    assert (пакет / "Contents" / "MacOS" / "AI Job Search").read_text() == "новая\n", \
        f"сценарий вышел с {r.returncode}: {r.stderr}"
    assert "AI Job Search.app" in (tmp_path / "открывали").read_text(), \
        "подставили и не открыли — человек остался без программы"
    assert not образ.parent.exists(), "скачанное осталось лежать"
    assert not (tmp_path / "Applications" / ".AI Job Search.app.old").exists()


@только_на_маке
def test_если_копирование_сорвалось_старая_возвращается(tmp_path):
    """Самая опасная минута: старая копия уже отодвинута, новая ещё не легла.
    Оборвись всё здесь — человек остался бы с половиной пакета."""
    образ = _образ(tmp_path, "новая\n")
    пакет = _установлено(tmp_path, "старая\n")

    _прогнать(tmp_path, образ, пакет,
              {"ditto": f'#!/bin/sh\necho сорвалось >> "{tmp_path}/копировали"\nexit 1\n'})

    # Иначе тест проходил бы и когда сценарий вообще не дошёл до подстановки:
    # «старая на месте» — правда и в том случае, а проверяем мы не это.
    assert (tmp_path / "копировали").exists(), "до подстановки дело не дошло"
    assert пакет.is_dir(), "программа пропала совсем"
    assert (пакет / "Contents" / "MacOS" / "AI Job Search").read_text() == "старая\n"


@только_на_маке
def test_образ_без_пакета_ничего_не_трогает(tmp_path):
    склад = tmp_path / "пусто"
    склад.mkdir()
    (склад / "readme.txt").write_text("тут пакета нет")
    рядом = tmp_path / "скачано"
    рядом.mkdir()
    образ = рядом / "AI Job Search.dmg"
    subprocess.run(["hdiutil", "create", "-volname", "AI Job Search", "-srcfolder", str(склад),
                    "-ov", "-format", "UDZO", str(образ)], check=True, capture_output=True)
    пакет = _установлено(tmp_path, "старая\n")

    _прогнать(tmp_path, образ, пакет)

    assert (пакет / "Contents" / "MacOS" / "AI Job Search").read_text() == "старая\n"


@только_на_маке
def test_пока_программа_жива_ничего_не_меняем(tmp_path):
    """Подменить работающую программу под собой хуже, чем не обновиться."""
    образ = _образ(tmp_path, "новая\n")
    пакет = _установлено(tmp_path, "старая\n")
    долгий = subprocess.Popen(["sleep", "120"])
    сценарий = tmp_path / "swap.sh"
    сценарий.write_text(update._MAC_SWAP, encoding="utf-8")
    сценарий.chmod(0o755)
    try:
        r = subprocess.run(["/bin/sh", str(сценарий), str(образ), str(пакет), str(долгий.pid)],
                           capture_output=True, text=True, timeout=180)
        assert r.returncode != 0, "не дождались, но полезли менять"
        assert (пакет / "Contents" / "MacOS" / "AI Job Search").read_text() == "старая\n"
    finally:
        долгий.kill()
