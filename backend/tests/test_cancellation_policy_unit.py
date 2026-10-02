"""Pure cancellation-policy boundary tests; no database or provider calls."""
import datetime as dt
from types import SimpleNamespace

import pytest

from app.modules.bookings.cancellation_service import (
    POLICY_TIMEZONE,
    _policy_refund_source,
    evaluate_cancellation_policy,
)


CHECK_IN = dt.date(2030, 4, 15)
CHECK_IN_AT = dt.datetime(2030, 4, 15, 12, 30, tzinfo=POLICY_TIMEZONE)


@pytest.mark.parametrize(
    ("offset", "expected_percent"),
    [
        (dt.timedelta(hours=48, minutes=1), 100),
        (dt.timedelta(hours=48), 50),
        (dt.timedelta(hours=47, minutes=59), 50),
        (dt.timedelta(minutes=-1), 0),
    ],
)
def test_cancellation_policy_boundaries(offset, expected_percent):
    now = (CHECK_IN_AT - offset).astimezone(dt.timezone.utc)
    scheduled, percent, target, review = evaluate_cancellation_policy(CHECK_IN, 10_000, now)
    assert scheduled == CHECK_IN_AT
    assert percent == expected_percent
    assert target == (10_000 if expected_percent == 100 else 5_000 if expected_percent == 50 else 0)
    assert review is (expected_percent == 0)


def test_checkin_is_fixed_1230_in_asia_kolkata_not_guest_arrival_time():
    now = (CHECK_IN_AT - dt.timedelta(hours=49)).astimezone(dt.timezone.utc)
    scheduled, _, _, _ = evaluate_cancellation_policy(CHECK_IN, 2_000, now)
    assert scheduled.hour == 12 and scheduled.minute == 30
    assert scheduled.tzinfo == POLICY_TIMEZONE
    assert scheduled.utcoffset() == dt.timedelta(hours=5, minutes=30)


def test_policy_requires_manual_review_after_scheduled_checkin():
    now = (CHECK_IN_AT + dt.timedelta(minutes=1)).astimezone(dt.timezone.utc)
    _, percent, target, review = evaluate_cancellation_policy(CHECK_IN, 2_000, now)
    assert (percent, target, review) == (0, 0.0, True)


def test_naive_cancellation_timestamp_is_rejected():
    with pytest.raises(ValueError, match="timezone-aware"):
        evaluate_cancellation_policy(CHECK_IN, 2_000, dt.datetime(2030, 4, 13, 12, 30))


def test_ota_origin_never_uses_razorpay_even_if_payment_row_is_misclassified():
    booking = SimpleNamespace(source="ota_booking_com", external_reservation_id="ota-123")
    payment = SimpleNamespace(mode="razorpay", reference_no="pay_provider_id")
    assert _policy_refund_source(booking, [payment]) == ("ota_reconciliation", True)


def test_ota_pay_at_property_uses_manual_ledger_classification():
    booking = SimpleNamespace(source="ota_booking_com", external_reservation_id="ota-123")
    payment = SimpleNamespace(mode="upi", reference_no="manual-ref")
    assert _policy_refund_source(booking, [payment]) == ("manual_pay_at_property", False)
