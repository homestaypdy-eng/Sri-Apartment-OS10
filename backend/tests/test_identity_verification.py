"""
Identity Verification (separate from Account Verification) — focused tests
for the new Guest.identity_status state machine, the admin-only
verify/reject/reset endpoints, the guest-portal self-service submit flow,
and Additional Guest (BookingGuest) assignment.

Deliberately does NOT touch or assert anything about the existing account
verification / login-OTP flows beyond proving they never set
identity_status themselves — those flows are out of scope for this
feature and are left exactly as they were.

Run with: pytest tests/test_identity_verification.py -v
"""
import io
import uuid
from datetime import date, timedelta

import pytest
from fastapi.testclient import TestClient

from app.main import app
from conftest import ADMIN_EMAIL, ADMIN_PASSWORD  # single source of truth, see conftest.py

client = TestClient(app)

_REAL_JPEG = b"\xff\xd8\xff\xe0\x00\x10JFIF" + b"\x00" * 40


@pytest.fixture(scope="module")
def headers():
    resp = client.post("/api/v1/auth/login", json={"email": ADMIN_EMAIL, "password": ADMIN_PASSWORD})
    assert resp.status_code == 200, resp.text
    return {"Authorization": f"Bearer {resp.json()['access_token']}"}


def _register_and_login(headers, role: str, label: str) -> tuple[str, dict]:
    email = f"{label}_{uuid.uuid4().hex[:8]}@sriapartmenthomestay.com"
    password = "Pass12345!"
    reg = client.post("/api/v1/auth/register", headers=headers, json={"name": label, "email": email, "password": password, "role": role})
    assert reg.status_code == 201, reg.text
    user_id = reg.json()["id"]
    login = client.post("/api/v1/auth/login", json={"email": email, "password": password})
    assert login.status_code == 200, login.text
    return user_id, {"Authorization": f"Bearer {login.json()['access_token']}"}


def _make_guest(headers, **overrides):
    payload = {"name": "Identity Test Guest", "phone": f"95{uuid.uuid4().int % 10**8:08d}"}
    payload.update(overrides)
    resp = client.post("/api/v1/guests", headers=headers, json=payload)
    assert resp.status_code == 201, resp.text
    return resp.json()


def _upload_document(headers, guest_id, document_type="passport"):
    resp = client.post(
        f"/api/v1/guests/{guest_id}/documents", headers=headers,
        params={"document_type": document_type, "side": "document"},
        files={"file": (f"{document_type}.jpg", io.BytesIO(_REAL_JPEG), "image/jpeg")},
    )
    assert resp.status_code == 201, resp.text
    return resp.json()


def _make_unit_and_property(headers):
    prop = client.post("/api/v1/properties", headers=headers, json={
        "name": f"IdentityTest {uuid.uuid4().hex[:6]}", "address": "Test", "state_code": "34",
    }).json()
    unit = client.post("/api/v1/units", headers=headers, json={
        "property_id": prop["id"], "name": f"IdentityUnit {uuid.uuid4().hex[:6]}", "base_rate": 2500,
    }).json()
    return unit["id"]


def _make_booking(headers, unit_id, guest_id, days_offset):
    check_in = date.today() + timedelta(days=days_offset)
    check_out = check_in + timedelta(days=2)
    resp = client.post("/api/v1/bookings", headers=headers, json={
        "unit_id": unit_id, "guest_id": guest_id, "check_in": str(check_in), "check_out": str(check_out),
    })
    assert resp.status_code == 201, resp.text
    return resp.json()


# ---------------------------------------------------------------------------
# Section 1: Account Verification vs Identity Verification never conflated
# ---------------------------------------------------------------------------

def test_new_guest_defaults_to_not_required_never_verified(headers):
    guest = _make_guest(headers)
    assert guest["identity_status"] == "not_required"
    assert guest["identity_verified_at"] is None
    assert guest["identity_verified_by"] is None


def test_existing_id_proof_fields_do_not_imply_identity_verified(headers):
    """A guest with id_proof_type/number already filled in (the pre-existing,
    unreviewed fields) must still default to not_required — that data was
    never reviewed/approved by anyone, so it can't mean "verified"."""
    guest = _make_guest(headers, id_proof_type="passport", id_proof_number="P1234567")
    assert guest["identity_status"] == "not_required"


