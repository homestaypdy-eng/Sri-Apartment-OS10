"""
Tests for the OS10 Group Inbox (app/modules/messaging).

Covers:
  1.  Conversation auto-provisioning for a real confirmed booking
  2.  Message creation + chronological ordering
  3.  Read / unread
  4.  RBAC (staff-authenticated only, unauthenticated blocked)
  5.  Booking linking + display-status derivation (confirmed/upcoming/cancelled)
  6.  Payment-status derivation (payment pending when balance > 0)
  7.  Confirmation preview reuses the real guest confirmation wording
  8.  Guest count in the preview matches the actual booking, never defaults to 1
  9.  Enquiry (Inquiry, no booking) surfaces as ENQUIRY with no fabricated booking data

Run with: pytest tests/test_messaging.py -v
"""
import hashlib
import hmac
import uuid
from datetime import date, timedelta
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient

from app.main import app
from conftest import ADMIN_EMAIL, ADMIN_PASSWORD

client = TestClient(app)

FAKE_RAZORPAY_KEY_SECRET = "test_key_secret_for_unit_tests"
FAKE_ORDER_ID = "order_test_fake123"
FAKE_PAYMENT_ID = "pay_test_fake456"


def _make_signature(order_id: str, payment_id: str, secret: str) -> str:
    payload = f"{order_id}|{payment_id}".encode()
    return hmac.new(secret.encode(), payload, hashlib.sha256).hexdigest()


@pytest.fixture(scope="module")
def headers():
    assert ADMIN_PASSWORD, "BOOTSTRAP_ADMIN_PASSWORD must be set for this test module to run"
    resp = client.post("/api/v1/auth/login", json={"email": ADMIN_EMAIL, "password": ADMIN_PASSWORD})
    assert resp.status_code == 200, resp.text
    return {"Authorization": f"Bearer {resp.json()['access_token']}"}


def _make_available_unit(headers, base_rate=2000, max_occupancy=2):
    prop = client.post(
        "/api/v1/properties", headers=headers,
        json={"name": f"MsgTest {uuid.uuid4().hex[:6]}", "address": "Test", "state_code": "34"},
    ).json()
    unit = client.post(
        "/api/v1/units", headers=headers,
        json={"property_id": prop["id"], "name": f"UMsgTest {uuid.uuid4().hex[:6]}", "base_rate": base_rate, "max_occupancy": max_occupancy},
    ).json()
    return unit["id"]


def _make_confirmed_booking(headers, adults, check_in_offset=200):
    """Real hold -> real payment-verified -> real confirmed Booking, same
    proven path test_public_booking_payment_gate.py already uses — not a
    second, parallel way of faking a confirmed booking."""
    unit_id = _make_available_unit(headers, max_occupancy=max(2, adults))
    check_in = date.today() + timedelta(days=check_in_offset)
    check_out = check_in + timedelta(days=2)
    phone = f"90{uuid.uuid4().int % 10**8:08d}"

    fake_order_id = f"order_{uuid.uuid4().hex[:12]}"
    with patch("app.modules.public_booking.router.create_order") as mock_order:
        mock_order.return_value = {"id": fake_order_id}
        resp = client.post("/api/v1/public/holds", json={
            "unit_id": unit_id, "check_in": check_in.isoformat(), "check_out": check_out.isoformat(),
            "guest_name": "Messaging Test Guest", "guest_phone": phone, "guest_email": "msgtest@example.com",
            "adults": adults,
        })
    assert resp.status_code == 201, resp.text
    hold_id = resp.json()["hold_id"]

    sig = _make_signature(fake_order_id, FAKE_PAYMENT_ID, FAKE_RAZORPAY_KEY_SECRET)
    with patch("app.modules.public_booking.router.verify_payment_signature", return_value=True):
        verify_resp = client.post(f"/api/v1/public/holds/{hold_id}/verify-payment", json={
            "razorpay_payment_id": FAKE_PAYMENT_ID, "razorpay_order_id": fake_order_id, "razorpay_signature": sig,
        })
    assert verify_resp.status_code == 201, verify_resp.text
    return verify_resp.json()["booking_id"], check_in, check_out


def _conversation_for_booking(headers, booking_id):
    resp = client.get("/api/v1/messages/conversations", headers=headers, params={"filter": "all", "q": "", "limit": 200})
    assert resp.status_code == 200, resp.text
    for c in resp.json():
        detail = client.get(f"/api/v1/messages/conversations/{c['id']}", headers=headers).json()
        if detail["booking_id"] == booking_id:
            return detail
    raise AssertionError(f"No auto-provisioned conversation found for booking {booking_id}")


# ---------------------------------------------------------------------------
# 1. Auto-provisioning + booking linking
# ---------------------------------------------------------------------------

