"""
Phase 3G: customer-facing Google OAuth login. Google is the only
social-login provider this project supports.

No real Google credentials exist in this environment (by design — see
oauth_service.py's own docstring on why client id/secret are never
invented), so these tests cover everything that IS testable without
them:

  - the credential-independent "not configured" gate on /status and
    /start (real, verifiable behavior today, exactly like the same gate
    on SMTP/SMS/Razorpay elsewhere in this codebase)
  - CSRF state token creation/verification (oauth_service.create_oauth_
    state/verify_oauth_state) — valid, wrong-provider, garbage, and
    expired tokens
  - id_token verification in oauth_service.exchange_google_code, tested
    against a LOCALLY-GENERATED RSA keypair standing in for Google's own
    signing key (this is legitimate: the code under test only ever
    trusts the JWKS response it fetches and the signature math, never
    Google specifically) — valid token, expired token, wrong audience,
    wrong issuer, and a token whose signature doesn't match the key its
    header claims are all exercised by monkeypatching httpx.post/get
  - the identity-resolution logic in oauth_service.resolve_customer_for_
    oauth, called directly with a synthetic (already-"verified") provider
    profile — this is the part of the flow that has nothing to do with
    actually talking to Google, and is exactly where the
    account-takeover-prevention logic this task cares about lives
  - the /confirm-link endpoint's authentication and cross-account checks

Run with: pytest tests/test_customer_oauth.py -v
"""
import datetime
import uuid

import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from fastapi.testclient import TestClient
from jose import jwk as jose_jwk, jwt as jose_jwt
from sqlalchemy import select

from app.core.config import settings
from app.db.session import SessionLocal
from app.main import app
from app.modules.customer_auth.models import CustomerAccount, CustomerSocialIdentity
from app.modules.customer_auth import oauth_service as oauth_service_module
from app.modules.customer_auth.oauth_service import (
    OAuthVerificationError,
    create_oauth_link_token,
    create_oauth_state,
    exchange_google_code,
    resolve_customer_for_oauth,
    verify_oauth_state,
)

client = TestClient(app)
REGISTER_URL = "/api/v1/customer-auth/register"
LOGIN_URL = "/api/v1/customer-auth/login"
VERIFY_OTP_URL = "/api/v1/customer-auth/login/verify-otp"
STATUS_URL = "/api/v1/customer-auth/oauth/status"
ME_URL = "/api/v1/customer-auth/me"

PASSWORD = "CorrectHorse123!"


@pytest.fixture
def db():
    session = SessionLocal()
    try:
        yield session
    finally:
        session.close()


@pytest.fixture(autouse=True)
def _clear_rate_limit_state():
    from app.shared import rate_limit as rate_limit_module

    rate_limit_module._attempts.clear()
    yield
    rate_limit_module._attempts.clear()


def _random_email() -> str:
    return f"oauth-{uuid.uuid4().hex[:10]}@example.com"


def _random_phone() -> str:
    return f"9{uuid.uuid4().int % 10**9:09d}"


def _random_subject() -> str:
    return f"sub-{uuid.uuid4().hex}"


def _register(email: str, phone: str | None = None, password: str = PASSWORD) -> dict:
    resp = client.post(REGISTER_URL, json={
        "first_name": "Ada", "last_name": "Lovelace", "email": email,
        "phone": phone or _random_phone(), "password": password,
        "terms_accepted": True, "privacy_accepted": True,
    })
    assert resp.status_code == 201, resp.text
    return resp.json()


def _login_and_get_token(email: str, password: str = PASSWORD) -> str:
    step1 = client.post(LOGIN_URL, json={"channel": "email", "identifier": email, "password": password})
    assert step1.status_code == 200, step1.text
    verify = client.post(VERIFY_OTP_URL, json={"challenge_id": step1.json()["challenge_id"], "code": step1.json()["dev_otp_code"]})
    assert verify.status_code == 200, verify.text
    return verify.json()["access_token"]


def _auth(token: str) -> dict:
    return {"Authorization": f"Bearer {token}"}


# ---------------------------------------------------------------------------
# Credential-independent "not configured" gate — real, verifiable today
# ---------------------------------------------------------------------------

def test_oauth_status_reports_unconfigured_in_this_environment():
    resp = client.get(STATUS_URL)
    assert resp.status_code == 200
    body = resp.json()
    assert body == {"google": False}
    assert "facebook" not in body  # Facebook was removed entirely — no such field remains


def test_google_start_returns_501_not_a_fake_redirect_when_unconfigured():
    resp = client.get("/api/v1/customer-auth/oauth/google/start", follow_redirects=False)
    assert resp.status_code == 501


def test_google_callback_returns_501_when_unconfigured():
    resp = client.get("/api/v1/customer-auth/oauth/google/callback?code=x&state=y", follow_redirects=False)
    assert resp.status_code == 501