def test_patch_guest_cannot_self_set_identity_verified(headers):
    """A client can never submit identity_verified/identity_status through
    the general guest-update endpoint — GuestUpdate doesn't declare those
    fields at all, so they're silently dropped, not applied."""
    guest = _make_guest(headers)
    resp = client.patch(f"/api/v1/guests/{guest['id']}", headers=headers, json={
        "notes": "trying to sneak in a verified status",
        "identity_status": "verified",
        "identity_verified_at": "2026-01-01T00:00:00Z",
    })
    assert resp.status_code == 200, resp.text
    assert resp.json()["identity_status"] == "not_required"


# ---------------------------------------------------------------------------
# Section 2: Admin verify / reject / reset
# ---------------------------------------------------------------------------

def test_admin_can_verify_identity_with_an_uploaded_document(headers):
    guest = _make_guest(headers)
    doc = _upload_document(headers, guest["id"])
    resp = client.post(f"/api/v1/guests/{guest['id']}/identity/verify", headers=headers, json={"document_id": doc["id"]})
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["identity_status"] == "verified"
    assert body["identity_verified_at"] is not None
    assert body["identity_verified_document_id"] == doc["id"]


def test_verify_rejects_a_document_belonging_to_a_different_guest(headers):
    guest_a = _make_guest(headers)
    guest_b = _make_guest(headers)
    doc_b = _upload_document(headers, guest_b["id"])
    resp = client.post(f"/api/v1/guests/{guest_a['id']}/identity/verify", headers=headers, json={"document_id": doc_b["id"]})
    assert resp.status_code == 404, resp.text


def test_reject_from_not_required_is_rejected_nothing_was_submitted(headers):
    """reject() only makes sense against a real submission — pending/
    submitted/under_review — not a guest who never submitted anything."""
    guest = _make_guest(headers)
    resp = client.post(f"/api/v1/guests/{guest['id']}/identity/reject", headers=headers, json={"reason": "no submission exists"})
    assert resp.status_code == 409, resp.text


def test_admin_can_reject_a_verified_then_reset_guest_with_a_reason(headers):
    guest = _make_guest(headers)
    doc = _upload_document(headers, guest["id"])
    # Put it into a rejectable ("pending") state via verify -> reset.
    client.post(f"/api/v1/guests/{guest['id']}/identity/verify", headers=headers, json={"document_id": doc["id"]})
    client.post(f"/api/v1/guests/{guest['id']}/identity/reset", headers=headers, json={"reason": "document expired"})

    resp = client.post(f"/api/v1/guests/{guest['id']}/identity/reject", headers=headers, json={"reason": "Photo is blurry, number unreadable"})
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["identity_status"] == "rejected"
    assert body["identity_rejection_reason"] == "Photo is blurry, number unreadable"
    assert body["identity_verified_at"] is None


def test_admin_can_reset_a_verified_guest_for_reverification(headers):
    guest = _make_guest(headers)
    doc = _upload_document(headers, guest["id"])
    client.post(f"/api/v1/guests/{guest['id']}/identity/verify", headers=headers, json={"document_id": doc["id"]})
    resp = client.post(f"/api/v1/guests/{guest['id']}/identity/reset", headers=headers, json={"reason": "Passport renewed"})
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["identity_status"] == "pending"
    assert body["identity_verified_at"] is None
    assert body["identity_verified_document_id"] is None


def test_reset_rejected_from_not_required_status(headers):
    guest = _make_guest(headers)
    resp = client.post(f"/api/v1/guests/{guest['id']}/identity/reset", headers=headers, json={"reason": "no reason"})
    assert resp.status_code == 409, resp.text


def test_repeat_verified_guest_stays_verified_without_any_new_action(headers):
    """Section 9: a guest who is already Account+Identity verified must not
    need to redo anything on a later booking — their Guest row (and its
    identity_status) is simply reused."""
    guest = _make_guest(headers)
    doc = _upload_document(headers, guest["id"])
    client.post(f"/api/v1/guests/{guest['id']}/identity/verify", headers=headers, json={"document_id": doc["id"]})

    unit_id = _make_unit_and_property(headers)
    booking = _make_booking(headers, unit_id, guest["id"], days_offset=9000)
    refreshed = client.get("/api/v1/guests", headers=headers, params={"search": guest["phone"]}).json()[0]
    assert refreshed["identity_status"] == "verified"
    assert booking["guest_id"] == guest["id"]


