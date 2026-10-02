"""
Tests for Reservation Management enhancements: edit, delete (with
financial-record protection), attachments, and audit logging.
Run with: pytest tests/test_booking_management.py -v
"""
import io
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


def _make_unit(headers, rate=2000, max_occupancy=2):
    prop = client.post("/api/v1/properties", headers=headers, json={"name": f"BkProp {uuid.uuid4().hex[:6]}", "address": "Test", "state_code": "34"}).json()
    unit = client.post("/api/v1/units", headers=headers, json={"property_id": prop["id"], "name": f"BkUnit {uuid.uuid4().hex[:6]}", "base_rate": rate, "max_occupancy": max_occupancy}).json()
    return unit["id"]


def _make_booking(headers, unit_id=None, rate=2000, days_offset=100):
    unit_id = unit_id or _make_unit(headers, rate)
    guest = client.post("/api/v1/guests", headers=headers, json={"name": "Booking Mgmt Guest", "phone": f"93{uuid.uuid4().int % 10**8:08d}"}).json()
    check_in = date.today() + timedelta(days=days_offset)
    check_out = check_in + timedelta(days=2)
    booking = client.post("/api/v1/bookings", headers=headers, json={
        "unit_id": unit_id, "guest_id": guest["id"],
        "check_in": check_in.isoformat(), "check_out": check_out.isoformat(),
    }).json()
    return booking, unit_id


def test_edit_booking_rate_and_dates(headers):
    booking, unit_id = _make_booking(headers)
    new_check_in = (date.today() + timedelta(days=200)).isoformat()
    new_check_out = (date.today() + timedelta(days=203)).isoformat()

    resp = client.patch(f"/api/v1/bookings/{booking['id']}", headers=headers, json={
        "check_in": new_check_in, "check_out": new_check_out, "rate_applied": 3500, "reason": "Guest requested new dates",
    })
    assert resp.status_code == 200, resp.text
    assert resp.json()["check_in"] == new_check_in
    assert resp.json()["check_out"] == new_check_out
    assert resp.json()["rate_applied"] == 3500.0


def test_edit_booking_logs_audit_with_before_after(headers):
    booking, _ = _make_booking(headers)
    client.patch(f"/api/v1/bookings/{booking['id']}", headers=headers, json={"rate_applied": 9999, "reason": "Test audit reason"})

    logs = client.get("/api/v1/audit/logs", headers=headers, params={"action": "booking.edit"}).json()
    match = next((l for l in logs if l["entity_id"] == booking["id"]), None)
    assert match is not None
    assert "Test audit reason" in match["details"]
    assert "9999" in match["new_value"]
    assert "rate=" in match["previous_value"]


def test_edit_booking_rejects_overlap(headers):
    unit_id = _make_unit(headers)
    booking_a, _ = _make_booking(headers, unit_id=unit_id, days_offset=300)
    booking_b, _ = _make_booking(headers, unit_id=unit_id, days_offset=310)

    resp = client.patch(f"/api/v1/bookings/{booking_b['id']}", headers=headers, json={
        "check_in": booking_a["check_in"], "check_out": booking_a["check_out"],
    })
    assert resp.status_code == 409


def test_edit_booking_invalid_guest_rejected(headers):
    booking, _ = _make_booking(headers)
    resp = client.patch(f"/api/v1/bookings/{booking['id']}", headers=headers, json={"guest_id": str(uuid.uuid4())})
    assert resp.status_code == 404


def test_delete_booking_with_no_payments_succeeds(headers):
    booking, _ = _make_booking(headers)
    resp = client.delete(f"/api/v1/bookings/{booking['id']}", headers=headers)
    assert resp.status_code == 204

    remaining = client.get("/api/v1/bookings", headers=headers).json()
    assert not any(b["id"] == booking["id"] for b in remaining)


def test_delete_booking_logs_audit(headers):
    booking, _ = _make_booking(headers)
    client.delete(f"/api/v1/bookings/{booking['id']}", headers=headers)

    logs = client.get("/api/v1/audit/logs", headers=headers, params={"action": "booking.delete"}).json()
    assert any(l["entity_id"] == booking["id"] for l in logs)


