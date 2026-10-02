"""
Resend HTTP API email provider (notifications.service._send_email_via_resend),
selected via settings.email_provider == "resend" inside send_email().

SAFETY: every test here mocks httpx.post directly — no test in this file
ever makes a real network call to Resend, and no test uses a real API key
(a fake, obviously-not-real string is used throughout, matching the
existing WhatsApp/SMS test convention in test_owner_and_whatsapp_
notifications.py). email_provider defaults to "gmail_smtp" everywhere
outside these tests (see core/config.py) — nothing here changes that
default, and every test that needs the "resend" path monkeypatches it
locally, scoped to that one test only.

Run with: pytest tests/test_resend_email_provider.py -v
"""
import base64
import json
import os
import tempfile
import uuid
from unittest.mock import MagicMock, patch

import httpx
import pytest
from fastapi.testclient import TestClient

from app.main import app
from app.modules.notifications.service import (
    EmailNotConfiguredError,
    ResendAPIError,
    ResendNotConfiguredError,
    _DEFAULT_EMAIL_LOGO_PATH,
    send_email,
)

client = TestClient(app)
GMAIL_CLIPPING_THRESHOLD_BYTES = 100_000  # ~102KB is Gmail's documented ballpark; kept a little under for margin

FAKE_API_KEY = "re_fake_test_key_never_real_1234567890"
FAKE_FROM_EMAIL = "noreply@send.sriapartment.in"


def _mock_success_response():
    response = MagicMock()
    response.raise_for_status.return_value = None
    return response


@pytest.fixture(autouse=True)
def _resend_configured(monkeypatch):
    """Every test in this file defaults to email_provider="resend" with a
    fake key/from-email already configured — individual tests override
    one or the other back to empty to test the "not configured" paths."""
    monkeypatch.setattr("app.modules.notifications.service.settings.email_provider", "resend")
    monkeypatch.setattr("app.modules.notifications.service.settings.resend_api_key", FAKE_API_KEY)
    monkeypatch.setattr("app.modules.notifications.service.settings.resend_from_email", FAKE_FROM_EMAIL)


# ---------------------------------------------------------------------------
# A. Normal text email
# ---------------------------------------------------------------------------

def test_resend_sends_plain_text_email():
    with patch("app.modules.notifications.service.httpx.post", return_value=_mock_success_response()) as mock_post:
        send_email("customer@example.com", "Your verification code", "Hello, your code is 123456.")

    mock_post.assert_called_once()
    args, kwargs = mock_post.call_args
    assert args[0] == "https://api.resend.com/emails"
    payload = kwargs["json"]
    assert payload["from"] == FAKE_FROM_EMAIL
    assert payload["to"] == ["customer@example.com"]
    assert payload["subject"] == "Your verification code"
    assert payload["text"] == "Hello, your code is 123456."
    assert "html" not in payload
    assert "attachments" not in payload


# ---------------------------------------------------------------------------
# B. HTML email
# ---------------------------------------------------------------------------

def test_resend_sends_html_email():
    with patch("app.modules.notifications.service.httpx.post", return_value=_mock_success_response()) as mock_post:
        send_email(
            "customer@example.com", "Welcome", "Welcome to Sri Apartment Homestay.",
            html_body="<p>Welcome to <b>Sri Apartment Homestay</b>.</p>",
        )

    payload = mock_post.call_args.kwargs["json"]
    assert payload["html"] == "<p>Welcome to <b>Sri Apartment Homestay</b>.</p>"
    assert payload["text"] == "Welcome to Sri Apartment Homestay."