def test_no_facebook_oauth_endpoints_remain():
    """Facebook's start/callback routes must be completely gone, not
    merely disabled — a 404 (route doesn't exist), never a 501 (route
    exists but is unconfigured)."""
    assert client.get("/api/v1/customer-auth/oauth/facebook/start", follow_redirects=False).status_code == 404
    assert client.get("/api/v1/customer-auth/oauth/facebook/callback", follow_redirects=False).status_code == 404


# ---------------------------------------------------------------------------
# CSRF state token — create_oauth_state / verify_oauth_state
# ---------------------------------------------------------------------------

def test_oauth_state_round_trips_for_the_correct_provider():
    state = create_oauth_state("google")
    assert verify_oauth_state(state, "google") is True


def test_oauth_state_rejected_for_a_different_provider():
    state = create_oauth_state("google")
    assert verify_oauth_state(state, "twitter") is False


def test_oauth_state_rejects_garbage_tokens():
    assert verify_oauth_state("not-a-real-token", "google") is False


def test_oauth_state_rejects_an_expired_token():
    now = datetime.datetime.now(datetime.timezone.utc)
    expired_payload = {
        "purpose": "oauth_state", "provider": "google", "nonce": "x",
        "iat": now - datetime.timedelta(minutes=20),
        "exp": now - datetime.timedelta(minutes=10),
    }
    expired_state = jose_jwt.encode(expired_payload, settings.jwt_secret_key, algorithm=settings.jwt_algorithm)
    assert verify_oauth_state(expired_state, "google") is False


# ---------------------------------------------------------------------------
# Google id_token verification — exchange_google_code, against a locally
# generated RSA keypair standing in for Google's own signing key
# ---------------------------------------------------------------------------

def _generate_rsa_keypair():
    private_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    private_pem = private_key.private_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PrivateFormat.PKCS8,
        encryption_algorithm=serialization.NoEncryption(),
    ).decode()
    public_pem = private_key.public_key().public_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PublicFormat.SubjectPublicKeyInfo,
    ).decode()
    return private_pem, public_pem


def _jwk_for(public_pem: str, kid: str) -> dict:
    jwk_dict = jose_jwk.construct(public_pem, algorithm="RS256").to_dict()
    jwk_dict["kid"] = kid
    return jwk_dict


def _make_id_token(private_pem: str, kid: str, **claim_overrides) -> str:
    now = datetime.datetime.now(datetime.timezone.utc)
    claims = {
        "sub": "google-subject-123",
        "email": "grace@example.com",
        "email_verified": True,
        "given_name": "Grace",
        "family_name": "Hopper",
        "aud": "test-google-client-id",
        "iss": "https://accounts.google.com",
        "iat": now,
        "exp": now + datetime.timedelta(minutes=5),
    }
    claims.update(claim_overrides)
    return jose_jwt.encode(claims, private_pem, algorithm="RS256", headers={"kid": kid})


class _FakeResponse:
    def __init__(self, json_body, status_code=200):
        self._json_body = json_body
        self.status_code = status_code

    def raise_for_status(self):
        if self.status_code >= 400:
            raise RuntimeError(f"HTTP {self.status_code}")

    def json(self):
        return self._json_body


def _patch_google_endpoints(monkeypatch, id_token: str, jwks_keys: list[dict]):
    def fake_post(url, data=None, timeout=None, **kwargs):
        assert url == oauth_service_module.GOOGLE_TOKEN_URL
        return _FakeResponse({"id_token": id_token})

    def fake_get(url, timeout=None, **kwargs):
        assert url == oauth_service_module.GOOGLE_JWKS_URL
        return _FakeResponse({"keys": jwks_keys})

    monkeypatch.setattr(oauth_service_module.httpx, "post", fake_post)
    monkeypatch.setattr(oauth_service_module.httpx, "get", fake_get)


@pytest.fixture
def _google_client_configured(monkeypatch):
    """exchange_google_code reads client_id/secret/redirect_uri straight
    from settings — point them at values matching the crafted tokens'
    audience, without needing real Google credentials configured."""
    monkeypatch.setattr(settings, "google_client_id", "test-google-client-id")
    monkeypatch.setattr(settings, "google_client_secret", "test-secret-not-real")


def test_valid_google_id_token_is_accepted_and_claims_extracted(monkeypatch, _google_client_configured):
    private_pem, public_pem = _generate_rsa_keypair()
    token = _make_id_token(private_pem, kid="key-1")
    _patch_google_endpoints(monkeypatch, token, [_jwk_for(public_pem, "key-1")])

    profile = exchange_google_code("some-auth-code")
    assert profile["subject"] == "google-subject-123"
    assert profile["email"] == "grace@example.com"
    assert profile["email_verified"] is True
    assert profile["first_name"] == "Grace"
    assert profile["last_name"] == "Hopper"


