"""
Live availability + double-booking protection across OS10 and the public
website (see app/shared/availability.py for the shared, hold-aware check).

Covers:
  1.  Overlap boundary semantics (adjacent/overlapping/identical/contains)
  2.  Cancelled booking releases inventory
  3.  An active website hold blocks OS10 manual booking creation (the core
      cross-system gap this audit found and fixed)
  4.  An expired hold does not block (no phantom holds)
  5.  Batch /units/availability endpoint
  6.  Concurrent booking attempts — exactly one succeeds
  7.  Concurrent hold attempts — exactly one succeeds

Run with: pytest tests/test_availability_and_double_booking.py -v
"""
import threading
import uuid
from datetime import date, timedelta
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient

from app.main import app

client = TestClient(app)

from conftest import ADMIN_EMAIL, ADMIN_PASSWORD  # single source of truth, see conftest.py


@pytest.fixture(scope="module")
def headers():
    resp = client.post("/api/v1/auth/login", json={"email": ADMIN_EMAIL, "password": ADMIN_PASSWORD})
    assert resp.status_code == 200
    return {"Authorization": f"Bearer {resp.json()['access_token']}"}


def _make_unit_and_guest(headers, base_rate=2500, max_occupancy=2):
    prop = client.post("/api/v1/properties", headers=headers, json={
        "name": f"AvailTest {uuid.uuid4().hex[:6]}", "address": "Test", "state_code": "34",
    }).json()
    unit = client.post("/api/v1/units", headers=headers, json={
        "property_id": prop["id"], "name": f"AvailUnit {uuid.uuid4().hex[:6]}", "base_rate": base_rate,
        "max_occupancy": max_occupancy,
    }).json()
    guest = client.post("/api/v1/guests", headers=headers, json={
        "name": "Avail Test Guest", "phone": f"90{uuid.uuid4().int % 10**8:08d}",
    }).json()
    return unit["id"], guest["id"]


def _availability(headers, unit_id, check_in, check_out):
    r = client.get(f"/api/v1/units/{unit_id}/availability", headers=headers, params={"check_in": check_in.isoformat(), "check_out": check_out.isoformat()})
    assert r.status_code == 200
    return r.json()["available"]


# ---------------------------------------------------------------------------
# Overlap boundary semantics (section 4 of the audit spec)
# ---------------------------------------------------------------------------

def test_adjacent_stay_after_existing_booking_is_allowed(headers):
    """Existing 18-20, new 20-22 — standard checkout/check-in same-day
    boundary: must be allowed."""
    unit_id, guest_id = _make_unit_and_guest(headers)
    base = date(2030, 9, 18)
    r1 = client.post("/api/v1/bookings", headers=headers, json={
        "unit_id": unit_id, "guest_id": guest_id, "check_in": base.isoformat(), "check_out": (base + timedelta(days=2)).isoformat(),
    })
    assert r1.status_code == 201, r1.text

    assert _availability(headers, unit_id, base + timedelta(days=2), base + timedelta(days=4)) is True
    r2 = client.post("/api/v1/bookings", headers=headers, json={
        "unit_id": unit_id, "guest_id": guest_id, "check_in": (base + timedelta(days=2)).isoformat(), "check_out": (base + timedelta(days=4)).isoformat(),
    })
    assert r2.status_code == 201, r2.text


def test_overlapping_stay_straddling_existing_booking_is_rejected(headers):
    """Existing 18-20, new 19-21 — must be rejected."""
    unit_id, guest_id = _make_unit_and_guest(headers)
    base = date(2030, 10, 18)
    client.post("/api/v1/bookings", headers=headers, json={
        "unit_id": unit_id, "guest_id": guest_id, "check_in": base.isoformat(), "check_out": (base + timedelta(days=2)).isoformat(),
    })
    assert _availability(headers, unit_id, base + timedelta(days=1), base + timedelta(days=3)) is False
    r = client.post("/api/v1/bookings", headers=headers, json={
        "unit_id": unit_id, "guest_id": guest_id, "check_in": (base + timedelta(days=1)).isoformat(), "check_out": (base + timedelta(days=3)).isoformat(),
    })
    assert r.status_code == 409


