"""
Tests for the notification-audit fix: owner notifications (both email
contacts) for direct website bookings, the honest WhatsApp abstraction
(no real provider configured anywhere in this project), and the
idempotency/duplicate-protection guarantees around both.

SAFETY: every test that would otherwise trigger a real send mocks
send_email/send_whatsapp (or httpx.post) directly — this dev container has
real Gmail SMTP configured, and BusinessSettings.contact_email/
secondary_contact_email default to the REAL owner addresses
(homestaypdy@gmail.com / sriapartmentpondy@gmail.com), so an unmocked
owner-notification test here would send a real email to a real person.
No test in this file makes a real network call to any email/SMS/WhatsApp
provider. No real booking is left in the dev database — everything here
runs against the isolated test database (see tests/conftest.py).

Covers:
  Owner email — primary + secondary both notified, same event, once each
  Owner email — duplicate webhook delivery does not duplicate
  Owner email — browser/webhook race does not duplicate
  Owner email — content includes the required booking fields
  Customer email — already-existing trigger preserved, correct recipient/content
  WhatsApp — not configured fails honestly, no fake success, abstraction is
             testable once configured, per-recipient duplicate protection
  Payment — invalid signature never notifies as confirmed; valid payment
            notifies owners exactly once
"""
import hashlib
import hmac
import os
import uuid
from datetime import date, timedelta
from unittest.mock import MagicMock, patch

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from app.core.config import settings
from app.db.session import SessionLocal
from app.main import app
from app.modules.bookings.models import Booking, Guest
from app.modules.notifications import service as notif_service
from app.modules.notifications.models import NotificationLog
from app.modules.notifications.service import WhatsAppNotConfiguredError
from app.modules.settings.models import BusinessSettings

client = TestClient(app)

from conftest import ADMIN_EMAIL, ADMIN_PASSWORD  # single source of truth, see conftest.py

FAKE_RAZORPAY_KEY_SECRET = "test_key_secret_for_unit_tests"


def _make_signature(order_id: str, payment_id: str, secret: str) -> str:
    return hmac.new(secret.encode(), f"{order_id}|{payment_id}".encode(), hashlib.sha256).hexdigest()


def _razorpay_captured_payload(order_id: str, payment_id: str, amount_paise: int) -> bytes:
    import json
    return json.dumps({
        "event": "payment.captured",
        "payload": {"payment": {"entity": {
            "order_id": order_id, "id": payment_id, "status": "captured",
            "amount": amount_paise, "currency": "INR",
        }}},
    }).encode()


@pytest.fixture(scope="module")
def headers():
    resp = client.post("/api/v1/auth/login", json={"email": ADMIN_EMAIL, "password": ADMIN_PASSWORD})
    assert resp.status_code == 200
    return {"Authorization": f"Bearer {resp.json()['access_token']}"}


def _make_available_unit(headers, base_rate=2000):
    prop = client.post("/api/v1/properties", headers=headers, json={
        "name": f"NotifTest {uuid.uuid4().hex[:6]}", "address": "Test", "state_code": "34",
    }).json()
    unit = client.post("/api/v1/units", headers=headers, json={
        "property_id": prop["id"], "name": f"NotifUnit {uuid.uuid4().hex[:6]}", "base_rate": base_rate,
    }).json()
    return prop["id"], unit["id"]


def _create_hold_mocked(unit_id, check_in, check_out, guest_phone=None):
    if guest_phone is None:
        guest_phone = f"77{uuid.uuid4().int % 10**8:08d}"
    fake_order_id = f"order_{uuid.uuid4().hex[:12]}"
    with patch("app.modules.public_booking.router.create_order") as mock_order:
        mock_order.return_value = {"id": fake_order_id}
        resp = client.post("/api/v1/public/holds", json={
            "unit_id": str(unit_id), "check_in": check_in.isoformat(), "check_out": check_out.isoformat(),
            "guest_name": "Notif Test Guest", "guest_phone": guest_phone, "guest_email": "notiftest@example.com",
        })
    return resp, fake_order_id


