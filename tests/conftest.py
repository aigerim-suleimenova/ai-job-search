"""Общая обвязка тестов.

Каталог данных подменяется ДО импорта jobsearch: profiles.DATA_ROOT читается
на импорте модуля, и после него переменную окружения менять уже поздно.
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


@pytest.fixture
def profile(tmp_path_factory):
    """Чистый профиль со своей базой на каждый тест."""
    slug = profiles.create("Тест " + tmp_path_factory.mktemp("p").name)
    profiles.set_active(slug)
    db.init()
    yield slug


@pytest.fixture
def cfg(profile):
    return config.load()


def job(key: str, **over) -> dict:
    """Вакансия с разумными полями — переопределяем только то, что проверяем."""
    base = dict(key=key, title="Senior Frontend Engineer", company="Northwind",
                location="Berlin", url=f"https://example.com/{key}", source="greenhouse",
                is_direct=1, is_agency=0, description="React, TypeScript",
                score=60, reason="триажная оценка", advice="", verified=False,
                posted_at="2026-07-20")
    base.update(over)
    return base
