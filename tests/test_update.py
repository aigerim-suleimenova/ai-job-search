"""Updating: learning about a new version and installing it.

Where the file comes from deserves attention of its own. GitHub sends a list of
files along with addresses, but an address in a response is only a suggestion: a
response can be tampered with on the way, and what is downloaded here will be
run. So the address is checked before downloading, and checked in the very place
that reaches the network — not only where it was chosen.
"""
import hashlib
import os
import platform
import subprocess
import time

import pytest

from jobsearch import update, version


# --- What counts as newer -----------------------------------------------------

@pytest.mark.parametrize("newer,ours,expected", [
    ("0.8.17", "0.8.16", True),
    ("0.9.0", "0.8.16", True),
    ("0.8.16", "0.8.16", False),
    ("0.8.15", "0.8.16", False),
    ("0.8.9", "0.8.10", False),          # by numbers, not alphabetically
    ("0.10.0", "0.9.9", True),
])
def test_comparing_versions(newer, ours, expected):
    assert update.is_newer(newer, ours) is expected


def test_running_from_source_is_offered_no_update():
    """From source the program calls itself "dev" and is behind nothing: what runs
    there is what the developer checked out."""
    assert update.is_newer("9.9.9", version.FALLBACK) is False
    assert update.is_newer("", "0.8.16") is False


# --- Where the file comes from ------------------------------------------------

def foreign_asset(url):
    return {"name": "AI Job Search Setup.exe", "url": url, "size": 1}


@pytest.mark.parametrize("address", [
    "https://example.com/evil.exe",
    "http://github.com/mrWD/ai-job-search/releases/download/v1/x.exe",   # not https
    "https://github.com/someone-else/ai-job-search/releases/download/v1/x.exe",
    "https://github.com.evil.test/mrWD/ai-job-search/releases/download/v1/x.exe",
    "https://raw.githubusercontent.com/mrWD/ai-job-search/main/x.exe",
    "",
    # A bypass that worked in the first version: the prefix comparison passed,
    # while requests collapsed the ".." only after the check and went off to
    # somebody else's repository. What must be checked is the address the network
    # will see.
    update.DOWNLOAD_PREFIX + "../../../../evilcorp/evil/releases/download/v1/x.exe",
    update.DOWNLOAD_PREFIX + "%2e%2e/%2e%2e/%2e%2e/evil/x/releases/download/v1/x.exe",
    update.DOWNLOAD_PREFIX,                       # a directory with no file
])
def test_we_download_only_from_our_own_releases(address):
    """A tampered response may name the file, but not where to take it from."""
    with pytest.raises(update.UpdateError) as caught:
        update.download(foreign_asset(address))
    assert caught.value.key == "update_err_bad_url"


def test_the_address_check_agrees_with_where_the_request_will_go():
    """The point of the check is to agree with what the network does, not with a
    guess about it. Disagreement is allowed in one direction only — refusing more
    than needed."""
    import requests as _r
    for url in (update.DOWNLOAD_PREFIX + "v1/setup.exe",
                update.DOWNLOAD_PREFIX + "../../../evil/x/releases/download/v1/x.exe",
                "https://evil.example/x.exe",
                "https://github.com.evil.test/x.exe"):
        we_allow = update._is_our_download(url)
        really_ours = _r.Request("GET", url).prepare().url.startswith(update.DOWNLOAD_PREFIX)
        assert not (we_allow and not really_ours), \
            f"we let through an address that goes elsewhere: {url}"


def test_our_own_address_passes_the_check(monkeypatch, tmp_path):
    """The other side: a genuine address must not be refused."""
    own = update.DOWNLOAD_PREFIX + "v0.8.17/AI%20Job%20Search%20Setup.exe"
    attempts = []

    class Answer:
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
                        lambda url, **kw: attempts.append(url) or Answer())

    path = update.download({"name": "AI Job Search Setup.exe", "url": own, "size": 4})

    assert attempts == [own], "we went to the wrong address"
    assert path.read_bytes() == b"data"