def _complete_valid_booking(headers, unit_id, check_in, check_out, guest_phone=None):
    """Full hold -> mocked-signature-verified booking. CALLER MUST wrap
    this in its own `patch("app.modules.notifications.service.send_email",
    ...)` — deliberately not done here, since nesting a second patch of
    the same target inside this helper would shadow the caller's mock for
    the duration of this call and silently swallow every recorded call
    (exactly the bug this comment now prevents someone from reintroducing).
    See the module docstring for why send_email must always be mocked.
    Returns (booking_id, hold_id, fake_order_id, payment_id)."""
    resp, fake_order_id = _create_hold_mocked(unit_id, check_in, check_out, guest_phone=guest_phone)
    assert resp.status_code == 201, resp.text
    hold_id = resp.json()["hold_id"]
    payment_id = f"pay_{uuid.uuid4().hex[:10]}"
    sig = _make_signature(fake_order_id, payment_id, FAKE_RAZORPAY_KEY_SECRET)

    with patch("app.modules.public_booking.router.verify_payment_signature", return_value=True):
        verify_resp = client.post(f"/api/v1/public/holds/{hold_id}/verify-payment", json={
            "razorpay_payment_id": payment_id, "razorpay_order_id": fake_order_id, "razorpay_signature": sig,
        })
    assert verify_resp.status_code == 201, verify_resp.text
    return verify_resp.json()["booking_id"], hold_id, fake_order_id, payment_id


def _owner_emails(db):
    from app.modules.settings.router import get_or_create_settings
    row = get_or_create_settings(db)
    return row.contact_email, row.secondary_contact_email


# ---------------------------------------------------------------------------
# Owner email — both addresses, correct content, idempotent
# ---------------------------------------------------------------------------

def test_owner_notification_reaches_primary_and_secondary_email(headers):
    db = SessionLocal()
    try:
        primary, secondary = _owner_emails(db)
        assert primary and secondary and primary != secondary  # sanity: two distinct configured addresses
    finally:
        db.close()

    _, unit_id = _make_available_unit(headers)
    check_in = date.today() + timedelta(days=900)
    check_out = check_in + timedelta(days=2)

    with patch("app.modules.notifications.service.send_email", return_value=None) as mock_send:
        booking_id, *_ = _complete_valid_booking(headers, unit_id, check_in, check_out)

    sent_to = {call.args[0] for call in mock_send.call_args_list}
    assert primary in sent_to, f"Primary owner {primary} was not emailed"
    assert secondary in sent_to, f"Secondary owner {secondary} was not emailed"

    db = SessionLocal()
    try:
        logs = db.scalars(select(NotificationLog).where(
            NotificationLog.booking_id == uuid.UUID(booking_id),
            NotificationLog.event_type == "owner_booking_notification",
            NotificationLog.channel == "email",
        )).all()
        recipients = {log.recipient for log in logs}
        assert recipients == {primary, secondary}
        assert all(log.success for log in logs)
    finally:
        db.close()


def test_owner_notification_content_includes_required_booking_fields(headers):
    _, unit_id = _make_available_unit(headers)
    check_in = date.today() + timedelta(days=905)
    check_out = check_in + timedelta(days=3)

    with patch("app.modules.notifications.service.send_email", return_value=None) as mock_send:
        booking_id, *_ = _complete_valid_booking(headers, unit_id, check_in, check_out)

    db = SessionLocal()
    try:
        booking = db.get(Booking, uuid.UUID(booking_id))
        guest = db.get(Guest, booking.guest_id)
    finally:
        db.close()

    owner_call_bodies = [
        call.args[2] for call in mock_send.call_args_list
        if call.args[1].startswith("New Direct Website Booking")
    ]
    assert len(owner_call_bodies) == 2  # one per owner recipient
    for body in owner_call_bodies:
        assert guest.name in body
        assert str(booking.id) in body
        assert str(booking.check_in) in body
        assert str(booking.check_out) in body
        assert "Total amount" in body
        assert "Payment received" in body
        assert "Booking status" in body
        assert "Invoice" in body


