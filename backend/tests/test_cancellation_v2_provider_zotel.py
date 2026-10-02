"""
OS10 Phase 2B: real (mocked) Razorpay refund architecture, refund-failure
safety, provider-side idempotency, and Zotel-triggered cancellation —
built on top of Step 2's already-tested manual cancellation workflow
(tests/test_cancellation_workflow.py, unchanged and still passing).

Covers the NEW ground from Part 12's 22 scenarios not already covered by
Step 2's suite: Razorpay success/failure (mocked), retry, duplicate-refund
protection via the real gateway_refund_id DB constraint (not a Python
bool), Zotel unpaid/paid/repeated/unknown/already-cancelled cancellation,
availability + audit + notification-idempotency for the Zotel path, and
GST/invoice-unchanged checks specific to the provider-refund path.

SAFETY: create_refund is ALWAYS mocked — this file makes no real call to
Razorpay. The same file-wide send_email/send_whatsapp/send_sms mock from
Step 2 is repeated here (both test files touch the same
BusinessSettings-creation risk independently).

Run with: pytest tests/test_cancellation_v2_provider_zotel.py -v
"""
import uuid
from concurrent.futures import ThreadPoolExecutor
from datetime import date, timedelta
from threading import Event
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from app.main import app
from app.db.session import SessionLocal
from app.modules.bookings.models import Booking, Guest
from app.modules.payment_gateway.models import Refund
from app.modules.payment_gateway.service import GatewayNotConfiguredError
from app.modules.payments.models import CreditNote, Invoice, Payment
from app.modules.notifications.models import NotificationLog
from app.modules.zotel import service as zotel_service
from app.modules.zotel.models import ZotelRoomMapping
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
    resp = client.post("/api/v1/properties", headers=headers,
                        json={"name": f"CancelV2Prop {uuid.uuid4().hex[:6]}", "address": "Test", "state_code": state_code})
    assert resp.status_code == 201, resp.text
    return resp.json()["id"]


def _make_unit(headers, property_id, base_rate=2000):
    resp = client.post("/api/v1/units", headers=headers,
                        json={"property_id": property_id, "name": f"CancelV2Unit {uuid.uuid4().hex[:6]}", "base_rate": base_rate})
    assert resp.status_code == 201, resp.text
    return resp.json()["id"]


def _make_guest(headers, state_code="34"):
    resp = client.post("/api/v1/guests", headers=headers,
                        json={"name": "Cancel V2 Guest", "phone": f"80{uuid.uuid4().int % 10**8:08d}",
                              "email": f"cancelv2{uuid.uuid4().hex[:8]}@example.com", "state_code": state_code})
    assert resp.status_code == 201, resp.text
    return resp.json()["id"]


def _make_booking(headers, unit_id, guest_id, check_in, check_out):
    resp = client.post("/api/v1/bookings", headers=headers,
                        json={"unit_id": unit_id, "guest_id": guest_id, "check_in": check_in, "check_out": check_out})
    assert resp.status_code == 201, resp.text
    return resp.json()["id"]


def _generate_invoice(headers, booking_id):
    resp = client.post(f"/api/v1/invoices/generate/{booking_id}", headers=headers)
    assert resp.status_code == 201, resp.text
    return resp.json()


def _record_razorpay_payment(booking_id, amount, gateway_payment_id):
    """
    Directly creates a razorpay-mode Payment row (same shape
    _convert_hold_to_booking produces for a real website booking:
    mode="razorpay", reference_no=<the gateway's own payment id>).
    POST /payments (record_payment) only accepts upi/card/bank_transfer/
    cash — it has no route to create a razorpay-mode row, since those are
    only ever created by the real payment-verification flow — so this
    goes directly to the DB, the same accepted pattern used elsewhere in
    this test suite (e.g. test_reports.py's _make_guest for state_code
    before Step 1B added its real write path).
    """
    db = SessionLocal()
    try:
        payment = Payment(booking_id=uuid.UUID(booking_id), amount=amount, mode="razorpay",
                           reference_no=gateway_payment_id, status="completed")
        db.add(payment)
        db.commit()
        db.refresh(payment)
        return payment.id
    finally:
        db.close()


def _cancel(headers, booking_id, refund_amount=None, refund_reason=None):
    body = {}
    if refund_amount is not None:
        body["refund_amount"] = refund_amount
    if refund_reason is not None:
        body["refund_reason"] = refund_reason
    return client.patch(f"/api/v1/bookings/{booking_id}/status", headers=headers,
                         params={"new_status": "cancelled"}, json=body if body else None)


