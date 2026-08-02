"""Обновление: узнать о новой версии и поставить её.

Отдельного внимания стоит то, откуда берётся файл. GitHub присылает список
файлов вместе с адресами, но адрес в ответе — это лишь предложение: ответ можно
подменить по дороге, а скачанное здесь будет запущено. Поэтому адрес проверяется
перед скачиванием, и проверяется в том самом месте, которое лезет в сеть, — а не
только там, где его выбирали.
"""
import hashlib

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


def test_где_ставить_руками_ничего_не_предлагается(monkeypatch):
    """Образ надо перетащить, архив распаковать — без человека не обойтись."""
    monkeypatch.setattr(update.platform, "system", lambda: "Darwin")
    assets = [{"name": "AI Job Search Setup.exe",
               "browser_download_url": update.DOWNLOAD_PREFIX + "v1/s.exe"}]
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
    net.забыть()
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
    monkeypatch.setattr(net, "_сессия_системы", lambda: Сессия())

    assert net.get("https://api.github.com/x").json() == {"ok": True}
    assert пошло_через == ["свой набор", "хранилище системы"]
    assert net.через_хранилище_системы() is True

    # и дальше ходим сразу туда: второй попытки на каждый запрос не надо
    пошло_через.clear()
    net.get("https://api.github.com/y")
    assert пошло_через == ["хранилище системы"]
    net.забыть()


def test_без_перехвата_ничего_не_меняется(monkeypatch):
    """У кого соединения не проверяют — для того всё как было."""
    from jobsearch import net
    net.забыть()
    звали = []

    monkeypatch.setattr(net.requests, "get",
                        lambda url, **kw: звали.append(url) or "ответ")
    monkeypatch.setattr(net, "_сессия_системы",
                        lambda: (_ for _ in ()).throw(AssertionError("зря полезли в хранилище")))

    assert net.get("https://example.com") == "ответ"
    assert net.через_хранилище_системы() is False
    net.забыть()


def test_другие_беды_сети_на_хранилище_не_списываются(monkeypatch):
    """Нет связи — это нет связи, а не чужой корень. Молча подменять одно другим
    значило бы прятать настоящую причину."""
    import requests

    from jobsearch import net
    net.забыть()
    monkeypatch.setattr(net.requests, "get",
                        lambda url, **kw: (_ for _ in ()).throw(
                            requests.exceptions.ConnectionError("нет связи")))

    with pytest.raises(requests.exceptions.ConnectionError):
        net.get("https://example.com")
    assert net.через_хранилище_системы() is False
    net.забыть()