def test_delete_booking_with_payment_is_blocked(headers):
    booking, _ = _make_booking(headers)
    client.post("/api/v1/payments", headers=headers, json={"booking_id": booking["id"], "amount": 500, "mode": "upi", "reference_no": "DELTEST"})

    resp = client.delete(f"/api/v1/bookings/{booking['id']}", headers=headers)
    assert resp.status_code == 409
    assert "payment" in resp.json()["detail"].lower()

    # Query directly by guest_id rather than scanning the full (now
    # correctly paginated/bounded) list — this is also a more realistic
    # way for a real caller to check "does this specific booking still
    # exist" than fetching everything and searching client-side.
    remaining = client.get("/api/v1/bookings", headers=headers, params={"guest_id": booking["guest_id"]}).json()
    assert any(b["id"] == booking["id"] for b in remaining)


def test_staff_cannot_delete_booking():
    admin_login = client.post("/api/v1/auth/login", json={"email": ADMIN_EMAIL, "password": ADMIN_PASSWORD})
    admin_headers = {"Authorization": f"Bearer {admin_login.json()['access_token']}"}
    email = f"bkstaff_{uuid.uuid4().hex[:6]}@sriapartmenthomestay.com"
    client.post("/api/v1/auth/register", headers=admin_headers, json={"name": "Bk Staff", "email": email, "password": "Pass12345!", "role": "staff"})
    staff_login = client.post("/api/v1/auth/login", json={"email": email, "password": "Pass12345!"})
    staff_headers = {"Authorization": f"Bearer {staff_login.json()['access_token']}"}

    booking, _ = _make_booking(admin_headers)
    resp = client.delete(f"/api/v1/bookings/{booking['id']}", headers=staff_headers)
    assert resp.status_code == 403


def test_upload_list_download_delete_attachment(headers):
    booking, _ = _make_booking(headers)

    fake_file = io.BytesIO(b"fake ID proof content")
    upload_resp = client.post(
        f"/api/v1/bookings/{booking['id']}/attachments",
        headers=headers,
        files={"file": ("id_proof.pdf", fake_file, "application/pdf")},
    )
    assert upload_resp.status_code == 201, upload_resp.text
    attachment = upload_resp.json()
    assert attachment["filename"] == "id_proof.pdf"

    listing = client.get(f"/api/v1/bookings/{booking['id']}/attachments", headers=headers).json()
    assert any(a["id"] == attachment["id"] for a in listing)

    download = client.get(f"/api/v1/bookings/{booking['id']}/attachments/{attachment['id']}/download", headers=headers)
    assert download.status_code == 200
    assert download.content == b"fake ID proof content"

    logs = client.get("/api/v1/audit/logs", headers=headers, params={"action": "booking.attachment_upload"}).json()
    assert any(l["entity_id"] == booking["id"] for l in logs)

    delete_resp = client.delete(f"/api/v1/bookings/{booking['id']}/attachments/{attachment['id']}", headers=headers)
    assert delete_resp.status_code == 204
    listing_after = client.get(f"/api/v1/bookings/{booking['id']}/attachments", headers=headers).json()
    assert not any(a["id"] == attachment["id"] for a in listing_after)


def test_booking_creation_logs_audit(headers):
    booking, _ = _make_booking(headers)
    logs = client.get("/api/v1/audit/logs", headers=headers, params={"action": "booking.create"}).json()
    assert any(l["entity_id"] == booking["id"] for l in logs)


def test_status_change_logs_audit(headers):
    booking, _ = _make_booking(headers)
    client.patch(f"/api/v1/bookings/{booking['id']}/status", headers=headers, params={"new_status": "checked_in"})

    logs = client.get("/api/v1/audit/logs", headers=headers, params={"action": "booking.status_change"}).json()
    match = next((l for l in logs if l["entity_id"] == booking["id"]), None)
    assert match is not None
    assert "confirmed -> checked_in" in match["details"]


def test_booking_timeline_shows_chronological_events(headers):
    booking, _ = _make_booking(headers)
    client.patch(f"/api/v1/bookings/{booking['id']}", headers=headers, json={"rate_applied": 2500, "reason": "Timeline test"})
    client.patch(f"/api/v1/bookings/{booking['id']}/status", headers=headers, params={"new_status": "checked_in"})

    timeline = client.get(f"/api/v1/bookings/{booking['id']}/timeline", headers=headers).json()
    actions = [e["action"] for e in timeline["events"]]
    assert actions == ["booking.create", "booking.edit", "booking.status_change"]
    assert timeline["events"][1]["new_value"] is not None
    assert "2500" in timeline["events"][1]["new_value"]