def _retry_refund(headers, booking_id, refund_amount, refund_reason=None):
    body = {"refund_amount": refund_amount}
    if refund_reason is not None:
        body["refund_reason"] = refund_reason
    return client.post(f"/api/v1/bookings/{booking_id}/retry-refund", headers=headers, json=body)


def _setup_paid_razorpay_booking(headers, base_rate=2000, nights=3, day_offset=400):
    """3 nights @ 2000, 5% slab -> subtotal 6000, tax 300, total 6300."""
    property_id = _make_property(headers, state_code="34")
    unit_id = _make_unit(headers, property_id, base_rate=base_rate)
    guest_id = _make_guest(headers, state_code="34")
    check_in = f"2030-02-{day_offset % 20 + 1:02d}"
    check_out_day = day_offset % 20 + 1 + nights
    check_out = f"2030-02-{check_out_day:02d}" if check_out_day <= 28 else f"2030-03-{check_out_day - 28:02d}"
    booking_id = _make_booking(headers, unit_id, guest_id, check_in, check_out)
    invoice = _generate_invoice(headers, booking_id)
    gateway_payment_id = f"pay_test_{uuid.uuid4().hex[:12]}"
    _record_razorpay_payment(booking_id, invoice["total"], gateway_payment_id)
    return booking_id, invoice, gateway_payment_id


# ---------------------------------------------------------------------------
# Razorpay refund: mocked success
# ---------------------------------------------------------------------------

def test_razorpay_refund_success_creates_payment_refund_row_and_credit_note(headers):
    booking_id, invoice, gateway_payment_id = _setup_paid_razorpay_booking(headers, day_offset=1)

    with patch("app.modules.bookings.cancellation_service.create_refund", return_value={"id": "rfnd_test_success_001"}) as mock_refund:
        resp = _cancel(headers, booking_id, refund_amount=6300.0, refund_reason="Guest requested")
    assert resp.status_code == 200, resp.text
    mock_refund.assert_called_once_with(gateway_payment_id, 6300.0)

    db = SessionLocal()
    try:
        refund_rows = db.scalars(select(Refund)).all()
        matching = [r for r in refund_rows if r.gateway_refund_id == "rfnd_test_success_001"]
        assert len(matching) == 1
        assert matching[0].status == "processed"

        neg_payments = db.scalars(select(Payment).where(Payment.booking_id == uuid.UUID(booking_id), Payment.amount < 0)).all()
        assert len(neg_payments) == 1
        assert float(neg_payments[0].amount) == -6300.0
        assert neg_payments[0].reference_no == "rfnd_test_success_001"

        db_invoice = db.get(Invoice, uuid.UUID(invoice["id"]))
        credit_notes = db.scalars(select(CreditNote).where(CreditNote.invoice_id == db_invoice.id)).all()
        assert len(credit_notes) == 1
        assert float(credit_notes[0].total) == 6300.0
        # Original invoice untouched
        assert float(db_invoice.total) == 6300.0
        assert float(db_invoice.cgst) == 150.0
        assert float(db_invoice.sgst) == 150.0
    finally:
        db.close()


def test_manual_payment_cancellation_never_calls_razorpay(headers):
    property_id = _make_property(headers, state_code="34")
    unit_id = _make_unit(headers, property_id, base_rate=2000)
    guest_id = _make_guest(headers, state_code="34")
    booking_id = _make_booking(headers, unit_id, guest_id, "2030-02-01", "2030-02-04")
    _generate_invoice(headers, booking_id)
    payment = client.post(
        "/api/v1/payments", headers=headers,
        json={"booking_id": booking_id, "amount": 6300, "mode": "upi", "reference_no": "manual-upi-test"},
    )
    assert payment.status_code == 201, payment.text

    with patch("app.modules.bookings.cancellation_service.create_refund") as provider_refund:
        response = _cancel(headers, booking_id, refund_amount=6300)
        assert response.status_code == 200, response.text
        provider_refund.assert_not_called()

    db = SessionLocal()
    try:
        refunds = db.scalars(select(Payment).where(Payment.booking_id == uuid.UUID(booking_id), Payment.amount < 0)).all()
        assert len(refunds) == 1
        assert refunds[0].mode == "upi"
    finally:
        db.close()


# ---------------------------------------------------------------------------
# Razorpay refund: mocked failure — the core of Part 3
# ---------------------------------------------------------------------------

