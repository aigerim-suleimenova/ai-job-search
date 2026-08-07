"""Your own endpoint, speaking OpenAI's language.

This one provider covers as much as all the command-line tools together:
OpenRouter with its hundreds of models, LM Studio and llama.cpp on your own
computer, vLLM, a corporate gateway, OpenAI itself. The protocol they share is
one, and adding them one at a time would never end.
"""
import pytest
import requests

from jobsearch import providers


class Answer:
    def __init__(self, payload, status=200):
        self._payload, self.status_code = payload, status

    @property
    def text(self):
        return "" if isinstance(self._payload, Exception) else str(self._payload)

    def raise_for_status(self):
        if self.status_code >= 400:
            raise requests.HTTPError(f"{self.status_code} for {self._payload}")

    def json(self):
        if isinstance(self._payload, Exception):
            raise self._payload
        return self._payload


def service(monkeypatch, payload, status=200):
    """Stands in for the network and remembers exactly what we sent."""
    sent = {}

    def post(url, headers=None, json=None, timeout=None):
        sent.update(url=url, headers=headers or {}, body=json)
        return Answer(payload, status)

    monkeypatch.setattr(providers.requests, "post", post)
    return sent


ANSWER = {"choices": [{"message": {"content": "  a ready answer  "}}]}
SETTINGS = {"api_base": "https://openrouter.ai/api/v1", "api_key": "sk-or-v1-0a1b2c3d",
             "api_model": "anthropic/claude-sonnet-5"}


def test_the_request_goes_where_it_should(monkeypatch):
    sent = service(monkeypatch, ANSWER)

    answer = providers.call_openai_api("hello", SETTINGS, timeout=30)

    assert answer == "a ready answer", "the answer was not parsed or not stripped"
    assert sent["url"] == "https://openrouter.ai/api/v1/chat/completions"
    assert sent["body"]["model"] == "anthropic/claude-sonnet-5"
    assert sent["body"]["messages"] == [{"role": "user", "content": "hello"}]
    assert sent["body"]["stream"] is False


def test_the_key_goes_in_a_header_not_in_the_url(monkeypatch):
    """We have been through this with the Telegram token: the address ends up in the
    text of the exception, and from there in the run log, which people show to
    others."""
    sent = service(monkeypatch, ANSWER)

    providers.call_openai_api("hello", SETTINGS, timeout=30)

    assert sent["headers"]["Authorization"] == "Bearer sk-or-v1-0a1b2c3d"
    assert "sk-or-v1-0a1b2c3d" not in sent["url"]


def test_it_works_without_a_key_too(monkeypatch):
    """LM Studio and llama.cpp on your own computer simply have no key."""
    sent = service(monkeypatch, ANSWER)
    own = dict(SETTINGS, api_base="http://127.0.0.1:1234/v1", api_key="")

    providers.call_openai_api("hello", own, timeout=30)

    assert "Authorization" not in sent["headers"], "we sent an empty key"


def test_the_key_stays_out_of_the_error_text(monkeypatch):
    def blow_up(url, **kw):
        raise requests.ConnectionError(f"no connection to {url}?key=sk-or-v1-0a1b2c3d")

    monkeypatch.setattr(providers.requests, "post", blow_up)

    with pytest.raises(providers.ProviderError) as caught:
        providers.call_openai_api("hello", SETTINGS, timeout=30)

    assert "sk-or-v1-0a1b2c3d" not in str(caught.value) + str(caught.value.fmt)


def test_a_trailing_slash_in_the_address_breaks_nothing(monkeypatch):
    sent = service(monkeypatch, ANSWER)
    providers.call_openai_api("x", dict(SETTINGS, api_base="https://a.example/v1/"), timeout=30)
    assert sent["url"] == "https://a.example/v1/chat/completions"


@pytest.mark.parametrize("missing_field,key", [
    ("api_base", "prov_err_no_api_base"),
    ("api_model", "prov_err_no_api_model"),
])
def test_with_no_settings_we_say_what_is_missing(missing_field, key):
    own = dict(SETTINGS, **{missing_field: ""})
    with pytest.raises(providers.ProviderError) as caught:
        providers.call_openai_api("hello", own, timeout=30)
    assert caught.value.key == key


def test_an_unfamiliar_answer_shape_is_explained_not_fatal(monkeypatch):
    """A service may put its own error in the body — that is what a person should be
    shown, not a traceback through somebody else's JSON."""
    service(monkeypatch, {"error": {"message": "no credits left"}})

    with pytest.raises(providers.ProviderError) as caught:
        providers.call_openai_api("hello", SETTINGS, timeout=30)

    assert "no credits" in str(caught.value)