def test_expired_google_id_token_is_rejected(monkeypatch, _google_client_configured):
    private_pem, public_pem = _generate_rsa_keypair()
    now = datetime.datetime.now(datetime.timezone.utc)
    token = _make_id_token(private_pem, kid="key-1", iat=now - datetime.timedelta(hours=2), exp=now - datetime.timedelta(hours=1))
    _patch_google_endpoints(monkeypatch, token, [_jwk_for(public_pem, "key-1")])

    with pytest.raises(OAuthVerificationError):
        exchange_google_code("some-auth-code")


def test_google_id_token_with_wrong_audience_is_rejected(monkeypatch, _google_client_configured):
    private_pem, public_pem = _generate_rsa_keypair()
    token = _make_id_token(private_pem, kid="key-1", aud="someone-elses-client-id")
    _patch_google_endpoints(monkeypatch, token, [_jwk_for(public_pem, "key-1")])

    with pytest.raises(OAuthVerificationError):
        exchange_google_code("some-auth-code")


def test_google_id_token_with_wrong_issuer_is_rejected(monkeypatch, _google_client_configured):
    private_pem, public_pem = _generate_rsa_keypair()
    token = _make_id_token(private_pem, kid="key-1", iss="https://not-google.example.com")
    _patch_google_endpoints(monkeypatch, token, [_jwk_for(public_pem, "key-1")])

    with pytest.raises(OAuthVerificationError):
        exchange_google_code("some-auth-code")


def test_google_id_token_with_invalid_signature_is_rejected(monkeypatch, _google_client_configured):
    """The token's header claims kid "key-1", but is actually signed with
    a DIFFERENT private key than the one whose public half is published
    under that kid in the (fake) JWKS response — simulating a tampered or
    forged token that isn't what it claims to be."""
    signing_private_pem, _ = _generate_rsa_keypair()
    _, published_public_pem = _generate_rsa_keypair()
    token = _make_id_token(signing_private_pem, kid="key-1")
    _patch_google_endpoints(monkeypatch, token, [_jwk_for(published_public_pem, "key-1")])

    with pytest.raises(OAuthVerificationError):
        exchange_google_code("some-auth-code")


def test_google_id_token_signed_by_an_unknown_key_id_is_rejected(monkeypatch, _google_client_configured):
    """No key in the JWKS response matches the token's kid at all."""
    private_pem, public_pem = _generate_rsa_keypair()
    token = _make_id_token(private_pem, kid="key-1")
    _patch_google_endpoints(monkeypatch, token, [_jwk_for(public_pem, "some-other-key")])

    with pytest.raises(OAuthVerificationError):
        exchange_google_code("some-auth-code")


# ---------------------------------------------------------------------------
# Identity resolution — the account-takeover-prevention logic
# ---------------------------------------------------------------------------

def test_first_time_oauth_signup_creates_a_new_account_with_no_phone_and_no_usable_password(db):
    email = _random_email()
    subject = _random_subject()

    outcome = resolve_customer_for_oauth(db, "google", subject, email, True, "Grace", "Hopper")

    assert outcome.status == "created"
    assert outcome.customer is not None
    assert outcome.customer.email == email
    assert outcome.customer.first_name == "Grace"
    assert outcome.customer.phone is None  # never fabricated

    from app.core.security import verify_password
    assert verify_password("", outcome.customer.password_hash) is False
    assert verify_password("password", outcome.customer.password_hash) is False

    identity = db.scalar(select(CustomerSocialIdentity).where(CustomerSocialIdentity.provider_subject == subject))
    assert identity is not None
    assert identity.customer_id == outcome.customer.id


def test_returning_oauth_customer_is_recognized_by_provider_subject_not_email(db):
    email = _random_email()
    subject = _random_subject()

    first = resolve_customer_for_oauth(db, "google", subject, email, True, "Grace", "Hopper")
    assert first.status == "created"

    # Same subject, even with a DIFFERENT email now attached at the
    # provider — identity is keyed on subject, not email.
    second = resolve_customer_for_oauth(db, "google", subject, "changed@example.com", True, "Grace", "Hopper")
    assert second.status == "logged_in"
    assert second.customer.id == first.customer.id

    accounts = db.scalars(select(CustomerAccount).where(CustomerAccount.id == first.customer.id)).all()
    assert len(accounts) == 1  # no duplicate account created