def test_razorpay_refund_failure_is_recoverable_no_false_success(headers):
    booking_id, invoice, gateway_payment_id = _setup_paid_razorpay_booking(headers, day_offset=2)

    with patch("app.modules.bookings.cancellation_service.create_refund", side_effect=Exception("Razorpay: gateway timeout")):
        resp = _cancel(headers, booking_id, refund_amount=6300.0, refund_reason="Guest requested")
    # The cancellation itself still succeeds — only the refund sub-step failed.
    assert resp.status_code == 200, resp.text
    assert resp.json()["status"] == "cancelled"

    db = SessionLocal()
    try:
        booking = db.get(Booking, uuid.UUID(booking_id))
        assert booking.status == "cancelled"
        assert booking.cancellation_requires_review is True
        assert booking.cancellation_refund_source == "razorpay"

        # No false success: no negative Payment row, no credit note.
        neg_payments = db.scalars(select(Payment).where(Payment.booking_id == uuid.UUID(booking_id), Payment.amount < 0)).all()
        assert neg_payments == []

        db_invoice = db.get(Invoice, uuid.UUID(invoice["id"]))
        credit_notes = db.scalars(select(CreditNote).where(CreditNote.invoice_id == db_invoice.id)).all()
        assert credit_notes == []

        # The ambiguous attempt stays reserved and auditable; a timeout may
        # have happened after Razorpay accepted it.
        refund_rows = db.scalars(select(Refund).where(Refund.payment_id.in_(
            select(Payment.id).where(Payment.booking_id == uuid.UUID(booking_id), Payment.amount > 0)
        ))).all()
        assert len(refund_rows) == 1
        assert refund_rows[0].status == "created"
        assert refund_rows[0].gateway_refund_id is None

        from app.modules.audit.models import AuditLog
        failed_audit = db.scalars(select(AuditLog).where(AuditLog.action == "payment.refund_outcome_unresolved", AuditLog.entity_id == str(refund_rows[0].id))).all()
        assert len(failed_audit) == 1
    finally:
        db.close()

    # Until provider reconciliation, the amount remains reserved and cannot
    # be retried as a second refund.
    from app.modules.bookings.cancellation_service import refund_available_for_booking
    db = SessionLocal()
    try:
        assert refund_available_for_booking(db, uuid.UUID(booking_id)) == 0.0
    finally:
        db.close()
    retry_resp = _retry_refund(headers, booking_id, 6300.0, refund_reason="Do not duplicate an unresolved provider refund")
    assert retry_resp.status_code == 409


def test_refund_retry_after_failure_succeeds_and_creates_credit_note(headers):
    booking_id, invoice, gateway_payment_id = _setup_paid_razorpay_booking(headers, day_offset=3)

    from app.modules.payment_gateway.service import GatewayNotConfiguredError
    with patch("app.modules.bookings.cancellation_service.create_refund", side_effect=GatewayNotConfiguredError("provider not configured; request was not sent")):
        _cancel(headers, booking_id, refund_amount=6300.0)

    db = SessionLocal()
    try:
        booking = db.get(Booking, uuid.UUID(booking_id))
        snapshot = (
            booking.cancellation_at,
            booking.cancellation_checkin_at,
            booking.cancellation_policy_percent,
            float(booking.cancellation_refund_target),
            booking.cancellation_refund_source,
        )
        assert snapshot[2:4] == (100, 6300.0)
    finally:
        db.close()

    with patch("app.modules.bookings.cancellation_service.create_refund", return_value={"id": "rfnd_retry_success_001"}) as mock_refund:
        retry_resp = _retry_refund(headers, booking_id, 6300.0, refund_reason="Retry after provider configuration failure")
        assert retry_resp.status_code == 200, retry_resp.text
        mock_refund.assert_called_once_with(gateway_payment_id, 6300.0)

    db = SessionLocal()
    try:
        booking = db.get(Booking, uuid.UUID(booking_id))
        assert (
            booking.cancellation_at,
            booking.cancellation_checkin_at,
            booking.cancellation_policy_percent,
            float(booking.cancellation_refund_target),
            booking.cancellation_refund_source,
        ) == snapshot
    finally:
        db.close()

    db = SessionLocal()
    try:
        neg_payments = db.scalars(select(Payment).where(Payment.booking_id == uuid.UUID(booking_id), Payment.amount < 0)).all()
        assert len(neg_payments) == 1  # the failed attempt created no Payment row; only the successful retry did
        assert float(neg_payments[0].amount) == -6300.0

        db_invoice = db.get(Invoice, uuid.UUID(invoice["id"]))
        credit_notes = db.scalars(select(CreditNote).where(CreditNote.invoice_id == db_invoice.id)).all()
        assert len(credit_notes) == 1
    finally:
        db.close()


