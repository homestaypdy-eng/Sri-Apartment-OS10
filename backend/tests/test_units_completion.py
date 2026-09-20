"""
Tests completing the Units module to full production coverage: unit
photos (the one genuine gap found), plus verification that the
Maintenance/Housekeeping links, Availability check, and Occupancy
reporting that already existed actually work as claimed rather than
assumed.
Run with: pytest tests/test_units_completion.py -v
"""
import io
import os
import uuid
from datetime import date, timedelta

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


def _make_unit(headers, rate=2000):
    prop = client.post("/api/v1/properties", headers=headers, json={"name": f"UnitCompProp {uuid.uuid4().hex[:6]}", "address": "Test", "state_code": "34"}).json()
    unit = client.post("/api/v1/units", headers=headers, json={"property_id": prop["id"], "name": f"UnitCompUnit {uuid.uuid4().hex[:6]}", "base_rate": rate}).json()
    return unit["id"]


def test_unit_photo_upload_list_download_delete(headers):
    unit_id = _make_unit(headers)
    fake_file = io.BytesIO(b"fake unit exterior photo")
    upload = client.post(
        f"/api/v1/units/{unit_id}/photos", headers=headers,
        params={"caption": "Exterior view"},
        files={"file": ("exterior.jpg", fake_file, "image/jpeg")},
    )
    assert upload.status_code == 201, upload.text
    photo = upload.json()
    assert photo["caption"] == "Exterior view"

    listing = client.get(f"/api/v1/units/{unit_id}/photos", headers=headers).json()
    assert any(p["id"] == photo["id"] for p in listing)

    download = client.get(f"/api/v1/units/{unit_id}/photos/{photo['id']}/download", headers=headers)
    assert download.status_code == 200
    assert download.content == b"fake unit exterior photo"

    # Photo-delete safety review: prove this is a genuine, complete
    # deletion (DB row + on-disk file), not just a 204 with something left
    # behind — this fake, test-created file/unit is the only thing touched.
    from app.db.session import SessionLocal
    from app.modules.properties.models import UnitPhoto

    db = SessionLocal()
    try:
        stored_path = db.get(UnitPhoto, photo["id"]).file_path
        assert os.path.exists(stored_path), "test setup: uploaded file should exist on disk before delete"
    finally:
        db.close()

    delete_resp = client.delete(f"/api/v1/units/{unit_id}/photos/{photo['id']}", headers=headers)
    assert delete_resp.status_code == 204

    db = SessionLocal()
    try:
        assert db.get(UnitPhoto, photo["id"]) is None, "UnitPhoto database record must be removed after delete"
    finally:
        db.close()
    assert not os.path.exists(stored_path), "physical photo file must be removed from disk after delete"

    listing_after_delete = client.get(f"/api/v1/units/{unit_id}/photos", headers=headers).json()
    assert not any(p["id"] == photo["id"] for p in listing_after_delete), "deleted photo must not appear in a subsequent listing"


def test_unit_photo_rejects_non_image_content_type(headers):
    unit_id = _make_unit(headers)
    fake_file = io.BytesIO(b"<script>alert(1)</script>")
    resp = client.post(
        f"/api/v1/units/{unit_id}/photos", headers=headers,
        files={"file": ("evil.html", fake_file, "text/html")},
    )
    assert resp.status_code == 415


def test_unit_photo_upload_logs_audit(headers):
    unit_id = _make_unit(headers)
    fake_file = io.BytesIO(b"test content")
    client.post(f"/api/v1/units/{unit_id}/photos", headers=headers, files={"file": ("test.jpg", fake_file, "image/jpeg")})
    logs = client.get("/api/v1/audit/logs", headers=headers, params={"action": "unit.photo_upload"}).json()
    assert any(l["entity_id"] == unit_id for l in logs)


def test_unit_photo_404_for_unknown_unit(headers):
    fake_file = io.BytesIO(b"test")
    resp = client.post(f"/api/v1/units/{uuid.uuid4()}/photos", headers=headers, files={"file": ("test.jpg", fake_file, "image/jpeg")})
    assert resp.status_code == 404


def test_maintenance_tickets_filterable_by_unit(headers):
    unit_id = _make_unit(headers)
    other_unit_id = _make_unit(headers)
    client.post("/api/v1/maintenance-tickets", headers=headers, json={"unit_id": unit_id, "description": "Unit-scoped test ticket"})
    client.post("/api/v1/maintenance-tickets", headers=headers, json={"unit_id": other_unit_id, "description": "Different unit ticket"})

    tickets = client.get("/api/v1/maintenance-tickets", headers=headers, params={"unit_id": unit_id}).json()
    assert all(t["unit_id"] == unit_id for t in tickets)
    assert any(t["description"] == "Unit-scoped test ticket" for t in tickets)
    assert not any(t["description"] == "Different unit ticket" for t in tickets)