def test_a_filename_from_the_response_does_not_lead_out_of_the_folder(monkeypatch):
    """The name comes from outside too. A filename on disk is made from it, and a
    ".." in it must not lead the write anywhere else."""
    class Answer:
        headers = {}

        def raise_for_status(self):
            pass

        def iter_content(self, chunk_size=0):
            yield b"x"

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

    monkeypatch.setattr(update.requests, "get", lambda url, **kw: Answer())
    asset = {"name": "../../../../Windows/System32/evil.exe",
             "url": update.DOWNLOAD_PREFIX + "v1/x.exe"}

    path = update.download(asset)

    assert ".." not in path.name, f"the name led out of the folder: {path}"
    assert path.name == "evil.exe", f"something odd is left of the name: {path.name}"
    # the main thing: the file landed where we sent it, not where the name pointed
    assert path.resolve().parent == path.parent.resolve()
    assert path.parent.name.startswith("aijs-update-")


# --- What was downloaded is checked against what was promised -----------------

def fake_network(monkeypatch, payload: bytes):
    class Answer:
        headers = {"Content-Length": str(len(payload))}

        def raise_for_status(self):
            pass

        def iter_content(self, chunk_size=0):
            yield payload

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

    monkeypatch.setattr(update.requests, "get", lambda url, **kw: Answer())


def own(**fields):
    base = {"name": "setup.exe", "url": update.DOWNLOAD_PREFIX + "v1/setup.exe"}
    base.update(fields)
    return base


def test_a_whole_file_is_accepted(monkeypatch):
    payload = b"installer bytes"
    fake_network(monkeypatch, payload)
    asset = own(size=len(payload), digest="sha256:" + hashlib.sha256(payload).hexdigest())

    path = update.download(asset)

    assert path.read_bytes() == payload


def test_a_half_downloaded_file_is_not_run(monkeypatch):
    """A dropped connection left a stub, and it was handed to the installer as it was."""
    fake_network(monkeypatch, b"half")
    asset = own(size=999999)

    with pytest.raises(update.UpdateError) as caught:
        update.download(asset)

    assert caught.value.key == "update_err_broken"


def test_tampered_contents_are_not_run(monkeypatch):
    """The size matches and the bytes do not: the address leads to a storage we were
    redirected to, and it is the bytes that must be trusted, not it."""
    payload = b"NOT the installer"
    fake_network(monkeypatch, payload)
    asset = own(size=len(payload),
                 digest="sha256:" + hashlib.sha256(b"the real installer").hexdigest())

    with pytest.raises(update.UpdateError) as caught:
        update.download(asset)

    assert caught.value.key == "update_err_broken"


def test_a_bad_file_is_wiped_rather_than_left_lying_about(monkeypatch):
    fake_network(monkeypatch, b"stub")
    asset = own(size=999999)
    made_dirs = []
    real_mkdtemp = update.tempfile.mkdtemp
    monkeypatch.setattr(update.tempfile, "mkdtemp",
                        lambda **kw: made_dirs.append(real_mkdtemp(**kw)) or made_dirs[-1])

    with pytest.raises(update.UpdateError):
        update.download(asset)

    from pathlib import Path
    leftovers = list(Path(made_dirs[0]).glob("*"))
    assert leftovers == [], f"the bad file is still on disk: {leftovers}"


def test_an_endless_response_will_not_fill_the_disk(monkeypatch):
    class Endless:
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

    monkeypatch.setattr(update.requests, "get", lambda url, **kw: Endless())
    monkeypatch.setattr(update, "MAX_DOWNLOAD", 4 * 1024 * 1024)

    with pytest.raises(update.UpdateError) as caught:
        update.download(own())

    assert caught.value.key == "update_err_broken"


# --- Choosing the file for this system ----------------------------------------

def test_on_windows_the_installer_is_taken(monkeypatch):
    monkeypatch.setattr(update.platform, "system", lambda: "Windows")
    assets = [
        {"name": "AI Job Search.dmg", "browser_download_url": update.DOWNLOAD_PREFIX + "v1/a.dmg"},
        {"name": "AI Job Search Setup.exe", "browser_download_url": update.DOWNLOAD_PREFIX + "v1/s.exe"},
    ]
    assert update._asset_for_this_os(assets)["name"] == "AI Job Search Setup.exe"


def test_an_installer_from_a_foreign_address_is_not_offered(monkeypatch):
    monkeypatch.setattr(update.platform, "system", lambda: "Windows")
    assets = [{"name": "AI Job Search Setup.exe",
               "browser_download_url": "https://example.com/setup.exe"}]
    assert update._asset_for_this_os(assets) == {}


