"""
Tests completing the Guest Requests module to full production coverage:
the extend_stay approval workflow (the real booking/finance integration
point), notification trigger attempts, and dashboard/reports integration.
Run with: pytest tests/test_guest_requests_completion.py -v
"""
import uuid
from datetime import date, timedelta

import pytest
from fastapi.testclient import TestClient

from app.main import app
from app.db.session import SessionLocal
from app.modules.portal.models import GuestRequest

client = TestClient(app)

from conftest import ADMIN_EMAIL, ADMIN_PASSWORD  # single source of truth, see conftest.py


@pytest.fixture(scope="module")
def headers():
    resp = client.post("/api/v1/auth/login", json={"email": ADMIN_EMAIL, "password": ADMIN_PASSWORD})
    assert resp.status_code == 200
    return {"Authorization": f"Bearer {resp.json()['access_token']}"}


def _make_booking(headers, rate=2000, days_offset=4000, nights=3):
    prop = client.post("/api/v1/properties", headers=headers, json={"name": f"GRProp {uuid.uuid4().hex[:6]}", "address": "Test", "state_code": "34"}).json()
    unit = client.post("/api/v1/units", headers=headers, json={"property_id": prop["id"], "name": f"GRUnit {uuid.uuid4().hex[:6]}", "base_rate": rate}).json()
    guest = client.post("/api/v1/guests", headers=headers, json={"name": "Extension Test Guest", "phone": f"93{uuid.uuid4().int % 10**8:08d}"}).json()
    check_in = date.today() + timedelta(days=days_offset)
    check_out = check_in + timedelta(days=nights)
    booking = client.post("/api/v1/bookings", headers=headers, json={
        "unit_id": unit["id"], "guest_id": guest["id"], "check_in": check_in.isoformat(), "check_out": check_out.isoformat(),
    }).json()
    return booking, guest, unit


def _make_extend_request(guest_id, booking_id, requested_check_out):
    db = SessionLocal()
    try:
        req = GuestRequest(
            guest_id=uuid.UUID(guest_id), booking_id=uuid.UUID(booking_id), request_type="extend_stay",
            description="Please extend my stay", requested_check_out_date=requested_check_out,
        )
        db.add(req)
        db.commit()
        db.refresh(req)
        return str(req.id)
    finally:
        db.close()


def test_approve_extension_actually_changes_booking_and_revenue(headers):
    """
    The real integration test: approving an extend_stay request must
    genuinely change the booking's check_out date (not just mark the
    request resolved), and that change must be reflected in revenue
    calculations automatically, since nothing else in the system computes
    revenue any other way than from Booking.check_in/check_out.
    """
    booking, guest, unit = _make_booking(headers, rate=2000, nights=3)
    original_check_out = booking["check_out"]
    new_check_out = (date.fromisoformat(original_check_out) + timedelta(days=2)).isoformat()

    request_id = _make_extend_request(guest["id"], booking["id"], date.fromisoformat(new_check_out))

    resp = client.post(f"/api/v1/guest-requests/{request_id}/approve-extension", headers=headers)
    assert resp.status_code == 200, resp.text
    data = resp.json()
    assert data["status"] == "resolved"
    assert data["resolved_at"] is not None

    updated_booking = next(b for b in client.get("/api/v1/bookings", headers=headers, params={"unit_id": unit["id"]}).json() if b["id"] == booking["id"])
    assert updated_booking["check_out"] == new_check_out

    check_in_date = date.fromisoformat(booking["check_in"])
    pnl = client.get(
        f"/api/v1/finance/apartment-pnl/{unit['id']}",
        headers=headers, params={"period_year": check_in_date.year, "period_month": check_in_date.month},
    ).json()
    expected_nights = (date.fromisoformat(new_check_out) - check_in_date).days
    assert pnl["revenue"] == 2000.0 * expected_nights


