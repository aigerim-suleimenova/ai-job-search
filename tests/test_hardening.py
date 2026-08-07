"""Five findings from a security review, closed in one go.

Not one of them fixes a breakage — they all limit the damage when something else
has already failed to hold: foreign text seeped into a page, a response was read
as the wrong type, a link led outside, a file unpacked into gigabytes.

SSRF lives separately, in test_web.py: all the scaffolding around visiting other
people's sites is already there.
"""
import io
import os
import zipfile

import pytest

from conftest import client_for
from jobsearch import config


# --- Where we can be led ------------------------------------------------------
#
# Where to return a person after an action comes from the page, in a back field.
# It used to be checked only for "is this page still being shown".

FOREIGN = [
    "https://evil.example/here",
    "http://evil.example/",
    "//evil.example/",           # no scheme — the browser fills in its own
    "/\\evil.example/",          # the browser straightens a backslash into an ordinary one
    "/\\/evil.example/",
    "https:/evil.example/",
    "javascript:alert(1)",
    "",
]

OURS = [
    "/results",
    "/welcome?step=model",
    "/models#provider-ollama",
    "/results?min=70&sort=score",
    "/",
]


@pytest.mark.parametrize("address", FOREIGN)
def test_we_do_not_lead_outside(address):
    from app import _here
    assert _here(address) == "/", f"{address!r} went straight through"


@pytest.mark.parametrize("address", OURS)
def test_our_own_addresses_are_left_as_they_are(address):
    """The other side: shutting out foreign addresses must not cost us returns to our
    own pages carrying a query and an anchor."""
    from app import _here
    assert _here(address) == address


def test_the_fallback_address_is_honoured():
    from app import _here
    assert _here("https://evil.example", "/results") == "/results"


def test_it_does_not_lead_away_through_the_page_either(profile):
    """The same thing for real: the back field reaches the Location header."""
    import app as app_module
    response = client_for(app_module.app, profile).post(
        "/models/provider", data={"provider": "claude_cli", "back": "//evil.example/"},
        follow_redirects=False)
    where = response.headers["location"]
    assert "evil" not in where, f"we were led to {where}"


# --- What the page is allowed to do -------------------------------------------

def test_the_headers_are_on_every_page(profile):
    import app as app_module
    response = client_for(app_module.app, profile).get("/results")
    for name in ("Content-Security-Policy", "X-Content-Type-Options",
                "Referrer-Policy", "X-Frame-Options"):
        assert name in response.headers, f"no {name} header"


def test_the_page_cannot_call_out(profile):
    """The important one. The page knows the address of the mail password, the
    Telegram token and the model keys: a script can read them, but there is
    nowhere to send them."""
    import app as app_module
    csp = client_for(app_module.app, profile).get("/results").headers["Content-Security-Policy"]
    assert "connect-src 'self'" in csp
    assert "form-action 'self'" in csp
    assert "default-src 'self'" in csp
    assert "frame-ancestors 'none'" in csp


def test_the_headers_are_there_on_a_refusal_too():
    """A refusal is an answer too, and it is shown in the window as well."""
    import app as app_module
    from fastapi.testclient import TestClient
    response = TestClient(app_module.app, base_url="http://foreign-host").get("/results")
    assert response.status_code == 403
    assert "X-Content-Type-Options" in response.headers


def test_the_static_files_come_with_the_headers(profile):
    import app as app_module
    response = client_for(app_module.app, profile).get("/static/style.css")
    assert response.status_code == 200
    assert "Content-Security-Policy" in response.headers


# --- Only their owner reads the settings --------------------------------------

@pytest.mark.skipif(os.name == "nt",
                    reason="on Windows chmod does not do permissions, and pretending it does will not do")
def test_the_settings_are_closed_to_another_login(profile):
    """config.json holds the Telegram token, the mail password and the model keys."""
    cfg = config.load()
    cfg["telegram"]["bot_token"] = "123:secret"
    config.save(cfg)
    mode = config.config_path().stat().st_mode & 0o777
    assert mode == 0o600, f"mode {oct(mode)} — the file is readable by more than its owner"


def test_the_permissions_are_narrowed_on_every_save(monkeypatch, profile):
    """The same thing, but checked everywhere.

    The test above runs only where permissions are real — which is to say, not on
    the development machine. Here we pretend to be a system with permissions and
    watch what the program asks for: otherwise this protection would not be
    checked on Windows at all, and could be broken unnoticed.
    """
    asked = []
    monkeypatch.setattr(config.os, "name", "posix")
    monkeypatch.setattr(config.os, "chmod", lambda p, mode: asked.append(mode))

    config.save(config.load())

    assert asked == [0o600], f"we asked for {[oct(m) for m in asked]}"


def test_on_windows_we_do_not_pretend_about_permissions(monkeypatch, profile):
    """os.chmod on Windows can only do "read only" and does not deal with access
    control lists. Calling it there would be pretending we had closed
    something."""
    asked = []
    monkeypatch.setattr(config.os, "name", "nt")
    monkeypatch.setattr(config.os, "chmod", lambda p, mode: asked.append(mode))

    config.save(config.load())

    assert asked == []


def test_saving_does_not_fail_when_permissions_will_not_set(monkeypatch, profile):
    """A foreign filesystem may not do permissions. That is no reason to lose the
    settings."""
    def refuse(*a, **kw):
        raise OSError("permissions are not a thing here")

    monkeypatch.setattr(config.os, "chmod", refuse)
    cfg = config.load()
    cfg["search"]["threshold"] = 63
    config.save(cfg)
    assert config.load()["search"]["threshold"] == 63


# --- The file that unfolds into gigabytes -------------------------------------

