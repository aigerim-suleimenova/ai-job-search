"""То, чем ставятся сами командные строки.

Copilot и Qwen приходят только через npm, а npm приходит с Node.js, которого на
обычном компьютере нет. В карточке было написано «npm install -g @github/copilot»,
человек уходил в терминал и получал «npm: команда не найдена» — уже вне нашей
программы, где помочь ему мы ничем не можем. Здесь проверяется, что про это
сказано вовремя и в самой программе.
"""
import pytest

from conftest import client_for
from jobsearch import config, providers


def нода(monkeypatch, найдена=True, версия="v22.14.0"):
    """Что программа увидит, спросив у компьютера про Node.js."""
    monkeypatch.setattr(providers, "tool_state",
                        lambda tool: (найдена and providers._major(версия) >= 22, версия)
                        if найдена else (False, ""))


def без_командных_строк(monkeypatch):
    """Ни одна из них не установлена — иначе нечего и ставить."""
    monkeypatch.setattr(providers, "_bin_exists", lambda name: False)
    monkeypatch.setattr(providers, "ollama_running", lambda: False)


# --- Кому что нужно ------------------------------------------------------------

@pytest.mark.parametrize("провайдер", ["copilot_cli", "qwen_cli"])
def test_ставятся_через_npm_значит_нужен_node(monkeypatch, провайдер):
    нода(monkeypatch, найдена=False)
    assert providers.missing_tool(провайдер) == "node"


@pytest.mark.parametrize("провайдер", ["claude_cli", "cursor_cli", "goose_cli",
                                       "ollama", "openai_api", "codex_cli"])
def test_у_кого_свой_установщик_ничего_не_требуют(monkeypatch, провайдер):
    """Гнать человека ставить Node.js ради того, кто прекрасно обойдётся без него,
    хуже, чем не сказать про Node.js вовсе. У Codex есть готовый бинарник."""
    нода(monkeypatch, найдена=False)
    assert providers.missing_tool(провайдер) == ""


def test_node_есть_значит_вопроса_нет(monkeypatch):
    нода(monkeypatch)
    assert providers.missing_tool("copilot_cli") == ""


def test_старый_node_это_не_то_же_самое_что_никакого(monkeypatch):
    """Сказать «установите Node.js» тому, у кого он есть, значит послать его
    по кругу: он поставит то же самое и вернётся с тем же."""
    нода(monkeypatch, версия="v18.20.0")
    assert providers.missing_tool("copilot_cli") == "node"
    assert providers.tool_info("node")["too_old"] is True
    assert providers.tool_info("node")["version"] == "v18.20.0"


def test_непонятная_версия_дороги_не_загораживает(monkeypatch):
    """Чужой формат вывода — наша беда, а не человека: пусть пробует."""
    monkeypatch.setattr(providers, "resolve_bin", lambda name: "/usr/bin/node")

    class Вывод:
        stdout, stderr = "какая-то невнятица", ""

    monkeypatch.setattr(providers.subprocess, "run", lambda *a, **kw: Вывод())
    providers.tool_state.cache_clear()
    assert providers.tool_state("node")[0] is True


def test_про_неизвестный_инструмент_молчим():
    assert providers.tool_state("несуществующий") == (True, "")
    assert providers.missing_tool("несуществующий_провайдер") == ""


# --- Экран --------------------------------------------------------------------

def test_выбор_ведёт_туда_где_сказано_чем_ставить(monkeypatch, profile):
    """Раньше человек возвращался на первый шаг к кнопке «Скачать», уходил по ней
    в инструкцию с «npm install -g …» и узнавал про npm уже в терминале."""
    import app as приложение
    без_командных_строк(monkeypatch)
    нода(monkeypatch, найдена=False)

    ответ = client_for(приложение.app, profile).post(
        "/models/provider", data={"provider": "copilot_cli", "back": "/welcome",
                                  "anchor": "welcome-continue"},
        follow_redirects=False)

    assert ответ.status_code == 303
    assert "step=tools" in ответ.headers["location"]


def test_выбор_того_кому_ничего_не_нужно_никуда_не_уводит(monkeypatch, profile):
    import app as приложение
    без_командных_строк(monkeypatch)
    нода(monkeypatch, найдена=False)

    ответ = client_for(приложение.app, profile).post(
        "/models/provider", data={"provider": "claude_cli", "back": "/welcome",
                                  "anchor": "welcome-continue"},
        follow_redirects=False)

    assert "step=tools" not in ответ.headers["location"]


def test_на_экране_сказано_что_и_зачем(monkeypatch, profile):
    import app as приложение
    без_командных_строк(monkeypatch)
    нода(monkeypatch, найдена=False)
    cfg = config.load()
    cfg["llm"]["provider"] = "copilot_cli"
    config.save(cfg)

    страница = client_for(приложение.app, profile).get("/welcome?step=tools").text

    assert "Node.js" in страница
    assert "npm" in страница, "не сказано, при чём тут Node.js"
    assert "GitHub Copilot" in страница, "не сказано, ради чего всё это"
    assert 'action="/tool/install"' in страница
    assert 'action="/tool/recheck"' in страница
    # Дальше не пускаем: ставить всё равно нечем
    assert "disabled" in страница


