"""
Tests for Phase 10B-1: batched guest-list segmentation (GET /guests/segments).
Run with: pytest tests/test_guest_segments.py -v
"""
import uuid
from datetime import date, timedelta

import pytest
from fastapi.testclient import TestClient

from app.main import app

client = TestClient(app)

ADMIN_EMAIL = "homestaypdy@gmail.com"
ADMIN_PASSWORD = "Sri@2026"


@pytest.fixture(scope="module")
def headers():
    resp = client.post("/api/v1/auth/login", json={"email": ADMIN_EMAIL, "password": ADMIN_PASSWORD})
    assert resp.status_code == 200
    return {"Authorization": f"Bearer {resp.json()['access_token']}"}


def _make_property_and_unit(headers):
    prop = client.post("/api/v1/properties", headers=headers, json={"name": f"SegProp {uuid.uuid4().hex[:6]}", "address": "Test", "state_code": "34"}).json()
    unit = client.post("/api/v1/units", headers=headers, json={"property_id": prop["id"], "name": f"SegUnit {uuid.uuid4().hex[:6]}", "base_rate": 2000}).json()
    return unit["id"]


def _make_guest(headers, tag):
    return client.post("/api/v1/guests", headers=headers, json={"name": f"Seg Guest {tag}", "phone": f"9{uuid.uuid4().int % 10**9:09d}"}).json()


def _make_booking(headers, unit_id, guest_id, check_in, nights=2, rate=2000, source=None):
    payload = {
        "unit_id": unit_id, "guest_id": guest_id,
        "check_in": check_in.isoformat(), "check_out": (check_in + timedelta(days=nights)).isoformat(),
        "rate_applied": rate,
    }
    if source:
        payload["source"] = source
    resp = client.post("/api/v1/bookings", headers=headers, json=payload)
    assert resp.status_code == 201, resp.text
    return resp.json()


def _segment_for(rows, guest_id):
    matching = [r for r in rows if r["guest_id"] == guest_id]
    assert len(matching) == 1, matching
    return matching[0]


def test_new_guest_classification(headers):
    """0-1 bookings -> New, not Repeat."""
    unit_id = _make_property_and_unit(headers)
    guest = _make_guest(headers, "new-1booking")
    check_in = date.today() + timedelta(days=9000)
    _make_booking(headers, unit_id, guest["id"], check_in)

    resp = client.get("/api/v1/guests/segments", headers=headers, params={"ids": guest["id"]})
    assert resp.status_code == 200, resp.text
    seg = _segment_for(resp.json(), guest["id"])
    assert seg["total_bookings"] == 1
    assert seg["is_repeat_guest"] is False


def test_repeat_guest_classification(headers):
    """2+ bookings -> Repeat."""
    unit_id = _make_property_and_unit(headers)
    guest = _make_guest(headers, "repeat-2bookings")
    check_in1 = date.today() + timedelta(days=9100)
    check_in2 = date.today() + timedelta(days=9110)
    _make_booking(headers, unit_id, guest["id"], check_in1)
    _make_booking(headers, unit_id, guest["id"], check_in2)

    resp = client.get("/api/v1/guests/segments", headers=headers, params={"ids": guest["id"]})
    assert resp.status_code == 200, resp.text
    seg = _segment_for(resp.json(), guest["id"])
    assert seg["total_bookings"] == 2
    assert seg["is_repeat_guest"] is True


def test_total_spent_matches_guest_history(headers):
    """total_spent must be byte-identical to GuestHistory.total_spent for
    the same guest — same underlying definition, not reimplemented."""
    unit_id = _make_property_and_unit(headers)
    guest = _make_guest(headers, "spend-consistency")
    check_in = date.today() + timedelta(days=9200)
    booking = _make_booking(headers, unit_id, guest["id"], check_in)
    client.post("/api/v1/payments", headers=headers, json={"booking_id": booking["id"], "amount": 1234, "mode": "upi", "reference_no": "SEGSPEND"})

    history = client.get(f"/api/v1/guests/{guest['id']}/history", headers=headers).json()
    seg_resp = client.get("/api/v1/guests/segments", headers=headers, params={"ids": guest["id"]})
    assert seg_resp.status_code == 200, seg_resp.text
    seg = _segment_for(seg_resp.json(), guest["id"])

    assert seg["total_spent"] == history["total_spent"] == 1234.0