def test_on_macos_the_disk_image_is_taken(monkeypatch):
    """There was no "Update" button on macOS at all — it said "install by hand",
    even though a ready bundle sits inside the image."""
    monkeypatch.setattr(update.platform, "system", lambda: "Darwin")
    assets = [
        {"name": "AI Job Search Setup.exe", "browser_download_url": update.DOWNLOAD_PREFIX + "v1/s.exe"},
        {"name": "AI Job Search.dmg", "browser_download_url": update.DOWNLOAD_PREFIX + "v1/a.dmg"},
    ]
    assert update._asset_for_this_os(assets)["name"] == "AI Job Search.dmg"


def test_an_image_from_a_foreign_address_is_not_offered(monkeypatch):
    monkeypatch.setattr(update.platform, "system", lambda: "Darwin")
    assets = [{"name": "AI Job Search.dmg",
               "browser_download_url": "https://example.com/a.dmg"}]
    assert update._asset_for_this_os(assets) == {}


def test_where_it_is_installed_by_hand_nothing_is_offered(monkeypatch):
    """Linux stays behind the release page: there it is an archive the person
    unpacked wherever they wanted, and guessing that place means guessing wrong
    one day."""
    monkeypatch.setattr(update.platform, "system", lambda: "Linux")
    assets = [{"name": "AI Job Search-linux.tar.gz",
               "browser_download_url": update.DOWNLOAD_PREFIX + "v1/l.tar.gz"}]
    assert update._asset_for_this_os(assets) == {}


# --- GitHub's answer ----------------------------------------------------------

def test_a_draft_and_a_prerelease_are_not_offered(monkeypatch):
    for field in ("draft", "prerelease"):
        monkeypatch.setattr(update.requests, "get",
                            lambda *a, **kw: _response({"tag_name": "v9.9.9", field: True}))
        assert update.fetch_latest() == {}


def test_an_unreachable_github_does_not_break_the_program(monkeypatch):
    def blow_up(*a, **kw):
        raise update.requests.RequestException("no network")

    monkeypatch.setattr(update.requests, "get", blow_up)
    assert update.fetch_latest() == {}


def _response(payload):
    class R:
        def raise_for_status(self):
            pass

        def json(self):
            return payload

    return R()


# --- The update note outlives the update itself -------------------------------

def test_after_installing_we_do_not_offer_the_same_version(monkeypatch):
    """This is exactly what was on the screen: "Version 0.8.20 is out — you have
    0.8.20", with an "Update" button beside it.

    The program found a new version and wrote it down. The person installed it —
    and the note outlived the install, because the data lives separately from the
    program, and the next question to GitHub is a day away. The page read the note
    as it was.
    """
    monkeypatch.setattr(update.appstate, "load", lambda: {"update": {
        "checked_at": 1.0, "version": "0.8.20",
        "notes_url": "https://example/tag/v0.8.20",
        "asset": {"name": "setup.exe", "url": update.DOWNLOAD_PREFIX + "v0.8.20/setup.exe"}}})
    monkeypatch.setattr(update.version, "current", lambda: "0.8.20")

    now = update.state()

    assert now["version"] == "", "we offered an update to the version already installed"
    assert not now["asset"], "the Update button would have stayed on the screen"


def test_a_genuine_update_is_still_visible(monkeypatch):
    """The other side: silencing everything would be easy and wrong."""
    monkeypatch.setattr(update.appstate, "load", lambda: {"update": {
        "checked_at": 1.0, "version": "0.9.0", "notes_url": "u",
        "asset": {"name": "setup.exe", "url": update.DOWNLOAD_PREFIX + "v0.9.0/setup.exe"}}})
    monkeypatch.setattr(update.version, "current", lambda: "0.8.20")

    now = update.state()

    assert now["version"] == "0.9.0"
    assert now["asset"]["name"] == "setup.exe"


def test_a_note_from_an_older_version_is_not_shown_either(monkeypatch):
    """Rolling back to an earlier build must not invite an "update" backwards."""
    monkeypatch.setattr(update.appstate, "load", lambda: {"update": {
        "checked_at": 1.0, "version": "0.8.15", "notes_url": "u", "asset": {"url": "x"}}})
    monkeypatch.setattr(update.version, "current", lambda: "0.8.20")

    assert update.state()["version"] == ""