def test_second_retry_after_success_is_rejected_no_double_refund(headers):
    booking_id, invoice, gateway_payment_id = _setup_paid_razorpay_booking(headers, day_offset=4)

    with patch("app.modules.bookings.cancellation_service.create_refund", return_value={"id": "rfnd_dup_check_001"}):
        _cancel(headers, booking_id, refund_amount=6300.0)

    # Attempting to retry-refund again on an already fully-refunded booking must be rejected.
    retry_resp = _retry_refund(headers, booking_id, 6300.0)
    assert retry_resp.status_code == 400, retry_resp.text

    db = SessionLocal()
    try:
        neg_payments = db.scalars(select(Payment).where(Payment.booking_id == uuid.UUID(booking_id), Payment.amount < 0)).all()
        assert len(neg_payments) == 1
        refund_rows = db.scalars(select(Refund).where(Refund.gateway_refund_id == "rfnd_dup_check_001")).all()
        assert len(refund_rows) == 1
    finally:
        db.close()


def test_gateway_refund_id_uniqueness_enforced_at_database_level(headers):
    """Part 4: real DB-level idempotency, not a Python boolean — the same
    gateway_refund_id can never be recorded twice, at the constraint level."""
    booking_id, _, _ = _setup_paid_razorpay_booking(headers, day_offset=5)
    db = SessionLocal()
    try:
        payment = db.scalar(select(Payment).where(Payment.booking_id == uuid.UUID(booking_id), Payment.amount > 0))
        r1 = Refund(payment_id=payment.id, amount=100.0, status="processed", gateway_refund_id="rfnd_unique_test_dup")
        db.add(r1)
        db.commit()

        r2 = Refund(payment_id=payment.id, amount=100.0, status="processed", gateway_refund_id="rfnd_unique_test_dup")
        db.add(r2)
        with pytest.raises(IntegrityError):
            db.commit()
        db.rollback()
    finally:
        db.close()


# ---------------------------------------------------------------------------
# Razorpay not configured (a real, distinct failure mode)
# ---------------------------------------------------------------------------

def test_razorpay_not_configured_treated_as_recoverable_failure(headers):
    booking_id, invoice, gateway_payment_id = _setup_paid_razorpay_booking(headers, day_offset=6)

    with patch("app.modules.bookings.cancellation_service.create_refund", side_effect=GatewayNotConfiguredError("Razorpay is not configured")):
        resp = _cancel(headers, booking_id, refund_amount=6300.0)
    assert resp.status_code == 200, resp.text  # cancellation still succeeds

    db = SessionLocal()
    try:
        neg_payments = db.scalars(select(Payment).where(Payment.booking_id == uuid.UUID(booking_id), Payment.amount < 0)).all()
        assert neg_payments == []
        refund_rows = db.scalars(select(Refund).where(Refund.payment_id.in_(
            select(Payment.id).where(Payment.booking_id == uuid.UUID(booking_id), Payment.amount > 0)
        ))).all()
        assert len(refund_rows) == 1
        assert refund_rows[0].status == "failed"
    finally:
        db.close()


def test_cancellation_and_standalone_gateway_refund_share_booking_payment_locks(headers):
    from app.modules.bookings import cancellation_service

    booking_id, _, _ = _setup_paid_razorpay_booking(headers, day_offset=8)
    db = SessionLocal()
    try:
        payment = db.scalar(select(Payment).where(Payment.booking_id == uuid.UUID(booking_id), Payment.amount > 0))
        payment_id = str(payment.id)
    finally:
        db.close()

    provider_started = Event()
    allow_provider_return = Event()

    def pause_after_durable_reservation(payment_gateway_id, amount):
        provider_started.set()
        if not allow_provider_return.wait(timeout=10):
            raise TimeoutError("test did not release mocked provider call")
        return {"id": "rfnd_cross_path_serialized"}

    with ThreadPoolExecutor(max_workers=1) as pool:
        with patch("app.modules.bookings.cancellation_service.create_refund", side_effect=pause_after_durable_reservation) as cancellation_provider, \
             patch("app.modules.payment_gateway.router.create_refund") as standalone_provider:
            cancellation_future = pool.submit(_cancel, headers, booking_id, 6300.0)
            assert provider_started.wait(timeout=10), "cancellation did not reach its mocked provider call"

            # Cancellation has durably reserved the full policy target. The
            # standalone route must wait on the same booking/payment locks,
            # then observe the reservation and refuse another refund.
            response = client.post(
                "/api/v1/payment-gateway/refunds",
                headers=headers,
                json={"payment_id": payment_id, "amount": 1.0, "reason": "concurrency regression"},
            )
            assert response.status_code == 409, response.text
            standalone_provider.assert_not_called()
            allow_provider_return.set()
            cancellation_response = cancellation_future.result(timeout=10)
            assert cancellation_response.status_code == 200, cancellation_response.text
            cancellation_provider.assert_called_once()

    db = SessionLocal()
    try:
        assert cancellation_service.refund_available_for_booking(db, uuid.UUID(booking_id)) == 0.0
    finally:
        db.close()