def test_identical_stay_is_rejected(headers):
    """Existing 18-20, new 18-20 — must be rejected."""
    unit_id, guest_id = _make_unit_and_guest(headers)
    base = date(2030, 11, 18)
    client.post("/api/v1/bookings", headers=headers, json={
        "unit_id": unit_id, "guest_id": guest_id, "check_in": base.isoformat(), "check_out": (base + timedelta(days=2)).isoformat(),
    })
    assert _availability(headers, unit_id, base, base + timedelta(days=2)) is False
    r = client.post("/api/v1/bookings", headers=headers, json={
        "unit_id": unit_id, "guest_id": guest_id, "check_in": base.isoformat(), "check_out": (base + timedelta(days=2)).isoformat(),
    })
    assert r.status_code == 409


def test_new_stay_containing_existing_booking_is_rejected(headers):
    """Existing 19-21, new 18-20 — must be rejected (overlaps the start)."""
    unit_id, guest_id = _make_unit_and_guest(headers)
    base = date(2030, 12, 19)
    client.post("/api/v1/bookings", headers=headers, json={
        "unit_id": unit_id, "guest_id": guest_id, "check_in": base.isoformat(), "check_out": (base + timedelta(days=2)).isoformat(),
    })
    new_check_in = base - timedelta(days=1)
    assert _availability(headers, unit_id, new_check_in, new_check_in + timedelta(days=2)) is False
    r = client.post("/api/v1/bookings", headers=headers, json={
        "unit_id": unit_id, "guest_id": guest_id, "check_in": new_check_in.isoformat(), "check_out": (new_check_in + timedelta(days=2)).isoformat(),
    })
    assert r.status_code == 409


def test_non_overlapping_dates_allowed(headers):
    unit_id, guest_id = _make_unit_and_guest(headers)
    base = date(2031, 1, 1)
    client.post("/api/v1/bookings", headers=headers, json={
        "unit_id": unit_id, "guest_id": guest_id, "check_in": base.isoformat(), "check_out": (base + timedelta(days=2)).isoformat(),
    })
    far = base + timedelta(days=30)
    assert _availability(headers, unit_id, far, far + timedelta(days=2)) is True


def test_overlap_query_handles_optional_and_uuid_booking_exclusion_on_postgres(headers):
    """The raw SQL bind must work both without an exclusion and with a
    PostgreSQL-typed UUID exclusion; edits exclude themselves but still see
    a different overlapping booking."""
    from app.db.session import SessionLocal
    from app.shared.availability import _has_overlap

    unit_id, guest_id = _make_unit_and_guest(headers)
    unit_uuid = uuid.UUID(unit_id)
    check_in = date(2037, 1, 10)
    check_out = check_in + timedelta(days=2)

    db = SessionLocal()
    try:
        free_range = _has_overlap(db, [unit_uuid], check_in, check_out)
        assert free_range[unit_uuid] is False
    finally:
        db.close()

    booking = client.post("/api/v1/bookings", headers=headers, json={
        "unit_id": unit_id, "guest_id": guest_id,
        "check_in": check_in.isoformat(), "check_out": check_out.isoformat(),
    })
    assert booking.status_code == 201, booking.text
    booking_uuid = uuid.UUID(booking.json()["id"])

    db = SessionLocal()
    try:
        self_excluded = _has_overlap(
            db, [unit_uuid], check_in, check_out, exclude_booking_id=booking_uuid,
        )
        other_booking_not_excluded = _has_overlap(
            db, [unit_uuid], check_in, check_out, exclude_booking_id=uuid.uuid4(),
        )
        assert self_excluded[unit_uuid] is False
        assert other_booking_not_excluded[unit_uuid] is True
    finally:
        db.close()