def test_экран_не_становится_тупиком(monkeypatch, profile):
    """Node.js поставили — на этом экране человека больше нечем занять, и
    оставлять его там значит требовать поставить то, что уже стоит."""
    import app as приложение
    без_командных_строк(monkeypatch)
    нода(monkeypatch)
    cfg = config.load()
    cfg["llm"]["provider"] = "copilot_cli"
    config.save(cfg)

    страница = client_for(приложение.app, profile).get("/welcome?step=tools").text

    assert "welcome-tools" not in страница


def test_старому_node_говорят_про_версию_а_не_про_установку(monkeypatch, profile):
    import app as приложение
    без_командных_строк(monkeypatch)
    нода(monkeypatch, версия="v18.20.0")
    cfg = config.load()
    cfg["llm"]["provider"] = "qwen_cli"
    config.save(cfg)

    страница = client_for(приложение.app, profile).get("/welcome?step=tools").text

    assert "v18.20.0" in страница, "не сказано, что именно нашлось"
    assert "22" in страница, "не сказано, что нужно"


# --- Кнопки -------------------------------------------------------------------

def test_скачать_открывает_страницу_node(monkeypatch, profile):
    import app as приложение
    открыто = []
    monkeypatch.setattr(приложение.webbrowser, "open", открыто.append)

    client_for(приложение.app, profile).post(
        "/tool/install", data={"tool": "node"}, follow_redirects=False)

    assert открыто == ["https://nodejs.org/en/download"]


def test_чужой_адрес_этой_кнопкой_не_открыть(monkeypatch, profile):
    """Со страницы приходит только код инструмента. Иначе кнопку можно было бы
    заставить открыть что угодно."""
    import app as приложение
    открыто = []
    monkeypatch.setattr(приложение.webbrowser, "open", открыто.append)

    client_for(приложение.app, profile).post(
        "/tool/install", data={"tool": "https://example.com/зловред"},
        follow_redirects=False)

    assert открыто == []


def test_проверить_снова_спрашивает_заново(monkeypatch, profile):
    """Человек только что вернулся с установки — старый ответ уже неверен."""
    import app as приложение
    забыли = []
    monkeypatch.setattr(providers, "forget_binaries", lambda: забыли.append(True))
    нода(monkeypatch)

    client_for(приложение.app, profile).post(
        "/tool/recheck", data={"tool": "node"}, follow_redirects=False)

    assert забыли, "ответ взяли из памяти, и установка осталась незамеченной"


def test_незнакомый_код_не_роняет_на_экран_ключ_перевода(profile):
    """Непереведённый ключ на экране выглядит как поломка — это мы уже проходили."""
    import app as приложение
    ответ = client_for(приложение.app, profile).post(
        "/tool/recheck", data={"tool": "чегототам"}, follow_redirects=False)
    assert "tool_" not in ответ.headers["location"]


def test_на_странице_моделей_про_это_тоже_сказано(monkeypatch, profile):
    """Провайдера меняют и после знакомства — и упираются в ту же стену. Экрана
    «что нужно установить» здесь нет, но молчать про npm, которого нет, нельзя."""
    import app as приложение
    без_командных_строк(monkeypatch)
    нода(monkeypatch, найдена=False)
    cfg = config.load()
    cfg["llm"]["provider"] = "copilot_cli"
    config.save(cfg)

    страница = client_for(приложение.app, profile).get("/models").text

    assert "Node.js" in страница


def test_чужие_карточки_про_node_не_говорят(monkeypatch, profile):
    """У Claude Code своя беда, и Node.js тут ни при чём."""
    import app as приложение
    без_командных_строк(monkeypatch)
    нода(monkeypatch, найдена=False)
    cfg = config.load()
    cfg["llm"]["provider"] = "claude_cli"
    config.save(cfg)

    страница = client_for(приложение.app, profile).get("/models").text

    assert "Node.js" not in страница


def test_на_странице_моделей_есть_и_кнопка_а_не_только_надпись(monkeypatch, profile):
    """Сказать, чего не хватает, и не сказать, где это взять, — полдела.
    На знакомстве для этого свой экран, здесь хватает кнопки."""
    import app as приложение
    без_командных_строк(monkeypatch)
    нода(monkeypatch, найдена=False)
    cfg = config.load()
    cfg["llm"]["provider"] = "qwen_cli"
    config.save(cfg)

    страница = client_for(приложение.app, profile).get("/models").text

    assert 'action="/tool/install"' in страница, "негде взять то, чего не хватает"


def test_кнопки_нет_когда_всё_на_месте(monkeypatch, profile):
    import app as приложение
    без_командных_строк(monkeypatch)
    нода(monkeypatch)
    cfg = config.load()
    cfg["llm"]["provider"] = "qwen_cli"
    config.save(cfg)

    страница = client_for(приложение.app, profile).get("/models").text

    assert 'action="/tool/install"' not in страница