@pytest.mark.parametrize("code", [400, 401, 402, 429, 500])
def test_a_services_refusal_is_explained_in_its_own_words(monkeypatch, code):
    """Found by checking live, not here.

    Real services answer trouble with an error code and a body giving the reason.
    The test next door used a 200 with the error in the body — nobody answers like
    that, and so this slipped past: raise_for_status turned the answer into an
    HTTPError, and a person out of credit on OpenRouter read "the service is
    unreachable". The service had answered properly and named the reason, and we
    were throwing it away."""
    service(monkeypatch, {"error": {"message": "insufficient_quota"}}, status=code)

    with pytest.raises(providers.ProviderError) as caught:
        providers.call_openai_api("hello", SETTINGS, timeout=30)

    shown = str(caught.value)
    assert "insufficient_quota" in shown, f"the reason was lost: {shown}"
    assert caught.value.key != "prov_err_api_unreachable", "the service answered and we are saying it did not"


def test_a_refusal_with_no_readable_body_names_at_least_the_code(monkeypatch):
    service(monkeypatch, "<html>502 Bad Gateway</html>", status=502)
    with pytest.raises(providers.ProviderError) as caught:
        providers.call_openai_api("hello", SETTINGS, timeout=30)
    assert "502" in str(caught.value)


def test_a_key_with_extras_does_not_pose_as_the_services_fault(monkeypatch):
    """Another live-check find. Only Latin script can go into a request header, and
    requests falls over that with a UnicodeEncodeError — which is a ValueError, so
    the person read "the service did not answer in JSON". About a service the
    request never reached."""
    service(monkeypatch, ANSWER)
    # Cyrillic on purpose: a header takes Latin script only, and this is the
    # UnicodeEncodeError being provoked.
    with_extras = dict(SETTINGS, api_key="sk-это-не-ключ")

    with pytest.raises(providers.ProviderError) as caught:
        providers.call_openai_api("hello", with_extras, timeout=30)

    assert caught.value.key == "prov_err_api_bad_key"


def test_not_json_is_explained(monkeypatch):
    service(monkeypatch, ValueError("this is not json"))
    with pytest.raises(providers.ProviderError) as caught:
        providers.call_openai_api("hello", SETTINGS, timeout=30)
    assert caught.value.key == "prov_err_api_not_json"


# --- How it looks to the rest of the program ----------------------------------

def test_nothing_to_install_so_it_is_always_ready():
    """The first step asks what to install. Your own endpoint has nothing to install.

    Readiness here used to mean "the address is already filled in", and until then
    the card hung there with a red "not installed" — beside an "Install" button
    with nothing to do. Nothing to install means ready."""
    empty = providers.available("claude", {})["openai_api"]
    filled_in = providers.available("claude", {"api_base": "https://a.example/v1"})["openai_api"]
    assert empty["ready"] is True
    assert filled_in["ready"] is True


def test_both_address_and_model_are_asked_at_the_second_step():
    """The address, the key and the model name are one question — "where do we go" —
    and it is asked whole at the second step.

    The address used to live in the card on the first step, where there was no
    width for it: the fields lined up side by side and were cut off halfway
    through the hint — of https://openrouter… you could see "https://ope". Until
    both are filled in, the second step is not passed."""
    empty = {"provider": "openai_api"}
    no_model = {"provider": "openai_api", "api_base": "https://a.example/v1"}
    no_address = {"provider": "openai_api", "api_model": "gpt-4o"}
    complete = dict(no_model, api_model="gpt-4o")
    assert providers.missing_piece(empty) == "model"
    assert providers.missing_piece(no_model) == "model"
    assert providers.missing_piece(no_address) == "model"
    assert providers.missing_piece(complete) == ""


def test_the_shared_call_carries_the_settings_through(monkeypatch):
    """providers.call gets the whole settings block — otherwise the address and the
    key simply do not reach it."""
    sent = service(monkeypatch, ANSWER)

    providers.call("hello", "openai_api", model="does not matter", timeout=30, llm=SETTINGS)

    assert sent["body"]["model"] == "anthropic/claude-sonnet-5"


def test_it_has_no_web_search():
    """Promising a web search that may not be there is worse than not promising."""
    assert providers.supports_web_search("openai_api") is False


def test_the_model_name_comes_from_this_endpoint_not_the_neighbour():
    """triage_model is left over from the previous provider, and the "Own endpoint"
    card said "in use: haiku" — the name of a Claude Code model that address has
    nothing to do with."""
    from jobsearch import providers as p
    leftover = {"provider": "openai_api", "triage_model": "haiku"}
    assert p.current_model(leftover) == ""
    assert p.current_model(dict(leftover, api_model="gpt-4o")) == "gpt-4o"
    assert p.current_model({"provider": "claude_cli", "triage_model": "haiku"}) == "haiku"


def test_the_models_page_does_not_name_the_other_providers_model(profile):
    """The same thing through the page: the value reaches the template."""
    from conftest import client_for
    from jobsearch import config
    import app as app_module

    cfg = config.load()
    cfg["llm"].update(provider="openai_api", triage_model="haiku", api_model="")
    config.save(cfg)

    page = client_for(app_module.app, profile).get("/models").text
    assert "haiku" not in page, "the previous provider's model name is on the page"