def test_owner_notification_carries_invoice_attachment_exactly_once_per_address(headers):
    """Business-email fix: each configured business address must receive
    exactly ONE email per booking, and that email must carry the invoice
    PDF — not a separate invoice-less notification plus a second invoice
    email. Guest-side behavior (a separate confirmation + a separate
    invoice_delivery email) is a distinct, preserved requirement and is
    NOT what this test is about."""
    db = SessionLocal()
    try:
        primary, secondary = _owner_emails(db)
    finally:
        db.close()

    _, unit_id = _make_available_unit(headers)
    check_in = date.today() + timedelta(days=930)
    check_out = check_in + timedelta(days=2)

    with patch("app.modules.notifications.service.send_email", return_value=None) as mock_send:
        booking_id, *_ = _complete_valid_booking(headers, unit_id, check_in, check_out)

    db = SessionLocal()
    try:
        from app.modules.payments.models import Invoice
        invoice = db.scalar(select(Invoice).where(Invoice.booking_id == uuid.UUID(booking_id)))
        assert invoice is not None and invoice.pdf_path
        invoice_no = invoice.invoice_no
    finally:
        db.close()

    # NOT invoice.pdf_path: dispatch_booking_notifications() deliberately
    # regenerates the invoice PDF fresh for the notification attachment
    # (generate_invoice_pdf_for_booking) rather than reusing the file at
    # invoice.pdf_path — a documented, intentional Phase 4A/3A choice so a
    # lost ephemeral-filesystem file (e.g. after a Railway redeploy) can
    # never silently produce an attachment-less email. That means the
    # attachment is a DIFFERENT file on disk than invoice.pdf_path, with
    # different random-suffix filenames, by design — same invoice content,
    # not the same file object. The correct invariant to test is therefore
    # "a real PDF for the correct invoice was attached", not "it's byte-
    # identical-path to the DB's stored pdf_path" (a previously-failing
    # assertion found in a production-readiness audit that mis-identified
    # this deliberate regeneration as a defect).
    #
    # Also NOT checked here: os.path.exists(attachment_path). That file is
    # intentionally deleted by dispatch_booking_notifications' own cleanup
    # (a `finally: os.remove(generated_pdf_path)` right after all triggers
    # run — see notifications/service.py) so the regenerated, send-only
    # copy never accumulates on the ephemeral filesystem. By the time this
    # assertion runs the file is already gone by design; checking for its
    # existence was itself a test-hygiene bug, not a real invariant.
    sanitized_invoice_no = invoice_no.replace("/", "_")
    for owner_email in (primary, secondary):
        calls_to_owner = [c for c in mock_send.call_args_list if c.args[0] == owner_email]
        assert len(calls_to_owner) == 1, (
            f"Expected exactly one email to {owner_email}, got {len(calls_to_owner)}"
        )
        # send_email(to_email, subject, body, attachment_path) — positional.
        attachment_path = calls_to_owner[0].args[3]
        assert attachment_path, "Business email must carry the invoice PDF"
        assert sanitized_invoice_no in os.path.basename(attachment_path), (
            f"Attached PDF {attachment_path!r} does not appear to be for invoice {invoice_no!r}"
        )

    db = SessionLocal()
    try:
        logs = db.scalars(select(NotificationLog).where(
            NotificationLog.booking_id == uuid.UUID(booking_id),
            NotificationLog.recipient.in_([primary, secondary]),
        )).all()
        # Exactly one NotificationLog row per business address, no separate
        # invoice-only or confirmation-only row logged against them.
        assert len(logs) == 2
        assert {log.event_type for log in logs} == {"owner_booking_notification"}
    finally:
        db.close()


