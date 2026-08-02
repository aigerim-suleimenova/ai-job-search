"""Starting over: the introduction that insists, erasing the data, removing a model.

All three come from one complaint. Somebody deleted the program and Ollama with
it, installed the new version, and was met by their old profile, their old
settings, and a model marked "in use" that was no longer anywhere on the disk.
Uninstalling does not touch the data directory — so a reinstall is not a fresh
start, and the app went on believing the introduction was over and the model was
there. There was no way from inside to say otherwise: not to erase the data, not
to remove a model, and not even to reach the one screen that could have fixed it.
"""
import re
from urllib.parse import unquote

import pytest

pytest.importorskip("httpx", reason="TestClient требует httpx")

from fastapi.testclient import TestClient  # noqa: E402

from jobsearch import appstate, config, db, profiles, providers  # noqa: E402

from conftest import job  # noqa: E402


@pytest.fixture
def client(profile):
    import app as app_module
    with TestClient(app_module.app, base_url="http://127.0.0.1:8765") as c:
        c.cookies.set("profile", profile)
        yield c


def строка_модели(html: str, model_id: str) -> str:
    """Кусок страницы, относящийся к одной модели."""
    anchor = "model-" + re.sub(r"[:/.]", "-", model_id)
    куски = html.split('<div class="model-row')
    return next(k for k in куски if f'id="{anchor}"' in k)


def без_модели(monkeypatch):
    """Ollama выбрана, но её на этой машине нет — ровно как после переустановки."""
    monkeypatch.setattr(providers, "ollama_running", lambda: False)
    monkeypatch.setattr(providers, "ollama_installed_models", lambda: set())
    cfg = config.load()
    cfg["llm"].update(provider="ollama", triage_model="gemma2:9b")
    config.save(cfg)


# --- The introduction insists ---------------------------------------------------

СТРАНИЦЫ = ["/", "/simple", "/results", "/coverage", "/models", "/notify", "/cv/check"]


@pytest.mark.parametrize("path", СТРАНИЦЫ)
def test_без_модели_видно_только_знакомство(client, monkeypatch, path):
    """Это и видел человек: настройки открывались, а поиск запустить было нечем."""
    без_модели(monkeypatch)
    r = client.get(path, follow_redirects=False)
    assert r.status_code == 303, f"{path} открылась без модели"
    assert r.headers["location"] == "/welcome"


def test_пройденное_знакомство_не_спасает_от_пропавшей_модели(client, monkeypatch):
    """Отметка «знакомство пройдено» переживает удаление программы — и раньше
    одной её хватало, чтобы пустить внутрь при неработающей модели."""
    без_модели(monkeypatch)
    assert appstate.setup_done(), "предусловие: отметка на месте"
    assert appstate.needs_setup(), "отметки хватило, хотя модели нет"


def test_с_рабочей_моделью_страницы_открываются(client):
    """Обратная сторона: настроенную программу знакомство держать не должно."""
    for path in СТРАНИЦЫ:
        assert client.get(path, follow_redirects=False).status_code == 200, path


def test_продолжить_без_модели_нельзя_и_в_обход_кнопки(client, monkeypatch):
    """Кнопка на странице выключена, но адрес можно позвать и руками."""
    без_модели(monkeypatch)
    (profiles.DATA_ROOT / "app.json").unlink(missing_ok=True)

    r = client.post("/welcome/done", follow_redirects=False)

    assert r.headers["location"].startswith("/welcome"), "пустили в программу без модели"
    assert not appstate.load().get("setup_done"), "знакомство отмечено пройденным без модели"


def test_чужая_модель_не_считается_поломкой(client, monkeypatch):
    """Кто-то мог скачать модель, которой нет в нашем списке. Это не беда."""
    monkeypatch.setattr(providers, "ollama_running", lambda: True)
    monkeypatch.setattr(providers, "ollama_installed_models", lambda: {"своя-модель:7b"})
    cfg = config.load()
    cfg["llm"].update(provider="ollama", triage_model="своя-модель:7b")
    config.save(cfg)

    assert providers.missing_piece(config.load()["llm"]) == ""


def test_причина_названа_точно(client, monkeypatch):
    """Запущенная Ollama без модели — не то же самое, что отсутствующая Ollama:
    иначе человека отправляют переустанавливать то, что и так работает."""
    monkeypatch.setattr(providers, "ollama_running", lambda: True)
    monkeypatch.setattr(providers, "ollama_installed_models", lambda: set())
    cfg = config.load()
    cfg["llm"].update(provider="ollama", triage_model="gemma2:9b")
    config.save(cfg)
    assert providers.missing_piece(config.load()["llm"]) == "model"

    monkeypatch.setattr(providers, "ollama_running", lambda: False)
    assert providers.missing_piece(config.load()["llm"]) == "provider"