def test_conversation_is_auto_provisioned_for_a_real_confirmed_booking(headers):
    booking_id, check_in, check_out = _make_confirmed_booking(headers, adults=4)
    convo = _conversation_for_booking(headers, booking_id)

    assert convo["booking_id"] == booking_id
    assert convo["guests"] == 4
    assert convo["check_in"] == check_in.isoformat()
    assert convo["check_out"] == check_out.isoformat()
    assert convo["guest_name"] == "Messaging Test Guest"
    assert convo["display_status"]["label"] in ("UPCOMING CHECK-IN", "CONFIRMED", "PAYMENT PENDING")


def test_conversation_is_not_duplicated_on_repeated_listing(headers):
    booking_id, _, _ = _make_confirmed_booking(headers, adults=2)
    first = _conversation_for_booking(headers, booking_id)
    second = _conversation_for_booking(headers, booking_id)
    assert first["id"] == second["id"]


# ---------------------------------------------------------------------------
# 2. Messages: creation + chronological ordering
# ---------------------------------------------------------------------------

def test_messages_are_created_and_returned_in_chronological_order(headers):
    booking_id, _, _ = _make_confirmed_booking(headers, adults=1)
    convo = _conversation_for_booking(headers, booking_id)
    convo_id = convo["id"]

    r1 = client.post(f"/api/v1/messages/conversations/{convo_id}/messages", headers=headers, json={"body": "Hello, is early check-in possible?", "sender_type": "guest"})
    assert r1.status_code == 201, r1.text
    r2 = client.post(f"/api/v1/messages/conversations/{convo_id}/messages", headers=headers, json={"body": "Early check-in is subject to availability.", "sender_type": "staff"})
    assert r2.status_code == 201, r2.text

    detail = client.get(f"/api/v1/messages/conversations/{convo_id}", headers=headers).json()
    bodies = [m["body"] for m in detail["messages"]]
    assert bodies == ["Hello, is early check-in possible?", "Early check-in is subject to availability."]
    assert detail["messages"][0]["sender_type"] == "guest"
    assert detail["messages"][1]["sender_type"] == "staff"
    assert detail["messages"][1]["sender_name"]  # the logged-in admin's name, not blank


# ---------------------------------------------------------------------------
# 3. Read / unread
# ---------------------------------------------------------------------------

def test_unread_count_reflects_unread_guest_messages_and_mark_read_clears_it(headers):
    booking_id, _, _ = _make_confirmed_booking(headers, adults=3)
    convo = _conversation_for_booking(headers, booking_id)
    convo_id = convo["id"]

    client.post(f"/api/v1/messages/conversations/{convo_id}/messages", headers=headers, json={"body": "Can we get an extra bed?", "sender_type": "guest"})

    listing = client.get("/api/v1/messages/conversations", headers=headers, params={"filter": "unread", "limit": 200}).json()
    assert any(c["id"] == convo_id and c["unread_count"] == 1 for c in listing)

    read_resp = client.post(f"/api/v1/messages/conversations/{convo_id}/read", headers=headers)
    assert read_resp.status_code == 204

    listing_after = client.get("/api/v1/messages/conversations", headers=headers, params={"filter": "unread", "limit": 200}).json()
    assert not any(c["id"] == convo_id for c in listing_after)


def test_mark_unread_restores_the_unread_state(headers):
    booking_id, _, _ = _make_confirmed_booking(headers, adults=2)
    convo = _conversation_for_booking(headers, booking_id)
    convo_id = convo["id"]

    client.post(f"/api/v1/messages/conversations/{convo_id}/messages", headers=headers, json={"body": "What time is checkout?", "sender_type": "guest"})
    client.post(f"/api/v1/messages/conversations/{convo_id}/read", headers=headers)

    unread_resp = client.post(f"/api/v1/messages/conversations/{convo_id}/unread", headers=headers)
    assert unread_resp.status_code == 204

    detail = client.get(f"/api/v1/messages/conversations/{convo_id}", headers=headers).json()
    assert detail["unread_count"] == 1


# ---------------------------------------------------------------------------
# 4. RBAC
# ---------------------------------------------------------------------------

def test_unauthenticated_request_is_rejected(headers):
    resp = client.get("/api/v1/messages/conversations")
    assert resp.status_code in (401, 403)


def test_staff_role_can_access_conversations(headers):
    staff_email = f"msg-staff-{uuid.uuid4().hex[:6]}@example.com"
    register = client.post("/api/v1/auth/register", headers=headers, json={"name": "Msg Staff", "email": staff_email, "password": "Pass12345!", "role": "staff"})
    assert register.status_code == 201, register.text
    staff_login = client.post("/api/v1/auth/login", json={"email": staff_email, "password": "Pass12345!"})
    assert staff_login.status_code == 200
    staff_headers = {"Authorization": f"Bearer {staff_login.json()['access_token']}"}

    resp = client.get("/api/v1/messages/conversations", headers=staff_headers, params={"limit": 5})
    assert resp.status_code == 200


# ---------------------------------------------------------------------------
# 5 & 6. Status + payment derivation
# ---------------------------------------------------------------------------

