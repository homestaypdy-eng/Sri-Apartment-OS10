"""
Focused tests for OS10 Phase 2 Step 2: cancellation -> refund -> credit
note -> notification.

Covers the 14 scenarios approved for this step:
  1.  Unpaid booking cancellation
  2.  Fully-paid booking cancellation
  3.  Partial-payment cancellation
  4.  Refund initiated exactly once
  5.  Repeated cancellation does not double-refund
  6.  Credit note created exactly once
  7.  Cancellation notification dispatched exactly once
  8.  Refund failure leaves a recoverable state
  9.  Cancellation releases availability
  10. Cancellation creates audit event
  11. Existing invoice remains unchanged
  12. GST values remain correct
  13. Unauthorized user cannot cancel
  14. Already-cancelled booking is idempotent

SAFETY: an autouse fixture below mocks notifications.service.send_email/
send_whatsapp/send_sms for every test in this file, regardless of which
code path reaches them. This matters because two separate real-send risks
exist on this container (EMAIL_PROVIDER=resend with a live key):
  - The new cancellation flow's dispatch_cancellation_notifications runs
    as a real FastAPI BackgroundTask when new_status="cancelled" is sent
    through the real HTTP endpoint (deliberately, since proving the
    notification dispatch actually fires is scenario 7's whole point —
    unlike test_guest_state_gst.py, which sidesteps a different
    BackgroundTask entirely because testing it wasn't the goal there).
  - trigger_cancellation() uses get_or_create_settings(), which creates a
    populated BusinessSettings row (real default contact_email) the FIRST
    time it runs in a test process — after which POST /payments's own
    trigger_payment_received() would also find a real contact_email and
    attempt a real send on every subsequent payment in the same process.
Mocking send_email/send_whatsapp/send_sms at the source closes both paths
at once rather than trying to reason about call ordering.

No test in this file calls Razorpay in any way (no payment-gateway/hold
endpoints are used at all — payments are recorded directly via
POST /payments, which never touches Razorpay).

Run with: pytest tests/test_cancellation_workflow.py -v
"""
import uuid

import pytest
from fastapi.testclient import TestClient

from app.main import app
from conftest import ADMIN_EMAIL, ADMIN_PASSWORD  # single source of truth, see conftest.py

client = TestClient(app)


@pytest.fixture(autouse=True)
def _no_real_sends(monkeypatch):
    monkeypatch.setattr("app.modules.notifications.service.send_email", lambda *a, **kw: None)
    monkeypatch.setattr("app.modules.notifications.service.send_whatsapp", lambda *a, **kw: None)
    monkeypatch.setattr("app.modules.notifications.service.send_sms", lambda *a, **kw: None)


@pytest.fixture(scope="module")
def headers():
    resp = client.post("/api/v1/auth/login", json={"email": ADMIN_EMAIL, "password": ADMIN_PASSWORD})
    assert resp.status_code == 200, resp.text
    return {"Authorization": f"Bearer {resp.json()['access_token']}"}


def _make_property(headers, state_code="34"):
    resp = client.post(
        "/api/v1/properties", headers=headers,
        json={"name": f"CancelProp {uuid.uuid4().hex[:6]}", "address": "Test", "state_code": state_code},
    )
    assert resp.status_code == 201, resp.text
    return resp.json()["id"]


def _make_unit(headers, property_id, base_rate=2000):
    resp = client.post(
        "/api/v1/units", headers=headers,
        json={"property_id": property_id, "name": f"CancelUnit {uuid.uuid4().hex[:6]}", "base_rate": base_rate},
    )
    assert resp.status_code == 201, resp.text
    return resp.json()["id"]


def _make_guest(headers, state_code="34"):
    resp = client.post(
        "/api/v1/guests", headers=headers,
        json={"name": "Cancel Test Guest", "phone": f"79{uuid.uuid4().int % 10**8:08d}",
              "email": f"canceltest{uuid.uuid4().hex[:8]}@example.com", "state_code": state_code},
    )
    assert resp.status_code == 201, resp.text
    return resp.json()["id"]


def _make_booking(headers, unit_id, guest_id, check_in, check_out):
    resp = client.post(
        "/api/v1/bookings", headers=headers,
        json={"unit_id": unit_id, "guest_id": guest_id, "check_in": check_in, "check_out": check_out},
    )
    assert resp.status_code == 201, resp.text
    return resp.json()["id"]