def test_approve_extension_logs_dual_audit_entries(headers):
    booking, guest, unit = _make_booking(headers, days_offset=4100)
    new_check_out = (date.fromisoformat(booking["check_out"]) + timedelta(days=1)).isoformat()
    request_id = _make_extend_request(guest["id"], booking["id"], date.fromisoformat(new_check_out))

    client.post(f"/api/v1/guest-requests/{request_id}/approve-extension", headers=headers)

    booking_logs = client.get("/api/v1/audit/logs", headers=headers, params={"action": "booking.edit"}).json()
    assert any(l["entity_id"] == booking["id"] and "guest request" in (l["details"] or "") for l in booking_logs)

    request_logs = client.get("/api/v1/audit/logs", headers=headers, params={"action": "guest_request.update"}).json()
    assert any(l["entity_id"] == request_id and "Extension approved" in (l["details"] or "") for l in request_logs)


def test_approve_extension_prevents_double_booking(headers):
    booking, guest, unit = _make_booking(headers, days_offset=4200, nights=2)
    blocking_check_in = date.fromisoformat(booking["check_out"]) + timedelta(days=1)
    other_guest = client.post("/api/v1/guests", headers=headers, json={"name": "Blocking Guest", "phone": f"94{uuid.uuid4().int % 10**8:08d}"}).json()
    client.post("/api/v1/bookings", headers=headers, json={
        "unit_id": unit["id"], "guest_id": other_guest["id"],
        "check_in": blocking_check_in.isoformat(), "check_out": (blocking_check_in + timedelta(days=2)).isoformat(),
    })

    requested_check_out = blocking_check_in + timedelta(days=1)
    request_id = _make_extend_request(guest["id"], booking["id"], requested_check_out)

    resp = client.post(f"/api/v1/guest-requests/{request_id}/approve-extension", headers=headers)
    assert resp.status_code == 409, resp.text

    still_original = next(b for b in client.get("/api/v1/bookings", headers=headers, params={"unit_id": unit["id"]}).json() if b["id"] == booking["id"])
    assert still_original["check_out"] == booking["check_out"]


def test_approve_extension_rejects_wrong_request_type(headers):
    guest = client.post("/api/v1/guests", headers=headers, json={"name": "Wrong Type Guest", "phone": f"95{uuid.uuid4().int % 10**8:08d}"}).json()
    db = SessionLocal()
    try:
        req = GuestRequest(guest_id=uuid.UUID(guest["id"]), request_type="service", description="Extra towels")
        db.add(req)
        db.commit()
        db.refresh(req)
        request_id = str(req.id)
    finally:
        db.close()

    resp = client.post(f"/api/v1/guest-requests/{request_id}/approve-extension", headers=headers)
    assert resp.status_code == 400


def test_approve_extension_rejects_date_not_after_current_checkout(headers):
    booking, guest, unit = _make_booking(headers, days_offset=4300)
    earlier_date = date.fromisoformat(booking["check_in"]) + timedelta(days=1)
    request_id = _make_extend_request(guest["id"], booking["id"], earlier_date)

    resp = client.post(f"/api/v1/guest-requests/{request_id}/approve-extension", headers=headers)
    assert resp.status_code == 400


def test_approve_extension_requires_staff_role():
    admin_login = client.post("/api/v1/auth/login", json={"email": ADMIN_EMAIL, "password": ADMIN_PASSWORD})
    admin_headers = {"Authorization": f"Bearer {admin_login.json()['access_token']}"}
    booking, guest, unit = _make_booking(admin_headers, days_offset=4400)
    new_check_out = date.fromisoformat(booking["check_out"]) + timedelta(days=1)
    request_id = _make_extend_request(guest["id"], booking["id"], new_check_out)

    resp = client.post(f"/api/v1/guest-requests/{request_id}/approve-extension")
    assert resp.status_code == 401


# --- Phase 7: approval state safety ---