def docx_of(size: int) -> bytes:
    """A .docx is a zip with XML inside. Repeating text compresses thousands of times."""
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("word/document.xml",
                       "<w:p><w:t>" + "А" * size + "</w:t></w:p>")
    return buf.getvalue()


def test_the_bomb_does_not_unfold(monkeypatch, profile):
    """A file of a few kilobytes unfolded into gigabytes and dragged the program into
    swap along with everything it was doing at the time. It arrives from the CV
    upload form — that is, from anyone who reached the window.

    The limit is lowered for the duration of the check: there is no sense
    assembling a real hundred megabytes in memory for this, and afterwards there
    would be nothing to tell a size refusal from an out-of-memory crash."""
    monkeypatch.setattr(config, "MAX_DOCX_XML", 50_000)
    bomb = docx_of(200_000)
    assert len(bomb) < 5_000, "inside the archive this should be small — otherwise it is not a bomb"

    with pytest.raises(config.CVError) as caught:
        config.save_cv("bomb.docx", bomb)
    assert caught.value.key == "cv_err_docx"


def test_a_forged_listing_does_not_bring_the_program_down(monkeypatch, profile):
    """Lying in the listing to slip past the limit is impossible: zipfile holds the
    unpacking to the declared bounds itself and catches a checksum mismatch.

    But it catches it with a BadZipFile exception, and unhandled that would have
    escaped from the CV upload form. We check the person gets a comprehensible
    refusal rather than a page with a traceback."""
    monkeypatch.setattr(config, "MAX_DOCX_XML", 50_000)
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("word/document.xml", "<w:t>" + "А" * 200_000 + "</w:t>")
    raw = buf.getvalue()
    # Change the declared size to a small one — the listing stops being true. We
    # take the number from zipfile rather than working it out in our heads: in
    # UTF-8 Cyrillic takes two bytes, and a figure guessed by eye would disagree
    # with what was written.
    with zipfile.ZipFile(io.BytesIO(raw)) as z:
        truthful = z.getinfo("word/document.xml").file_size.to_bytes(4, "little")
    lie = (1234).to_bytes(4, "little")
    assert raw.count(truthful) >= 1, "we did not find where the size is written"
    raw = raw.replace(truthful, lie)

    with pytest.raises(config.CVError) as caught:
        config.save_cv("lies.docx", raw)
    assert caught.value.key.startswith("cv_err_"), "an exception that is not ours escaped"


def test_an_ordinary_docx_is_read(profile):
    """The other side: the limit is generous on purpose, and a real CV does not run
    into it."""
    text = config.save_cv("cv.docx", docx_of(2000))
    assert "А" * 100 in text


# --- Somebody else's text on our page -----------------------------------------

def test_the_error_text_is_escaped(monkeypatch, profile):
    """What settles in the text of an exception is not written by us: the model's
    answer and pieces of the job description, and the description is written by
    anyone at all. Without escaping, a <script> from somebody's posting would run
    on our page, and our page walks the addresses of this program as one of its
    own."""
    import app as app_module
    from conftest import job as job_at
    from jobsearch import db, scoring

    db.init()
    run = db.start_run()
    db.save_job(job_at("nasty"), run)
    row = db.get_job_by_key("nasty") if hasattr(db, "get_job_by_key") else None
    job_id = row["id"] if row else 1
    config.cv_text_path().write_text("An ordinary CV. " * 30, encoding="utf-8")

    def blow_up(*a, **kw):
        raise ValueError("the model answered: <script>fetch('//elsewhere')</script>")

    monkeypatch.setattr(scoring, "generate_cv", blow_up)

    page = client_for(app_module.app, profile).get(f"/cv/{job_id}").text

    assert "<script>" not in page, "a foreign script reached the page as it was"
    assert "&lt;script&gt;" in page, "the error text vanished entirely — the person has nothing to read"


# --- The headers must not bolt the door from the inside -----------------------

def test_the_referrer_policy_does_not_null_the_origin(profile):
    """Exactly what burned us.

    With Referrer-Policy: no-referrer the browser sends "Origin: null" when a form
    is submitted, and the "did this come from our own page" check refused its own
    window: instead of a page the person saw "Not from this program.". The header
    had been set out of politeness to other sites, and it cost the whole program
    its working. same-origin gives exactly the same result outside — a foreign
    site learns nothing — and leaves Origin in place."""
    import app as app_module
    policy = client_for(app_module.app, profile).get("/results").headers["Referrer-Policy"]
    assert policy != "no-referrer", "this value nulls the Origin on forms"
    assert policy in ("same-origin", "strict-origin-when-cross-origin",
                        "strict-origin"), f"unknown policy: {policy}"


def test_our_own_window_is_recognised_by_sec_fetch_site(profile):
    """This is what a request from a real form in the program's window looks like.
    Sec-Fetch-Site is set by the browser and cannot be forged from a page — so it
    settles the matter without Origin, whatever Origin arrives as."""
    import app as app_module
    response = client_for(app_module.app, profile).post(
        "/models/provider", data={"provider": "claude_cli", "back": "/models"},
        headers={"Sec-Fetch-Site": "same-origin", "Origin": "null"},
        follow_redirects=False)
    assert response.status_code == 303, "the program refused its own window"


def test_a_foreign_page_still_does_not_get_through(profile):
    """The other side: Sec-Fetch-Site can be trusted only because it calls foreign
    things foreign."""
    import app as app_module
    for site in ("cross-site", "same-site"):
        response = client_for(app_module.app, profile).post(
            "/models/provider", data={"provider": "ollama"},
            headers={"Sec-Fetch-Site": site, "Origin": "http://127.0.0.1:8766"},
            follow_redirects=False)
        assert response.status_code == 403, f"{site} went straight through"
