"""Секреты не должны оседать в файлах, которые человек кому-то показывает.

Токен бота Telegram обязан стоять прямо в адресе — так устроен их API. А requests
вкладывает адрес в текст своего исключения, и это исключение попадало в журнал
прогона: он хранится в базе, показывается на странице результатов и первым делом
прикладывается к жалобе на ошибку. Токен — это полная власть над ботом и вся его
переписка.

Вдобавок requests.RequestException — это OSError, а не RuntimeError, поэтому оно
пролетало мимо всех наших `except RuntimeError` прямо в общий обработчик, а тот
пишет traceback целиком.
"""
import pytest
import requests

from jobsearch import notify

ТОКЕН = "123456789:AAHfake_TOKEN_value_do_not_use"


def оборвать_связь(monkeypatch):
    def взорваться(*a, **kw):
        raise requests.ConnectionError(
            f"HTTPSConnectionPool(host='api.telegram.org', port=443): "
            f"Max retries exceeded with url: /bot{ТОКЕН}/sendMessage")

    monkeypatch.setattr(notify.requests, "post", взорваться)
    monkeypatch.setattr(notify.requests, "get", взорваться)


def test_токен_не_попадает_в_текст_ошибки(monkeypatch):
    оборвать_связь(monkeypatch)
    cfg = {"telegram": {"bot_token": ТОКЕН, "chat_id": "42"}}

    with pytest.raises(notify.NotifyError) as поймано:
        notify.send_message(cfg, "привет")

    текст = str(поймано.value) + str(поймано.value.fmt)
    assert ТОКЕН not in текст, "токен виден в ошибке, которая уйдёт в журнал"
    assert "***" in текст, "токен не заменён, а просто пропал вместе с причиной"


def test_токен_не_течёт_и_при_поиске_chat_id(monkeypatch):
    оборвать_связь(monkeypatch)

    with pytest.raises(notify.NotifyError) as поймано:
        notify.detect_chat_id(ТОКЕН)

    assert ТОКЕН not in str(поймано.value) + str(поймано.value.fmt)


def test_ошибка_сети_ловится_нашими_же_обработчиками(monkeypatch):
    """Они написаны на RuntimeError, а requests бросает наследника OSError —
    и всё пролетало мимо, в общий обработчик с полным traceback."""
    assert not issubclass(requests.RequestException, RuntimeError), \
        "предпосылка изменилась — проверку стоит переписать"
    assert issubclass(notify.NotifyError, RuntimeError)

    оборвать_связь(monkeypatch)
    cfg = {"telegram": {"bot_token": ТОКЕН, "chat_id": "42"}}

    try:
        notify.send_message(cfg, "привет")
    except RuntimeError:
        pass                      # то, что и ждут вызывающие
    except Exception as e:        # noqa: BLE001
        pytest.fail(f"мимо обработчиков пролетело {type(e).__name__}")


def test_журнал_прогона_остаётся_без_токена(profile, monkeypatch):
    """Сквозная проверка: до самого журнала, который лежит в базе."""
    from jobsearch import config, db, pipeline
    оборвать_связь(monkeypatch)
    monkeypatch.setattr(pipeline.aggregators, "collect", lambda cfg, log, cov: [])
    cfg = config.load()
    cfg["telegram"].update(bot_token=ТОКЕН, chat_id="42")
    cfg["search"].update(discover_per_run=0, discover_ats_per_run=0)
    config.save(cfg)

    pipeline.run(trigger="test", profile=profile)

    журнал = db.recent_runs(1)[0]["log"] or ""
    assert ТОКЕН not in журнал, "токен записан в журнал прогона"


# --- CV и сетевые инструменты не встречаются в одном запросе ---------------------

def test_в_запрос_с_сетью_не_попадает_ничего_личного(profile, monkeypatch):
    """Описание вакансии пишет посторонний, и до шести тысяч его знаков попадали
    в тот же запрос, где лежали резюме, зарплатные ожидания и виза, — при
    разрешённых WebSearch и WebFetch и без человека, который мог бы возразить."""
    from jobsearch import config, llm, scoring

    запросы = []

    def запомнить(prompt, **kw):
        запросы.append({"prompt": prompt, "tools": kw.get("allowed_tools")})
        return {}

    monkeypatch.setattr(llm, "ask_json", запомнить)
    cfg = config.load()
    cfg["profile"].update(summary="Виктор Лавров", salary="120000 EUR",
                          visa_note="нужна Blue Card", roles="Frontend")
    config.save(cfg)
    cv = "СЕКРЕТНОЕ-РЕЗЮМЕ Виктор Лавров frontend@example.com +49 30 1234567"

    scoring.deep_analyze({"title": "Frontend", "company": "Acme", "location": "Berlin",
                          "url": "https://example.com/1",
                          "description": "React. " * 100},
                         cfg, cv, lambda _m: None, research=True)

    с_сетью = [з for з in запросы if з["tools"]]
    без_сети = [з for з in запросы if not з["tools"]]
    assert с_сетью, "запрос с поиском в интернете вовсе не сделан"
    assert без_сети, "разбор под кандидата не сделан"

    for з in с_сетью:
        assert "СЕКРЕТНОЕ-РЕЗЮМЕ" not in з["prompt"], "резюме ушло в запрос с сетью"
        assert "120000 EUR" not in з["prompt"], "зарплатные ожидания ушли в запрос с сетью"
        assert "Blue Card" not in з["prompt"], "виза ушла в запрос с сетью"


def test_без_изучения_компании_сеть_не_включается(profile, monkeypatch):
    from jobsearch import config, llm, scoring
    инструменты = []
    monkeypatch.setattr(llm, "ask_json",
                        lambda prompt, **kw: инструменты.append(kw.get("allowed_tools")) or {})

    scoring.deep_analyze({"title": "T", "company": "C", "location": "",
                          "description": "x" * 400},
                         config.load(), "CV", lambda _m: None, research=False)

    assert all(not и for и in инструменты), "инструменты выданы там, где их не просили"


def test_ссылка_javascript_от_модели_не_доезжает_до_страницы(profile, monkeypatch):
    """Экранирование бережёт от разрыва атрибута, но «javascript:» — законное
    значение href, и щелчок по номеру источника выполнил бы чужой сценарий."""
    import json as _json
    from jobsearch import config, llm, scoring

    monkeypatch.setattr(llm, "ask_json", lambda prompt, **kw: {
        "match": 80, "reason": "ок",
        "sources": ["javascript:fetch('/app/reset',{method:'POST'})",
                    "data:text/html,<script>x</script>",
                    "https://glassdoor.com/acme"]})

    job = {"title": "T", "company": "C", "location": "", "description": "x" * 400}
    scoring.deep_analyze(job, config.load(), "CV", lambda _m: None, research=False)

    ссылки = _json.loads(job["advice"])["sources"]
    assert ссылки == ["https://glassdoor.com/acme"], f"пропустили опасную ссылку: {ссылки}"