def test_resend_inline_image_cid_becomes_a_content_id_attachment_not_a_data_uri():
    """CORRECTED behavior: Resend's attachments[] array has a real
    content_id field for exactly this (their own docs: '<img
    src="cid:...">' matched against an attachment's content_id) — the
    same cid: convention MIME uses. html_body must be passed through
    byte-for-byte unmodified (still containing the literal cid:logo
    reference); the image becomes its own attachments[] entry instead.

    This replaces an earlier, incorrect implementation that inlined the
    image as a base64 data: URI directly into the HTML — measured, on a
    real send, to balloon a 1.8KB template into a >2MB HTML body that
    Gmail clipped (see notifications/service.py's own docstring on
    _send_email_via_resend for the full incident)."""
    with tempfile.NamedTemporaryFile(suffix=".png", delete=False) as f:
        f.write(b"\x89PNG\r\n\x1a\nfake-png-bytes")
        logo_path = f.name
    try:
        with patch("app.modules.notifications.service.httpx.post", return_value=_mock_success_response()) as mock_post:
            send_email(
                "customer@example.com", "Welcome", "Welcome.",
                html_body='<img src="cid:logo">', inline_images={"logo": logo_path},
            )
        payload = mock_post.call_args.kwargs["json"]
        # The HTML is untouched — still has the cid: reference, no data: URI.
        assert payload["html"] == '<img src="cid:logo">'
        assert "data:image" not in payload["html"]
        assert "base64" not in payload["html"]

        assert len(payload["attachments"]) == 1
        attachment = payload["attachments"][0]
        assert attachment["content_id"] == "logo"
        assert attachment["filename"] == os.path.basename(logo_path)
        assert base64.b64decode(attachment["content"]) == b"\x89PNG\r\n\x1a\nfake-png-bytes"
    finally:
        os.unlink(logo_path)


def test_resend_inline_image_and_regular_attachment_coexist():
    """A booking-confirmation-shaped call: an inline logo (content_id)
    AND a real downloadable attachment (invoice PDF, no content_id) in
    the same send — both must end up in attachments[], distinguishable
    by the presence/absence of content_id."""
    with tempfile.NamedTemporaryFile(suffix=".png", delete=False) as logo_f:
        logo_f.write(b"\x89PNG fake logo")
        logo_path = logo_f.name
    with tempfile.NamedTemporaryFile(suffix=".pdf", delete=False) as pdf_f:
        pdf_f.write(b"%PDF fake invoice")
        pdf_path = pdf_f.name
    try:
        with patch("app.modules.notifications.service.httpx.post", return_value=_mock_success_response()) as mock_post:
            send_email(
                "customer@example.com", "Your invoice", "See attached.",
                attachment_path=pdf_path,
                html_body='<img src="cid:logo">', inline_images={"logo": logo_path},
            )
        attachments = mock_post.call_args.kwargs["json"]["attachments"]
        assert len(attachments) == 2
        inline = next(a for a in attachments if a.get("content_id") == "logo")
        real_attachment = next(a for a in attachments if "content_id" not in a)
        assert base64.b64decode(inline["content"]) == b"\x89PNG fake logo"
        assert real_attachment["filename"] == os.path.basename(pdf_path)
        assert base64.b64decode(real_attachment["content"]) == b"%PDF fake invoice"
    finally:
        os.unlink(logo_path)
        os.unlink(pdf_path)


# ---------------------------------------------------------------------------
# C. Attachment
# ---------------------------------------------------------------------------

def test_resend_sends_attachment_base64_encoded_with_original_filename():
    with tempfile.NamedTemporaryFile(suffix=".pdf", prefix="invoice-INV-2026-001-", delete=False) as f:
        f.write(b"%PDF-1.4 fake invoice bytes")
        pdf_path = f.name
    try:
        with patch("app.modules.notifications.service.httpx.post", return_value=_mock_success_response()) as mock_post:
            send_email("customer@example.com", "Your invoice", "Please find your invoice attached.", attachment_path=pdf_path)

        payload = mock_post.call_args.kwargs["json"]
        assert len(payload["attachments"]) == 1
        attachment = payload["attachments"][0]
        assert attachment["filename"] == os.path.basename(pdf_path)
        assert base64.b64decode(attachment["content"]) == b"%PDF-1.4 fake invoice bytes"
    finally:
        os.unlink(pdf_path)


def test_resend_missing_attachment_path_is_silently_skipped():
    """Matches the existing Gmail SMTP behavior: a non-existent
    attachment_path is silently skipped, never raises — see
    _send_email_via_gmail_smtp's identical `if attachment_path and
    os.path.exists(attachment_path)` guard."""
    with patch("app.modules.notifications.service.httpx.post", return_value=_mock_success_response()) as mock_post:
        send_email("customer@example.com", "Subject", "Body", attachment_path="/no/such/file.pdf")

    payload = mock_post.call_args.kwargs["json"]
    assert "attachments" not in payload


# ---------------------------------------------------------------------------
# D. Authorization header correctness, without exposing the key anywhere
# ---------------------------------------------------------------------------

