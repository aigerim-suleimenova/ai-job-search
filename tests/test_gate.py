"""Only the program itself is entitled to talk to this server.

It listens on the loopback and cannot be reached from the network — but "only
from this computer" is not the same as "only from this program". Any page open in
an ordinary browser lives on the same computer, and until now nothing stood in
its way: three dozen state-changing actions never asked who was asking. One form
on somebody else's site was enough to redirect the summaries to a stranger's
Telegram, replace the path to the model program, or wipe all the data at once.

The Host check closes DNS rebinding: a name that led to somebody else's server a
minute ago and now leads to 127.0.0.1 makes a foreign script "ours" as far as the
browser is concerned — and then it can not only send but read.
"""
import pytest

pytest.importorskip("httpx", reason="TestClient needs httpx")

from fastapi.testclient import TestClient  # noqa: E402

import app as app_module  # noqa: E402


@pytest.fixture
def client(profile):
    with TestClient(app_module.app, base_url="http://127.0.0.1:8765") as c:
        c.cookies.set("profile", profile)
        yield c


# --- A foreign name -----------------------------------------------------------

@pytest.mark.parametrize("host", [
    "evil.example",
    "attacker.tld:8765",
    "rebind.example:8766",
    "ai-job-search.attacker.test",
])
def test_we_do_not_answer_to_a_foreign_name(client, host):
    """Exactly what DNS rebinding makes use of."""
    r = client.get("/simple", headers={"Host": host})
    assert r.status_code == 403, f"we answered a Host of {host}"


@pytest.mark.parametrize("host", [
    "127.0.0.1:8765", "127.0.0.1", "localhost:8765", "localhost", "[::1]:8765",
])
def test_we_answer_to_our_own_names(client, host):
    """The other side of it: the program's own window knocks on all of these."""
    r = client.get("/simple", headers={"Host": host}, follow_redirects=True)
    assert r.status_code == 200, f"we did not answer our own name: {host}"


def test_a_foreign_name_closes_the_static_files_too(client):
    """The check comes before everything else, not only before the pages."""
    assert client.get("/static/style.css", headers={"Host": "evil.example"}).status_code == 403


# --- A foreign origin ---------------------------------------------------------

ACTIONS = [
    ("/save", {"telegram_token": "stranger", "telegram_chat": "1"}),
    ("/app/reset", {}),
    ("/run", {}),
    ("/models/select", {"model": "haiku"}),
    ("/notify/telegram", {"bot_token": "stranger", "chat_id": "1"}),
    ("/set_theme", {"theme": "dark"}),
    ("/profile/delete", {"slug": "me"}),
    ("/app/update/install", {}),
]


@pytest.mark.parametrize("path,data", ACTIONS)
def test_a_form_from_another_site_does_not_go_through(client, path, data):
    r = client.post(path, data=data,
                    headers={"Origin": "https://evil.example",
                             "Referer": "https://evil.example/page"},
                    follow_redirects=False)
    assert r.status_code == 403, f"{path} ran at a stranger's request"


@pytest.mark.parametrize("path,data", ACTIONS)
def test_the_browser_names_the_foreign_origin_outright(client, path, data):
    """Sec-Fetch-Site is set by the browser; a page cannot forge it."""
    r = client.post(path, data=data, headers={"Sec-Fetch-Site": "cross-site"},
                    follow_redirects=False)
    assert r.status_code == 403, f"{path} ran with cross-site"


def test_our_own_actions_go_through(client):
    """The thing that must not break: the program's own forms."""
    for headers in ({"Origin": "http://127.0.0.1:8765", "Sec-Fetch-Site": "same-origin"},
                      {"Sec-Fetch-Site": "none"},          # the address was typed by hand
                      {}):                                  # the embedded browser says nothing
        r = client.post("/set_theme", data={"theme": "auto"},
                        headers=headers, follow_redirects=False)
        assert r.status_code == 303, f"our own form was refused with {headers}"


def test_reading_is_not_forbidden_when_the_name_is_ours(client):
    """The restriction is on actions only: an ordinary link followed from inside
    the program arrives with no Origin, and there is nothing to stop."""
    assert client.get("/simple", headers={"Sec-Fetch-Site": "cross-site"},
                      follow_redirects=True).status_code == 200


def test_dns_rebinding_does_not_let_them_read(client):
    """The two tricks joined: once the name is re-resolved, the attacker's page
    becomes "ours" as far as the browser goes — but not as far as we go."""
    r = client.get("/results", headers={"Host": "evil.example",
                                        "Origin": "http://evil.example",
                                        "Sec-Fetch-Site": "same-origin"})
    assert r.status_code == 403