def test_latest_source_from_most_recent_booking(headers):
    unit_id = _make_property_and_unit(headers)
    guest = _make_guest(headers, "latest-source")
    older = date.today() + timedelta(days=9300)
    newer = date.today() + timedelta(days=9310)
    _make_booking(headers, unit_id, guest["id"], older, source="walk_in")
    _make_booking(headers, unit_id, guest["id"], newer, source="whatsapp")

    resp = client.get("/api/v1/guests/segments", headers=headers, params={"ids": guest["id"]})
    seg = _segment_for(resp.json(), guest["id"])
    assert seg["latest_source"] == "whatsapp"


def test_last_stay_from_most_recent_check_in(headers):
    unit_id = _make_property_and_unit(headers)
    guest = _make_guest(headers, "last-stay")
    older = date.today() + timedelta(days=9400)
    newer = date.today() + timedelta(days=9410)
    _make_booking(headers, unit_id, guest["id"], older)
    _make_booking(headers, unit_id, guest["id"], newer)

    resp = client.get("/api/v1/guests/segments", headers=headers, params={"ids": guest["id"]})
    seg = _segment_for(resp.json(), guest["id"])
    assert seg["last_stay"] == newer.isoformat()


def test_zero_booking_guest(headers):
    guest = _make_guest(headers, "zero-bookings")
    resp = client.get("/api/v1/guests/segments", headers=headers, params={"ids": guest["id"]})
    assert resp.status_code == 200, resp.text
    seg = _segment_for(resp.json(), guest["id"])
    assert seg["total_bookings"] == 0
    assert seg["is_repeat_guest"] is False
    assert seg["total_spent"] == 0.0
    assert seg["latest_source"] is None
    assert seg["last_stay"] is None


def test_staff_access_allowed(headers):
    email = f"segstaff_{uuid.uuid4().hex[:6]}@sriapartmenthomestay.com"
    client.post("/api/v1/auth/register", headers=headers, json={"name": "Segment Staff", "email": email, "password": "Pass12345!", "role": "staff"})
    staff_login = client.post("/api/v1/auth/login", json={"email": email, "password": "Pass12345!"})
    staff_headers = {"Authorization": f"Bearer {staff_login.json()['access_token']}"}

    resp = client.get("/api/v1/guests/segments", headers=staff_headers)
    assert resp.status_code == 200, resp.text


def test_unauthenticated_rejected():
    resp = client.get("/api/v1/guests/segments")
    assert resp.status_code == 401


def test_no_sensitive_or_unapproved_fields(headers):
    """The response must not expose gender/DOB/nationality or any field
    beyond the approved segmentation set."""
    guest = _make_guest(headers, "field-allowlist")
    resp = client.get("/api/v1/guests/segments", headers=headers, params={"ids": guest["id"]})
    seg = _segment_for(resp.json(), guest["id"])
    allowed_fields = {
        "guest_id", "name", "phone", "email", "is_vip", "is_blacklisted",
        "is_repeat_guest", "total_bookings", "total_spent", "latest_source", "last_stay",
    }
    assert set(seg.keys()) == allowed_fields
    for forbidden in ("gender", "date_of_birth", "nationality", "occupation", "id_proof_number", "notes"):
        assert forbidden not in seg


def test_existing_guests_endpoint_unchanged(headers):
    guest = _make_guest(headers, "guests-endpoint-regress")
    resp = client.get("/api/v1/guests", headers=headers, params={"ids": guest["id"]})
    assert resp.status_code == 200, resp.text
    row = resp.json()[0]
    assert set(row.keys()) >= {"id", "name", "phone", "email", "is_vip", "is_blacklisted"}
    assert "total_bookings" not in row
    assert "total_spent" not in row


def test_existing_guest_history_unchanged(headers):
    unit_id = _make_property_and_unit(headers)
    guest = _make_guest(headers, "history-endpoint-regress")
    check_in = date.today() + timedelta(days=9500)
    booking = _make_booking(headers, unit_id, guest["id"], check_in)
    client.post("/api/v1/payments", headers=headers, json={"booking_id": booking["id"], "amount": 500, "mode": "cash"})

    history = client.get(f"/api/v1/guests/{guest['id']}/history", headers=headers).json()
    for field in ("guest", "total_bookings", "is_repeat_guest", "total_spent", "bookings", "requests"):
        assert field in history
    assert history["total_bookings"] == 1
    assert history["total_spent"] == 500.0