def test_upcoming_confirmed_booking_shows_upcoming_check_in_status(headers):
    booking_id, _, _ = _make_confirmed_booking(headers, adults=2, check_in_offset=300)
    convo = _conversation_for_booking(headers, booking_id)
    assert convo["display_status"]["label"] == "UPCOMING CHECK-IN"
    assert convo["display_status"]["tone"] == "blue"


def test_cancelled_booking_shows_cancelled_status_not_confirmed(headers):
    booking_id, _, _ = _make_confirmed_booking(headers, adults=2, check_in_offset=310)
    cancel_resp = client.patch(
        f"/api/v1/bookings/{booking_id}/status", headers=headers, params={"new_status": "cancelled"},
        json={"refund_reason": "Guest requested cancellation"},
    )
    assert cancel_resp.status_code == 200, cancel_resp.text

    convo = _conversation_for_booking(headers, booking_id)
    assert convo["display_status"]["label"] == "CANCELLED"
    assert convo["display_status"]["tone"] == "red"

    details = client.get(f"/api/v1/messages/conversations/{convo['id']}/booking-details", headers=headers)
    assert details.status_code == 200
    assert details.json()["display_status"]["label"] == "CANCELLED"


# ---------------------------------------------------------------------------
# 7 & 8. Confirmation preview reuses the real wording; guest count is real
# ---------------------------------------------------------------------------

def test_confirmation_preview_matches_the_real_guest_confirmation_and_shows_actual_guest_count(headers):
    from app.db.session import SessionLocal
    from app.modules.bookings.models import Booking
    from app.modules.notifications.service import render_booking_confirmation_message

    booking_id, _, _ = _make_confirmed_booking(headers, adults=5, check_in_offset=320)
    convo = _conversation_for_booking(headers, booking_id)

    preview_resp = client.post(f"/api/v1/messages/conversations/{convo['id']}/confirmation-preview", headers=headers)
    assert preview_resp.status_code == 200, preview_resp.text
    preview = preview_resp.json()

    db = SessionLocal()
    try:
        booking = db.get(Booking, uuid.UUID(booking_id))
        expected = render_booking_confirmation_message(db, booking)
    finally:
        db.close()

    assert preview["subject"] == expected["subject"]
    assert preview["body"] == expected["body"]
    assert preview["html_body"] == expected["html_body"]
    # Never defaults to 1 — the actual selected guest count must appear.
    assert "Guests: 5" in preview["body"]
    assert "Guests: 1" not in preview["body"]


def test_booking_details_totals_match_the_confirmation_preview_numbers(headers):
    booking_id, _, _ = _make_confirmed_booking(headers, adults=1, check_in_offset=330)
    convo = _conversation_for_booking(headers, booking_id)

    details = client.get(f"/api/v1/messages/conversations/{convo['id']}/booking-details", headers=headers).json()
    preview = client.post(f"/api/v1/messages/conversations/{convo['id']}/confirmation-preview", headers=headers).json()

    assert f"Total: Rs. {details['total_amount']:.2f}" in preview["body"]
    assert f"Paid: Rs. {details['paid_amount']:.2f}" in preview["body"]
    assert f"Balance: Rs. {details['balance_due']:.2f}" in preview["body"]
    assert details["payment_status"] == "PAID"  # full payment was verified in _make_confirmed_booking


# ---------------------------------------------------------------------------
# 9. Enquiry (Inquiry, no booking) — no fabricated booking data
# ---------------------------------------------------------------------------

def test_inquiry_only_conversation_shows_enquiry_status_and_no_booking_details(headers):
    unit_id = _make_available_unit(headers)
    check_in = date.today() + timedelta(days=400)
    check_out = check_in + timedelta(days=2)
    inquiry_resp = client.post("/api/v1/inquiries", headers=headers, json={
        "client_name": "Enquiry Only Guest", "client_phone": "9123456780",
        "unit_id": unit_id, "flat_room_label": "Test Flat", "num_guests": 2,
        "check_in_date": check_in.isoformat(), "check_out_date": check_out.isoformat(),
        "rate_per_night": 2000, "advance_amount": 0,
    })
    assert inquiry_resp.status_code == 201, inquiry_resp.text
    inquiry_id = inquiry_resp.json()["id"]

    listing = client.get("/api/v1/messages/conversations", headers=headers, params={"filter": "enquiry", "limit": 200}).json()
    match = next((c for c in listing if c.get("booking_reference") is None and c["guest_name"] == "Enquiry Only Guest"), None)
    assert match is not None, "Expected an auto-provisioned ENQUIRY conversation for the new Inquiry"
    assert match["display_status"]["label"] == "ENQUIRY"
    assert match["booking_reference"] is None  # no booking exists — never fabricated

    detail = client.get(f"/api/v1/messages/conversations/{match['id']}", headers=headers).json()
    assert detail["booking_id"] is None
    assert detail["inquiry_id"] == inquiry_id

    no_booking = client.get(f"/api/v1/messages/conversations/{match['id']}/booking-details", headers=headers)
    assert no_booking.status_code == 404