def test_booking_timeline_unknown_booking_404s(headers):
    resp = client.get(f"/api/v1/bookings/{uuid.uuid4()}/timeline", headers=headers)
    assert resp.status_code == 404


def test_attachment_upload_rejects_oversized_file(headers):
    booking, _ = _make_booking(headers)
    oversized = io.BytesIO(b"x" * (11 * 1024 * 1024))  # 11MB, over the 10MB cap
    resp = client.post(
        f"/api/v1/bookings/{booking['id']}/attachments",
        headers=headers,
        files={"file": ("huge.jpg", oversized, "image/jpeg")},
    )
    assert resp.status_code == 413


def test_attachment_upload_rejects_empty_file(headers):
    booking, _ = _make_booking(headers)
    empty = io.BytesIO(b"")
    resp = client.post(
        f"/api/v1/bookings/{booking['id']}/attachments",
        headers=headers,
        files={"file": ("empty.jpg", empty, "image/jpeg")},
    )
    assert resp.status_code == 400


# --- Invoice-edit guard: once a booking has an invoice, dates/unit/rate
# (the fields that feed GST/total calculation) can no longer be edited,
# since that would silently desynchronize the invoice from the booking. ---

def test_edit_booking_blocked_after_invoice_for_dates(headers):
    booking, _ = _make_booking(headers)
    gen = client.post(f"/api/v1/invoices/generate/{booking['id']}", headers=headers)
    assert gen.status_code == 201, gen.text

    new_check_in = (date.today() + timedelta(days=500)).isoformat()
    new_check_out = (date.today() + timedelta(days=503)).isoformat()
    resp = client.patch(f"/api/v1/bookings/{booking['id']}", headers=headers, json={"check_in": new_check_in, "check_out": new_check_out})
    assert resp.status_code == 409
    assert "invoice" in resp.json()["detail"].lower()


def test_edit_booking_blocked_after_invoice_for_rate(headers):
    booking, _ = _make_booking(headers)
    client.post(f"/api/v1/invoices/generate/{booking['id']}", headers=headers)

    resp = client.patch(f"/api/v1/bookings/{booking['id']}", headers=headers, json={"rate_applied": 5000})
    assert resp.status_code == 409
    assert "invoice" in resp.json()["detail"].lower()


def test_edit_booking_blocked_after_invoice_for_unit(headers):
    booking, _ = _make_booking(headers)
    other_unit_id = _make_unit(headers)
    client.post(f"/api/v1/invoices/generate/{booking['id']}", headers=headers)

    resp = client.patch(f"/api/v1/bookings/{booking['id']}", headers=headers, json={"unit_id": other_unit_id})
    assert resp.status_code == 409
    assert "invoice" in resp.json()["detail"].lower()


def test_edit_booking_still_allowed_after_invoice_for_non_invoice_fields(headers):
    """The guard only blocks the fields that feed GST/total calculation —
    everything else (notes, guest count, arrival time, etc.) must remain
    editable even after an invoice exists."""
    unit_id = _make_unit(headers, max_occupancy=3)
    booking, _ = _make_booking(headers, unit_id=unit_id)
    client.post(f"/api/v1/invoices/generate/{booking['id']}", headers=headers)

    resp = client.patch(f"/api/v1/bookings/{booking['id']}", headers=headers, json={"internal_notes": "Post-invoice note", "adults": 3})
    assert resp.status_code == 200, resp.text
    assert resp.json()["internal_notes"] == "Post-invoice note"
    assert resp.json()["adults"] == 3


def test_edit_booking_dates_still_works_before_invoice(headers):
    """Regression: the guard must not fire when no invoice exists yet."""
    booking, _ = _make_booking(headers)
    new_check_in = (date.today() + timedelta(days=600)).isoformat()
    new_check_out = (date.today() + timedelta(days=602)).isoformat()
    resp = client.patch(f"/api/v1/bookings/{booking['id']}", headers=headers, json={"check_in": new_check_in, "check_out": new_check_out})
    assert resp.status_code == 200, resp.text


# --- Cancel (reuses the existing status-transition endpoint) ---

def test_cancel_booking_valid_transition_from_confirmed(headers):
    booking, _ = _make_booking(headers)
    resp = client.patch(f"/api/v1/bookings/{booking['id']}/status", headers=headers, params={"new_status": "cancelled"})
    assert resp.status_code == 200, resp.text
    assert resp.json()["status"] == "cancelled"