def test_resend_authorization_header_uses_bearer_scheme_with_configured_key():
    with patch("app.modules.notifications.service.httpx.post", return_value=_mock_success_response()) as mock_post:
        send_email("customer@example.com", "Subject", "Body")

    headers = mock_post.call_args.kwargs["headers"]
    assert headers["Authorization"] == f"Bearer {FAKE_API_KEY}"
    # The key only ever appears inside this one header value — never
    # duplicated into the JSON payload itself.
    payload = mock_post.call_args.kwargs["json"]
    assert FAKE_API_KEY not in str(payload)


# ---------------------------------------------------------------------------
# E / F. Missing configuration fails clearly, before any network call
# ---------------------------------------------------------------------------

def test_resend_missing_api_key_fails_clearly(monkeypatch):
    monkeypatch.setattr("app.modules.notifications.service.settings.resend_api_key", "")
    with patch("app.modules.notifications.service.httpx.post") as mock_post:
        with pytest.raises(ResendNotConfiguredError, match="RESEND_API_KEY"):
            send_email("customer@example.com", "Subject", "Body")
    mock_post.assert_not_called()


def test_resend_missing_from_email_fails_clearly(monkeypatch):
    monkeypatch.setattr("app.modules.notifications.service.settings.resend_from_email", "")
    with patch("app.modules.notifications.service.httpx.post") as mock_post:
        with pytest.raises(ResendNotConfiguredError, match="RESEND_FROM_EMAIL"):
            send_email("customer@example.com", "Subject", "Body")
    mock_post.assert_not_called()


def test_resend_not_configured_error_is_an_email_not_configured_error():
    """Deliberate inheritance: every existing caller that already catches
    EmailNotConfiguredError (e.g. customer_auth/router.py's
    forgot_customer_password) keeps working unchanged if email_provider
    is ever switched to "resend" there, with no call-site changes."""
    assert issubclass(ResendNotConfiguredError, EmailNotConfiguredError)


# ---------------------------------------------------------------------------
# G. Resend HTTP error handling — no automatic retry (deliberately, for now)
# ---------------------------------------------------------------------------

def test_resend_http_error_raises_resend_api_error_with_useful_non_secret_detail():
    error_response = MagicMock()
    error_response.status_code = 422
    error_response.text = '{"message":"Invalid `from` field"}'
    error_response.raise_for_status.side_effect = httpx.HTTPStatusError(
        "422 error", request=MagicMock(), response=error_response,
    )
    with patch("app.modules.notifications.service.httpx.post", return_value=error_response):
        with pytest.raises(ResendAPIError) as exc_info:
            send_email("customer@example.com", "Subject", "Body")

    message = str(exc_info.value)
    assert "422" in message
    assert "Invalid `from` field" in message
    assert FAKE_API_KEY not in message


def test_resend_network_error_raises_resend_api_error():
    with patch("app.modules.notifications.service.httpx.post", side_effect=httpx.ConnectError("Connection refused")):
        with pytest.raises(ResendAPIError) as exc_info:
            send_email("customer@example.com", "Subject", "Body")

    assert "ConnectError" in str(exc_info.value)
    assert FAKE_API_KEY not in str(exc_info.value)


def test_resend_does_not_retry_automatically():
    """Explicit per the implementation scope: a single failed attempt
    calls httpx.post exactly once — retry/backoff strategy is deliberately
    deferred to a separate change."""
    with patch("app.modules.notifications.service.httpx.post", side_effect=httpx.ConnectError("boom")) as mock_post:
        with pytest.raises(ResendAPIError):
            send_email("customer@example.com", "Subject", "Body")
    mock_post.assert_called_once()


def test_resend_explicit_timeout_is_passed_to_httpx():
    with patch("app.modules.notifications.service.httpx.post", return_value=_mock_success_response()) as mock_post:
        send_email("customer@example.com", "Subject", "Body", timeout=9)
    assert mock_post.call_args.kwargs["timeout"] == 9


# ---------------------------------------------------------------------------
# H. Existing Gmail SMTP path is untouched and still selected by default
# ---------------------------------------------------------------------------

def test_gmail_smtp_path_is_used_when_email_provider_is_gmail_smtp(monkeypatch):
    monkeypatch.setattr("app.modules.notifications.service.settings.email_provider", "gmail_smtp")
    with patch("app.modules.notifications.service._send_email_via_gmail_smtp") as mock_gmail, \
         patch("app.modules.notifications.service._send_email_via_resend") as mock_resend:
        send_email("customer@example.com", "Subject", "Body")

    mock_gmail.assert_called_once()
    mock_resend.assert_not_called()