def test_owner_notification_attachment_is_freshly_regenerated_not_the_stored_invoice_file(headers):
    """
    Regression test locking in WHY the previous test does not compare
    against invoice.pdf_path directly: dispatch_booking_notifications()
    regenerates the invoice PDF fresh (generate_invoice_pdf_for_booking)
    rather than reading the possibly-lost file at invoice.pdf_path — a
    deliberate Phase 4A/3A ephemeral-filesystem-resilience choice. This
    test asserts that divergence explicitly, so a future change that
    "fixes" the two paths to be identical (silently reintroducing the
    lost-file risk this design avoids) fails a test instead of passing
    unnoticed.
    """
    _, unit_id = _make_available_unit(headers)
    check_in = date.today() + timedelta(days=931)
    check_out = check_in + timedelta(days=2)

    with patch("app.modules.notifications.service.send_email", return_value=None) as mock_send:
        booking_id, *_ = _complete_valid_booking(headers, unit_id, check_in, check_out)

    db = SessionLocal()
    try:
        from app.modules.payments.models import Invoice
        invoice = db.scalar(select(Invoice).where(Invoice.booking_id == uuid.UUID(booking_id)))
        assert invoice is not None and invoice.pdf_path
        stored_pdf_path = invoice.pdf_path
    finally:
        db.close()

    owner_calls = [c for c in mock_send.call_args_list if len(c.args) > 3 and c.args[3]]
    assert owner_calls, "Expected at least one owner email carrying an attachment"
    attached_path = owner_calls[0].args[3]

    assert attached_path != stored_pdf_path, (
        "The notification attachment is expected to be a freshly regenerated "
        "file, distinct from invoice.pdf_path — see this test's docstring."
    )
    # NOT os.path.exists(attached_path): dispatch_booking_notifications()
    # deletes that freshly-regenerated file in its own `finally` block
    # immediately after all triggers run (notifications/service.py), by
    # design, so it never lingers on the ephemeral filesystem — it is
    # correct for it to be gone by the time this assertion runs. The
    # original invoice.pdf_path file (created separately at booking time,
    # never touched by that cleanup) is expected to still be present.
    assert os.path.exists(stored_pdf_path)


def test_owner_notification_not_duplicated_on_duplicate_webhook(headers):
    _, unit_id = _make_available_unit(headers)
    check_in = date.today() + timedelta(days=910)
    check_out = check_in + timedelta(days=2)
    phone = f"78{uuid.uuid4().int % 10**8:08d}"

    resp, fake_order_id = _create_hold_mocked(unit_id, check_in, check_out, guest_phone=phone)
    assert resp.status_code == 201
    hold_id = resp.json()["hold_id"]
    payment_id = f"pay_ownerdup_{uuid.uuid4().hex[:10]}"
    from app.modules.payment_gateway.models import GatewayOrder
    db = SessionLocal()
    try:
        gateway_order = db.scalar(select(GatewayOrder).where(GatewayOrder.gateway_order_id == fake_order_id))
        assert gateway_order is not None
        amount_paise = round(float(gateway_order.amount) * 100)
    finally:
        db.close()
    body = _razorpay_captured_payload(fake_order_id, payment_id, amount_paise)

    with patch("app.modules.payment_gateway.router.verify_webhook_signature", return_value=True), \
         patch("app.modules.notifications.service.send_email", return_value=None) as mock_send:
        r1 = client.post("/api/v1/payment-gateway/webhook", content=body, headers={"X-Razorpay-Signature": "sig"})
        assert r1.status_code == 200, r1.text
        r2 = client.post("/api/v1/payment-gateway/webhook", content=body, headers={"X-Razorpay-Signature": "sig"})
        assert r2.status_code == 200, r2.text

    owner_sends = [c for c in mock_send.call_args_list if c.args[1].startswith("New Direct Website Booking")]
    assert len(owner_sends) == 2, f"Expected exactly 2 owner emails (one per address) despite duplicate webhook, got {len(owner_sends)}"

    from app.modules.public_booking.models import BookingHold
    db = SessionLocal()
    try:
        hold = db.get(BookingHold, hold_id)
        booking = db.get(Booking, hold.booking_id)
        email_logs = db.scalars(select(NotificationLog).where(
            NotificationLog.booking_id == booking.id,
            NotificationLog.event_type == "owner_booking_notification",
            NotificationLog.channel == "email",
        )).all()
        assert len(email_logs) == 2, f"Expected exactly 2 owner-notification email log rows despite duplicate webhook, got {len(email_logs)}"

        # Same per-recipient idempotency guard applies to WhatsApp — both
        # numbers attempted once each, never duplicated by the second
        # webhook delivery.
        wa_logs = db.scalars(select(NotificationLog).where(
            NotificationLog.booking_id == booking.id,
            NotificationLog.event_type == "owner_booking_notification",
            NotificationLog.channel == "whatsapp",
        )).all()
        assert len(wa_logs) == 2, f"Expected exactly 2 owner-notification WhatsApp log rows despite duplicate webhook, got {len(wa_logs)}"
        assert len({log.recipient for log in wa_logs}) == 2  # both distinct numbers, not the same one twice
    finally:
        db.close()


