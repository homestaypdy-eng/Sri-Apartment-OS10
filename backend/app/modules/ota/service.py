"""
OTA Channel Manager sync logic.

HONEST SCOPE: Booking.com, Airbnb, and Agoda all require becoming an
approved connectivity partner before any API calls work — this is a
business/legal onboarding process specific to each platform, not something
achievable with a code change or API key, and their partner APIs are also
outside this sandbox's network allowlist. This module implements the real
data model, sync workflow, and logging any real integration would need, and
runs it against a MockOTAAdapter that simulates responses so the whole
feature is genuinely clickable and testable today. Swapping in a real
adapter later (once partnership + credentials exist) means implementing
the same `sync_reservations`/`push_availability`/`push_rates` interface
against that platform's real API — the rest of this module doesn't change.
"""
import random
from datetime import date, timedelta

from sqlalchemy.orm import Session

from app.modules.ota.models import OTASyncLog


class MockOTAAdapter:
    """Simulates an OTA partner API. Clearly named Mock — never confused
    with a real integration in logs, UI, or code."""

    def __init__(self, channel: str):
        self.channel = channel

    def fetch_reservations(self) -> list[dict]:
        # Deterministic-ish fake data so a demo/test always has something to show
        return [
            {
                "external_reservation_id": f"{self.channel}-{random.randint(10000, 99999)}",
                "guest_name": "Sample OTA Guest",
                "check_in": (date.today() + timedelta(days=7)).isoformat(),
                "check_out": (date.today() + timedelta(days=9)).isoformat(),
            }
        ]

    def push_availability(self, unit_mappings: list[dict]) -> bool:
        return True

    def push_rates(self, unit_mappings: list[dict]) -> bool:
        return True

    def push_booking_update(self, external_reservation_id: str, status: str) -> bool:
        return True

    def sync_cancellations(self) -> list[str]:
        return []


def get_adapter(channel: str) -> MockOTAAdapter:
    return MockOTAAdapter(channel)


def run_sync(db: Session, channel: str, sync_type: str, triggered_by=None) -> OTASyncLog:
    adapter = get_adapter(channel)
    try:
        if sync_type == "reservation_import":
            reservations = adapter.fetch_reservations()
            message = f"[MOCK] Fetched {len(reservations)} reservation(s) from {channel}"
            records = len(reservations)
        elif sync_type == "availability_sync":
            adapter.push_availability([])
            message = f"[MOCK] Pushed availability to {channel}"
            records = 0
        elif sync_type == "rate_sync":
            adapter.push_rates([])
            message = f"[MOCK] Pushed rates to {channel}"
            records = 0
        elif sync_type == "cancellation_sync":
            cancelled = adapter.sync_cancellations()
            message = f"[MOCK] {len(cancelled)} cancellation(s) synced from {channel}"
            records = len(cancelled)
        else:
            message = f"[MOCK] Booking update pushed to {channel}"
            records = 0

        log = OTASyncLog(channel=channel, sync_type=sync_type, status="success", message=message, records_affected=records, triggered_by=triggered_by)
    except Exception as e:
        log = OTASyncLog(channel=channel, sync_type=sync_type, status="failed", message=str(e), triggered_by=triggered_by)

    db.add(log)
    db.commit()
    db.refresh(log)
    return log
