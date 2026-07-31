"""База: то, что ломается тихо.

Из-за одной строки здесь однажды пропали результаты сорокаминутного прогона —
вставка молча игнорировалась, и глубокий разбор не доезжал до базы. Никакой
ошибки при этом не возникало: просто в списке не было советов.
"""
from jobsearch import db

from conftest import job


def test_новая_вакансия_сохраняется(profile):
    db.save_job(job("k1"), run_id=1)
    rows = db.matched_jobs(min_score=0)
    assert len(rows) == 1
    assert rows[0]["title"] == "Senior Frontend Engineer"
    assert rows[0]["score"] == 60


def test_глубокий_разбор_перезаписывает_триажную_оценку(profile):
    db.save_job(job("k1", score=60, reason="быстрая оценка"), run_id=1)
    db.save_job(job("k1", score=88, reason="точная оценка", advice='{"cv_changes":[]}',
                    verified=True), run_id=1)
    row = db.matched_jobs(min_score=0)[0]
    assert row["score"] == 88
    assert row["reason"] == "точная оценка"
    assert row["advice"] == '{"cv_changes":[]}'
    assert row["verified"] == 1


def test_повторный_триаж_не_затирает_разобранное(profile):
    """Следующий прогон встречает ту же вакансию и оценивает её быстро.
    Это не повод терять уже сделанный разбор."""
    db.save_job(job("k1", score=88, reason="точная", advice='{"a":1}', verified=True), run_id=1)
    db.save_job(job("k1", score=55, reason="быстрая", advice="", verified=False), run_id=2)
    row = db.matched_jobs(min_score=0)[0]
    assert row["score"] == 88, "триаж перезаписал подтверждённую оценку"
    assert row["advice"] == '{"a":1}', "советы потерялись"
    assert row["run_id"] == 2, "вакансия должна числиться за свежим прогоном"


def test_отметка_просмотрено_переживает_повторную_встречу(profile):
    db.save_job(job("k1"), run_id=1)
    job_id = db.matched_jobs(min_score=0)[0]["id"]
    db.set_viewed(job_id, True)
    db.save_job(job("k1", score=70), run_id=2)
    assert db.get_job(job_id)["viewed"] == 1


def test_дата_первой_встречи_не_меняется(profile):
    db.save_job(job("k1"), run_id=1)
    first = db.matched_jobs(min_score=0)[0]["first_seen"]
    db.save_job(job("k1", score=90, verified=True), run_id=2)
    assert db.matched_jobs(min_score=0)[0]["first_seen"] == first


def test_фильтр_по_порогу(profile):
    db.save_job(job("low", score=40), run_id=1)
    db.save_job(job("high", score=80), run_id=1)
    assert {r["key"] for r in db.matched_jobs(min_score=70)} == {"high"}


def test_фильтр_по_датам_публикации(profile):
    db.save_job(job("old", posted_at="2026-06-01"), run_id=1)
    db.save_job(job("new", posted_at="2026-07-25"), run_id=1)
    db.save_job(job("без даты", posted_at=""), run_id=1)

    свежие = {r["key"] for r in db.matched_jobs(min_score=0, posted_from="2026-07-01")}
    assert свежие == {"new"}, "вакансии без даты не должны проходить фильтр «от»"

    старые = {r["key"] for r in db.matched_jobs(min_score=0, posted_to="2026-06-30")}
    assert старые == {"old"}


def test_фильтр_по_источнику_и_прогону(profile):
    db.save_job(job("прямая", is_direct=1, is_agency=0), run_id=1)
    db.save_job(job("агентство", is_direct=0, is_agency=1), run_id=2)
    assert {r["key"] for r in db.matched_jobs(min_score=0, source="direct")} == {"прямая"}
    assert {r["key"] for r in db.matched_jobs(min_score=0, source="agency")} == {"агентство"}
    assert {r["key"] for r in db.matched_jobs(min_score=0, run_id=2)} == {"агентство"}


def test_фильтр_по_просмотренности(profile):
    db.save_job(job("a"), run_id=1)
    db.save_job(job("b"), run_id=1)
    db.set_viewed(db.matched_jobs(min_score=0)[0]["id"], True)
    новые = db.matched_jobs(min_score=0, viewed="new")
    виденные = db.matched_jobs(min_score=0, viewed="seen")
    assert len(новые) == 1 and len(виденные) == 1


def test_счётчики_и_отметить_все(profile):
    for i in range(3):
        db.save_job(job(f"k{i}", score=75), run_id=1)
    assert db.counts(min_score=0)["unseen"] == 3
    db.mark_all_viewed(min_score=0)
    assert db.counts(min_score=0)["unseen"] == 0


def test_сортировки_не_падают(profile):
    db.save_job(job("a", score=80), run_id=1)
    db.save_job(job("b", score=90, posted_at=""), run_id=1)
    for sort in db.SORTS:
        assert len(db.matched_jobs(min_score=0, sort=sort)) == 2, f"сортировка {sort}"