def test_owner_notification_not_duplicated_on_browser_webhook_race(headers):
    """Webhook converts first; a subsequent browser verify-payment call on
    the same hold must not re-notify the owners (mirrors
    test_verify_payment_after_webhook_already_converted_is_idempotent)."""
    _, unit_id = _make_available_unit(headers)
    check_in = date.today() + timedelta(days=915)
    check_out = check_in + timedelta(days=2)
    phone = f"79{uuid.uuid4().int % 10**8:08d}"

    resp, fake_order_id = _create_hold_mocked(unit_id, check_in, check_out, guest_phone=phone)
    assert resp.status_code == 201
    hold_id = resp.json()["hold_id"]
    payment_id = f"pay_race_{uuid.uuid4().hex[:10]}"
    from app.modules.payment_gateway.models import GatewayOrder
    db = SessionLocal()
    try:
        gateway_order = db.scalar(select(GatewayOrder).where(GatewayOrder.gateway_order_id == fake_order_id))
        assert gateway_order is not None
        amount_paise = round(float(gateway_order.amount) * 100)
    finally:
        db.close()
    webhook_body = _razorpay_captured_payload(fake_order_id, payment_id, amount_paise)

    with patch("app.modules.payment_gateway.router.verify_webhook_signature", return_value=True), \
         patch("app.modules.notifications.service.send_email", return_value=None) as mock_send:
        webhook_resp = client.post("/api/v1/payment-gateway/webhook", content=webhook_body, headers={"X-Razorpay-Signature": "sig"})
        assert webhook_resp.status_code == 200

        sig = _make_signature(fake_order_id, payment_id, FAKE_RAZORPAY_KEY_SECRET)
        with patch("app.modules.public_booking.router.verify_payment_signature", return_value=True):
            verify_resp = client.post(f"/api/v1/public/holds/{hold_id}/verify-payment", json={
                "razorpay_payment_id": payment_id, "razorpay_order_id": fake_order_id, "razorpay_signature": sig,
            })
        assert verify_resp.status_code == 201, verify_resp.text

    owner_sends = [c for c in mock_send.call_args_list if c.args[1].startswith("New Direct Website Booking")]
    assert len(owner_sends) == 2, f"Expected exactly 2 owner emails total (webhook path only), browser replay must not re-send, got {len(owner_sends)}"


# ---------------------------------------------------------------------------
# Customer email — preserved, correct recipient/content, honest failure
# ---------------------------------------------------------------------------

def test_customer_receives_confirmation_at_correct_recipient_with_booking_details(headers):
    _, unit_id = _make_available_unit(headers)
    check_in = date.today() + timedelta(days=920)
    check_out = check_in + timedelta(days=2)
    guest_email = f"customer{uuid.uuid4().hex[:8]}@example.com"

    fake_order_id = f"order_{uuid.uuid4().hex[:12]}"
    with patch("app.modules.public_booking.router.create_order") as mock_order:
        mock_order.return_value = {"id": fake_order_id}
        hold_resp = client.post("/api/v1/public/holds", json={
            "unit_id": str(unit_id), "check_in": check_in.isoformat(), "check_out": check_out.isoformat(),
            "guest_name": "Real Customer", "guest_phone": f"70{uuid.uuid4().int % 10**8:08d}", "guest_email": guest_email,
        })
    assert hold_resp.status_code == 201
    hold_id = hold_resp.json()["hold_id"]
    payment_id = f"pay_{uuid.uuid4().hex[:10]}"
    sig = _make_signature(fake_order_id, payment_id, FAKE_RAZORPAY_KEY_SECRET)

    with patch("app.modules.public_booking.router.verify_payment_signature", return_value=True), \
         patch("app.modules.notifications.service.send_email", return_value=None) as mock_send:
        verify_resp = client.post(f"/api/v1/public/holds/{hold_id}/verify-payment", json={
            "razorpay_payment_id": payment_id, "razorpay_order_id": fake_order_id, "razorpay_signature": sig,
        })
    assert verify_resp.status_code == 201, verify_resp.text
    body = verify_resp.json()
    # Notification dispatch is now a background task scheduled after this
    # response is built (payment->booking timeout fix — see
    # dispatch_booking_notifications' docstring), so the outcome isn't known
    # yet on this fast path.
    assert body["confirmation_emailed"] is None

    customer_sends = [c for c in mock_send.call_args_list if c.args[0] == guest_email]
    # Booking/notification finalization audit: the guest gets exactly ONE
    # confirmation email, with the invoice attached to it directly — not a
    # separate invoice-less confirmation plus a second invoice email.
    assert len(customer_sends) == 1, f"Expected exactly 1 email to the guest, got {len(customer_sends)}"
    assert "Confirmed" in customer_sends[0].args[1]
    assert customer_sends[0].args[3] is not None, "Guest confirmation email must carry the invoice PDF"
    assert body["invoice_no"] is not None  # invoice reference available to the customer

    # Replay (hold now "converted") reads the real outcome back from
    # NotificationLog, once the background dispatch above has completed.
    with patch("app.modules.public_booking.router.verify_payment_signature", return_value=True):
        replay_resp = client.post(f"/api/v1/public/holds/{hold_id}/verify-payment", json={
            "razorpay_payment_id": payment_id, "razorpay_order_id": fake_order_id, "razorpay_signature": sig,
        })
    assert replay_resp.status_code == 201, replay_resp.text
    assert replay_resp.json()["confirmation_emailed"] is True


