"""
Z1 — Zotel client retry hardening: Retry-After, jitter, configurable retries.

Covers client._get's transient-retry behaviour:
  (a) a retryable response's Retry-After header is honored (integer seconds,
      or an HTTP-date converted to seconds), capped at a sane maximum, and
      used in place of the computed backoff when present;
  (b) without Retry-After, the exponential backoff carries full jitter
      (sleep is a random value in [0, base]);
  (c) MAX_RETRIES and the request timeout are configurable via settings,
      defaulting to the current values;
  (d) the retryable status set stays {429,500,502,503,504} and a
      non-retryable 4xx raises immediately with no sleep.

No real network anywhere — httpx.Client is patched and time.sleep is mocked.
"""
from datetime import datetime, timedelta, timezone
from email.utils import format_datetime
from unittest.mock import MagicMock, patch

import pytest

from app.core.config import settings
from app.modules.zotel import client as zotel_client
from app.modules.zotel.client import ZotelAPIError


@pytest.fixture
def configured_zotel(monkeypatch):
    monkeypatch.setattr(settings, "zotel_base_url", "https://test-property.example.com/api/external/v1")
    monkeypatch.setattr(settings, "zotel_api_key", "pk_live_test_fake_key")
    monkeypatch.setattr(settings, "zotel_api_secret", "test_fake_secret")
    monkeypatch.setattr(settings, "zotel_hotel_id", "hotel-uuid-fake-0001")


def _resp(status_code, body, headers=None):
    r = MagicMock()
    r.status_code = status_code
    r.json.return_value = body
    r.headers = headers or {}
    return r


def _ok():
    return _resp(200, {"success": True, "meta": {}, "data": {}})


def _429(headers=None):
    return _resp(429, {"success": False, "message": "Rate limit exceeded"}, headers=headers)


def _call(configured_zotel):
    from datetime import date, timedelta as _td
    return zotel_client.get_availability(date.today(), date.today() + _td(days=1))


# ---------------------------------------------------------------------------
# (a) Retry-After honored.
# ---------------------------------------------------------------------------

def test_retry_after_integer_seconds_is_honored(configured_zotel):
    with patch("app.modules.zotel.client.httpx.Client") as MockClient, \
         patch("app.modules.zotel.client.time.sleep") as mock_sleep:
        MockClient.return_value.__enter__.return_value.get.side_effect = [_429({"Retry-After": "2"}), _ok()]
        result = _call(configured_zotel)
    assert result["success"] is True
    # The single backoff used the Retry-After value, not the computed backoff.
    assert mock_sleep.call_count == 1
    assert mock_sleep.call_args_list[0].args[0] == pytest.approx(2.0)


def test_retry_after_is_capped_at_max(configured_zotel):
    with patch("app.modules.zotel.client.httpx.Client") as MockClient, \
         patch("app.modules.zotel.client.time.sleep") as mock_sleep:
        MockClient.return_value.__enter__.return_value.get.side_effect = [_429({"Retry-After": "600"}), _ok()]
        _call(configured_zotel)
    slept = mock_sleep.call_args_list[0].args[0]
    assert slept == pytest.approx(zotel_client.MAX_RETRY_AFTER_SECONDS)
    assert slept <= 60.0


def test_retry_after_http_date_is_converted_and_capped(configured_zotel):
    # An HTTP-date an hour in the future converts to ~3600s, then caps at max.
    http_date = format_datetime(datetime.now(timezone.utc) + timedelta(hours=1), usegmt=True)
    with patch("app.modules.zotel.client.httpx.Client") as MockClient, \
         patch("app.modules.zotel.client.time.sleep") as mock_sleep:
        MockClient.return_value.__enter__.return_value.get.side_effect = [_429({"Retry-After": http_date}), _ok()]
        _call(configured_zotel)
    assert mock_sleep.call_args_list[0].args[0] == pytest.approx(zotel_client.MAX_RETRY_AFTER_SECONDS)