def test_cancel_booking_rejected_from_checked_in(headers):
    booking, _ = _make_booking(headers)
    client.patch(f"/api/v1/bookings/{booking['id']}/status", headers=headers, params={"new_status": "checked_in"})
    resp = client.patch(f"/api/v1/bookings/{booking['id']}/status", headers=headers, params={"new_status": "cancelled"})
    assert resp.status_code == 409


def test_cancel_booking_rejected_when_already_cancelled(headers):
    booking, _ = _make_booking(headers)
    client.patch(f"/api/v1/bookings/{booking['id']}/status", headers=headers, params={"new_status": "cancelled"})
    resp = client.patch(f"/api/v1/bookings/{booking['id']}/status", headers=headers, params={"new_status": "cancelled"})
    assert resp.status_code == 409


def test_cancel_booking_logs_audit(headers):
    booking, _ = _make_booking(headers)
    client.patch(f"/api/v1/bookings/{booking['id']}/status", headers=headers, params={"new_status": "cancelled"})

    logs = client.get("/api/v1/audit/logs", headers=headers, params={"action": "booking.status_change"}).json()
    match = next((l for l in logs if l["entity_id"] == booking["id"] and "cancelled" in l["details"]), None)
    assert match is not None
    assert "confirmed -> cancelled" in match["details"]


def test_cancel_booking_preserves_existing_payment(headers):
    """Cancelling must not touch existing financial records — the payment
    stays exactly as it was; refunding (if needed) is a separate, explicit
    action against the payment itself."""
    booking, _ = _make_booking(headers)
    payment = client.post("/api/v1/payments", headers=headers, json={"booking_id": booking["id"], "amount": 1000, "mode": "upi", "reference_no": "CANCELPAYTEST"}).json()

    client.patch(f"/api/v1/bookings/{booking['id']}/status", headers=headers, params={"new_status": "cancelled"})

    payments_after = client.get(f"/api/v1/payments/{booking['id']}", headers=headers).json()
    match = next((p for p in payments_after if p["id"] == payment["id"]), None)
    assert match is not None
    assert match["amount"] == 1000.0
    assert match["status"] == "completed"


# --- Delete blockers not yet covered (Payment is already tested above) ---

def test_delete_booking_with_invoice_is_blocked(headers):
    booking, _ = _make_booking(headers)
    gen = client.post(f"/api/v1/invoices/generate/{booking['id']}", headers=headers)
    assert gen.status_code == 201, gen.text

    resp = client.delete(f"/api/v1/bookings/{booking['id']}", headers=headers)
    assert resp.status_code == 409
    assert "invoice" in resp.json()["detail"].lower()


def test_delete_booking_with_security_deposit_is_blocked(headers):
    booking, _ = _make_booking(headers)
    dep = client.post("/api/v1/finance/deposits", headers=headers, json={"booking_id": booking["id"], "amount": 2000})
    assert dep.status_code == 201, dep.text

    resp = client.delete(f"/api/v1/bookings/{booking['id']}", headers=headers)
    assert resp.status_code == 409
    assert "security deposit" in resp.json()["detail"].lower()


def test_delete_booking_with_ota_commission_is_blocked(headers):
    booking, _ = _make_booking(headers)
    comm = client.post("/api/v1/finance/ota-commissions", headers=headers, json={
        "booking_id": booking["id"], "channel": "booking_com", "commission_percent": 15, "commission_amount": 300,
    })
    assert comm.status_code == 201, comm.text

    resp = client.delete(f"/api/v1/bookings/{booking['id']}", headers=headers)
    assert resp.status_code == 409
    assert "ota commission" in resp.json()["detail"].lower()


# --- Edit: notes fields ---

def test_edit_booking_notes(headers):
    booking, _ = _make_booking(headers)
    resp = client.patch(f"/api/v1/bookings/{booking['id']}", headers=headers, json={
        "internal_notes": "VIP guest, upgrade if possible", "special_requests": "Late checkout requested",
    })
    assert resp.status_code == 200, resp.text
    assert resp.json()["internal_notes"] == "VIP guest, upgrade if possible"
    assert resp.json()["special_requests"] == "Late checkout requested"


# --- Edit: stale-update / optimistic concurrency protection ---