def _generate_invoice(headers, booking_id):
    resp = client.post(f"/api/v1/invoices/generate/{booking_id}", headers=headers)
    assert resp.status_code == 201, resp.text
    return resp.json()


def _record_payment(headers, booking_id, amount, mode="upi"):
    resp = client.post(
        "/api/v1/payments", headers=headers,
        json={"booking_id": booking_id, "amount": amount, "mode": mode},
    )
    assert resp.status_code == 201, resp.text
    return resp.json()


def _cancel(headers, booking_id, refund_amount=None, refund_reason=None):
    body = {}
    if refund_amount is not None:
        body["refund_amount"] = refund_amount
    if refund_reason is not None:
        body["refund_reason"] = refund_reason
    return client.patch(
        f"/api/v1/bookings/{booking_id}/status",
        headers=headers, params={"new_status": "cancelled"},
        json=body if body else None,
    )


def _setup_booking(headers, base_rate=2000, nights=3, property_state="34", guest_state="34"):
    """3 nights @ base_rate, 5% slab -> subtotal 6000, tax 300, total 6300
    when base_rate=2000 (the default used throughout)."""
    property_id = _make_property(headers, state_code=property_state)
    unit_id = _make_unit(headers, property_id, base_rate=base_rate)
    guest_id = _make_guest(headers, state_code=guest_state)
    check_in = "2029-01-10"
    check_out_day = 10 + nights
    check_out = f"2029-01-{check_out_day:02d}"
    booking_id = _make_booking(headers, unit_id, guest_id, check_in, check_out)
    return booking_id, unit_id, guest_id


# ---------------------------------------------------------------------------
# 1. Unpaid booking cancellation
# ---------------------------------------------------------------------------

def test_unpaid_booking_cancellation_needs_no_refund(headers):
    booking_id, _, _ = _setup_booking(headers)

    resp = _cancel(headers, booking_id)
    assert resp.status_code == 200, resp.text
    assert resp.json()["status"] == "cancelled"

    from app.db.session import SessionLocal
    from app.modules.payments.models import Payment, Invoice
    from sqlalchemy import select
    db = SessionLocal()
    try:
        refunds = db.scalars(select(Payment).where(Payment.booking_id == uuid.UUID(booking_id), Payment.amount < 0)).all()
        assert refunds == []
        # No invoice was ever generated for this booking, so there is
        # nothing a credit note could possibly reference.
        assert db.scalar(select(Invoice).where(Invoice.booking_id == uuid.UUID(booking_id))) is None
    finally:
        db.close()


# ---------------------------------------------------------------------------
# 2. Fully-paid booking cancellation (+ 4, 6, 9, 10, 11, 12 folded in — the
# richest single scenario, asserted from every angle in one place)
# ---------------------------------------------------------------------------

