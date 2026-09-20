"""
Tests for the security-hardening pass: login rate limiting, password
complexity validation, security response headers, and production
startup validation (JWT secret / CORS origin guards).
Run with: pytest tests/test_security_hardening.py -v
"""
import uuid

import pytest
from fastapi.testclient import TestClient

from app.core.config import settings
from app.main import _is_local_or_private_origin, _validate_production_config, app
from app.shared.rate_limit import _attempts

client = TestClient(app)

from conftest import ADMIN_EMAIL, ADMIN_PASSWORD  # single source of truth, see conftest.py


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


# ---------------------------------------------------------------------------
# WEBSITE-04A: CORS production-guard RFC1918/private-IP hardening
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("origin", [
    "http://localhost:5173",
    "http://127.0.0.1:5173",
    "http://192.168.1.80:5173",   # RFC1918 class C
    "http://10.0.0.5:5173",       # RFC1918 class A
    "http://172.20.3.4:5173",     # RFC1918 class B
    "http://[::1]:5173",          # IPv6 loopback
])
def test_is_local_or_private_origin_flags_local_and_lan_addresses(origin):
    assert _is_local_or_private_origin(origin) is True


@pytest.mark.parametrize("origin", [
    "https://sriapartmenthomestay.com",
    "https://app.sriapartmenthomestay.com",
    "http://8.8.8.8",  # a real public IP
])
def test_is_local_or_private_origin_allows_real_domains_and_public_ips(origin):
    assert _is_local_or_private_origin(origin) is False


def test_production_startup_blocked_when_cors_is_lan_ip_only(monkeypatch):
    """The gap this phase closes: a private LAN IP alone used to slip past
    the old literal 'localhost'/'127.0.0.1' substring check."""
    monkeypatch.setattr(settings, "environment", "production")
    monkeypatch.setattr(settings, "jwt_secret_key", "a-real-random-production-secret")
    monkeypatch.setattr(settings, "cors_allowed_origins", "http://192.168.1.80:5173")
    with pytest.raises(RuntimeError, match="STARTUP BLOCKED"):
        _validate_production_config()


def test_production_startup_blocked_when_cors_is_localhost_only(monkeypatch):
    monkeypatch.setattr(settings, "environment", "production")
    monkeypatch.setattr(settings, "jwt_secret_key", "a-real-random-production-secret")
    monkeypatch.setattr(settings, "cors_allowed_origins", "http://localhost:5173,http://127.0.0.1:5173")
    with pytest.raises(RuntimeError, match="STARTUP BLOCKED"):
        _validate_production_config()


def test_production_startup_allows_real_domain_alongside_local_origins(monkeypatch):
    """A mix of local + a real domain must NOT be blocked — only
    all-local/all-private origin lists are rejected."""
    monkeypatch.setattr(settings, "environment", "production")
    monkeypatch.setattr(settings, "jwt_secret_key", "a-real-random-production-secret")
    monkeypatch.setattr(settings, "cors_allowed_origins", "http://localhost:5173,https://sriapartmenthomestay.com")
    _validate_production_config()  # must not raise


def test_non_production_startup_never_blocked_by_cors_guard(monkeypatch):
    """Local development must keep working exactly as before — the guard
    only ever applies when ENVIRONMENT=production."""
    monkeypatch.setattr(settings, "environment", "development")
    monkeypatch.setattr(settings, "cors_allowed_origins", "http://192.168.1.80:5173")
    _validate_production_config()  # must not raise regardless of CORS value