def test_cancelled_booking_releases_inventory(headers):
    unit_id, guest_id = _make_unit_and_guest(headers)
    base = date(2031, 2, 1)
    r1 = client.post("/api/v1/bookings", headers=headers, json={
        "unit_id": unit_id, "guest_id": guest_id, "check_in": base.isoformat(), "check_out": (base + timedelta(days=2)).isoformat(),
    })
    booking_id = r1.json()["id"]
    assert _availability(headers, unit_id, base, base + timedelta(days=2)) is False

    cancel = client.patch(f"/api/v1/bookings/{booking_id}/status", headers=headers, params={"new_status": "cancelled"})
    assert cancel.status_code == 200
    assert _availability(headers, unit_id, base, base + timedelta(days=2)) is True


# ---------------------------------------------------------------------------
# Cross-system hold-awareness (the core gap this audit found and fixed)
# ---------------------------------------------------------------------------

def test_active_website_hold_blocks_os10_manual_booking(headers):
    """Before this fix, OS10's availability check and create_booking only
    looked at the bookings table — a website guest mid-payment (an active
    BookingHold) was invisible to OS10, so staff could confirm a manual
    booking for the same unit/dates while that guest's card was being
    charged, and the guest's payment would then fail to convert."""
    unit_id, guest_id = _make_unit_and_guest(headers)
    base = date(2031, 3, 1)
    check_in, check_out = base.isoformat(), (base + timedelta(days=2)).isoformat()

    with patch("app.modules.public_booking.router.create_order") as mock_order:
        mock_order.return_value = {"id": f"order_{uuid.uuid4().hex[:10]}"}
        hold_resp = client.post("/api/v1/public/holds", json={
            "unit_id": unit_id, "check_in": check_in, "check_out": check_out,
            "guest_name": "Hold Aware Guest", "guest_phone": f"91{uuid.uuid4().int % 10**8:08d}", "guest_email": "holdaware@example.com",
        })
    assert hold_resp.status_code == 201, hold_resp.text

    assert _availability(headers, unit_id, base, base + timedelta(days=2)) is False

    blocked = client.post("/api/v1/bookings", headers=headers, json={
        "unit_id": unit_id, "guest_id": guest_id, "check_in": check_in, "check_out": check_out,
    })
    assert blocked.status_code == 409, blocked.text
    assert "progress" in blocked.json()["detail"].lower() or "booked" in blocked.json()["detail"].lower()


def test_expired_hold_does_not_block_os10_booking(headers):
    """A hold whose TTL has passed must not create a phantom unavailable
    date — even if its status hasn't been lazily flipped to 'expired' yet."""
    unit_id, guest_id = _make_unit_and_guest(headers)
    base = date(2031, 4, 1)
    check_in, check_out = base.isoformat(), (base + timedelta(days=2)).isoformat()

    with patch("app.modules.public_booking.router.create_order") as mock_order:
        mock_order.return_value = {"id": f"order_{uuid.uuid4().hex[:10]}"}
        hold_resp = client.post("/api/v1/public/holds", json={
            "unit_id": unit_id, "check_in": check_in, "check_out": check_out,
            "guest_name": "Expiring Guest", "guest_phone": f"92{uuid.uuid4().int % 10**8:08d}", "guest_email": "expiring@example.com",
        })
    hold_id = hold_resp.json()["hold_id"]

    from app.db.session import SessionLocal
    from sqlalchemy import text as sa_text
    db = SessionLocal()
    db.execute(sa_text("UPDATE booking_holds SET expires_at = now() - interval '1 minute' WHERE id = :id"), {"id": hold_id})
    db.commit()
    db.close()

    assert _availability(headers, unit_id, base, base + timedelta(days=2)) is True
    allowed = client.post("/api/v1/bookings", headers=headers, json={
        "unit_id": unit_id, "guest_id": guest_id, "check_in": check_in, "check_out": check_out,
    })
    assert allowed.status_code == 201, allowed.text