def test_edit_booking_without_expected_updated_at_still_works(headers):
    """Backward compatibility: omitting the concurrency token entirely
    performs the edit unchecked, same as before this field existed."""
    booking, _ = _make_booking(headers)
    resp = client.patch(f"/api/v1/bookings/{booking['id']}", headers=headers, json={"rate_applied": 4321})
    assert resp.status_code == 200, resp.text
    assert resp.json()["rate_applied"] == 4321.0


def test_edit_booking_with_fresh_expected_updated_at_succeeds(headers):
    booking, _ = _make_booking(headers)
    resp = client.patch(f"/api/v1/bookings/{booking['id']}", headers=headers, json={
        "rate_applied": 4500, "expected_updated_at": booking["updated_at"],
    })
    assert resp.status_code == 200, resp.text
    assert resp.json()["rate_applied"] == 4500.0


def test_edit_booking_with_stale_expected_updated_at_rejected(headers):
    """Simulates two staff editing the same booking: the first save
    succeeds and bumps updated_at; the second save, still holding the
    booking's *original* updated_at, must be rejected rather than silently
    overwriting the first staff member's change."""
    booking, _ = _make_booking(headers)
    stale_token = booking["updated_at"]

    first = client.patch(f"/api/v1/bookings/{booking['id']}", headers=headers, json={
        "rate_applied": 5000, "expected_updated_at": stale_token,
    })
    assert first.status_code == 200, first.text

    second = client.patch(f"/api/v1/bookings/{booking['id']}", headers=headers, json={
        "rate_applied": 6000, "expected_updated_at": stale_token,
    })
    assert second.status_code == 409, second.text

    # The first staff member's change must have stuck, not been overwritten.
    current = client.get("/api/v1/bookings", headers=headers, params={"guest_id": booking["guest_id"]}).json()
    match = next(b for b in current if b["id"] == booking["id"])
    assert match["rate_applied"] == 5000.0


# --- Archive / restore (safe soft-delete) ---

def test_archive_booking_removes_from_active_list(headers):
    booking, _ = _make_booking(headers)
    resp = client.post(f"/api/v1/bookings/{booking['id']}/archive", headers=headers, json={"reason": "Duplicate entry"})
    assert resp.status_code == 200, resp.text
    assert resp.json()["is_archived"] is True

    active = client.get("/api/v1/bookings", headers=headers, params={"guest_id": booking["guest_id"]}).json()
    assert not any(b["id"] == booking["id"] for b in active)

    with_archived = client.get("/api/v1/bookings", headers=headers, params={"guest_id": booking["guest_id"], "include_archived": True}).json()
    assert any(b["id"] == booking["id"] for b in with_archived)


def test_archive_booking_logs_audit(headers):
    booking, _ = _make_booking(headers)
    client.post(f"/api/v1/bookings/{booking['id']}/archive", headers=headers, json={"reason": "Test archive reason"})

    logs = client.get("/api/v1/audit/logs", headers=headers, params={"action": "booking.archive"}).json()
    match = next((l for l in logs if l["entity_id"] == booking["id"]), None)
    assert match is not None
    assert "Test archive reason" in match["details"]


def test_archive_booking_is_idempotent(headers):
    booking, _ = _make_booking(headers)
    first = client.post(f"/api/v1/bookings/{booking['id']}/archive", headers=headers, json={"reason": "First"})
    assert first.status_code == 200

    second = client.post(f"/api/v1/bookings/{booking['id']}/archive", headers=headers, json={"reason": "Second"})
    assert second.status_code == 200
    assert second.json()["is_archived"] is True

    # The no-op second call must not have overwritten the original reason.
    logs = client.get("/api/v1/audit/logs", headers=headers, params={"action": "booking.archive"}).json()
    matches = [l for l in logs if l["entity_id"] == booking["id"]]
    assert len(matches) == 1
    assert "First" in matches[0]["details"]


def test_restore_booking_returns_to_active_list(headers):
    booking, _ = _make_booking(headers)
    client.post(f"/api/v1/bookings/{booking['id']}/archive", headers=headers, json={"reason": "temp"})

    resp = client.post(f"/api/v1/bookings/{booking['id']}/restore", headers=headers)
    assert resp.status_code == 200, resp.text
    assert resp.json()["is_archived"] is False

    active = client.get("/api/v1/bookings", headers=headers, params={"guest_id": booking["guest_id"]}).json()
    assert any(b["id"] == booking["id"] for b in active)


