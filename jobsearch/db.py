"""SQLite: the jobs already seen and the history of runs (a database per profile)."""
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timezone

from . import profiles


def db_path():
    return profiles.dir() / "jobs.db"

SCHEMA = """
CREATE TABLE IF NOT EXISTS jobs (
    id INTEGER PRIMARY KEY,
    key TEXT UNIQUE,
    title TEXT, company TEXT, location TEXT, url TEXT,
    source TEXT, is_direct INTEGER DEFAULT 0, is_agency INTEGER DEFAULT 0,
    description TEXT,
    score INTEGER, reason TEXT, advice TEXT, verified INTEGER DEFAULT 0,
    first_seen TEXT, run_id INTEGER
);
CREATE TABLE IF NOT EXISTS runs (
    id INTEGER PRIMARY KEY,
    started TEXT, finished TEXT,
    found INTEGER DEFAULT 0, fresh INTEGER DEFAULT 0, matched INTEGER DEFAULT 0,
    status TEXT DEFAULT 'running', log TEXT DEFAULT ''
);
"""


_initialized = set()


def now() -> str:
    return datetime.now(timezone.utc).astimezone().strftime("%Y-%m-%d %H:%M:%S")


@contextmanager
def conn():
    path = db_path()
    c = sqlite3.connect(path, timeout=30)
    c.row_factory = sqlite3.Row
    try:
        if str(path) not in _initialized:
            c.executescript(SCHEMA)
            for alter in ("ALTER TABLE runs ADD COLUMN coverage TEXT",
                          "ALTER TABLE jobs ADD COLUMN verified INTEGER DEFAULT 0",
                          "ALTER TABLE jobs ADD COLUMN tailored_cv TEXT",
                          "ALTER TABLE jobs ADD COLUMN posted_at TEXT",
                          "ALTER TABLE jobs ADD COLUMN viewed INTEGER DEFAULT 0",
                          # The occupation from the Europe-wide taxonomy: it
                          # reached the model, but afterwards there was no way to
                          # look at it — and no way to show it on the card.
                          "ALTER TABLE jobs ADD COLUMN occupation TEXT",
                          # What the posting itself says about the right to work.
                          "ALTER TABLE jobs ADD COLUMN visa TEXT"):
                try:
                    c.execute(alter)
                except sqlite3.OperationalError:
                    pass  # the column is already there
            _initialized.add(str(path))
        yield c
        c.commit()
    finally:
        c.close()


def init() -> None:
    with conn():  # the schema is created lazily in conn() on first touch of the profile database
        pass


def forget_databases() -> None:
    """Forget which databases have a schema — they have just been erased.

    Without this the next connection to a path that has been wiped would be
    trusted as already prepared, and the first query would meet no tables.
    """
    _initialized.clear()


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
        # This used to be INSERT OR IGNORE: a row was written once and never
        # changed again. While jobs were saved only at the end, that worked. As
        # soon as they began to be saved during triage, the deep analysis of the
        # same job stopped reaching the database — the insert was silently
        # ignored. We update what has become known more precisely and leave the
        # "seen" mark and the date of first meeting alone.
        c.execute(
            """INSERT INTO jobs
               (key, title, company, location, url, source, is_direct, is_agency,
                description, score, reason, advice, verified, posted_at, first_seen, run_id,
                occupation, visa)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
               ON CONFLICT(key) DO UPDATE SET
                 score    = CASE WHEN excluded.verified = 1 OR jobs.score IS NULL
                                 THEN excluded.score ELSE jobs.score END,
                 reason   = CASE WHEN excluded.verified = 1 OR jobs.reason = ''
                                 THEN excluded.reason ELSE jobs.reason END,
                 advice   = CASE WHEN excluded.advice != '' THEN excluded.advice
                                 ELSE jobs.advice END,
                 verified = MAX(jobs.verified, excluded.verified),
                 posted_at = CASE WHEN excluded.posted_at != '' THEN excluded.posted_at
                                  ELSE jobs.posted_at END,
                 run_id   = excluded.run_id,
                 occupation = CASE WHEN excluded.occupation != '' THEN excluded.occupation
                                   ELSE jobs.occupation END,
                 visa     = CASE WHEN excluded.visa != '' THEN excluded.visa
                                 ELSE jobs.visa END""",
            (
                job["key"], job.get("title", ""), job.get("company", ""),
                job.get("location", ""), job.get("url", ""), job.get("source", ""),
                1 if job.get("is_direct") else 0, 1 if job.get("is_agency") else 0,
                (job.get("description") or "")[:8000],
                job.get("score"), job.get("reason", ""), job.get("advice", ""),
                1 if job.get("verified") else 0,
                job.get("posted_at", "") or "",
                now(), run_id,
                job.get("occupation", "") or "",
                job.get("visa", "") or "",
            ),
        )