def test_resend_path_is_used_only_when_email_provider_is_resend():
    """email_provider is forced to "resend" by the autouse fixture above
    for every test in this file — this test confirms the OTHER branch
    (gmail) is correspondingly never touched while it's selected."""
    with patch("app.modules.notifications.service._send_email_via_gmail_smtp") as mock_gmail, \
         patch("app.modules.notifications.service._send_email_via_resend") as mock_resend:
        send_email("customer@example.com", "Subject", "Body")

    mock_resend.assert_called_once()
    mock_gmail.assert_not_called()


def test_default_email_provider_setting_is_gmail_smtp():
    """The actual pydantic-settings default (not monkeypatched) — proves
    production, which never sets EMAIL_PROVIDER at all today, is
    unaffected by this feature existing."""
    from app.core.config import Settings

    assert Settings.model_fields["email_provider"].default == "gmail_smtp"


# ---------------------------------------------------------------------------
# Regression coverage for the real Gmail "Message clipped" incident: an
# oversized (1.55MB) logo, inlined as base64 directly into the HTML,
# produced a >2MB message. Fixed two ways — a dedicated, size-optimized
# email logo asset (_DEFAULT_EMAIL_LOGO_PATH), and correct Resend
# attachment/content_id handling (see _send_email_via_resend). Both are
# covered together here since either alone regressing would reproduce
# the incident.
# ---------------------------------------------------------------------------

def test_email_optimized_logo_asset_exists_and_is_small():
    assert os.path.exists(_DEFAULT_EMAIL_LOGO_PATH)
    size = os.path.getsize(_DEFAULT_EMAIL_LOGO_PATH)
    assert size < 50_000, f"email logo asset is {size} bytes, expected well under 50KB"


def _register_customer(email: str, password: str = "CorrectHorse123!") -> dict:
    resp = client.post("/api/v1/customer-auth/register", json={
        "first_name": "Test", "last_name": "User", "email": email,
        "phone": f"9{uuid.uuid4().int % 10**9:09d}", "password": password,
        "terms_accepted": True, "privacy_accepted": True,
    })
    assert resp.status_code == 201, resp.text
    return resp.json()


def test_real_otp_flow_via_resend_produces_a_small_payload_with_no_inline_data_uri(monkeypatch):
    """End-to-end regression test: drives the REAL customer login OTP
    flow (register -> login step 1, forced into the production+resend
    branch) and inspects the ACTUAL payload _send_email_via_resend
    builds — the real _render_customer_otp_html output and the real
    (now size-optimized) logo asset, not a synthetic html_body like the
    unit tests above. email_provider/resend_api_key/resend_from_email
    are already forced to the fake-configured "resend" state by this
    file's autouse fixture; only environment needs overriding here, to
    reach the production dispatch branch the real incident happened on."""
    email = f"otp-payload-size-{uuid.uuid4().hex[:10]}@example.com"
    _register_customer(email)
    monkeypatch.setattr("app.modules.customer_auth.router.settings.environment", "production")

    with patch("app.modules.notifications.service.httpx.post", return_value=_mock_success_response()) as mock_post:
        resp = client.post(
            "/api/v1/customer-auth/login",
            json={"channel": "email", "identifier": email, "password": "CorrectHorse123!"},
        )
        assert resp.status_code == 200, resp.text

    mock_post.assert_called_once()
    payload = mock_post.call_args.kwargs["json"]

    html_size = len(payload["html"].encode("utf-8"))
    assert html_size < 5_000, f"OTP email HTML is {html_size} bytes — inline image leaked into the HTML again"
    assert "base64" not in payload["html"]
    assert "cid:logo" in payload["html"]

    assert len(payload["attachments"]) == 1
    logo_attachment = payload["attachments"][0]
    assert logo_attachment["content_id"] == "logo"
    assert logo_attachment["filename"] == "logo-email.png"

    total_payload_size = len(json.dumps(payload).encode("utf-8"))
    assert total_payload_size < GMAIL_CLIPPING_THRESHOLD_BYTES, (
        f"total Resend payload is {total_payload_size} bytes — still at risk of the same Gmail clipping incident"
    )