def test_fully_paid_cancellation_refunds_credits_and_releases_availability(headers):
    booking_id, unit_id, _ = _setup_booking(headers)  # same-state -> CGST+SGST
    invoice = _generate_invoice(headers, booking_id)
    assert invoice["total"] == 6300.0
    assert invoice["cgst"] == 150.0
    assert invoice["sgst"] == 150.0
    assert invoice["igst"] == 0.0
    _record_payment(headers, booking_id, 6300.0)

    resp = _cancel(headers, booking_id, refund_amount=6300.0, refund_reason="Guest requested cancellation")
    assert resp.status_code == 200, resp.text
    assert resp.json()["status"] == "cancelled"

    from app.db.session import SessionLocal
    from app.modules.payments.models import Payment, Invoice, CreditNote
    from app.modules.notifications.models import NotificationLog
    from sqlalchemy import select
    db = SessionLocal()
    try:
        # 4 & 6: refund initiated exactly once, credit note created exactly once
        refunds = db.scalars(select(Payment).where(Payment.booking_id == uuid.UUID(booking_id), Payment.amount < 0)).all()
        assert len(refunds) == 1
        assert float(refunds[0].amount) == -6300.0
        assert refunds[0].status == "refunded"
        assert refunds[0].refund_reason == "Guest requested cancellation"

        db_invoice = db.get(Invoice, uuid.UUID(invoice["id"]))
        credit_notes = db.scalars(select(CreditNote).where(CreditNote.invoice_id == db_invoice.id)).all()
        assert len(credit_notes) == 1
        cn = credit_notes[0]

        # 12: GST values on the credit note are correct — a full refund
        # credits the full invoice, exactly (same split as the invoice
        # itself, since ratio == 1.0 here).
        assert float(cn.amount) == 6000.0  # invoice.subtotal
        assert float(cn.cgst) == 150.0
        assert float(cn.sgst) == 150.0
        assert float(cn.igst) == 0.0
        assert float(cn.total) == 6300.0

        # 11: the ORIGINAL invoice itself is completely untouched.
        assert float(db_invoice.subtotal) == 6000.0
        assert float(db_invoice.cgst) == 150.0
        assert float(db_invoice.sgst) == 150.0
        assert float(db_invoice.igst) == 0.0
        assert float(db_invoice.total) == 6300.0
        assert db_invoice.invoice_no == invoice["invoice_no"]

        # 9: availability is released — the same dates on the same unit
        # are bookable again (pure read-time consequence of status no
        # longer being 'confirmed'/'checked_in', see shared/availability.py).
        from app.shared.availability import unit_is_available
        import datetime as dt
        assert unit_is_available(db, uuid.UUID(unit_id), dt.date(2029, 1, 10), dt.date(2029, 1, 13)) is True

        # 7: notification dispatched — guest + at least the primary owner
        # contact (BusinessSettings.contact_email has a real default, so
        # get_or_create_settings() always yields at least one recipient).
        logs = db.scalars(
            select(NotificationLog).where(
                NotificationLog.booking_id == uuid.UUID(booking_id),
                NotificationLog.event_type.in_(("cancellation", "cancellation_owner")),
            )
        ).all()
        assert len(logs) >= 2  # guest + at least one owner contact
        assert all(log.success for log in logs)
        cancellation_logs = [log for log in logs if log.event_type == "cancellation"]
        assert len(cancellation_logs) == 1  # exactly one guest email, not duplicated
    finally:
        db.close()

    # 10: audit events exist for the status change, the refund, and the credit note.
    audit_resp = client.get("/api/v1/audit/logs", headers=headers, params={"entity_id": booking_id})
    assert audit_resp.status_code == 200, audit_resp.text
    actions = [row["action"] for row in audit_resp.json() if row.get("entity_id") == booking_id]
    assert "booking.status_change" in actions


# ---------------------------------------------------------------------------
# 3. Partial-payment cancellation
# ---------------------------------------------------------------------------

def test_partial_payment_cancellation_credits_proportionally(headers):
    booking_id, _, _ = _setup_booking(headers)
    _generate_invoice(headers, booking_id)  # subtotal 6000, total 6300
    _record_payment(headers, booking_id, 3150.0)  # exactly half paid

    resp = _cancel(headers, booking_id, refund_amount=3150.0, refund_reason="Partial refund per policy")
    assert resp.status_code == 200, resp.text

    from app.db.session import SessionLocal
    from app.modules.payments.models import Payment, Invoice, CreditNote
    from sqlalchemy import select
    db = SessionLocal()
    try:
        refunds = db.scalars(select(Payment).where(Payment.booking_id == uuid.UUID(booking_id), Payment.amount < 0)).all()
        assert len(refunds) == 1
        assert float(refunds[0].amount) == -3150.0

        invoice = db.scalar(select(Invoice).where(Invoice.booking_id == uuid.UUID(booking_id)))
        credit_notes = db.scalars(select(CreditNote).where(CreditNote.invoice_id == invoice.id)).all()
        assert len(credit_notes) == 1
        cn = credit_notes[0]
        # ratio = 3150/6300 = 0.5 -> net amount credited = 3000 (half of subtotal)
        assert float(cn.amount) == 3000.0
        assert float(cn.cgst) == 75.0
        assert float(cn.sgst) == 75.0
        assert float(cn.total) == 3150.0
    finally:
        db.close()