def mark_seen(key: str, run_id: int, title: str = "", company: str = "") -> None:
    """Remember a job with no result, so it is not processed a second time."""
    with conn() as c:
        c.execute(
            "INSERT OR IGNORE INTO jobs(key, title, company, first_seen, run_id) VALUES (?,?,?,?,?)",
            (key, title, company, now(), run_id),
        )


def get_job(job_id: int):
    with conn() as c:
        r = c.execute("SELECT * FROM jobs WHERE id=?", (job_id,)).fetchone()
        return dict(r) if r else None


def save_tailored_cv(job_id: int, cv_json: str) -> None:
    with conn() as c:
        c.execute("UPDATE jobs SET tailored_cv=? WHERE id=?", (cv_json, job_id))


SORTS = {
    "default": "run_id DESC, is_direct DESC, score DESC",   # as before: newest run → direct → score
    "score": "score DESC, run_id DESC",
    "posted": "CASE WHEN posted_at IS NULL OR posted_at='' THEN 1 ELSE 0 END, posted_at DESC, score DESC",
    "found": "first_seen DESC, score DESC",
    "company": "LOWER(company) ASC, score DESC",
}


def matched_jobs(limit: int = 300, min_score: int = 0, sort: str = "default",
                 viewed: str = "all", source: str = "all", run_id: int = 0,
                 posted_from: str = "", posted_to: str = "") -> list:
    """Scored jobs. viewed: all|new|seen; source: all|direct|agency|aggregator;
    run_id > 0 — one particular run only; posted_from/posted_to — the publication
    period as YYYY-MM-DD. Jobs without a date do not fall into a period: their date
    is unknown, and passing them off as matching would not be honest."""
    where = ["score IS NOT NULL", "score >= ?"]
    params = [min_score]
    if viewed == "new":
        where.append("COALESCE(viewed, 0) = 0")
    elif viewed == "seen":
        where.append("COALESCE(viewed, 0) = 1")
    if source == "direct":
        where.append("is_direct = 1 AND COALESCE(is_agency, 0) = 0")
    elif source == "agency":
        where.append("is_agency = 1")
    elif source == "aggregator":
        where.append("COALESCE(is_direct, 0) = 0 AND COALESCE(is_agency, 0) = 0")
    if run_id:
        where.append("run_id = ?")
        params.append(run_id)
    if posted_from:
        where.append("posted_at != '' AND posted_at >= ?")
        params.append(posted_from)
    if posted_to:
        where.append("posted_at != '' AND posted_at <= ?")
        params.append(posted_to)
    order = SORTS.get(sort, SORTS["default"])
    params.append(limit)
    with conn() as c:
        rows = c.execute(
            f"SELECT * FROM jobs WHERE {' AND '.join(where)} ORDER BY {order} LIMIT ?",
            params,
        ).fetchall()
        return [dict(r) for r in rows]


def set_viewed(job_id: int, viewed: bool = True) -> None:
    with conn() as c:
        c.execute("UPDATE jobs SET viewed=? WHERE id=?", (1 if viewed else 0, job_id))


def mark_all_viewed(min_score: int = 0) -> int:
    with conn() as c:
        cur = c.execute(
            "UPDATE jobs SET viewed=1 WHERE score IS NOT NULL AND score >= ? AND COALESCE(viewed,0)=0",
            (min_score,),
        )
        return cur.rowcount


def counts(min_score: int = 0) -> dict:
    with conn() as c:
        row = c.execute(
            """SELECT COUNT(*) AS total,
                      SUM(CASE WHEN COALESCE(viewed,0)=0 THEN 1 ELSE 0 END) AS unseen
               FROM jobs WHERE score IS NOT NULL AND score >= ?""",
            (min_score,),
        ).fetchone()
        return {"total": row["total"] or 0, "unseen": row["unseen"] or 0}


def recent_runs(limit: int = 10) -> list:
    with conn() as c:
        rows = c.execute("SELECT * FROM runs ORDER BY id DESC LIMIT ?", (limit,)).fetchall()
        return [dict(r) for r in rows]


def all_scores() -> list:
    """Every score, descending — for suggesting a threshold."""
    with conn() as c:
        return [r["score"] for r in c.execute(
            "SELECT score FROM jobs WHERE score IS NOT NULL ORDER BY score DESC")]


def suggest_threshold(current: int, want: int = 12) -> int:
    """The threshold that would leave about `want` jobs in the list (a multiple of 5).
    Returns 0 if the current threshold already gives enough."""
    scores = all_scores()
    above = sum(1 for s in scores if s >= current)
    if above >= max(1, want // 2) or not scores:
        return 0  # the current threshold is fine — no suggestion needed
    target = scores[min(want, len(scores)) - 1]
    sugg = (target // 5) * 5
    return sugg if 0 < sugg < current else 0