# --- When a middleman inspects the connections --------------------------------

def test_when_connections_are_intercepted_we_move_to_the_system_store(monkeypatch):
    """Viktor hit this twice in one day. First all nine job sources returned zero and
    the run reported success. Then the program stopped noticing new versions: he
    sat on 0.8.28 while 0.8.29 and 0.8.30 came out.

    One cause: the antivirus on the machine opens the secure connection itself,
    reads it and re-signs it with its own root. That root sits in the system trust
    store — otherwise the browser would not work — but requests does not use the
    system store, it carries its own bundle. The check silently came back
    empty."""
    import requests

    from jobsearch import net
    net.forget()
    went_via = []

    class Answer:
        status_code = 200

        @staticmethod
        def json():
            return {"ok": True}

    def ordinary(url, **kw):
        went_via.append("own bundle")
        raise requests.exceptions.SSLError("certificate verify failed")

    class Session:
        def get(self, url, **kw):
            went_via.append("system store")
            return Answer()

    monkeypatch.setattr(net.requests, "get", ordinary)
    monkeypatch.setattr(net, "_system_session", lambda: Session())

    assert net.get("https://api.github.com/x").json() == {"ok": True}
    assert went_via == ["own bundle", "system store"]
    assert net.using_system_store() is True

    # and from here we go straight there: no second attempt on every request
    went_via.clear()
    net.get("https://api.github.com/y")
    assert went_via == ["system store"]
    net.forget()


def test_with_no_interception_nothing_changes(monkeypatch):
    """For anyone whose connections are not inspected, everything is as it was."""
    from jobsearch import net
    net.forget()
    called = []

    monkeypatch.setattr(net.requests, "get",
                        lambda url, **kw: called.append(url) or "answered")
    monkeypatch.setattr(net, "_system_session",
                        lambda: (_ for _ in ()).throw(AssertionError("no business reaching for the store")))

    assert net.get("https://example.com") == "answered"
    assert net.using_system_store() is False
    net.forget()


def test_other_network_troubles_are_not_blamed_on_the_store(monkeypatch):
    """No connection is no connection, not a foreign root. Silently swapping one for
    the other would hide the real cause."""
    import requests

    from jobsearch import net
    net.forget()
    monkeypatch.setattr(net.requests, "get",
                        lambda url, **kw: (_ for _ in ()).throw(
                            requests.exceptions.ConnectionError("no connection")))

    with pytest.raises(requests.exceptions.ConnectionError):
        net.get("https://example.com")
    assert net.using_system_store() is False
    net.forget()


# --- When we ask about a new version ------------------------------------------
#
# We take the real function before the shared fixture stubs it out: conftest gags
# the update check in every test so they do not reach the network.
from jobsearch.update import check_in_background as _real_check

def test_we_ask_not_only_at_startup(monkeypatch, profile):
    """The check ran exactly once, at startup. Viktor opened the program before
    0.8.32 came out, it found nothing — and never asked again. And there is no
    reason to close it: it has a background mode and a schedule, it is meant to
    stand open."""
    from jobsearch import appstate, update
    started = []
    monkeypatch.setattr(update.threading, "Thread",
                        lambda **kw: type("Thread", (), {"start": lambda s: started.append(1)})())

    # the last time was long ago
    st = appstate.load()
    st["update"] = {"checked_at": time.time() - update.CHECK_EVERY - 60, "version": ""}
    appstate.save(st)
    assert update.due() is True
    _real_check()
    assert started == [1], "the interval elapsed and we never went to ask"


def test_we_do_not_pester_more_often_than_the_interval(monkeypatch, profile):
    """Otherwise every page draw would get a thread of its own."""
    from jobsearch import appstate, update
    started = []
    monkeypatch.setattr(update.threading, "Thread",
                        lambda **kw: type("Thread", (), {"start": lambda s: started.append(1)})())

    st = appstate.load()
    st["update"] = {"checked_at": time.time() - 60, "version": ""}
    appstate.save(st)
    assert update.due() is False
    _real_check()
    assert started == [], "we asked before the interval was up"