# --- Erasing everything ---------------------------------------------------------

def test_удаление_данных_стирает_профили_и_отметку(client, profile):
    config.save(config.load())                       # у профиля появились настройки
    db.save_job(job("k1", score=70), run_id=1)
    assert (profiles.PROFILES_DIR / profile / "config.json").exists()

    r = client.post("/app/reset", follow_redirects=False)

    assert r.status_code == 303
    assert r.headers["location"].startswith("/welcome")
    assert not (profiles.PROFILES_DIR / profile).exists(), "данные человека остались"
    assert not (profiles.DATA_ROOT / "app.json").exists(), "отметка о знакомстве осталась"
    assert not appstate.setup_done(), "программа считает себя настроенной"


def test_после_удаления_программа_работает_дальше(client):
    """Стереть данные — не то же самое, что сломать программу: пустой профиль
    должен появиться сам, а база — снова завестись."""
    client.post("/app/reset", follow_redirects=False)

    assert profiles.list_profiles(), "не осталось ни одного профиля"
    profiles.set_active(profiles.default_slug())
    db.init()
    assert db.matched_jobs(min_score=0) == [], "база не завелась заново"
    assert client.get("/welcome").status_code == 200


def test_удаление_не_трогает_то_чем_программа_живёт(client):
    """Замок одиночного запуска и журнал аварий — не данные человека. Стереть их
    значит выбить опору из-под той самой копии, что стирает, а на Windows
    открытый журнал и не удалится."""
    (profiles.DATA_ROOT / "running.json").write_text("{}", encoding="utf-8")
    (profiles.DATA_ROOT / "errors.log").write_text("былое", encoding="utf-8")

    client.post("/app/reset", follow_redirects=False)

    assert (profiles.DATA_ROOT / "running.json").exists(), "замок запуска удалён"
    assert (profiles.DATA_ROOT / "errors.log").exists(), "журнал аварий удалён"


def test_удаление_забирает_и_то_о_чём_никто_не_помнил(client):
    """Стирается всё, кроме служебного, а не список известных имён: файл, который
    в программе заведут завтра, иначе молча пережил бы «удалить всё»."""
    (profiles.DATA_ROOT / "что-то-новое.json").write_text("{}", encoding="utf-8")

    client.post("/app/reset", follow_redirects=False)

    assert not (profiles.DATA_ROOT / "что-то-новое.json").exists()


def test_во_время_поиска_данные_не_стираются(client):
    """Прогон пишет в те самые файлы: часть из них молча вернулась бы обратно."""
    from jobsearch import pipeline
    config.save(config.load())
    pipeline.state["running"] = True
    try:
        r = client.post("/app/reset", follow_redirects=False)
    finally:
        pipeline.state["running"] = False

    assert r.headers["location"].startswith("/?"), "не объяснили, почему не стёрли"
    assert profiles.list_profiles(), "данные всё-таки стёрли посреди поиска"


def test_неудалившийся_файл_назван_а_не_скрыт(client, monkeypatch):
    """Что-то может не удалиться — молчать об этом нельзя: человек уйдёт в
    уверенности, что начал с чистого листа."""
    import app as app_module
    monkeypatch.setattr(app_module.profiles, "wipe_all", lambda: ["jobs.db"])
    cfg = config.load()
    cfg["ui"]["lang"] = "en"
    config.save(cfg)

    r = client.post("/app/reset", follow_redirects=False)

    место = r.headers["location"]
    assert "jobs.db" in место, f"не назвали, что осталось: {место}"
    assert "clean+sheet" not in место and "clean%20sheet" not in место, \
        "отчитались о чистом листе, оставив файл"


def test_знакомство_предлагает_стереть_только_когда_есть_что(client, monkeypatch):
    без_модели(monkeypatch)
    config.save(config.load())                       # что-то уже есть
    assert 'action="/app/reset"' in client.get("/welcome").text

    profiles.wipe_all()
    profiles.ensure_migrated()
    profiles.set_active(profiles.default_slug())
    assert 'action="/app/reset"' not in client.get("/welcome").text, \
        "новому человеку предложили стереть то, чего у него нет"


# --- Removing a downloaded model ------------------------------------------------