def test_restore_booking_is_idempotent(headers):
    booking, _ = _make_booking(headers)
    resp = client.post(f"/api/v1/bookings/{booking['id']}/restore", headers=headers)
    assert resp.status_code == 200
    assert resp.json()["is_archived"] is False


def test_archive_booking_preserves_payment_and_can_still_be_deleted_is_blocked(headers):
    """Archiving a booking with a real payment must not touch the
    payment, and the existing hard-delete financial guard must still apply
    to an archived booking exactly as it does to an active one."""
    booking, _ = _make_booking(headers)
    client.post("/api/v1/payments", headers=headers, json={"booking_id": booking["id"], "amount": 750, "mode": "cash"})
    client.post(f"/api/v1/bookings/{booking['id']}/archive", headers=headers, json={"reason": "Old record"})

    payments = client.get(f"/api/v1/payments/{booking['id']}", headers=headers).json()
    assert any(p["amount"] == 750 for p in payments)

    delete_resp = client.delete(f"/api/v1/bookings/{booking['id']}", headers=headers)
    assert delete_resp.status_code == 409


def test_staff_cannot_archive_booking():
    admin_login = client.post("/api/v1/auth/login", json={"email": ADMIN_EMAIL, "password": ADMIN_PASSWORD})
    admin_headers = {"Authorization": f"Bearer {admin_login.json()['access_token']}"}
    email = f"bkstaff_{uuid.uuid4().hex[:6]}@sriapartmenthomestay.com"
    client.post("/api/v1/auth/register", headers=admin_headers, json={"name": "Bk Staff 2", "email": email, "password": "Pass12345!", "role": "staff"})
    staff_login = client.post("/api/v1/auth/login", json={"email": email, "password": "Pass12345!"})
    staff_headers = {"Authorization": f"Bearer {staff_login.json()['access_token']}"}

    booking, _ = _make_booking(admin_headers)
    resp = client.post(f"/api/v1/bookings/{booking['id']}/archive", headers=staff_headers, json={})
    assert resp.status_code == 403


def test_archive_booking_unauthenticated_rejected(headers):
    booking, _ = _make_booking(headers)
    resp = client.post(f"/api/v1/bookings/{booking['id']}/archive", json={})
    assert resp.status_code == 401


def test_archive_unknown_booking_404s(headers):
    resp = client.post(f"/api/v1/bookings/{uuid.uuid4()}/archive", headers=headers, json={})
    assert resp.status_code == 404


def test_restore_booking_logs_audit(headers):
    booking, _ = _make_booking(headers)
    client.post(f"/api/v1/bookings/{booking['id']}/archive", headers=headers, json={"reason": "temp"})
    client.post(f"/api/v1/bookings/{booking['id']}/restore", headers=headers)

    logs = client.get("/api/v1/audit/logs", headers=headers, params={"action": "booking.restore"}).json()
    assert any(l["entity_id"] == booking["id"] for l in logs)


def test_staff_cannot_restore_booking():
    admin_login = client.post("/api/v1/auth/login", json={"email": ADMIN_EMAIL, "password": ADMIN_PASSWORD})
    admin_headers = {"Authorization": f"Bearer {admin_login.json()['access_token']}"}
    email = f"bkstaff_{uuid.uuid4().hex[:6]}@sriapartmenthomestay.com"
    client.post("/api/v1/auth/register", headers=admin_headers, json={"name": "Bk Staff 3", "email": email, "password": "Pass12345!", "role": "staff"})
    staff_login = client.post("/api/v1/auth/login", json={"email": email, "password": "Pass12345!"})
    staff_headers = {"Authorization": f"Bearer {staff_login.json()['access_token']}"}

    booking, _ = _make_booking(admin_headers)
    client.post(f"/api/v1/bookings/{booking['id']}/archive", headers=admin_headers, json={})
    resp = client.post(f"/api/v1/bookings/{booking['id']}/restore", headers=staff_headers)
    assert resp.status_code == 403


def test_restore_booking_unauthenticated_rejected(headers):
    booking, _ = _make_booking(headers)
    client.post(f"/api/v1/bookings/{booking['id']}/archive", headers=headers, json={})
    resp = client.post(f"/api/v1/bookings/{booking['id']}/restore")
    assert resp.status_code == 401


def test_restore_unknown_booking_404s(headers):
    resp = client.post(f"/api/v1/bookings/{uuid.uuid4()}/restore", headers=headers)
    assert resp.status_code == 404