def test_oauth_email_matching_an_existing_account_requires_link_confirmation_not_silent_takeover(db):
    email = _random_email()
    existing = _register(email)
    accounts_before = db.query(CustomerAccount).count()

    outcome = resolve_customer_for_oauth(db, "google", _random_subject(), email, True, "Someone", "Else")

    assert outcome.status == "link_required"
    assert outcome.customer is None
    assert outcome.link_token
    # Nothing was created or linked yet — the account existed already,
    # and no CustomerSocialIdentity should exist for this subject.
    assert db.query(CustomerAccount).count() == accounts_before
    assert db.scalar(select(CustomerSocialIdentity).where(CustomerSocialIdentity.customer_id == uuid.UUID(existing["id"]))) is None


def test_unverified_email_never_triggers_link_required_or_matches_an_existing_account(db):
    """An OAuth email the provider itself did not mark verified must never
    be trusted enough to match an existing account — treated exactly like
    a brand-new identity with no email at all."""
    email = _random_email()
    _register(email)

    outcome = resolve_customer_for_oauth(db, "google", _random_subject(), email, False, "Someone", "Else")
    assert outcome.status == "created"
    assert outcome.customer.email != email  # got a synthetic placeholder, not the unverified claim


def test_oauth_signup_without_an_email_gets_a_synthetic_placeholder_not_a_fabricated_real_email(db):
    outcome = resolve_customer_for_oauth(db, "google", _random_subject(), None, False, "NoEmail", "Person")
    assert outcome.status == "created"
    assert outcome.customer.email.endswith("@oauth.invalid")


# ---------------------------------------------------------------------------
# POST /oauth/{provider}/confirm-link
# ---------------------------------------------------------------------------

def test_confirm_link_requires_authentication():
    resp = client.post("/api/v1/customer-auth/oauth/google/confirm-link", json={"link_token": "whatever"})
    assert resp.status_code == 401


def test_confirm_link_completes_after_the_customer_proves_ownership_via_normal_login(db):
    email = _random_email()
    _register(email)

    link_token = create_oauth_link_token("google", _random_subject(), email)
    token = _login_and_get_token(email)

    resp = client.post("/api/v1/customer-auth/oauth/google/confirm-link", headers=_auth(token), json={"link_token": link_token})
    assert resp.status_code == 200, resp.text

    me = client.get(ME_URL, headers=_auth(token)).json()
    identity = db.scalar(select(CustomerSocialIdentity).where(CustomerSocialIdentity.customer_id == uuid.UUID(me["id"])))
    assert identity is not None
    assert identity.provider == "google"


def test_confirm_link_rejects_a_token_issued_for_a_different_customers_email(db):
    email_a, email_b = _random_email(), _random_email()
    _register(email_a)
    _register(email_b)

    link_token_for_a = create_oauth_link_token("google", _random_subject(), email_a)
    token_b = _login_and_get_token(email_b)

    resp = client.post("/api/v1/customer-auth/oauth/google/confirm-link", headers=_auth(token_b), json={"link_token": link_token_for_a})
    assert resp.status_code == 400


def test_confirm_link_rejects_invalid_or_garbage_tokens(db):
    email = _random_email()
    _register(email)
    token = _login_and_get_token(email)

    resp = client.post("/api/v1/customer-auth/oauth/google/confirm-link", headers=_auth(token), json={"link_token": "not-a-real-token"})
    assert resp.status_code == 400


def test_confirm_link_rejects_unknown_provider(db):
    email = _random_email()
    _register(email)
    token = _login_and_get_token(email)

    resp = client.post("/api/v1/customer-auth/oauth/twitter/confirm-link", headers=_auth(token), json={"link_token": "x"})
    assert resp.status_code == 404


# ---------------------------------------------------------------------------
# Isolation — an OAuth-created customer's session behaves exactly like any
# other customer session (same /me, same cross-customer isolation)
# ---------------------------------------------------------------------------

def test_oauth_created_customer_session_only_ever_sees_its_own_profile(db):
    from app.core.security import create_customer_session_token
    from app.modules.customer_auth.service import create_customer_session

    outcome_a = resolve_customer_for_oauth(db, "google", _random_subject(), _random_email(), True, "Grace", "Hopper")
    outcome_b = resolve_customer_for_oauth(db, "google", _random_subject(), _random_email(), True, "Ada", "Lovelace")
    db.commit()

    session_a = create_customer_session(db, outcome_a.customer.id)
    token_a = create_customer_session_token(str(outcome_a.customer.id), jti=str(session_a.id))

    me = client.get(ME_URL, headers=_auth(token_a)).json()
    assert me["id"] == str(outcome_a.customer.id)
    assert me["id"] != str(outcome_b.customer.id)
    assert "password_hash" not in me


def test_existing_email_and_mobile_otp_login_unaffected_by_oauth_module(db):
    """Regression: adding the OAuth router/model must not disturb the
    existing password+OTP login this whole module is built around."""
    email = _random_email()
    _register(email)
    assert _login_and_get_token(email)