def test_модель_удаляется_из_ollama(client, monkeypatch):
    ушло = {}

    def подмена(model):
        ушло["model"] = model

    monkeypatch.setattr(providers, "delete_model", подмена)
    r = client.post("/models/delete", data={"model": "gemma2:9b", "back": "/models"},
                    follow_redirects=False)

    assert ушло == {"model": "gemma2:9b"}, "запрос на удаление не ушёл"
    assert r.status_code == 303


def test_удаление_используемой_модели_ведёт_к_знакомству(client, monkeypatch):
    """Удалить ту, что в ходу, можно — но думать станет нечем, и сказать об этом
    надо сразу, а не молча высадить на неработающую страницу."""
    cfg = config.load()
    cfg["llm"].update(provider="ollama", triage_model="gemma2:9b")
    config.save(cfg)
    monkeypatch.setattr(providers, "delete_model", lambda model: None)
    monkeypatch.setattr(providers, "ollama_installed_models", lambda: set())

    r = client.post("/models/delete", data={"model": "gemma2:9b", "back": "/models"},
                    follow_redirects=False)

    assert r.headers["location"].startswith("/welcome"), r.headers["location"]
    assert "gemma2:9b" in unquote(r.headers["location"]), "не сказали, что именно удалили"


def test_во_время_скачивания_не_удаляем(client, monkeypatch):
    monkeypatch.setattr(providers, "pull_in_progress", lambda: True)
    трогали = []
    monkeypatch.setattr(providers, "delete_model", lambda model: трогали.append(model))

    client.post("/models/delete", data={"model": "gemma2:9b"}, follow_redirects=False)

    assert трогали == [], "удаляли модель, пока идёт скачивание"


def test_объяснение_не_теряется_по_дороге(client, monkeypatch):
    """Страница, с которой пришли, могла только что закрыться — модели больше нет.
    Отправить человека обратно на неё значит, что гейт уведёт его на знакомство,
    а объяснение отвалится вместе с адресом: незнакомый экран и ни слова о том,
    почему он тут."""
    без_модели(monkeypatch)

    def взорваться(model):
        raise providers.ProviderError(key="prov_err_ollama_down")

    monkeypatch.setattr(providers, "delete_model", взорваться)

    r = client.post("/models/delete", data={"model": "gemma2:9b", "back": "/models"},
                    follow_redirects=False)

    место = r.headers["location"]
    assert место.startswith("/welcome?"), f"объяснение потерялось по пути: {место}"
    assert "Ollama" in unquote(место), "не сказали, что именно случилось"


def test_ошибка_удаления_объясняется_человеку(client, monkeypatch):
    def взорваться(model):
        raise providers.ProviderError(key="prov_err_ollama_down")

    monkeypatch.setattr(providers, "delete_model", взорваться)
    cfg = config.load()
    cfg["ui"]["lang"] = "en"
    config.save(cfg)

    r = client.post("/models/delete", data={"model": "gemma2:9b", "back": "/models"},
                    follow_redirects=False)

    assert "Ollama" in r.headers["location"], r.headers["location"]


def test_кнопка_удаления_есть_у_скачанной_и_нет_у_остальных(client, monkeypatch):
    from jobsearch import i18n
    monkeypatch.setattr(providers, "ollama_installed_models", lambda: {"gemma2:9b"})
    cfg = config.load()
    cfg["ui"]["lang"] = "en"
    cfg["llm"].update(provider="ollama", triage_model="gemma2:9b")
    config.save(cfg)

    html = client.get("/models").text

    скачанная = строка_модели(html, "gemma2:9b")
    нескачанная = строка_модели(html, "llama3.1:8b")

    assert "/models/delete" in скачанная, "у скачанной модели нет кнопки удаления"
    assert i18n.t("en", "models_delete") in скачанная
    assert "/models/delete" not in нескачанная, "предложили удалить то, чего не скачано"


def test_вопрос_перед_удалением_не_ломается_об_апостроф(client):
    """Текст вопроса уезжает в confirm(). Раньше он стоял прямо в onsubmit, и
    апостроф — по-французски его не избежать — обрывал строку: кнопка молча
    переставала и спрашивать, и удалять."""
    from jobsearch import config as cfg_mod, i18n
    for lang in i18n.UI_LANGS:
        cfg = cfg_mod.load()
        cfg["ui"]["lang"] = lang
        cfg_mod.save(cfg)

        html = client.get("/").text

        assert "data-confirm=" in html, f"{lang}: вопросов на странице не осталось вовсе"
        assert "confirm('" not in html, \
            f"{lang}: текст вопроса снова стоит строкой внутри кода"