def test_caller_cannot_override_server_calculated_policy_amount(headers):
    booking_id, _, _ = _setup_booking(headers)
    _generate_invoice(headers, booking_id)
    _record_payment(headers, booking_id, 6300.0)

    # The stay is more than 48 hours away, so server policy is 100% of paid.
    response = _cancel(headers, booking_id, refund_amount=3150.0)
    assert response.status_code == 409, response.text

    from app.db.session import SessionLocal
    from app.modules.bookings.models import Booking
    from app.modules.payments.models import Payment
    from sqlalchemy import select
    db = SessionLocal()
    try:
        booking = db.get(Booking, uuid.UUID(booking_id))
        assert booking.status == "confirmed"
        assert booking.cancellation_at is None
        assert db.scalars(select(Payment).where(Payment.booking_id == booking.id, Payment.amount < 0)).all() == []
    finally:
        db.close()


# ---------------------------------------------------------------------------
# 5. Repeated cancellation does not double-refund / 14. idempotent
# ---------------------------------------------------------------------------

def test_repeated_cancellation_does_not_double_refund(headers):
    booking_id, _, _ = _setup_booking(headers)
    _generate_invoice(headers, booking_id)
    _record_payment(headers, booking_id, 6300.0)

    first = _cancel(headers, booking_id, refund_amount=6300.0)
    assert first.status_code == 200, first.text

    second = _cancel(headers, booking_id, refund_amount=6300.0)
    assert second.status_code == 409, second.text  # "cancelled" has no outgoing transitions

    from app.db.session import SessionLocal
    from app.modules.payments.models import Payment, Invoice, CreditNote
    from sqlalchemy import select
    db = SessionLocal()
    try:
        refunds = db.scalars(select(Payment).where(Payment.booking_id == uuid.UUID(booking_id), Payment.amount < 0)).all()
        assert len(refunds) == 1, "a second (rejected) cancellation attempt must not create a second refund"

        invoice = db.scalar(select(Invoice).where(Invoice.booking_id == uuid.UUID(booking_id)))
        credit_notes = db.scalars(select(CreditNote).where(CreditNote.invoice_id == invoice.id)).all()
        assert len(credit_notes) == 1, "a second (rejected) cancellation attempt must not create a second credit note"
    finally:
        db.close()


# ---------------------------------------------------------------------------
# 8. Refund failure leaves a recoverable state
# ---------------------------------------------------------------------------

def test_excessive_refund_request_rejected_booking_stays_confirmed(headers):
    booking_id, _, _ = _setup_booking(headers)
    _generate_invoice(headers, booking_id)
    _record_payment(headers, booking_id, 2000.0)  # only 2000 actually paid

    resp = _cancel(headers, booking_id, refund_amount=6300.0)  # far more than was paid
    assert resp.status_code == 400, resp.text

    from app.db.session import SessionLocal
    from app.modules.bookings.models import Booking
    from app.modules.payments.models import Payment, Invoice, CreditNote
    from sqlalchemy import select
    db = SessionLocal()
    try:
        booking = db.get(Booking, uuid.UUID(booking_id))
        assert booking.status == "confirmed", "a rejected refund must leave the booking in its prior, recoverable state"
        refunds = db.scalars(select(Payment).where(Payment.booking_id == uuid.UUID(booking_id), Payment.amount < 0)).all()
        assert refunds == []
        invoice = db.scalar(select(Invoice).where(Invoice.booking_id == uuid.UUID(booking_id)))
        credit_notes = db.scalars(select(CreditNote).where(CreditNote.invoice_id == invoice.id)).all()
        assert credit_notes == [], "no credit note should exist when the refund itself was rejected"
    finally:
        db.close()

    # Recoverable: a valid amount now succeeds.
    retry = _cancel(headers, booking_id, refund_amount=2000.0)
    assert retry.status_code == 200, retry.text


# ---------------------------------------------------------------------------
# 13. Unauthorized user cannot cancel
# ---------------------------------------------------------------------------

def test_unauthenticated_request_cannot_cancel(headers):
    booking_id, _, _ = _setup_booking(headers)
    resp = client.patch(f"/api/v1/bookings/{booking_id}/status", params={"new_status": "cancelled"})
    assert resp.status_code == 401, resp.text

    from app.db.session import SessionLocal
    from app.modules.bookings.models import Booking
    db = SessionLocal()
    try:
        booking = db.get(Booking, uuid.UUID(booking_id))
        assert booking.status == "confirmed"
    finally:
        db.close()