def test_housekeeping_tasks_filterable_by_unit(headers):
    unit_id = _make_unit(headers)
    other_unit_id = _make_unit(headers)
    client.post("/api/v1/housekeeping-tasks", headers=headers, json={"unit_id": unit_id, "task_type": "routine"})
    client.post("/api/v1/housekeeping-tasks", headers=headers, json={"unit_id": other_unit_id, "task_type": "routine"})

    tasks = client.get("/api/v1/housekeeping-tasks", headers=headers, params={"unit_id": unit_id}).json()
    assert all(t["unit_id"] == unit_id for t in tasks)


def test_availability_check_detects_real_conflict(headers):
    unit_id = _make_unit(headers)
    guest = client.post("/api/v1/guests", headers=headers, json={"name": "Availability Test Guest", "phone": f"91{uuid.uuid4().int % 10**8:08d}"}).json()
    check_in = date.today() + timedelta(days=9500)
    check_out = check_in + timedelta(days=3)
    client.post("/api/v1/bookings", headers=headers, json={"unit_id": unit_id, "guest_id": guest["id"], "check_in": check_in.isoformat(), "check_out": check_out.isoformat()})

    resp = client.get(f"/api/v1/units/{unit_id}/availability", headers=headers, params={"check_in": check_in.isoformat(), "check_out": check_out.isoformat()})
    assert resp.status_code == 200
    assert resp.json()["available"] is False

    free_check_in = check_out + timedelta(days=5)
    free_check_out = free_check_in + timedelta(days=2)
    resp2 = client.get(f"/api/v1/units/{unit_id}/availability", headers=headers, params={"check_in": free_check_in.isoformat(), "check_out": free_check_out.isoformat()})
    assert resp2.json()["available"] is True


def test_unit_performance_report_includes_occupancy(headers):
    unit_id = _make_unit(headers, rate=1000)
    guest = client.post("/api/v1/guests", headers=headers, json={"name": "Occupancy Test Guest", "phone": f"92{uuid.uuid4().int % 10**8:08d}"}).json()
    year, month = 2053, 6
    client.post("/api/v1/bookings", headers=headers, json={"unit_id": unit_id, "guest_id": guest["id"], "check_in": f"{year}-{month:02d}-01", "check_out": f"{year}-{month:02d}-04"})

    date_from = f"{year}-{month:02d}-01"
    date_to = f"{year}-{month:02d}-30"
    performance = client.get("/api/v1/reports/unit-performance", headers=headers, params={"date_from": date_from, "date_to": date_to}).json()
    match = next((u for u in performance if u["unit_id"] == unit_id), None)
    assert match is not None
    assert match["room_nights_sold"] == 3
    assert match["revenue"] == 3000.0
    assert match["occupancy_rate_percent"] > 0


def test_existing_unit_update_still_works(headers):
    unit_id = _make_unit(headers)
    resp = client.patch(f"/api/v1/units/{unit_id}", headers=headers, json={"floor": "3", "amenities": "AC, WiFi, TV"})
    assert resp.status_code == 200
    assert resp.json()["floor"] == "3"
    assert resp.json()["amenities"] == "AC, WiFi, TV"


# ---------------------------------------------------------------------------
# Admin-control phase 2: structured unit details (bedroom/bathroom counts,
# per-unit house rules, per-unit check-in/check-out time).
# ---------------------------------------------------------------------------

def test_new_unit_has_no_structured_details_by_default(headers):
    """Backward compatibility: a unit created before this phase (or without
    these fields set) must come back as None, not some invented default —
    the website's fallback logic depends on genuinely knowing 'unset'."""
    unit_id = _make_unit(headers)
    unit = client.get("/api/v1/units", headers=headers).json()
    this_unit = next(u for u in unit if u["id"] == unit_id)
    assert this_unit["bedroom_count"] is None
    assert this_unit["bathroom_count"] is None
    assert this_unit["house_rules"] is None
    assert this_unit["checkin_time"] is None
    assert this_unit["checkout_time"] is None


def test_unit_structured_details_can_be_set_and_read_back(headers):
    unit_id = _make_unit(headers)
    resp = client.patch(f"/api/v1/units/{unit_id}", headers=headers, json={
        "bedroom_count": 2, "bathroom_count": 2,
        "house_rules": "No smoking indoors. No parties after 10pm.",
        "checkin_time": "12:30 PM", "checkout_time": "11:00 AM",
    })
    assert resp.status_code == 200
    body = resp.json()
    assert body["bedroom_count"] == 2
    assert body["bathroom_count"] == 2
    assert body["house_rules"] == "No smoking indoors. No parties after 10pm."
    assert body["checkin_time"] == "12:30 PM"
    assert body["checkout_time"] == "11:00 AM"


def test_unit_structured_details_reach_the_public_website_endpoint(headers):
    unit_id = _make_unit(headers)
    client.patch(f"/api/v1/units/{unit_id}", headers=headers, json={"bedroom_count": 3, "bathroom_count": 1})
    # Deactivated-by-default new property/unit pairs are still "available"
    # status + active by default from _make_unit, so this is genuinely
    # public-visible without any extra activation step.
    public = client.get("/api/v1/public/units").json()
    public_unit = next((u for u in public if u["id"] == unit_id), None)
    assert public_unit is not None, "expected the newly created unit to be publicly listed"
    assert public_unit["bedroom_count"] == 3
    assert public_unit["bathroom_count"] == 1