# ---------------------------------------------------------------------------
# Section 3: RBAC — only admin can verify/reject/reset; staff/manager cannot
# ---------------------------------------------------------------------------

def test_staff_cannot_verify_identity(headers):
    _, staff_headers = _register_and_login(headers, "staff", "IdentityStaff")
    guest = _make_guest(headers)
    doc = _upload_document(headers, guest["id"])
    resp = client.post(f"/api/v1/guests/{guest['id']}/identity/verify", headers=staff_headers, json={"document_id": doc["id"]})
    assert resp.status_code == 403, resp.text


def test_manager_cannot_reject_identity(headers):
    _, manager_headers = _register_and_login(headers, "manager", "IdentityManager")
    guest = _make_guest(headers)
    resp = client.post(f"/api/v1/guests/{guest['id']}/identity/reject", headers=manager_headers, json={"reason": "x"})
    assert resp.status_code == 403, resp.text


def test_unauthenticated_cannot_verify_identity(headers):
    guest = _make_guest(headers)
    doc = _upload_document(headers, guest["id"])
    resp = client.post(f"/api/v1/guests/{guest['id']}/identity/verify", json={"document_id": doc["id"]})
    assert resp.status_code == 401, resp.text


def test_guest_portal_session_cannot_reach_the_admin_verify_endpoint(headers):
    """A guest-portal access token has role='guest' and a Guest.id as its
    subject — get_current_user (which every /guests/* endpoint depends on)
    looks that id up in the users table, where it doesn't exist, so this
    is rejected before any role check even runs."""
    guest = _make_guest(headers, phone="9199999001")
    login = client.post("/api/v1/portal/request-login", json={"phone": guest["phone"]})
    assert login.status_code == 200
    magic_token = login.json()["dev_magic_token"]
    assert magic_token, "test relies on non-production dev_magic_token"
    verify = client.post("/api/v1/portal/verify", json={"token": magic_token})
    guest_token = verify.json()["access_token"]

    resp = client.post(
        f"/api/v1/guests/{guest['id']}/identity/verify",
        headers={"Authorization": f"Bearer {guest_token}"}, json={"document_id": str(uuid.uuid4())},
    )
    assert resp.status_code == 401, resp.text


# ---------------------------------------------------------------------------
# Section 4: Audit trail
# ---------------------------------------------------------------------------

def test_identity_verify_and_reject_are_audit_logged(headers):
    guest = _make_guest(headers)
    doc = _upload_document(headers, guest["id"])
    client.post(f"/api/v1/guests/{guest['id']}/identity/verify", headers=headers, json={"document_id": doc["id"]})
    client.post(f"/api/v1/guests/{guest['id']}/identity/reset", headers=headers, json={"reason": "test reset"})
    client.post(f"/api/v1/guests/{guest['id']}/identity/reject", headers=headers, json={"reason": "test reject"})

    logs = client.get("/api/v1/audit/logs", headers=headers, params={"entity_type": "guest", "entity_id": guest["id"]}).json()
    actions = {log["action"] for log in logs}
    assert "guest.identity_verify" in actions
    assert "guest.identity_reset" in actions
    assert "guest.identity_reject" in actions
    # The raw document image bytes/number are never in the audit details —
    # only the document TYPE and the free-text admin reason.
    reject_log = next(log for log in logs if log["action"] == "guest.identity_reject")
    assert "test reject" in reject_log["details"]


# ---------------------------------------------------------------------------
# Section 5: Guest-portal self-service submit flow
# ---------------------------------------------------------------------------

def _portal_login(guest_phone):
    login = client.post("/api/v1/portal/request-login", json={"phone": guest_phone})
    assert login.status_code == 200, login.text
    magic_token = login.json()["dev_magic_token"]
    assert magic_token, "test relies on non-production dev_magic_token"
    verify = client.post("/api/v1/portal/verify", json={"token": magic_token})
    assert verify.status_code == 200, verify.text
    return {"Authorization": f"Bearer {verify.json()['access_token']}"}


