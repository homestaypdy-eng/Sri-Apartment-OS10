"""
Tests for the security-hardening pass: login rate limiting, password
complexity validation, and security response headers.
Run with: pytest tests/test_security_hardening.py -v
"""
import uuid

import pytest
from fastapi.testclient import TestClient

from app.main import app
from app.shared.rate_limit import _attempts

client = TestClient(app)

ADMIN_EMAIL = "homestaypdy@gmail.com"
ADMIN_PASSWORD = "Sri@2026"


@pytest.fixture(autouse=True)
def clear_rate_limit_state():
    """Rate limiter state is process-global — reset between tests so one
    test's failed attempts don't leak into another's assertions."""
    _attempts.clear()
    yield
    _attempts.clear()


def test_security_headers_present_on_every_response():
    resp = client.get("/health")
    assert resp.headers["x-content-type-options"] == "nosniff"
    assert resp.headers["x-frame-options"] == "DENY"
    assert resp.headers["referrer-policy"] == "strict-origin-when-cross-origin"


def test_login_rate_limited_after_five_failed_attempts():
    email = f"ratelimit_{uuid.uuid4().hex[:6]}@sriapartmenthomestay.com"
    for _ in range(5):
        resp = client.post("/api/v1/auth/login", json={"email": email, "password": "wrong"})
        assert resp.status_code == 401

    sixth = client.post("/api/v1/auth/login", json={"email": email, "password": "wrong"})
    assert sixth.status_code == 429


def test_rate_limit_is_per_key_not_global():
    email_a = f"ratelimit_a_{uuid.uuid4().hex[:6]}@sriapartmenthomestay.com"
    email_b = f"ratelimit_b_{uuid.uuid4().hex[:6]}@sriapartmenthomestay.com"

    for _ in range(5):
        client.post("/api/v1/auth/login", json={"email": email_a, "password": "wrong"})

    blocked = client.post("/api/v1/auth/login", json={"email": email_a, "password": "wrong"})
    assert blocked.status_code == 429

    not_blocked = client.post("/api/v1/auth/login", json={"email": email_b, "password": "wrong"})
    assert not_blocked.status_code == 401


def test_successful_login_resets_rate_limit_counter():
    for _ in range(4):
        client.post("/api/v1/auth/login", json={"email": ADMIN_EMAIL, "password": "wrong"})

    success = client.post("/api/v1/auth/login", json={"email": ADMIN_EMAIL, "password": ADMIN_PASSWORD})
    assert success.status_code == 200

    resp = client.post("/api/v1/auth/login", json={"email": ADMIN_EMAIL, "password": "wrong"})
    assert resp.status_code == 401


def test_password_complexity_rejects_letters_only():
    admin_login = client.post("/api/v1/auth/login", json={"email": ADMIN_EMAIL, "password": ADMIN_PASSWORD})
    headers = {"Authorization": f"Bearer {admin_login.json()['access_token']}"}
    resp = client.post(
        "/api/v1/auth/register",
        headers=headers,
        json={"name": "Weak Pass", "email": f"weak_{uuid.uuid4().hex[:6]}@sriapartmenthomestay.com", "password": "alllowercase", "role": "staff"},
    )
    assert resp.status_code == 422


def test_password_complexity_rejects_digits_only():
    admin_login = client.post("/api/v1/auth/login", json={"email": ADMIN_EMAIL, "password": ADMIN_PASSWORD})
    headers = {"Authorization": f"Bearer {admin_login.json()['access_token']}"}
    resp = client.post(
        "/api/v1/auth/register",
        headers=headers,
        json={"name": "Weak Pass", "email": f"weak_{uuid.uuid4().hex[:6]}@sriapartmenthomestay.com", "password": "12345678", "role": "staff"},
    )
    assert resp.status_code == 422


def test_password_complexity_accepts_letter_and_digit():
    admin_login = client.post("/api/v1/auth/login", json={"email": ADMIN_EMAIL, "password": ADMIN_PASSWORD})
    headers = {"Authorization": f"Bearer {admin_login.json()['access_token']}"}
    resp = client.post(
        "/api/v1/auth/register",
        headers=headers,
        json={"name": "Valid Pass", "email": f"valid_{uuid.uuid4().hex[:6]}@sriapartmenthomestay.com", "password": "Valid1234", "role": "staff"},
    )
    assert resp.status_code == 201