def test_cancelled_hold_does_not_block_os10_booking(headers):
    """Same guarantee as test_expired_hold_does_not_block_os10_booking, from
    the other release path: a hold that was explicitly cancelled (e.g. the
    guest's Razorpay payment failed — see
    test_razorpay_failure_cancels_hold_and_frees_unit_immediately in
    test_public_booking_payment_gate.py, which proves the cancelled hold
    frees the unit for a NEW hold) must equally free the unit for a direct
    OS10 booking. unit_is_available()'s hold-overlap query only ever
    matches status IN ('pending_payment', 'paid') — 'cancelled' is excluded
    by that same WHERE clause, so this is the same code path as the expired
    case, checked from the OS10-booking side instead of the hold side."""
    unit_id, guest_id = _make_unit_and_guest(headers)
    base = date(2031, 4, 15)
    check_in, check_out = base.isoformat(), (base + timedelta(days=2)).isoformat()

    with patch("app.modules.public_booking.router.create_order") as mock_order:
        mock_order.return_value = {"id": f"order_{uuid.uuid4().hex[:10]}"}
        hold_resp = client.post("/api/v1/public/holds", json={
            "unit_id": unit_id, "check_in": check_in, "check_out": check_out,
            "guest_name": "Cancelling Guest", "guest_phone": f"94{uuid.uuid4().int % 10**8:08d}", "guest_email": "cancelling@example.com",
        })
    hold_id = hold_resp.json()["hold_id"]

    from app.db.session import SessionLocal
    from sqlalchemy import text as sa_text
    db = SessionLocal()
    db.execute(sa_text("UPDATE booking_holds SET status = 'cancelled' WHERE id = :id"), {"id": hold_id})
    db.commit()
    db.close()

    assert _availability(headers, unit_id, base, base + timedelta(days=2)) is True
    allowed = client.post("/api/v1/bookings", headers=headers, json={
        "unit_id": unit_id, "guest_id": guest_id, "check_in": check_in, "check_out": check_out,
    })
    assert allowed.status_code == 201, allowed.text


def test_website_hold_creation_still_blocked_by_existing_os10_booking(headers):
    """The reverse direction was already correct before this audit — an
    OS10-confirmed booking must still block a new website hold."""
    unit_id, guest_id = _make_unit_and_guest(headers)
    base = date(2031, 5, 1)
    check_in, check_out = base.isoformat(), (base + timedelta(days=2)).isoformat()
    r = client.post("/api/v1/bookings", headers=headers, json={
        "unit_id": unit_id, "guest_id": guest_id, "check_in": check_in, "check_out": check_out,
    })
    assert r.status_code == 201

    with patch("app.modules.public_booking.router.create_order") as mock_order:
        mock_order.return_value = {"id": f"order_{uuid.uuid4().hex[:10]}"}
        hold_resp = client.post("/api/v1/public/holds", json={
            "unit_id": unit_id, "check_in": check_in, "check_out": check_out,
            "guest_name": "Blocked Guest", "guest_phone": f"93{uuid.uuid4().int % 10**8:08d}", "guest_email": "blocked@example.com",
        })
    assert hold_resp.status_code == 409, hold_resp.text


# ---------------------------------------------------------------------------
# Batch availability endpoint (section 19 — avoid N+1)
# ---------------------------------------------------------------------------