def test_approve_extension_rejects_already_resolved_request(headers):
    """The core Phase 7 gap: approving the same request twice must not
    silently re-run the extension a second time."""
    booking, guest, unit = _make_booking(headers, days_offset=4500)
    new_check_out = (date.fromisoformat(booking["check_out"]) + timedelta(days=2)).isoformat()
    request_id = _make_extend_request(guest["id"], booking["id"], date.fromisoformat(new_check_out))

    first = client.post(f"/api/v1/guest-requests/{request_id}/approve-extension", headers=headers)
    assert first.status_code == 200, first.text

    second = client.post(f"/api/v1/guest-requests/{request_id}/approve-extension", headers=headers)
    assert second.status_code == 409, second.text

    still_extended_once = next(b for b in client.get("/api/v1/bookings", headers=headers, params={"unit_id": unit["id"]}).json() if b["id"] == booking["id"])
    assert still_extended_once["check_out"] == new_check_out


def test_approve_extension_rejects_cancelled_booking(headers):
    booking, guest, unit = _make_booking(headers, days_offset=4600)
    cancel = client.patch(f"/api/v1/bookings/{booking['id']}/status", headers=headers, params={"new_status": "cancelled"})
    assert cancel.status_code == 200, cancel.text

    new_check_out = date.fromisoformat(booking["check_out"]) + timedelta(days=2)
    request_id = _make_extend_request(guest["id"], booking["id"], new_check_out)

    resp = client.post(f"/api/v1/guest-requests/{request_id}/approve-extension", headers=headers)
    assert resp.status_code == 409, resp.text

    still_cancelled = next(b for b in client.get("/api/v1/bookings", headers=headers, params={"unit_id": unit["id"]}).json() if b["id"] == booking["id"])
    assert still_cancelled["status"] == "cancelled"
    assert still_cancelled["check_out"] == booking["check_out"]


def test_rejected_extension_approval_does_not_create_audit_entries(headers):
    """A rejected (already-resolved) approval attempt must not add any new
    booking.edit or guest_request.update audit entries — same "no
    misleading successful-mutation log" rule as Phase 4's user checks."""
    booking, guest, unit = _make_booking(headers, days_offset=4700)
    new_check_out = date.fromisoformat(booking["check_out"]) + timedelta(days=2)
    request_id = _make_extend_request(guest["id"], booking["id"], new_check_out)

    first = client.post(f"/api/v1/guest-requests/{request_id}/approve-extension", headers=headers)
    assert first.status_code == 200

    before = client.get("/api/v1/audit/logs", headers=headers, params={"entity_type": "guest_request", "entity_id": request_id}).json()

    second = client.post(f"/api/v1/guest-requests/{request_id}/approve-extension", headers=headers)
    assert second.status_code == 409

    after = client.get("/api/v1/audit/logs", headers=headers, params={"entity_type": "guest_request", "entity_id": request_id}).json()
    assert len(after) == len(before)