# ---------------------------------------------------------------------------
# Zotel-triggered cancellation
# ---------------------------------------------------------------------------

def _zotel_mapping(db, unit_id):
    room_type_id = f"rt-{uuid.uuid4().hex[:8]}"
    mapping = ZotelRoomMapping(unit_id=uuid.UUID(unit_id), zotel_room_type_id=room_type_id, zotel_room_type_name="Test Room")
    db.add(mapping)
    db.commit()
    return room_type_id


def _zotel_detail(txn_id, room_type_id, check_in, check_out, status="created", paid_amount=0, total_amount=6300):
    return {"success": True, "meta": {}, "data": {
        "transaction_id": txn_id, "status": status,
        "customer": {"name": "Zotel V2 Guest", "email": None, "phone": f"92{uuid.uuid4().int % 10**9:09d}"},
        "checkin": check_in.isoformat(), "checkout": check_out.isoformat(),
        "total_amount": total_amount, "paid_amount": paid_amount, "due_amount": total_amount - paid_amount,
        "payment_method": "pay_now" if paid_amount else "pay_at_hotel",
        "rooms": [{"room_number": "Z1", "category": {"id": room_type_id, "name": "Test Room"},
                   "rate_plan": {"id": "rp-1", "name": "EP"}, "num_adults": 2, "num_children": 0}],
    }}


def _zotel_import_then_cancel(headers, status_first="created", paid_amount=0, day_offset=100):
    property_id = _make_property(headers, state_code="34")
    unit_id = _make_unit(headers, property_id)
    db = SessionLocal()
    try:
        room_type_id = _zotel_mapping(db, unit_id)
    finally:
        db.close()

    txn_id = f"txn-v2-{uuid.uuid4().hex[:10]}"
    check_in = date.today() + timedelta(days=day_offset)
    check_out = check_in + timedelta(days=2)

    with patch("app.modules.zotel.client.get_all_bookings", return_value=[{"transaction_id": txn_id, "status": status_first}]), \
         patch("app.modules.zotel.client.get_booking_detail", return_value=_zotel_detail(txn_id, room_type_id, check_in, check_out, status=status_first, paid_amount=paid_amount)):
        zotel_service.sync_bookings(SessionLocal())

    booking_id = None
    db = SessionLocal()
    try:
        booking = db.scalar(select(Booking).where(Booking.external_reservation_id == txn_id))
        booking_id = booking.id
    finally:
        db.close()

    with patch("app.modules.zotel.client.get_all_bookings", return_value=[{"transaction_id": txn_id, "status": "cancelled"}]), \
         patch("app.modules.zotel.client.get_booking_detail", return_value=_zotel_detail(txn_id, room_type_id, check_in, check_out, status="cancelled", paid_amount=paid_amount)):
        sync_log = zotel_service.sync_bookings(SessionLocal())

    return booking_id, unit_id, txn_id, sync_log, check_in, check_out


def test_zotel_cancellation_of_unpaid_booking(headers):
    booking_id, unit_id, txn_id, sync_log, check_in, check_out = _zotel_import_then_cancel(headers, paid_amount=0, day_offset=110)
    assert sync_log.records_updated == 1

    db = SessionLocal()
    try:
        booking = db.get(Booking, booking_id)
        assert booking.status == "cancelled"
        neg_payments = db.scalars(select(Payment).where(Payment.booking_id == booking_id, Payment.amount < 0)).all()
        assert neg_payments == []

        from app.shared.availability import unit_is_available
        assert unit_is_available(db, uuid.UUID(unit_id), check_in, check_out) is True

        from app.modules.audit.models import AuditLog
        audit = db.scalars(select(AuditLog).where(AuditLog.entity_id == str(booking_id), AuditLog.action == "booking.status_change")).all()
        assert len(audit) == 1
        assert "zotel" in audit[0].details

        logs = db.scalars(select(NotificationLog).where(NotificationLog.booking_id == booking_id, NotificationLog.event_type == "cancellation_owner")).all()
        assert len(logs) >= 1
    finally:
        db.close()