def test_a_page_starts_the_check(monkeypatch, profile):
    """Exactly the place where this should happen: a person opened a page."""
    import app as app_module
    from conftest import client_for
    from jobsearch import update
    called = []
    monkeypatch.setattr(app_module.update_mod, "check_in_background",
                        lambda: called.append(1))

    client_for(app_module.app, profile).get("/results")

    assert called, "the page was drawn and we never went to ask"


# --- Installing on macOS ------------------------------------------------------
#
# There was no "Update" button here: the program said "install by hand". Yet a
# ready .app bundle sits inside the image, and swapping it in for the old one is
# exactly what a person does by hand, dragging the bundle into Applications.

def test_from_source_there_is_nothing_to_install(monkeypatch, tmp_path):
    """There is no .app bundle — so the program is running on a developer's machine."""
    monkeypatch.setattr(update.platform, "system", lambda: "Darwin")
    monkeypatch.setattr(update.removal, "program_path", lambda: "")
    with pytest.raises(update.UpdateError) as e:
        update.install(tmp_path / "a.dmg")
    assert e.value.key == "update_err_manual"


def test_without_write_permission_we_do_not_close_the_program(monkeypatch, tmp_path):
    """We check the permission BEFORE closing the program. Otherwise it would simply
    vanish from the screen and the update would silently fail to happen — leaving
    the person with nothing."""
    bundle = tmp_path / "AI Job Search.app"
    bundle.mkdir()
    monkeypatch.setattr(update.platform, "system", lambda: "Darwin")
    monkeypatch.setattr(update.removal, "program_path", lambda: str(bundle))
    monkeypatch.setattr(update.os, "access", lambda p, m: False)
    launched = []
    monkeypatch.setattr(update.subprocess, "Popen", lambda *a, **k: launched.append(a))

    with pytest.raises(update.UpdateError) as e:
        update.install(tmp_path / "a.dmg")
    assert e.value.key == "update_err_readonly"
    assert e.value.fmt["where"] == str(bundle)
    assert launched == [], "the program would have gone off to close for nothing"


def test_on_linux_it_is_still_by_hand(monkeypatch, tmp_path):
    monkeypatch.setattr(update.platform, "system", lambda: "Linux")
    with pytest.raises(update.UpdateError) as e:
        update.install(tmp_path / "a.tar.gz")
    assert e.value.key == "update_err_manual"


# --- The same swap, but on a real macOS ---------------------------------------
#
# Everything above checks our branches with stubs. The bundle is swapped in by an
# sh script: hdiutil, ditto and waiting on somebody else's process live there, and
# none of that can be checked with a stub. So here it is for real: we build the
# image, put an "installed" copy in place, run the script and look at what
# happened. In CI this runs on macos-latest (see .github/workflows/build.yml).

mac_only = pytest.mark.skipif(platform.system() != "Darwin",
                                    reason="swapping in a bundle happens only on macOS")


def _image_with(tmp_path, contents: str, bundle_name: str = "AI Job Search.app"):
    """Builds a .dmg with the bundle inside — like the one put in a release."""
    staging = tmp_path / "staging"
    inside = staging / bundle_name / "Contents" / "MacOS"
    inside.mkdir(parents=True)
    (inside / "AI Job Search").write_text(contents)
    (inside.parent / "Info.plist").write_text("<plist/>")
    beside = tmp_path / "downloaded"          # the script removes the folder entirely
    beside.mkdir()
    image = beside / "AI Job Search.dmg"
    subprocess.run(["hdiutil", "create", "-volname", "AI Job Search",
                    "-srcfolder", str(staging), "-ov", "-format", "UDZO", str(image)],
                   check=True, capture_output=True)
    return image


def _installed_copy(tmp_path, contents: str):
    bundle = tmp_path / "Applications" / "AI Job Search.app"
    (bundle / "Contents" / "MacOS").mkdir(parents=True)
    (bundle / "Contents" / "MacOS" / "AI Job Search").write_text(contents)
    return bundle


