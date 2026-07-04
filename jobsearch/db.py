"""SQLite: виденные вакансии и история прогонов."""
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timezone

from .config import DATA_DIR

DB_PATH = DATA_DIR / "jobs.db"

SCHEMA = """
CREATE TABLE IF NOT EXISTS jobs (
    id INTEGER PRIMARY KEY,
    key TEXT UNIQUE,
    title TEXT, company TEXT, location TEXT, url TEXT,
    source TEXT, is_direct INTEGER DEFAULT 0, is_agency INTEGER DEFAULT 0,
    description TEXT,
    score INTEGER, reason TEXT, advice TEXT,
    first_seen TEXT, run_id INTEGER
);
CREATE TABLE IF NOT EXISTS runs (
    id INTEGER PRIMARY KEY,
    started TEXT, finished TEXT,
    found INTEGER DEFAULT 0, fresh INTEGER DEFAULT 0, matched INTEGER DEFAULT 0,
    status TEXT DEFAULT 'running', log TEXT DEFAULT ''
);
"""


def now() -> str:
    return datetime.now(timezone.utc).astimezone().strftime("%Y-%m-%d %H:%M:%S")


@contextmanager
def conn():
    DATA_DIR.mkdir(exist_ok=True)
    c = sqlite3.connect(DB_PATH, timeout=30)
    c.row_factory = sqlite3.Row
    try:
        yield c
        c.commit()
    finally:
        c.close()


def init() -> None:
    with conn() as c:
        c.executescript(SCHEMA)
        try:
            c.execute("ALTER TABLE runs ADD COLUMN coverage TEXT")
        except sqlite3.OperationalError:
            pass  # колонка уже есть


def seen_keys() -> set:
    with conn() as c:
        return {r["key"] for r in c.execute("SELECT key FROM jobs")}


def start_run() -> int:
    with conn() as c:
        cur = c.execute("INSERT INTO runs(started) VALUES (?)", (now(),))
        return cur.lastrowid


def finish_run(run_id: int, found: int, fresh: int, matched: int, status: str, log: str,
               coverage: str = "") -> None:
    with conn() as c:
        c.execute(
            "UPDATE runs SET finished=?, found=?, fresh=?, matched=?, status=?, log=?, coverage=? WHERE id=?",
            (now(), found, fresh, matched, status, log, coverage, run_id),
        )


def save_job(job: dict, run_id: int) -> None:
    with conn() as c:
        c.execute(
            """INSERT OR IGNORE INTO jobs
               (key, title, company, location, url, source, is_direct, is_agency,
                description, score, reason, advice, first_seen, run_id)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (
                job["key"], job.get("title", ""), job.get("company", ""),
                job.get("location", ""), job.get("url", ""), job.get("source", ""),
                1 if job.get("is_direct") else 0, 1 if job.get("is_agency") else 0,
                (job.get("description") or "")[:8000],
                job.get("score"), job.get("reason", ""), job.get("advice", ""),
                now(), run_id,
            ),
        )


def mark_seen(key: str, run_id: int, title: str = "", company: str = "") -> None:
    """Запоминаем вакансию без результата, чтобы не обрабатывать повторно."""
    with conn() as c:
        c.execute(
            "INSERT OR IGNORE INTO jobs(key, title, company, first_seen, run_id) VALUES (?,?,?,?,?)",
            (key, title, company, now(), run_id),
        )


def matched_jobs(limit: int = 300, min_score: int = 0) -> list:
    with conn() as c:
        rows = c.execute(
            """SELECT * FROM jobs WHERE score IS NOT NULL AND score >= ?
               ORDER BY run_id DESC, is_direct DESC, score DESC LIMIT ?""",
            (min_score, limit),
        ).fetchall()
        return [dict(r) for r in rows]


def recent_runs(limit: int = 10) -> list:
    with conn() as c:
        rows = c.execute("SELECT * FROM runs ORDER BY id DESC LIMIT ?", (limit,)).fetchall()
        return [dict(r) for r in rows]