def test_batch_availability_endpoint_matches_per_unit_checks(headers):
    unit_id_1, guest_id = _make_unit_and_guest(headers)
    unit_id_2, _ = _make_unit_and_guest(headers)
    base = date(2031, 6, 1)
    client.post("/api/v1/bookings", headers=headers, json={
        "unit_id": unit_id_1, "guest_id": guest_id, "check_in": base.isoformat(), "check_out": (base + timedelta(days=2)).isoformat(),
    })

    r = client.get("/api/v1/units/availability", headers=headers, params={"check_in": base.isoformat(), "check_out": (base + timedelta(days=2)).isoformat()})
    assert r.status_code == 200
    by_id = {row["unit_id"]: row["available"] for row in r.json()}
    assert by_id[unit_id_1] is False
    assert by_id[unit_id_2] is True
    # No internal-only fields beyond what's needed to render a picker
    assert set(r.json()[0].keys()) == {"unit_id", "name", "unit_code", "available"}


def test_batch_availability_all_units_booked_shows_none_available(headers):
    """Section 6/12.4: if every candidate unit is booked for the range,
    the batch response must show every one of them unavailable — the
    frontend is expected to render 'No availability' from that, not from
    a special-cased backend field."""
    unit_ids = []
    guest_id = None
    base = date(2031, 7, 1)
    for _ in range(3):
        uid, gid = _make_unit_and_guest(headers)
        guest_id = gid
        unit_ids.append(uid)
        client.post("/api/v1/bookings", headers=headers, json={
            "unit_id": uid, "guest_id": gid, "check_in": base.isoformat(), "check_out": (base + timedelta(days=2)).isoformat(),
        })
    r = client.get("/api/v1/units/availability", headers=headers, params={"check_in": base.isoformat(), "check_out": (base + timedelta(days=2)).isoformat()})
    by_id = {row["unit_id"]: row["available"] for row in r.json()}
    assert all(by_id[uid] is False for uid in unit_ids)


def test_maintenance_unit_never_offered_by_batch_endpoint(headers):
    unit_id, _ = _make_unit_and_guest(headers)
    client.patch(f"/api/v1/units/{unit_id}", headers=headers, json={"status": "maintenance"})
    base = date(2031, 8, 1)
    r = client.get("/api/v1/units/availability", headers=headers, params={"check_in": base.isoformat(), "check_out": (base + timedelta(days=2)).isoformat()})
    assert not any(row["unit_id"] == unit_id for row in r.json()), "Maintenance unit must not appear as a candidate at all"


# ---------------------------------------------------------------------------
# Real concurrency (section 10/17.10) — genuine threads, real DB, real
# EXCLUDE constraint, not a mocked/serialized simulation.
# ---------------------------------------------------------------------------