# ---------------------------------------------------------------------------
# (b) Full jitter on the computed backoff.
# ---------------------------------------------------------------------------

def test_jittered_backoff_without_retry_after(configured_zotel):
    with patch("app.modules.zotel.client.httpx.Client") as MockClient, \
         patch("app.modules.zotel.client.time.sleep") as mock_sleep:
        MockClient.return_value.__enter__.return_value.get.side_effect = [_429(), _ok()]
        _call(configured_zotel)
    assert mock_sleep.call_count == 1
    slept = mock_sleep.call_args_list[0].args[0]
    # Full jitter on attempt 1: base = 2**0 = 1 -> sleep in [0, 1].
    assert 0.0 <= slept <= 1.0


def test_jitter_is_actually_random_not_fixed(configured_zotel):
    """Full jitter should vary run to run (not the fixed 2**(n-1) backoff)."""
    observed = set()
    for _ in range(8):
        with patch("app.modules.zotel.client.httpx.Client") as MockClient, \
             patch("app.modules.zotel.client.time.sleep") as mock_sleep:
            MockClient.return_value.__enter__.return_value.get.side_effect = [_429(), _429(), _ok()]
            _call(configured_zotel)
        for c in mock_sleep.call_args_list:
            observed.add(round(c.args[0], 6))
    # With real jitter across many attempts, we should see several distinct
    # sleep values rather than the two fixed values {1, 2} of pure backoff.
    assert len(observed) > 2


# ---------------------------------------------------------------------------
# (c) Configurable MAX_RETRIES / timeout.
# ---------------------------------------------------------------------------

def test_sustained_429_exhausts_default_max_retries_and_raises(configured_zotel):
    with patch("app.modules.zotel.client.httpx.Client") as MockClient, \
         patch("app.modules.zotel.client.time.sleep"):
        mock_get = MockClient.return_value.__enter__.return_value.get
        mock_get.return_value = _429()
        with pytest.raises(ZotelAPIError):
            _call(configured_zotel)
        assert mock_get.call_count == zotel_client.MAX_RETRIES


def test_max_retries_is_configurable_via_settings(configured_zotel, monkeypatch):
    monkeypatch.setattr(settings, "zotel_max_retries", 5)
    with patch("app.modules.zotel.client.httpx.Client") as MockClient, \
         patch("app.modules.zotel.client.time.sleep"):
        mock_get = MockClient.return_value.__enter__.return_value.get
        mock_get.return_value = _429()
        with pytest.raises(ZotelAPIError):
            _call(configured_zotel)
        assert mock_get.call_count == 5


def test_timeout_is_configurable_via_settings(configured_zotel, monkeypatch):
    monkeypatch.setattr(settings, "zotel_timeout_seconds", 42.0)
    with patch("app.modules.zotel.client.httpx.Client") as MockClient:
        MockClient.return_value.__enter__.return_value.get.return_value = _ok()
        _call(configured_zotel)
    # The configured timeout was passed when constructing the httpx client.
    _, kwargs = MockClient.call_args
    assert kwargs.get("timeout") == 42.0


# ---------------------------------------------------------------------------
# (d) Non-retryable 4xx raises immediately with no sleep.
# ---------------------------------------------------------------------------

def test_non_retryable_400_raises_immediately_without_sleeping(configured_zotel):
    with patch("app.modules.zotel.client.httpx.Client") as MockClient, \
         patch("app.modules.zotel.client.time.sleep") as mock_sleep:
        mock_get = MockClient.return_value.__enter__.return_value.get
        mock_get.return_value = _resp(400, {"success": False, "message": "Bad request"})
        with pytest.raises(ZotelAPIError) as exc:
            _call(configured_zotel)
        assert exc.value.status_code == 400
        assert mock_get.call_count == 1
        mock_sleep.assert_not_called()
