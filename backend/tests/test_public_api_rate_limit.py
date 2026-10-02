"""Focused tests for shared rate limits on public booking mutations."""

import uuid
from datetime import date, timedelta

import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient
from sqlalchemy.exc import SQLAlchemyError
from starlette.requests import Request

from app.db.session import SessionLocal
from app.main import app
from app.shared.database_rate_limit import client_ip, enforce_rate_limit


def _request(peer: str, forwarded: str | None = None) -> Request:
    headers = []
    if forwarded is not None:
        headers.append((b"x-forwarded-for", forwarded.encode("ascii")))
    return Request({
        "type": "http",
        "http_version": "1.1",
        "method": "POST",
        "scheme": "http",
        "path": "/api/v1/public/holds",
        "raw_path": b"/api/v1/public/holds",
        "query_string": b"",
        "headers": headers,
        "client": (peer, 4321),
        "server": ("testserver", 80),
    })


def test_limit_allows_requests_through_limit_then_returns_generic_429():
    db = SessionLocal()
    scope = f"test-{uuid.uuid4()}"
    request = _request("192.0.2.11")
    try:
        enforce_rate_limit(db, request, scope=scope, max_requests=3, window_seconds=60, now=120)
        enforce_rate_limit(db, request, scope=scope, max_requests=3, window_seconds=60, now=120)
        enforce_rate_limit(db, request, scope=scope, max_requests=3, window_seconds=60, now=120)
        with pytest.raises(HTTPException) as raised:
            enforce_rate_limit(db, request, scope=scope, max_requests=3, window_seconds=60, now=120)
        assert raised.value.status_code == 429
        assert raised.value.headers["Retry-After"] == "60"
        assert raised.value.detail == "Too many requests. Please try again later."
        assert "192.0.2.11" not in raised.value.detail
        assert "bucket" not in raised.value.detail
    finally:
        db.close()


def test_clients_and_endpoint_scopes_have_independent_counters():
    db = SessionLocal()
    scope = f"test-{uuid.uuid4()}"
    try:
        enforce_rate_limit(db, _request("192.0.2.21"), scope=scope, max_requests=1, now=120)
        enforce_rate_limit(db, _request("192.0.2.22"), scope=scope, max_requests=1, now=120)
        enforce_rate_limit(db, _request("192.0.2.21"), scope=f"{scope}-verify", max_requests=1, now=120)
        with pytest.raises(HTTPException) as raised:
            enforce_rate_limit(db, _request("192.0.2.21"), scope=scope, max_requests=1, now=120)
        assert raised.value.status_code == 429
    finally:
        db.close()


def test_arbitrary_forwarded_header_cannot_spoof_client_identity():
    assert client_ip(_request("198.51.100.10", "203.0.113.99")) == "198.51.100.10"
    assert client_ip(_request("198.51.100.10", "198.51.100.20")) == "198.51.100.10"
    assert client_ip(_request("198.51.100.20", "198.51.100.10")) == "198.51.100.20"


def test_counter_store_failure_fails_closed():
    class BrokenDatabase:
        rolled_back = False

        def execute(self, *_args, **_kwargs):
            raise SQLAlchemyError("private database detail")

        def rollback(self):
            self.rolled_back = True

    db = BrokenDatabase()
    with pytest.raises(HTTPException) as raised:
        enforce_rate_limit(db, _request("203.0.113.12"), scope="test", max_requests=2)
    assert raised.value.status_code == 503
    assert raised.value.detail == "Booking service is temporarily unavailable."
    assert "private database detail" not in raised.value.detail
    assert db.rolled_back is True


def test_hold_endpoint_keeps_business_validation_and_enforces_limit():
    class FixedPeer:
        def __init__(self, application):
            self.application = application

        async def __call__(self, scope, receive, send):
            await self.application({**scope, "client": ("198.51.100.81", 4321)}, receive, send)

    test_client = TestClient(FixedPeer(app))
    body = {
        "unit_id": str(uuid.uuid4()),
        "check_in": (date.today() + timedelta(days=30)).isoformat(),
        "check_out": (date.today() + timedelta(days=31)).isoformat(),
        "guest_name": "Rate Limit Test",
        "guest_phone": "9876543210",
        # Correction 5: guest_email is now mandatory on CreateHoldRequest —
        # this test's own intent (business validation runs before the rate
        # limit trips) is unrelated to email, so it just needs a valid one
        # to keep reaching that 404 rather than a schema-validation 422.
        "guest_email": "ratelimit-test@example.com",
    }
    responses = [test_client.post("/api/v1/public/holds", json=body) for _ in range(10)]
    assert all(response.status_code == 404 for response in responses)
    limited = test_client.post("/api/v1/public/holds", json=body)
    assert limited.status_code == 429
    assert limited.headers["retry-after"]
    assert limited.json() == {"detail": "Too many requests. Please try again later."}


def test_verify_payment_endpoint_keeps_hold_lookup_and_enforces_its_limit():
    class FixedPeer:
        def __init__(self, application):
            self.application = application

        async def __call__(self, scope, receive, send):
            await self.application({**scope, "client": ("198.51.100.82", 4321)}, receive, send)

    test_client = TestClient(FixedPeer(app))
    hold_id = uuid.uuid4()
    payload = {
        "razorpay_payment_id": "pay_test_only",
        "razorpay_order_id": "order_test_only",
        "razorpay_signature": "invalid_test_signature",
    }
    path = f"/api/v1/public/holds/{hold_id}/verify-payment"
    responses = [test_client.post(path, json=payload) for _ in range(30)]
    assert all(response.status_code == 404 for response in responses)
    limited = test_client.post(path, json=payload)
    assert limited.status_code == 429
    assert limited.headers["retry-after"]
    assert limited.json() == {"detail": "Too many requests. Please try again later."}