def test_customer_email_failure_reported_honestly_not_as_sent(headers):
    """When the actual send fails, confirmation_emailed must be False, never True — once the outcome is known."""
    _, unit_id = _make_available_unit(headers)
    check_in = date.today() + timedelta(days=925)
    check_out = check_in + timedelta(days=2)

    fake_order_id = f"order_{uuid.uuid4().hex[:12]}"
    with patch("app.modules.public_booking.router.create_order") as mock_order:
        mock_order.return_value = {"id": fake_order_id}
        hold_resp = client.post("/api/v1/public/holds", json={
            "unit_id": str(unit_id), "check_in": check_in.isoformat(), "check_out": check_out.isoformat(),
            "guest_name": "Fail Test Guest", "guest_phone": f"71{uuid.uuid4().int % 10**8:08d}", "guest_email": "failtest@example.com",
        })
    assert hold_resp.status_code == 201
    hold_id = hold_resp.json()["hold_id"]
    payment_id = f"pay_{uuid.uuid4().hex[:10]}"
    sig = _make_signature(fake_order_id, payment_id, FAKE_RAZORPAY_KEY_SECRET)

    with patch("app.modules.public_booking.router.verify_payment_signature", return_value=True), \
         patch("app.modules.notifications.service.send_email", side_effect=RuntimeError("simulated SMTP failure")):
        verify_resp = client.post(f"/api/v1/public/holds/{hold_id}/verify-payment", json={
            "razorpay_payment_id": payment_id, "razorpay_order_id": fake_order_id, "razorpay_signature": sig,
        })
    assert verify_resp.status_code == 201  # booking itself must still succeed
    # Fast path: dispatch just scheduled, outcome not yet known — never
    # falsely reported as sent (True) on this response either.
    assert verify_resp.json()["confirmation_emailed"] is None

    # Replay reads the real (failed) outcome back from NotificationLog.
    with patch("app.modules.public_booking.router.verify_payment_signature", return_value=True):
        replay_resp = client.post(f"/api/v1/public/holds/{hold_id}/verify-payment", json={
            "razorpay_payment_id": payment_id, "razorpay_order_id": fake_order_id, "razorpay_signature": sig,
        })
    assert replay_resp.status_code == 201
    assert replay_resp.json()["confirmation_emailed"] is False  # never falsely reported as sent


# ---------------------------------------------------------------------------
# WhatsApp — honest abstraction, no real/assumed provider
# ---------------------------------------------------------------------------

def test_send_whatsapp_raises_not_configured_when_env_empty(monkeypatch):
    monkeypatch.setattr(settings, "whatsapp_access_token", "")
    monkeypatch.setattr(settings, "whatsapp_phone_number_id", "")
    with pytest.raises(WhatsAppNotConfiguredError):
        notif_service.send_whatsapp("+919999999999", "test message")


