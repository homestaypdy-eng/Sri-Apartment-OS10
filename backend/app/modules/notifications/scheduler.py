"""
Real, working background scheduler using APScheduler running in-process
(no Celery/Redis needed for this). Disabled by default (see
Settings.enable_scheduler) so tests and one-off scripts don't spawn a
background thread unintentionally — a production deployment sets
ENABLE_SCHEDULER=true.
"""
import logging

from apscheduler.schedulers.background import BackgroundScheduler

from app.core.config import settings
from app.db.session import SessionLocal
from app.modules.notifications.service import scan_and_send_due_reminders

logger = logging.getLogger("notifications.scheduler")

_scheduler: BackgroundScheduler | None = None


def _run_reminder_scan():
    db = SessionLocal()
    try:
        results = scan_and_send_due_reminders(db)
        logger.info("Reminder scan complete: %s", results)
    except Exception:
        logger.exception("Reminder scan failed")
    finally:
        db.close()


def _expire_stale_holds():
    """P0-4: Background sweep that converts pending_payment holds past their TTL
    to 'expired', releasing the unit for new guests. The create_hold endpoint
    also runs a targeted per-unit sweep synchronously, so this job is a safety
    net for units that receive no new booking traffic."""
    from sqlalchemy import text as _text

    db = SessionLocal()
    try:
        result = db.execute(
            _text(
                "UPDATE booking_holds SET status='expired', updated_at=now() "
                "WHERE status='pending_payment' AND expires_at < now()"
            )
        )
        db.commit()
        if result.rowcount:
            logger.info("Hold expiry sweep: expired %s stale hold(s)", result.rowcount)
    except Exception:
        logger.exception("Hold expiry sweep failed")
        db.rollback()
    finally:
        db.close()


def start_scheduler() -> BackgroundScheduler | None:
    global _scheduler
    if not settings.enable_scheduler:
        logger.info("Scheduler disabled (ENABLE_SCHEDULER not set) — automatic reminders will not run.")
        return None
    if _scheduler is not None:
        return _scheduler

    _scheduler = BackgroundScheduler()
    _scheduler.add_job(
        _run_reminder_scan,
        "interval",
        minutes=settings.reminder_scan_interval_minutes,
        id="reminder_scan",
        next_run_time=None,  # first run waits one interval; call scan_and_send_due_reminders() directly for immediate testing
    )
    _scheduler.add_job(
        _expire_stale_holds,
        "interval",
        minutes=5,
        id="hold_expiry_cleanup",
        next_run_time=None,
    )
    _scheduler.start()
    logger.info("Notification scheduler started (interval: %s min)", settings.reminder_scan_interval_minutes)
    return _scheduler


def stop_scheduler() -> None:
    global _scheduler
    if _scheduler is not None:
        _scheduler.shutdown(wait=False)
        _scheduler = None
