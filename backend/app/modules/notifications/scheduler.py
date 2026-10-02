"""
Real, working background scheduler using APScheduler running in-process
(no Celery/Redis needed for this). Disabled by default (see
Settings.enable_scheduler) so tests and one-off scripts don't spawn a
background thread unintentionally — a production deployment sets
ENABLE_SCHEDULER=true. Each application process owns its own scheduler;
the Zotel sync advisory lock prevents concurrent sync work across processes,
but multiple instances can still schedule redundant sequential sync runs.
"""
import logging
from threading import RLock

from apscheduler.schedulers.background import BackgroundScheduler

from app.core.config import (
    ZOTEL_SYNC_INTERVAL_DEFAULT_MINUTES,
    ZOTEL_SYNC_INTERVAL_MAX_MINUTES,
    ZOTEL_SYNC_INTERVAL_MIN_MINUTES,
    settings,
)
from app.db.session import SessionLocal
from app.modules.notifications.service import scan_and_send_due_reminders, scan_and_send_escalations
from app.modules.zotel import client as zotel_client, service as zotel_service

logger = logging.getLogger("notifications.scheduler")

_scheduler: BackgroundScheduler | None = None
_scheduler_lock = RLock()
_zotel_not_configured_logged = False


def _run_reminder_scan():
    db = SessionLocal()
    try:
        results = scan_and_send_due_reminders(db)
        logger.info("Reminder scan complete: %s", results)
    except Exception:
        logger.exception("Reminder scan failed")
    finally:
        db.close()


def _run_escalation_scan():
    """scan_and_send_escalations already existed with full dedup/idempotency
    via NotificationLog, and was only reachable manually via
    POST /notifications/scan-escalations. Its own docstring already says
    it's "reusing the same scan-and-dedupe infrastructure as the reminder
    scan rather than building a separate scheduling path" — so it runs on
    the same existing reminder_scan_interval_minutes cadence rather than
    introducing a new interval setting."""
    db = SessionLocal()
    try:
        results = scan_and_send_escalations(db)
        logger.info("Escalation scan complete: %s", results)
    except Exception:
        logger.exception("Escalation scan failed")
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


def _run_zotel_sync():
    """Run the existing sync service without allowing job errors to escape."""
    global _zotel_not_configured_logged
    db = None
    try:
        if not zotel_client.is_configured():
            if not _zotel_not_configured_logged:
                logger.info("Scheduled Zotel sync skipped: Zotel is not configured; no API call was made.")
                _zotel_not_configured_logged = True
            return

        _zotel_not_configured_logged = False
        db = SessionLocal()
        # triggered_by remains NULL for this automatic run; manual route
        # invocations pass the authenticated user's ID into the same service.
        sync_log = zotel_service.sync_bookings(db, triggered_by=None)
        log_method = logger.warning if sync_log.status == "failed" else logger.info
        log_method(
            "Scheduled Zotel sync finished: sync_log_id=%s status=%s fetched=%s imported=%s updated=%s skipped=%s conflicted=%s failed=%s",
            sync_log.id,
            sync_log.status,
            sync_log.records_fetched,
            sync_log.records_imported,
            sync_log.records_updated,
            sync_log.records_skipped,
            sync_log.records_conflicted,
            sync_log.records_failed,
        )
    except zotel_service.ZotelSyncAlreadyRunning:
        logger.info("Scheduled Zotel sync skipped: another sync is already running.")
    except zotel_service.ZotelSyncLockUnavailable:
        logger.warning("Scheduled Zotel sync skipped: the PostgreSQL sync lock is unavailable.")
    except Exception as exc:
        # Do not include exception messages or tracebacks: provider/client
        # exceptions must never leak configured credential material.
        logger.error("Scheduled Zotel sync failed (exception type=%s)", type(exc).__name__)
    finally:
        if db is not None:
            try:
                db.close()
            except Exception as exc:
                logger.error("Scheduled Zotel database session close failed (exception type=%s)", type(exc).__name__)


def _zotel_interval_minutes() -> int:
    """Defensive guard for runtime overrides in addition to Settings validation."""
    interval = settings.zotel_sync_interval_minutes
    if (
        isinstance(interval, bool)
        or not isinstance(interval, int)
        or not ZOTEL_SYNC_INTERVAL_MIN_MINUTES <= interval <= ZOTEL_SYNC_INTERVAL_MAX_MINUTES
    ):
        logger.warning(
            "Unsafe ZOTEL_SYNC_INTERVAL_MINUTES at scheduler startup; using %s minutes",
            ZOTEL_SYNC_INTERVAL_DEFAULT_MINUTES,
        )
        return ZOTEL_SYNC_INTERVAL_DEFAULT_MINUTES
    return interval


def start_scheduler() -> BackgroundScheduler | None:
    global _scheduler
    with _scheduler_lock:
        if not settings.enable_scheduler:
            logger.info("Scheduler disabled (ENABLE_SCHEDULER not set) — automatic jobs will not run.")
            return None
        if _scheduler is not None:
            return _scheduler

        scheduler = BackgroundScheduler()
        scheduler.add_job(
            _run_reminder_scan,
            "interval",
            minutes=settings.reminder_scan_interval_minutes,
            id="reminder_scan",
            next_run_time=None,
        )
        scheduler.add_job(
            _expire_stale_holds,
            "interval",
            minutes=5,
            id="hold_expiry_cleanup",
            next_run_time=None,
        )
        scheduler.add_job(
            _run_escalation_scan,
            "interval",
            minutes=settings.reminder_scan_interval_minutes,
            id="escalation_scan",
            next_run_time=None,
        )
        scheduler.add_job(
            _run_zotel_sync,
            "interval",
            minutes=_zotel_interval_minutes(),
            id="zotel_booking_sync",
            next_run_time=None,
            replace_existing=True,
            coalesce=True,
            max_instances=1,
            misfire_grace_time=300,
        )
        scheduler.start()
        _scheduler = scheduler
        logger.info(
            "Background scheduler started (reminder interval: %s min; Zotel sync interval: %s min)",
            settings.reminder_scan_interval_minutes,
            _zotel_interval_minutes(),
        )
        return _scheduler


def stop_scheduler() -> None:
    global _scheduler
    with _scheduler_lock:
        if _scheduler is not None:
            scheduler = _scheduler
            _scheduler = None
            scheduler.shutdown(wait=False)