def test_negative_bedroom_count_rejected(headers):
    unit_id = _make_unit(headers)
    resp = client.patch(f"/api/v1/units/{unit_id}", headers=headers, json={"bedroom_count": -1})
    assert resp.status_code == 422


# ---------------------------------------------------------------------------
# Admin-control phase 2: photo reorder / primary / alt-text (caption).
# ---------------------------------------------------------------------------

def _upload_photo(headers, unit_id, filename="photo.jpg", caption=None):
    fake_file = io.BytesIO(b"fake photo bytes")
    params = {"caption": caption} if caption else {}
    resp = client.post(
        f"/api/v1/units/{unit_id}/photos", headers=headers, params=params,
        files={"file": (filename, fake_file, "image/jpeg")},
    )
    assert resp.status_code == 201, resp.text
    return resp.json()


def test_existing_photos_default_to_unordered_not_primary(headers):
    """Backward compatibility: every photo uploaded before this phase (or
    without explicit sort_order/is_primary) must default to sort_order=0,
    is_primary=false — reproducing today's plain insertion-order display,
    not silently picking a 'random' cover photo."""
    unit_id = _make_unit(headers)
    photo = _upload_photo(headers, unit_id)
    assert photo["sort_order"] == 0
    assert photo["is_primary"] is False


def test_photo_caption_doubles_as_alt_text_and_is_editable(headers):
    unit_id = _make_unit(headers)
    photo = _upload_photo(headers, unit_id)
    resp = client.patch(f"/api/v1/units/{unit_id}/photos/{photo['id']}", headers=headers, json={"caption": "Bright living room"})
    assert resp.status_code == 200
    assert resp.json()["caption"] == "Bright living room"

    public_photos = client.get(f"/api/v1/public/units/{unit_id}/photos").json()
    assert public_photos[0]["caption"] == "Bright living room"


def test_setting_one_photo_primary_unsets_any_other(headers):
    unit_id = _make_unit(headers)
    photo_a = _upload_photo(headers, unit_id, "a.jpg")
    photo_b = _upload_photo(headers, unit_id, "b.jpg")

    r1 = client.patch(f"/api/v1/units/{unit_id}/photos/{photo_a['id']}", headers=headers, json={"is_primary": True})
    assert r1.json()["is_primary"] is True

    r2 = client.patch(f"/api/v1/units/{unit_id}/photos/{photo_b['id']}", headers=headers, json={"is_primary": True})
    assert r2.json()["is_primary"] is True

    listing = client.get(f"/api/v1/units/{unit_id}/photos", headers=headers).json()
    primaries = [p for p in listing if p["is_primary"]]
    assert len(primaries) == 1
    assert primaries[0]["id"] == photo_b["id"]


def test_primary_photo_sorts_first_on_the_public_website(headers):
    unit_id = _make_unit(headers)
    photo_a = _upload_photo(headers, unit_id, "a.jpg")
    photo_b = _upload_photo(headers, unit_id, "b.jpg")
    photo_c = _upload_photo(headers, unit_id, "c.jpg")

    # Make c.jpg both the primary AND not the first-uploaded — proves
    # ordering is driven by is_primary/sort_order, not upload sequence.
    client.patch(f"/api/v1/units/{unit_id}/photos/{photo_c['id']}", headers=headers, json={"is_primary": True})

    public_photos = client.get(f"/api/v1/public/units/{unit_id}/photos").json()
    assert public_photos[0]["id"] == photo_c["id"]


def test_sort_order_controls_display_order_among_non_primary_photos(headers):
    unit_id = _make_unit(headers)
    photo_a = _upload_photo(headers, unit_id, "a.jpg")
    photo_b = _upload_photo(headers, unit_id, "b.jpg")

    # Reorder: b before a, by giving b a lower sort_order.
    client.patch(f"/api/v1/units/{unit_id}/photos/{photo_a['id']}", headers=headers, json={"sort_order": 2})
    client.patch(f"/api/v1/units/{unit_id}/photos/{photo_b['id']}", headers=headers, json={"sort_order": 1})

    public_photos = client.get(f"/api/v1/public/units/{unit_id}/photos").json()
    assert [p["id"] for p in public_photos] == [photo_b["id"], photo_a["id"]]


def test_photo_update_404_for_unknown_photo(headers):
    unit_id = _make_unit(headers)
    resp = client.patch(f"/api/v1/units/{unit_id}/photos/{uuid.uuid4()}", headers=headers, json={"caption": "x"})
    assert resp.status_code == 404


def test_photo_update_logs_audit(headers):
    unit_id = _make_unit(headers)
    photo = _upload_photo(headers, unit_id)
    client.patch(f"/api/v1/units/{unit_id}/photos/{photo['id']}", headers=headers, json={"caption": "Updated"})

    from app.db.session import SessionLocal
    from app.modules.audit.models import AuditLog
    from sqlalchemy import select as sa_select
    db = SessionLocal()
    try:
        logs = db.scalars(sa_select(AuditLog).where(AuditLog.action == "unit.photo_update", AuditLog.entity_id == str(unit_id))).all()
        assert len(logs) >= 1
    finally:
        db.close()
