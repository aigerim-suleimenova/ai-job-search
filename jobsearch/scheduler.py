"""Планировщик регулярных прогонов (пока приложение запущено)."""
from apscheduler.schedulers.background import BackgroundScheduler

from . import pipeline

_scheduler = BackgroundScheduler()
JOB_ID = "search"

_UNIT_SECONDS = {"hours": 3600, "days": 86400, "weeks": 604800}


def start() -> None:
    if not _scheduler.running:
        _scheduler.start()


def reschedule(cfg: dict) -> None:
    sched = cfg.get("schedule", {})
    existing = _scheduler.get_job(JOB_ID)
    if existing:
        existing.remove()
    if not sched.get("enabled"):
        return
    unit = sched.get("every_unit", "days")
    value = max(1, int(sched.get("every_value", 1)))
    seconds = _UNIT_SECONDS.get(unit, 86400) * value
    _scheduler.add_job(
        lambda: pipeline.run_async("schedule"),
        "interval", seconds=seconds, id=JOB_ID, max_instances=1,
    )


def next_run_time():
    job = _scheduler.get_job(JOB_ID)
    return job.next_run_time if job else None