def test_owner_whatsapp_attempts_reach_both_configured_numbers_but_fail_honestly(headers):
    """
    Business requirement: every paid direct website booking must ATTEMPT
    owner notification to both WhatsApp numbers (+91 8870237237 primary,
    +91 9361946138 secondary) — exactly as it does for both owner emails.
    No WhatsApp provider is configured anywhere in this project, so both
    attempts are expected to fail honestly (never a fabricated success),
    but BOTH must be attempted and BOTH must be logged.
    """
    _, unit_id = _make_available_unit(headers)
    check_in = date.today() + timedelta(days=930)
    check_out = check_in + timedelta(days=2)

    with patch("app.modules.notifications.service.send_email", return_value=None):
        booking_id, *_ = _complete_valid_booking(headers, unit_id, check_in, check_out)

    db = SessionLocal()
    try:
        row = db.scalar(select(BusinessSettings))
        assert row.whatsapp_business_number and row.secondary_whatsapp_number and row.whatsapp_business_number != row.secondary_whatsapp_number

        wa_logs = db.scalars(select(NotificationLog).where(
            NotificationLog.booking_id == uuid.UUID(booking_id),
            NotificationLog.event_type == "owner_booking_notification",
            NotificationLog.channel == "whatsapp",
        )).all()
        recipients = {log.recipient for log in wa_logs}
        assert recipients == {row.whatsapp_business_number, row.secondary_whatsapp_number}, \
            f"Expected WhatsApp attempts to both {row.whatsapp_business_number} and {row.secondary_whatsapp_number}, got {recipients}"
        assert len(wa_logs) == 2  # exactly one attempt per number, not zero, not duplicated
        assert all(log.success is False for log in wa_logs)  # honest — no provider configured, never a fake success
        assert all("not configured" in (log.error_message or "").lower() for log in wa_logs)
    finally:
        db.close()


def test_customer_whatsapp_attempt_logged_as_failed_not_fake_success(headers):
    _, unit_id = _make_available_unit(headers)
    check_in = date.today() + timedelta(days=935)
    check_out = check_in + timedelta(days=2)

    with patch("app.modules.notifications.service.send_email", return_value=None):
        booking_id, *_ = _complete_valid_booking(headers, unit_id, check_in, check_out)

    db = SessionLocal()
    try:
        log = db.scalar(select(NotificationLog).where(
            NotificationLog.booking_id == uuid.UUID(booking_id),
            NotificationLog.event_type == "booking_confirmation",
            NotificationLog.channel == "whatsapp",
        ))
        assert log is not None
        assert log.success is False
        assert "not configured" in (log.error_message or "").lower()
    finally:
        db.close()


def test_confirmation_page_never_claims_whatsapp_sent_when_not_configured(headers):
    _, unit_id = _make_available_unit(headers)
    check_in = date.today() + timedelta(days=940)
    check_out = check_in + timedelta(days=2)

    with patch("app.modules.notifications.service.send_email", return_value=None):
        booking_id, hold_id, fake_order_id, payment_id = _complete_valid_booking(headers, unit_id, check_in, check_out)

    sig = _make_signature(fake_order_id, payment_id, FAKE_RAZORPAY_KEY_SECRET)
    replay_resp = client.post(f"/api/v1/public/holds/{hold_id}/verify-payment", json={
        "razorpay_payment_id": payment_id, "razorpay_order_id": fake_order_id, "razorpay_signature": sig,
    })
    assert replay_resp.status_code == 201
    assert replay_resp.json()["confirmation_whatsapp_sent"] is False


def test_send_whatsapp_success_when_configured_abstraction_is_testable(monkeypatch):
    """Proves the provider abstraction itself works end-to-end (request
    shape, auth header, endpoint URL) once real credentials exist — without
    ever making a real network call. This is the "configured-provider
    abstraction is testable" requirement."""
    monkeypatch.setattr(settings, "whatsapp_access_token", "fake_test_token_never_real")
    monkeypatch.setattr(settings, "whatsapp_phone_number_id", "1234567890")
    monkeypatch.setattr(settings, "whatsapp_api_version", "v19.0")

    fake_response = MagicMock()
    fake_response.raise_for_status.return_value = None
    with patch("app.modules.notifications.service.httpx.post", return_value=fake_response) as mock_post:
        notif_service.send_whatsapp("+919361946138", "Test message")

    mock_post.assert_called_once()
    call_args, call_kwargs = mock_post.call_args
    assert call_args[0] == "https://graph.facebook.com/v19.0/1234567890/messages"
    assert call_kwargs["headers"]["Authorization"] == "Bearer fake_test_token_never_real"
    assert call_kwargs["json"]["to"] == "919361946138"
    assert call_kwargs["json"]["type"] == "text"


