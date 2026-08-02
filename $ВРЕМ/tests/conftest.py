"""The shared scaffolding for the tests.

The data directory is swapped BEFORE jobsearch is imported: profiles.DATA_ROOT is
read at import time, and changing the environment variable after that is too late.
"""
import os
import shutil
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

_DATA = Path(tempfile.mkdtemp(prefix="aijs-tests-"))
os.environ["AIJS_DATA_DIR"] = str(_DATA)

import pytest  # noqa: E402

from jobsearch import config, db, profiles  # noqa: E402


def pytest_sessionfinish(session, exitstatus):
    shutil.rmtree(_DATA, ignore_errors=True)


@pytest.fixture(autouse=True)
def настроено(monkeypatch):
    """The app as it is once somebody has set it up: a provider that answers.

    The pages are shown only when there is a model to think with — without this
    every page test would be quietly sent to the introduction instead. The
    answers would still be 200 and the checks would still pass, having looked at
    the introduction eight times and at the pages not once.

    Tests about the introduction itself override this, and the ones about
    erasing everything take the mark away on purpose.
    """
    from jobsearch import appstate, autostart, providers, update
    monkeypatch.setattr(providers, "_bin_exists", lambda name: True)
    monkeypatch.setattr(providers, "ollama_running", lambda: True)
    monkeypatch.setattr(providers, "ollama_installed_models",
                        lambda: {m["id"] for m in providers.LOCAL_MODELS})
    # Start-at-login lives outside the data directory — in the registry on
    # Windows, in LaunchAgents on a Mac. A test that erases everything turns it
    # off for real, and it would be the developer's own machine it turned it off
    # on. Nothing here may reach that far.
    monkeypatch.setattr(autostart, "set_enabled", lambda on: "")
    # Asking GitHub whether a new version is out is a real call to the internet,
    # made on every startup. A test suite has no business reaching outside the
    # machine: it would be slow, it would fail on a train, and it would tell
    # somebody else's server how often the tests run.
    monkeypatch.setattr(update, "fetch_latest", lambda: {})
    monkeypatch.setattr(update, "check_in_background", lambda: None)
    appstate.mark_setup_done(config.load()["llm"])


@pytest.fixture
def profile(tmp_path_factory):
    """A clean profile with its own database for every test."""
    slug = profiles.create("Тест " + tmp_path_factory.mktemp("p").name)
    profiles.set_active(slug)
    db.init()
    yield slug


@pytest.fixture
def cfg(profile):
    return config.load()


BASE_URL = "http://127.0.0.1:8765"


def client_for(app, profile: str = ""):
    """TestClient, стучащийся по тому адресу, по которому программа и живёт.

    По умолчанию он представляется как «testserver», а сервер теперь отзывается
    только на свои имена — иначе подмену DNS не остановить. Настоящее окно
    программы всегда приходит на 127.0.0.1, и тесты должны приходить так же:
    иначе они проверяли бы не то приложение, которое видит человек.
    """
    from fastapi.testclient import TestClient
    c = TestClient(app, base_url=BASE_URL)
    if profile:
        c.cookies.set("profile", profile)
    return c


def job(key: str, **over) -> dict:
    """A job with sensible fields — we override only what the test is about."""
    base = dict(key=key, title="Senior Frontend Engineer", company="Northwind",
                location="Berlin", url=f"https://example.com/{key}", source="greenhouse",
                is_direct=1, is_agency=0, description="React, TypeScript",
                score=60, reason="триажная оценка", advice="", verified=False,
                posted_at="2026-07-20")
    base.update(over)
    return base