def test_concurrent_booking_attempts_exactly_one_succeeds(headers):
    unit_id, guest_id = _make_unit_and_guest(headers)
    base = date(2031, 9, 1)
    check_in, check_out = base.isoformat(), (base + timedelta(days=2)).isoformat()

    results = []
    barrier = threading.Barrier(8)

    def attempt():
        barrier.wait()
        r = client.post("/api/v1/bookings", headers=headers, json={
            "unit_id": unit_id, "guest_id": guest_id, "check_in": check_in, "check_out": check_out,
        })
        results.append(r.status_code)

    threads = [threading.Thread(target=attempt) for _ in range(8)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert results.count(201) == 1, f"Expected exactly 1 success, got {results.count(201)} in {sorted(results)}"
    assert results.count(409) == 7, f"Expected exactly 7 conflicts, got {results.count(409)} in {sorted(results)}"

    from app.db.session import SessionLocal
    from sqlalchemy import text as sa_text
    db = SessionLocal()
    count = db.execute(sa_text(
        "SELECT COUNT(*) FROM bookings WHERE unit_id = :uid AND check_in = :ci AND check_out = :co AND status = 'confirmed'"
    ), {"uid": unit_id, "ci": base, "co": base + timedelta(days=2)}).scalar()
    db.close()
    assert count == 1, f"Expected exactly 1 row physically in the database, found {count}"


def test_concurrent_hold_attempts_exactly_one_succeeds(headers):
    unit_id, _ = _make_unit_and_guest(headers)
    base = date(2031, 10, 1)
    check_in, check_out = base.isoformat(), (base + timedelta(days=2)).isoformat()

    results = []
    barrier = threading.Barrier(6)

    def attempt(i):
        with patch("app.modules.public_booking.router.create_order") as mock_order:
            mock_order.return_value = {"id": f"order_{uuid.uuid4().hex[:10]}"}
            barrier.wait()
            r = client.post("/api/v1/public/holds", json={
                "unit_id": unit_id, "check_in": check_in, "check_out": check_out,
                "guest_name": f"Race Guest {i}", "guest_phone": f"9{i}{uuid.uuid4().int % 10**7:07d}", "guest_email": f"race{i}@example.com",
            })
            results.append(r.status_code)

    threads = [threading.Thread(target=attempt, args=(i,)) for i in range(6)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert results.count(201) == 1, f"Expected exactly 1 successful hold, got {results.count(201)} in {sorted(results)}"
    assert results.count(409) == 5, f"Expected exactly 5 conflicts, got {results.count(409)} in {sorted(results)}"


def test_booking_max_occupancy_direct_api_and_boundary(headers):
    unit_id, guest_id = _make_unit_and_guest(headers, max_occupancy=2)
    base = date(2038, 1, 5)
    exact = client.post("/api/v1/bookings", headers=headers, json={
        "unit_id": unit_id, "guest_id": guest_id, "check_in": base.isoformat(),
        "check_out": (base + timedelta(days=1)).isoformat(), "adults": 1, "children": 1,
    })
    assert exact.status_code == 201, exact.text
    over = client.post("/api/v1/bookings", headers=headers, json={
        "unit_id": unit_id, "guest_id": guest_id, "check_in": (base + timedelta(days=2)).isoformat(),
        "check_out": (base + timedelta(days=3)).isoformat(), "adults": 2, "children": 1,
    })
    assert over.status_code == 422
    assert "maximum occupancy" in over.json()["detail"]


def test_unit_capacity_must_be_positive(headers):
    prop = client.post("/api/v1/properties", headers=headers, json={
        "name": f"CapacityProp {uuid.uuid4().hex[:6]}", "address": "Test", "state_code": "34",
    }).json()
    response = client.post("/api/v1/units", headers=headers, json={
        "property_id": prop["id"], "name": "Zero capacity", "base_rate": 2000, "max_occupancy": 0,
    })
    assert response.status_code == 422


def test_booking_edit_cannot_raise_party_above_capacity(headers):
    unit_id, guest_id = _make_unit_and_guest(headers, max_occupancy=2)
    base = date(2038, 2, 5)
    created = client.post("/api/v1/bookings", headers=headers, json={
        "unit_id": unit_id, "guest_id": guest_id, "check_in": base.isoformat(),
        "check_out": (base + timedelta(days=1)).isoformat(), "adults": 1, "children": 1,
    })
    assert created.status_code == 201, created.text
    changed = client.patch(f"/api/v1/bookings/{created.json()['id']}", headers=headers, json={"adults": 3, "reason": "capacity test"})
    assert changed.status_code == 422


def test_public_hold_rejects_over_capacity_before_provider_order(headers):
    from app.db.session import SessionLocal
    from app.modules.public_booking.models import PublicRateLimitBucket

    with SessionLocal() as db:
        db.query(PublicRateLimitBucket).delete(synchronize_session=False)
        db.commit()
    unit_id, _guest_id = _make_unit_and_guest(headers, max_occupancy=1)
    base = date(2038, 3, 5)
    with patch("app.modules.public_booking.router.create_order") as create_order:
        response = client.post("/api/v1/public/holds", json={
            "unit_id": unit_id, "check_in": base.isoformat(), "check_out": (base + timedelta(days=1)).isoformat(),
            "guest_name": "Capacity Test", "guest_phone": "919900001111", "guest_email": "capacity@example.com", "adults": 2,
        })
    assert response.status_code == 422
    create_order.assert_not_called()
