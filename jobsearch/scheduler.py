"""Расписание прогонов (пока приложение запущено).

Режимы (schedule.mode):
  off        — вручную;
  interval   — раз в N часов/дней/недель (APScheduler, по одной задаче на профиль);
  continuous — непрерывно: один прогон за другим, вечно (отдельный фоновый поток).
"""
import threading
import time

from apscheduler.schedulers.background import BackgroundScheduler

from . import config, pipeline, profiles

_scheduler = BackgroundScheduler()
_UNIT_SECONDS = {"hours": 3600, "days": 86400, "weeks": 604800}

# непрерывный воркер
_cont_thread = None
_cont_stop = False


def _job_id(slug: str) -> str:
    return f"search:{slug}"


def start() -> None:
    if not _scheduler.running:
        _scheduler.start()
    _start_continuous_worker()


def _start_continuous_worker() -> None:
    global _cont_thread
    if _cont_thread and _cont_thread.is_alive():
        return
    _cont_thread = threading.Thread(target=_continuous_loop, daemon=True)
    _cont_thread.start()


def _continuous_loop() -> None:
    """Вечный цикл: по кругу прогоняет все профили в режиме continuous.
    Один прогон за раз (pipeline сериализуется своим локом)."""
    while not _cont_stop:
        ran = False
        for p in profiles.list_profiles():
            if _cont_stop:
                break
            slug = p["slug"]
            profiles.set_active(slug)
            sched = config.load().get("schedule", {})
            if sched.get("mode") != "continuous":
                continue
            ran = True
            pipeline.run(trigger="continuous", profile=slug)  # блокирует до конца прогона
            cooldown = max(1, int(sched.get("continuous_cooldown_min", 20))) * 60
            slept = 0
            while slept < cooldown and not _cont_stop:
                # прерываемся раньше, если профиль вышел из непрерывного режима
                time.sleep(5)
                slept += 5
                if config.load().get("schedule", {}).get("mode") != "continuous":
                    break
        if not ran:
            time.sleep(20)  # никого в непрерывном режиме — ждём


def stop() -> None:
    """Остановить расписание и непрерывный режим — вызывается при закрытии приложения."""
    global _cont_stop
    _cont_stop = True
    try:
        _scheduler.shutdown(wait=False)
    except Exception:  # noqa: BLE001 — планировщик мог не запускаться
        pass


def reschedule_all() -> None:
    """Пересобирает интервальные задачи. Восстанавливает активный профиль."""
    saved = profiles.active()
    try:
        for job in list(_scheduler.get_jobs()):
            job.remove()
        for p in profiles.list_profiles():
            slug = p["slug"]
            profiles.set_active(slug)
            sched = config.load().get("schedule", {})
            if sched.get("mode") != "interval":
                continue
            unit = sched.get("every_unit", "days")
            value = max(1, int(sched.get("every_value", 1)))
            seconds = _UNIT_SECONDS.get(unit, 86400) * value
            _scheduler.add_job(
                pipeline.run_async, "interval", seconds=seconds,
                args=["schedule", slug], id=_job_id(slug), max_instances=1,
            )
    finally:
        profiles.set_active(saved)
    _start_continuous_worker()  # на случай если поток не запущен


def reschedule(cfg: dict = None) -> None:
    reschedule_all()


def next_run_time(slug: str = None):
    job = _scheduler.get_job(_job_id(slug or profiles.active()))
    return job.next_run_time if job else None