def test_approve_extension_concurrent_requests_only_one_succeeds(headers):
    """Two near-simultaneous approval clicks on the same request must
    result in exactly one successful extension, not two applied in
    sequence and not a lost/duplicated update."""
    import threading

    booking, guest, unit = _make_booking(headers, days_offset=4800)
    new_check_out = (date.fromisoformat(booking["check_out"]) + timedelta(days=2)).isoformat()
    request_id = _make_extend_request(guest["id"], booking["id"], date.fromisoformat(new_check_out))

    results = []

    def do_approve():
        results.append(client.post(f"/api/v1/guest-requests/{request_id}/approve-extension", headers=headers))

    threads = [threading.Thread(target=do_approve), threading.Thread(target=do_approve)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    statuses = sorted(r.status_code for r in results)
    assert statuses == [200, 409], [r.text for r in results]

    final_booking = next(b for b in client.get("/api/v1/bookings", headers=headers, params={"unit_id": unit["id"]}).json() if b["id"] == booking["id"])
    assert final_booking["check_out"] == new_check_out

    booking_logs = client.get("/api/v1/audit/logs", headers=headers, params={"action": "booking.edit"}).json()
    matching = [l for l in booking_logs if l["entity_id"] == booking["id"] and "guest request" in (l["details"] or "")]
    assert len(matching) == 1


def test_resolving_request_attempts_notification(headers):
    """
    SMTP isn't configured in this test environment, so the actual email
    send will fail — the real thing to verify is that the attempt is
    genuinely made and logged, not that it silently does nothing.
    """
    guest = client.post("/api/v1/guests", headers=headers, json={"name": "Notify Test Guest", "phone": f"96{uuid.uuid4().int % 10**8:08d}", "email": "notifytest@example.com"}).json()
    db = SessionLocal()
    try:
        req = GuestRequest(guest_id=uuid.UUID(guest["id"]), request_type="service", description="Test notification")
        db.add(req)
        db.commit()
        db.refresh(req)
        request_id = str(req.id)
    finally:
        db.close()

    resp = client.patch(f"/api/v1/guest-requests/{request_id}", headers=headers, json={"status": "resolved"})
    assert resp.status_code == 200

    logs = client.get("/api/v1/notifications/logs", headers=headers, params={"event_type": "guest_request_resolved"}).json()
    assert any(l["guest_request_id"] == request_id for l in logs)


def test_dashboard_reflects_open_guest_requests(headers):
    before = client.get("/api/v1/reports/dashboard", headers=headers).json()
    guest = client.post("/api/v1/guests", headers=headers, json={"name": "Dashboard Count Guest", "phone": f"97{uuid.uuid4().int % 10**8:08d}"}).json()
    db = SessionLocal()
    try:
        req = GuestRequest(guest_id=uuid.UUID(guest["id"]), request_type="service", description="Dashboard count test")
        db.add(req)
        db.commit()
    finally:
        db.close()

    after = client.get("/api/v1/reports/dashboard", headers=headers).json()
    assert after["guest_requests_open"] >= before["guest_requests_open"] + 1


def test_guest_requests_report_export_pdf_and_excel(headers):
    for fmt in ("pdf", "xlsx"):
        resp = client.get("/api/v1/reports/export", headers=headers, params={"report": "guest_requests", "format": fmt})
        assert resp.status_code == 200, resp.text
        if fmt == "pdf":
            assert resp.content[:4] == b"%PDF"
        else:
            assert len(resp.content) > 100


def test_guests_endpoint_ids_filter_bypasses_pagination_window(headers):
    """
    Regression test for a real bug found during live E2E verification:
    the Guest Requests page previously resolved guest names via a generic
    paginated /guests list (first 200, alphabetical) — with 200+ guests
    in the system, a request from a guest outside that window would
    silently show as unresolved. The ?ids= filter fetches an exact set
    regardless of total guest count or alphabetical position.
    """
    guest = client.post("/api/v1/guests", headers=headers, json={"name": "ZZZ Ids Filter Guest", "phone": f"98{uuid.uuid4().int % 10**8:08d}"}).json()
    resp = client.get("/api/v1/guests", headers=headers, params={"ids": guest["id"]})
    assert resp.status_code == 200
    assert len(resp.json()) == 1
    assert resp.json()[0]["id"] == guest["id"]


def test_guests_ids_filter_accepts_multiple(headers):
    g1 = client.post("/api/v1/guests", headers=headers, json={"name": "Multi Id Guest 1", "phone": f"99{uuid.uuid4().int % 10**8:08d}"}).json()
    g2 = client.post("/api/v1/guests", headers=headers, json={"name": "Multi Id Guest 2", "phone": f"89{uuid.uuid4().int % 10**8:08d}"}).json()
    resp = client.get("/api/v1/guests", headers=headers, params={"ids": f"{g1['id']},{g2['id']}"})
    assert resp.status_code == 200
    returned_ids = {g["id"] for g in resp.json()}
    assert returned_ids == {g1["id"], g2["id"]}