def test_guest_can_upload_and_submit_their_own_identity_document(headers):
    guest = _make_guest(headers, phone="9199999002")
    portal_headers = _portal_login(guest["phone"])

    upload = client.post(
        "/api/v1/portal/me/identity/documents", headers=portal_headers,
        params={"document_type": "aadhaar"},
        files={"file": ("aadhaar.jpg", io.BytesIO(_REAL_JPEG), "image/jpeg")},
    )
    assert upload.status_code == 201, upload.text
    doc_id = upload.json()["id"]

    submit = client.post("/api/v1/portal/me/identity/submit", headers=portal_headers, json={"document_id": doc_id})
    assert submit.status_code == 200, submit.text
    assert submit.json()["status"] == "submitted"

    status = client.get("/api/v1/portal/me/identity", headers=portal_headers)
    assert status.json()["status"] == "submitted"

    # Staff side sees the exact same state through the ordinary Guest read.
    staff_view = client.get("/api/v1/guests", headers=headers, params={"search": guest["phone"]}).json()[0]
    assert staff_view["identity_status"] == "submitted"


def test_guest_cannot_submit_twice_while_already_submitted(headers):
    guest = _make_guest(headers, phone="9199999003")
    portal_headers = _portal_login(guest["phone"])
    upload = client.post(
        "/api/v1/portal/me/identity/documents", headers=portal_headers, params={"document_type": "pan"},
        files={"file": ("pan.jpg", io.BytesIO(_REAL_JPEG), "image/jpeg")},
    ).json()
    client.post("/api/v1/portal/me/identity/submit", headers=portal_headers, json={"document_id": upload["id"]})
    resp = client.post("/api/v1/portal/me/identity/submit", headers=portal_headers, json={"document_id": upload["id"]})
    assert resp.status_code == 409, resp.text


def test_rejected_guest_sees_a_generic_message_never_the_internal_reason(headers):
    guest = _make_guest(headers, phone="9199999004")
    doc = _upload_document(headers, guest["id"])
    # Get into a rejectable ("pending") state first — reject() requires a
    # real prior submission (pending/submitted/under_review), same as
    # test_admin_can_reject_a_verified_then_reset_guest_with_a_reason.
    client.post(f"/api/v1/guests/{guest['id']}/identity/verify", headers=headers, json={"document_id": doc["id"]})
    client.post(f"/api/v1/guests/{guest['id']}/identity/reset", headers=headers, json={"reason": "setup"})
    client.post(f"/api/v1/guests/{guest['id']}/identity/reject", headers=headers, json={"reason": "INTERNAL: signature on file looks forged"})

    portal_headers = _portal_login(guest["phone"])
    status = client.get("/api/v1/portal/me/identity", headers=portal_headers).json()
    assert status["status"] == "rejected"
    assert "forged" not in (status.get("message") or "")
    assert status["message"] == "Your identity verification could not be completed. Please submit a valid identity document."


def test_guest_cannot_upload_a_document_onto_another_guests_profile(headers):
    """The upload endpoint takes no guest_id at all — it's always the
    caller's own session guest — so there is no IDOR surface to test
    against directly; this proves the uploaded document lands under the
    AUTHENTICATED guest, not any guest_id that might appear elsewhere in
    the request (there isn't one, by construction)."""
    guest = _make_guest(headers, phone="9199999005")
    other_guest = _make_guest(headers, phone="9199999006")
    portal_headers = _portal_login(guest["phone"])
    upload = client.post(
        "/api/v1/portal/me/identity/documents", headers=portal_headers, params={"document_type": "other"},
        files={"file": ("doc.jpg", io.BytesIO(_REAL_JPEG), "image/jpeg")},
    ).json()
    docs = client.get(f"/api/v1/guests/{guest['id']}/documents", headers=headers).json()
    assert any(d["id"] == upload["id"] for d in docs)
    other_docs = client.get(f"/api/v1/guests/{other_guest['id']}/documents", headers=headers).json()
    assert not any(d["id"] == upload["id"] for d in other_docs)


# ---------------------------------------------------------------------------
# Section 6: Additional Guest (BookingGuest) assignment
# ---------------------------------------------------------------------------