def _run_script(tmp_path, image, bundle, extra_stubs: dict = None):
    """Runs the script in place of the program, with stubs slipped onto its PATH.

    open is always stubbed: the real one would open a window inside CI.
    """
    own_cmds = tmp_path / "own-commands"
    own_cmds.mkdir(exist_ok=True)
    (own_cmds / "open").write_text(f'#!/bin/sh\necho "$@" >> "{tmp_path}/opened"\n')
    for name, body in (extra_stubs or {}).items():
        (own_cmds / name).write_text(body)
    for f in own_cmds.iterdir():
        f.chmod(0o755)

    # Somebody else's process in place of our program: the script waits for it to go.
    stand_in_process = subprocess.Popen(["sleep", "1"])
    script = tmp_path / "swap.sh"
    script.write_text(update._MAC_SWAP, encoding="utf-8")
    script.chmod(0o755)
    proc = subprocess.Popen(
        ["/bin/sh", str(script), str(image), str(bundle), str(stand_in_process.pid)],
        env={**os.environ, "PATH": f"{own_cmds}:{os.environ['PATH']}"},
        stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    # Wait for it and reap it. Without this the process stays unreaped and answers
    # kill -0 with "alive" — the script honestly waited the full twenty seconds and
    # left with nothing, while the test reported "the bundle was not swapped in"
    # for no visible reason.
    stand_in_process.wait()
    out, err = proc.communicate(timeout=180)
    return subprocess.CompletedProcess(proc.args, proc.returncode, out, err)


@mac_only
def test_the_bundle_is_swapped_in(tmp_path):
    image = _image_with(tmp_path, "new\n")
    bundle = _installed_copy(tmp_path, "old\n")

    r = _run_script(tmp_path, image, bundle)

    assert (bundle / "Contents" / "MacOS" / "AI Job Search").read_text() == "new\n", \
        f"the script exited with {r.returncode}: {r.stderr}"
    assert "AI Job Search.app" in (tmp_path / "opened").read_text(), \
        "swapped in and never opened — the person is left with no program"
    assert not image.parent.exists(), "the download is still lying about"
    assert not (tmp_path / "Applications" / ".AI Job Search.app.old").exists()


@mac_only
def test_if_the_copy_breaks_off_the_old_one_comes_back(tmp_path):
    """The most dangerous moment: the old copy is already moved aside and the new one
    is not down yet. Break off here and the person is left with half a bundle."""
    image = _image_with(tmp_path, "new\n")
    bundle = _installed_copy(tmp_path, "old\n")

    _run_script(tmp_path, image, bundle,
              {"ditto": f'#!/bin/sh\necho broke-off >> "{tmp_path}/copied"\nexit 1\n'})

    # Otherwise the test would pass even when the script never reached the swap:
    # "the old one is in place" is true in that case too, and that is not what we
    # are checking.
    assert (tmp_path / "copied").exists(), "it never got as far as the swap"
    assert bundle.is_dir(), "the program vanished entirely"
    assert (bundle / "Contents" / "MacOS" / "AI Job Search").read_text() == "old\n"


@mac_only
def test_an_image_with_no_bundle_touches_nothing(tmp_path):
    staging = tmp_path / "empty"
    staging.mkdir()
    (staging / "readme.txt").write_text("no bundle here")
    beside = tmp_path / "downloaded"
    beside.mkdir()
    image = beside / "AI Job Search.dmg"
    subprocess.run(["hdiutil", "create", "-volname", "AI Job Search", "-srcfolder", str(staging),
                    "-ov", "-format", "UDZO", str(image)], check=True, capture_output=True)
    bundle = _installed_copy(tmp_path, "old\n")

    _run_script(tmp_path, image, bundle)

    assert (bundle / "Contents" / "MacOS" / "AI Job Search").read_text() == "old\n"


@mac_only
def test_while_the_program_is_alive_we_change_nothing(tmp_path):
    """Swapping a running program out from under itself is worse than not updating."""
    image = _image_with(tmp_path, "new\n")
    bundle = _installed_copy(tmp_path, "old\n")
    long_lived = subprocess.Popen(["sleep", "120"])
    script = tmp_path / "swap.sh"
    script.write_text(update._MAC_SWAP, encoding="utf-8")
    script.chmod(0o755)
    try:
        r = subprocess.run(["/bin/sh", str(script), str(image), str(bundle), str(long_lived.pid)],
                           capture_output=True, text=True, timeout=180)
        assert r.returncode != 0, "we did not wait it out, yet went ahead and swapped"
        assert (bundle / "Contents" / "MacOS" / "AI Job Search").read_text() == "old\n"
    finally:
        long_lived.kill()
