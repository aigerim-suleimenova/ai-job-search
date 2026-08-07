"""The database: the things that break quietly.

One line here once lost the results of a forty-minute run — the insert was
silently ignored and the deep analysis never reached the database. No error came
of it at all: there simply was no advice in the list.
"""
from jobsearch import db

from conftest import job


def test_a_new_job_is_saved(profile):
    db.save_job(job("k1"), run_id=1)
    rows = db.matched_jobs(min_score=0)
    assert len(rows) == 1
    assert rows[0]["title"] == "Senior Frontend Engineer"
    assert rows[0]["score"] == 60


def test_the_deep_analysis_overwrites_the_triage_score(profile):
    db.save_job(job("k1", score=60, reason="a quick score"), run_id=1)
    db.save_job(job("k1", score=88, reason="an exact score", advice='{"cv_changes":[]}',
                    verified=True), run_id=1)
    row = db.matched_jobs(min_score=0)[0]
    assert row["score"] == 88
    assert row["reason"] == "an exact score"
    assert row["advice"] == '{"cv_changes":[]}'
    assert row["verified"] == 1


def test_a_repeat_triage_does_not_wipe_what_was_analysed(profile):
    """The next run meets the same job and scores it quickly.
    That is no reason to lose the analysis already done."""
    db.save_job(job("k1", score=88, reason="exact", advice='{"a":1}', verified=True), run_id=1)
    db.save_job(job("k1", score=55, reason="quick", advice="", verified=False), run_id=2)
    row = db.matched_jobs(min_score=0)[0]
    assert row["score"] == 88, "triage overwrote a confirmed score"
    assert row["advice"] == '{"a":1}', "the advice was lost"
    assert row["run_id"] == 2, "the job should belong to the newest run"


def test_the_seen_mark_outlives_meeting_the_job_again(profile):
    db.save_job(job("k1"), run_id=1)
    job_id = db.matched_jobs(min_score=0)[0]["id"]
    db.set_viewed(job_id, True)
    db.save_job(job("k1", score=70), run_id=2)
    assert db.get_job(job_id)["viewed"] == 1


def test_the_first_seen_date_does_not_change(profile):
    db.save_job(job("k1"), run_id=1)
    first = db.matched_jobs(min_score=0)[0]["first_seen"]
    db.save_job(job("k1", score=90, verified=True), run_id=2)
    assert db.matched_jobs(min_score=0)[0]["first_seen"] == first


def test_filtering_by_threshold(profile):
    db.save_job(job("low", score=40), run_id=1)
    db.save_job(job("high", score=80), run_id=1)
    assert {r["key"] for r in db.matched_jobs(min_score=70)} == {"high"}


def test_filtering_by_posting_date(profile):
    db.save_job(job("old", posted_at="2026-06-01"), run_id=1)
    db.save_job(job("new", posted_at="2026-07-25"), run_id=1)
    db.save_job(job("no date", posted_at=""), run_id=1)

    recent = {r["key"] for r in db.matched_jobs(min_score=0, posted_from="2026-07-01")}
    assert recent == {"new"}, "jobs with no date must not pass the from filter"

    older = {r["key"] for r in db.matched_jobs(min_score=0, posted_to="2026-06-30")}
    assert older == {"old"}


def test_filtering_by_source_and_run(profile):
    db.save_job(job("direct", is_direct=1, is_agency=0), run_id=1)
    db.save_job(job("agency", is_direct=0, is_agency=1), run_id=2)
    assert {r["key"] for r in db.matched_jobs(min_score=0, source="direct")} == {"direct"}
    assert {r["key"] for r in db.matched_jobs(min_score=0, source="agency")} == {"agency"}
    assert {r["key"] for r in db.matched_jobs(min_score=0, run_id=2)} == {"agency"}


def test_filtering_by_seen(profile):
    db.save_job(job("a"), run_id=1)
    db.save_job(job("b"), run_id=1)
    db.set_viewed(db.matched_jobs(min_score=0)[0]["id"], True)
    unseen_rows = db.matched_jobs(min_score=0, viewed="new")
    seen_rows = db.matched_jobs(min_score=0, viewed="seen")
    assert len(unseen_rows) == 1 and len(seen_rows) == 1


def test_the_counters_and_mark_all(profile):
    for i in range(3):
        db.save_job(job(f"k{i}", score=75), run_id=1)
    assert db.counts(min_score=0)["unseen"] == 3
    db.mark_all_viewed(min_score=0)
    assert db.counts(min_score=0)["unseen"] == 0


def test_the_sort_orders_do_not_fall_over(profile):
    db.save_job(job("a", score=80), run_id=1)
    db.save_job(job("b", score=90, posted_at=""), run_id=1)
    for sort in db.SORTS:
        assert len(db.matched_jobs(min_score=0, sort=sort)) == 2, f"sort order {sort}"
