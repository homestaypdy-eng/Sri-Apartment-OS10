"""
Razorpay integration.

WHAT'S REAL:
- `verify_webhook_signature()` implements Razorpay's documented HMAC-SHA256
  webhook verification exactly. This is pure cryptography, testable today
  with a fake secret and payload — no live account needed, and it's the
  same code that will validate real webhook calls in production the
  moment a real RAZORPAY_WEBHOOK_SECRET is configured.
- `verify_payment_signature()` implements Razorpay's documented client-side
  payment verification: HMAC-SHA256 of "{order_id}|{payment_id}" using
  RAZORPAY_KEY_SECRET. Called after the browser Checkout widget completes,
  before a BookingHold is converted to a confirmed Booking.
- The order/payment/refund data model, statuses, and API contract are real
  and match Razorpay's actual concepts (order_id, payment_id, receipt).

WHAT'S HONESTLY GATED:
- `create_order()` would call Razorpay's live API to create a real payment
  order. This sandbox has no RAZORPAY_KEY_ID/SECRET and no network route to
  api.razorpay.com, so this raises GatewayNotConfiguredError instead of
  faking a response — exactly the same honesty pattern used for SMTP email
  elsewhere in this system. Setting real credentials activates it with no
  code change (the `requests` call is real, just currently unreachable).
"""
import hashlib
import hmac
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP

from app.core.config import settings


class GatewayNotConfiguredError(Exception):
    pass


def verify_webhook_signature(payload_body: bytes, received_signature: str, webhook_secret: str | None = None) -> bool:
    """
    Razorpay's documented webhook verification: HMAC-SHA256 of the raw
    request body, using the webhook secret configured in the Razorpay
    dashboard, compared against the X-Razorpay-Signature header.
    """
    secret = webhook_secret or settings.razorpay_webhook_secret
    if not secret:
        raise GatewayNotConfiguredError("RAZORPAY_WEBHOOK_SECRET is not configured")

    expected = hmac.new(secret.encode(), payload_body, hashlib.sha256).hexdigest()
    return hmac.compare_digest(expected, received_signature)


def create_order(amount_rupees: float, receipt: str, notes: dict | None = None) -> dict:
    """
    Would create a live Razorpay order (POST /v1/orders) and return its
    order_id for the frontend Checkout widget. Raises if not configured —
    see module docstring for why that's the honest behavior here.
    """
    if not settings.razorpay_key_id or not settings.razorpay_key_secret:
        raise GatewayNotConfiguredError(
            "Razorpay is not configured. Set RAZORPAY_KEY_ID and RAZORPAY_KEY_SECRET as environment variables."
        )

    import requests  # local import: only needed on the live-credentials path

    resp = requests.post(
        "https://api.razorpay.com/v1/orders",
        auth=(settings.razorpay_key_id, settings.razorpay_key_secret),
        json={
            "amount": int(round(amount_rupees * 100)),  # Razorpay works in paise
            "currency": "INR",
            "receipt": receipt,
            "notes": notes or {},
        },
        timeout=15,
    )
    resp.raise_for_status()
    return resp.json()


def verify_payment_signature(
    razorpay_order_id: str,
    razorpay_payment_id: str,
    razorpay_signature: str,
    key_secret: str | None = None,
) -> bool:
    """
    Razorpay's documented client-payment verification: HMAC-SHA256 of
    "{order_id}|{payment_id}" using RAZORPAY_KEY_SECRET (not the webhook
    secret). Called server-side after the browser Checkout widget completes
    to confirm the payment is genuine before converting a BookingHold.
    """
    secret = key_secret or settings.razorpay_key_secret
    if not secret:
        raise GatewayNotConfiguredError("RAZORPAY_KEY_SECRET is not configured")
    payload = f"{razorpay_order_id}|{razorpay_payment_id}".encode()
    expected = hmac.new(secret.encode(), payload, hashlib.sha256).hexdigest()
    return hmac.compare_digest(expected, razorpay_signature)


def create_refund(payment_gateway_id: str, amount_rupees: float) -> dict:
    if not settings.razorpay_key_id or not settings.razorpay_key_secret:
        raise GatewayNotConfiguredError(
            "Razorpay is not configured. Set RAZORPAY_KEY_ID and RAZORPAY_KEY_SECRET as environment variables."
        )

    import requests

    resp = requests.post(
        f"https://api.razorpay.com/v1/payments/{payment_gateway_id}/refund",
        auth=(settings.razorpay_key_id, settings.razorpay_key_secret),
        json={"amount": int(round(amount_rupees * 100))},
        timeout=15,
    )
    resp.raise_for_status()
    return resp.json()


def fetch_payment(razorpay_payment_id: str) -> dict:
    """Fetch the provider's canonical payment state before booking conversion.

    A valid Checkout signature binds an order ID to a payment ID, but does
    not by itself prove the payment is captured or that the provider-recorded
    amount/currency match our hold. Network/API failures intentionally
    propagate so callers leave the hold unconverted and can retry/reconcile.
    """
    if not settings.razorpay_key_id or not settings.razorpay_key_secret:
        raise GatewayNotConfiguredError(
            "Razorpay is not configured. Set RAZORPAY_KEY_ID and RAZORPAY_KEY_SECRET as environment variables."
        )

    import requests

    resp = requests.get(
        f"https://api.razorpay.com/v1/payments/{razorpay_payment_id}",
        auth=(settings.razorpay_key_id, settings.razorpay_key_secret),
        timeout=10,
    )
    resp.raise_for_status()
    return resp.json()


def payment_matches_order(payment: dict, *, order_id: str, amount_rupees: float, currency: str) -> bool:
    """Validate provider payment fields against the immutable local order."""
    try:
        expected_paise = int((Decimal(str(amount_rupees)) * 100).quantize(Decimal("1"), rounding=ROUND_HALF_UP))
        actual_decimal = Decimal(str(payment.get("amount")))
        if not actual_decimal.is_finite() or actual_decimal != actual_decimal.to_integral_value():
            return False
        actual_amount = int(actual_decimal)
    except (InvalidOperation, TypeError, ValueError):
        return False
    return (
        payment.get("id") is not None
        and payment.get("order_id") == order_id
        and payment.get("status") == "captured"
        and actual_amount == expected_paise
        and str(payment.get("currency", "")).upper() == currency.upper()
    )


def verify_provider_payment(razorpay_payment_id: str, *, order_id: str, amount_rupees: float, currency: str) -> bool:
    payment = fetch_payment(razorpay_payment_id)
    return payment.get("id") == razorpay_payment_id and payment_matches_order(
        payment, order_id=order_id, amount_rupees=amount_rupees, currency=currency
    )