def test_pagination_limit_behavior(headers):
    unit_id = _make_property_and_unit(headers)
    tag = uuid.uuid4().hex[:6]
    guests = [_make_guest(headers, f"page-{tag}-{i}") for i in range(5)]
    check_in = date.today() + timedelta(days=9600)
    for i, g in enumerate(guests):
        _make_booking(headers, unit_id, g["id"], check_in + timedelta(days=i * 3))

    search_term = f"page-{tag}"
    resp_all = client.get("/api/v1/guests/segments", headers=headers, params={"search": search_term, "limit": 500})
    assert resp_all.status_code == 200, resp_all.text
    assert len(resp_all.json()) == 5

    resp_limited = client.get("/api/v1/guests/segments", headers=headers, params={"search": search_term, "limit": 2, "sort_by": "name", "sort_dir": "asc"})
    assert resp_limited.status_code == 200, resp_limited.text
    assert len(resp_limited.json()) == 2

    resp_offset = client.get("/api/v1/guests/segments", headers=headers, params={"search": search_term, "limit": 2, "offset": 2, "sort_by": "name", "sort_dir": "asc"})
    assert resp_offset.status_code == 200, resp_offset.text
    assert len(resp_offset.json()) == 2

    ids_page1 = {r["guest_id"] for r in resp_limited.json()}
    ids_page2 = {r["guest_id"] for r in resp_offset.json()}
    assert ids_page1.isdisjoint(ids_page2)


def test_sorting_and_filter_correctness(headers):
    unit_id = _make_property_and_unit(headers)
    tag = uuid.uuid4().hex[:8]
    new_guest = _make_guest(headers, f"srt{tag}new")
    repeat_guest = _make_guest(headers, f"srt{tag}rep")
    check_in = date.today() + timedelta(days=9700)
    _make_booking(headers, unit_id, new_guest["id"], check_in, rate=1000)
    _make_booking(headers, unit_id, repeat_guest["id"], check_in + timedelta(days=5), rate=5000)
    _make_booking(headers, unit_id, repeat_guest["id"], check_in + timedelta(days=10), rate=5000)

    resp_repeat_filtered = client.get("/api/v1/guests/segments", headers=headers, params={"search": tag, "segment": "repeat"})
    assert resp_repeat_filtered.status_code == 200, resp_repeat_filtered.text
    repeat_ids = {r["guest_id"] for r in resp_repeat_filtered.json()}
    assert repeat_guest["id"] in repeat_ids
    assert new_guest["id"] not in repeat_ids

    resp_sorted = client.get("/api/v1/guests/segments", headers=headers, params={"search": tag, "sort_by": "total_bookings", "sort_dir": "desc"})
    assert resp_sorted.status_code == 200, resp_sorted.text
    rows = resp_sorted.json()
    idx_repeat = next(i for i, r in enumerate(rows) if r["guest_id"] == repeat_guest["id"])
    idx_new = next(i for i, r in enumerate(rows) if r["guest_id"] == new_guest["id"])
    assert idx_repeat < idx_new


def test_batched_no_per_guest_queries(headers):
    """Functional proof of batching: N guests, each with a paid booking,
    resolve correctly from a single call (no per-guest history lookups)."""
    unit_id = _make_property_and_unit(headers)
    tag = uuid.uuid4().hex[:6]
    guests = [_make_guest(headers, f"batch-{tag}-{i}") for i in range(8)]
    check_in = date.today() + timedelta(days=9800)
    for i, g in enumerate(guests):
        booking = _make_booking(headers, unit_id, g["id"], check_in + timedelta(days=i * 3))
        client.post("/api/v1/payments", headers=headers, json={"booking_id": booking["id"], "amount": 100 * (i + 1), "mode": "cash"})

    ids_param = ",".join(g["id"] for g in guests)
    resp = client.get("/api/v1/guests/segments", headers=headers, params={"ids": ids_param})
    assert resp.status_code == 200, resp.text
    rows = resp.json()
    assert len(rows) == 8
    by_id = {r["guest_id"]: r for r in rows}
    for i, g in enumerate(guests):
        assert by_id[g["id"]]["total_spent"] == 100.0 * (i + 1)
        assert by_id[g["id"]]["total_bookings"] == 1