def test_whatsapp_duplicate_protection_per_recipient(headers):
    """Direct service-level test of the per-recipient idempotency guard
    trigger_website_booking_owner_notification relies on — calling it
    twice for the same booking must not re-log a second attempt for a
    recipient already notified."""
    _, unit_id = _make_available_unit(headers)
    check_in = date.today() + timedelta(days=945)
    check_out = check_in + timedelta(days=2)

    with patch("app.modules.notifications.service.send_email", return_value=None):
        booking_id, *_ = _complete_valid_booking(headers, unit_id, check_in, check_out)

    db = SessionLocal()
    try:
        booking = db.get(Booking, uuid.UUID(booking_id))
        from app.modules.payments.models import Invoice
        invoice = db.scalar(select(Invoice).where(Invoice.booking_id == booking.id))

        before = db.scalars(select(NotificationLog).where(
            NotificationLog.booking_id == booking.id,
            NotificationLog.event_type == "owner_booking_notification",
        )).all()
        before_count = len(before)

        # Calling the trigger a SECOND time directly (simulating some future
        # call site invoking it again) must not add any new log rows.
        notif_service.trigger_website_booking_owner_notification(db, booking, invoice)
        db.commit()

        after = db.scalars(select(NotificationLog).where(
            NotificationLog.booking_id == booking.id,
            NotificationLog.event_type == "owner_booking_notification",
        )).all()
        assert len(after) == before_count, "Calling the owner-notification trigger twice must not create duplicate log rows"
    finally:
        db.close()


# ---------------------------------------------------------------------------
# Payment — invalid never notifies, valid notifies exactly once
# ---------------------------------------------------------------------------

def test_invalid_signature_never_triggers_any_notification(headers):
    _, unit_id = _make_available_unit(headers)
    check_in = date.today() + timedelta(days=950)
    check_out = check_in + timedelta(days=2)

    resp, fake_order_id = _create_hold_mocked(unit_id, check_in, check_out)
    assert resp.status_code == 201
    hold_id = resp.json()["hold_id"]

    with patch("app.modules.notifications.service.send_email") as mock_send:
        bad_resp = client.post(f"/api/v1/public/holds/{hold_id}/verify-payment", json={
            "razorpay_payment_id": "pay_invalid", "razorpay_order_id": fake_order_id, "razorpay_signature": "not_a_real_signature",
        })
    assert bad_resp.status_code == 400
    mock_send.assert_not_called()

    db = SessionLocal()
    try:
        from app.modules.public_booking.models import BookingHold
        hold = db.get(BookingHold, hold_id)
        logs = db.scalars(select(NotificationLog).where(NotificationLog.event_type == "owner_booking_notification")).all()
        # No booking exists for this hold, so there is nothing a stray log row could even attach to
        assert hold.status == "pending_payment"
        assert hold.booking_id is None
    finally:
        db.close()


def test_valid_payment_triggers_owner_notification_exactly_once(headers):
    _, unit_id = _make_available_unit(headers)
    check_in = date.today() + timedelta(days=955)
    check_out = check_in + timedelta(days=2)

    with patch("app.modules.notifications.service.send_email", return_value=None) as mock_send:
        booking_id, *_ = _complete_valid_booking(headers, unit_id, check_in, check_out)

    owner_sends = [c for c in mock_send.call_args_list if c.args[1].startswith("New Direct Website Booking")]
    assert len(owner_sends) == 2  # exactly once per owner address, not zero, not more

    db = SessionLocal()
    try:
        logs = db.scalars(select(NotificationLog).where(
            NotificationLog.booking_id == uuid.UUID(booking_id),
            NotificationLog.event_type == "owner_booking_notification",
            NotificationLog.channel == "email",
        )).all()
        assert len(logs) == 2
    finally:
        db.close()