def test_zotel_cancellation_of_paid_booking_issues_no_automatic_refund(headers):
    """Part 5: refund decision is deliberately NOT automated for Zotel
    cancellations — the business cancellation policy is unresolved (see
    Phase 2B report), and a Zotel-recorded payment has no Razorpay
    reference to refund through in any case."""
    booking_id, unit_id, txn_id, sync_log, check_in, check_out = _zotel_import_then_cancel(headers, paid_amount=6300, day_offset=120)
    assert sync_log.records_updated == 1

    db = SessionLocal()
    try:
        booking = db.get(Booking, booking_id)
        assert booking.status == "cancelled"

        positive_payments = db.scalars(select(Payment).where(Payment.booking_id == booking_id, Payment.amount > 0)).all()
        assert len(positive_payments) == 1
        assert positive_payments[0].mode == "zotel"

        neg_payments = db.scalars(select(Payment).where(Payment.booking_id == booking_id, Payment.amount < 0)).all()
        assert neg_payments == [], "no refund should be auto-issued for a Zotel cancellation"

        from app.modules.audit.models import AuditLog
        audit = db.scalars(select(AuditLog).where(AuditLog.entity_id == str(booking_id), AuditLog.action == "booking.status_change")).all()
        assert "paid_amount=6300.00" in audit[0].details
        assert "refund decision pending" in audit[0].details
    finally:
        db.close()


def test_repeated_zotel_cancellation_is_idempotent(headers):
    booking_id, unit_id, txn_id, first_sync_log, check_in, check_out = _zotel_import_then_cancel(headers, paid_amount=0, day_offset=130)
    room_type_id = None
    db = SessionLocal()
    try:
        mapping = db.scalar(select(ZotelRoomMapping).where(ZotelRoomMapping.unit_id == uuid.UUID(unit_id)))
        room_type_id = mapping.zotel_room_type_id
    finally:
        db.close()

    with patch("app.modules.zotel.client.get_all_bookings", return_value=[{"transaction_id": txn_id, "status": "cancelled"}]), \
         patch("app.modules.zotel.client.get_booking_detail", return_value=_zotel_detail(txn_id, room_type_id, check_in, check_out, status="cancelled")):
        second_sync = zotel_service.sync_bookings(SessionLocal())

    assert second_sync.records_skipped == 1
    assert second_sync.records_updated == 0

    db = SessionLocal()
    try:
        from app.modules.audit.models import AuditLog
        audit = db.scalars(select(AuditLog).where(AuditLog.entity_id == str(booking_id), AuditLog.action == "booking.status_change")).all()
        assert len(audit) == 1, "a repeated Zotel cancellation must not create a second status-change audit entry"

        logs = db.scalars(select(NotificationLog).where(NotificationLog.booking_id == booking_id, NotificationLog.event_type == "cancellation_owner")).all()
        recipients = [l.recipient for l in logs]
        assert len(recipients) == len(set(recipients)), "no recipient should be notified twice"
    finally:
        db.close()


def test_zotel_cancellation_unknown_booking_reference_does_not_crash(headers):
    property_id = _make_property(headers, state_code="34")
    unit_id = _make_unit(headers, property_id)
    db = SessionLocal()
    try:
        room_type_id = _zotel_mapping(db, unit_id)
    finally:
        db.close()

    unknown_txn_id = f"txn-unknown-{uuid.uuid4().hex[:10]}"
    check_in = date.today() + timedelta(days=140)
    check_out = check_in + timedelta(days=2)

    with patch("app.modules.zotel.client.get_all_bookings", return_value=[{"transaction_id": unknown_txn_id, "status": "cancelled"}]), \
         patch("app.modules.zotel.client.get_booking_detail", return_value=_zotel_detail(unknown_txn_id, room_type_id, check_in, check_out, status="cancelled")):
        sync_log = zotel_service.sync_bookings(SessionLocal())

    assert sync_log.records_skipped == 1  # "already cancelled; not imported" — no booking ever created, no crash
    db = SessionLocal()
    try:
        assert db.scalar(select(Booking).where(Booking.external_reservation_id == unknown_txn_id)) is None
    finally:
        db.close()