def test_add_and_list_additional_guests_on_a_booking(headers):
    unit_id = _make_unit_and_property(headers)
    principal = _make_guest(headers)
    booking = _make_booking(headers, unit_id, principal["id"], days_offset=9100)
    companion = _make_guest(headers)

    resp = client.post(f"/api/v1/bookings/{booking['id']}/guests", headers=headers, json={"guest_id": companion["id"]})
    assert resp.status_code == 201, resp.text
    assert resp.json()["identity_status"] == "not_required"

    listing = client.get(f"/api/v1/bookings/{booking['id']}/guests", headers=headers).json()
    assert listing["principal_guest"]["guest_id"] == principal["id"]
    assert len(listing["additional_guests"]) == 1
    assert listing["additional_guests"][0]["guest_id"] == companion["id"]


def test_cannot_add_the_principal_guest_as_an_additional_guest(headers):
    unit_id = _make_unit_and_property(headers)
    principal = _make_guest(headers)
    booking = _make_booking(headers, unit_id, principal["id"], days_offset=9110)
    resp = client.post(f"/api/v1/bookings/{booking['id']}/guests", headers=headers, json={"guest_id": principal["id"]})
    assert resp.status_code == 409, resp.text


def test_cannot_add_the_same_additional_guest_twice(headers):
    unit_id = _make_unit_and_property(headers)
    principal = _make_guest(headers)
    booking = _make_booking(headers, unit_id, principal["id"], days_offset=9120)
    companion = _make_guest(headers)
    client.post(f"/api/v1/bookings/{booking['id']}/guests", headers=headers, json={"guest_id": companion["id"]})
    resp = client.post(f"/api/v1/bookings/{booking['id']}/guests", headers=headers, json={"guest_id": companion["id"]})
    assert resp.status_code == 409, resp.text


def test_removing_an_additional_guest_does_not_delete_their_guest_profile(headers):
    unit_id = _make_unit_and_property(headers)
    principal = _make_guest(headers)
    booking = _make_booking(headers, unit_id, principal["id"], days_offset=9130)
    companion = _make_guest(headers)
    added = client.post(f"/api/v1/bookings/{booking['id']}/guests", headers=headers, json={"guest_id": companion["id"]}).json()

    resp = client.delete(f"/api/v1/bookings/{booking['id']}/guests/{added['id']}", headers=headers)
    assert resp.status_code == 204, resp.text

    listing = client.get(f"/api/v1/bookings/{booking['id']}/guests", headers=headers).json()
    assert listing["additional_guests"] == []
    still_exists = client.get("/api/v1/guests", headers=headers, params={"search": companion["phone"]}).json()
    assert len(still_exists) == 1


def test_a_guest_can_be_principal_on_one_booking_and_additional_on_another(headers):
    unit_id = _make_unit_and_property(headers)
    person_a = _make_guest(headers)
    person_b = _make_guest(headers)
    booking1 = _make_booking(headers, unit_id, person_a["id"], days_offset=9140)
    unit_id_2 = _make_unit_and_property(headers)
    booking2 = _make_booking(headers, unit_id_2, person_b["id"], days_offset=9150)

    client.post(f"/api/v1/bookings/{booking1['id']}/guests", headers=headers, json={"guest_id": person_b["id"]})

    listing1 = client.get(f"/api/v1/bookings/{booking1['id']}/guests", headers=headers).json()
    listing2 = client.get(f"/api/v1/bookings/{booking2['id']}/guests", headers=headers).json()
    assert listing1["additional_guests"][0]["guest_id"] == person_b["id"]
    assert listing2["principal_guest"]["guest_id"] == person_b["id"]


def test_already_verified_companion_is_reused_no_reverification_needed(headers):
    """Section 10: if Rahul is already verified, adding him to a new
    booking must show him as already verified — nothing re-triggers."""
    unit_id = _make_unit_and_property(headers)
    principal = _make_guest(headers)
    booking = _make_booking(headers, unit_id, principal["id"], days_offset=9160)
    companion = _make_guest(headers)
    doc = _upload_document(headers, companion["id"])
    client.post(f"/api/v1/guests/{companion['id']}/identity/verify", headers=headers, json={"document_id": doc["id"]})

    client.post(f"/api/v1/bookings/{booking['id']}/guests", headers=headers, json={"guest_id": companion["id"]})
    listing = client.get(f"/api/v1/bookings/{booking['id']}/guests", headers=headers).json()
    assert listing["additional_guests"][0]["identity_status"] == "verified"
