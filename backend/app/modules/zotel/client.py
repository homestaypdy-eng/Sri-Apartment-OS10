"""
Zotel External API client — built strictly from the documented contract
("ZOTEL API.pdf", v1, last updated May 2026). Every endpoint path, header
name, query parameter, and response shape below is taken directly from
that document, not assumed or ported from any other source.

Endpoints implemented (all the documentation defines as read operations):
  GET /availability
  GET /prices
  GET /coupons
  GET /bookings              (cursor-paginated)
  GET /bookings/{transaction_id}

POST /bookings is documented but deliberately NOT implemented here — see
the zotel module's own report/README note: creating a live reservation
against Zotel is a real, external-facing mutation with no self-service
sandbox (the docs' own FAQ says staging credentials must be requested
separately from Zotel support), so it stays out of scope for this
integration phase per the project's explicit "do not send POST booking
requests" instruction.

There is no documented endpoint for OS10 to PUSH its own availability or
rates INTO Zotel (no PUT/PATCH/POST for /availability or /prices exists
anywhere in the 25-page document) — only GET. Outbound sync is therefore
not implementable against this API as documented; see zotel/service.py's
module docstring for the full explanation.

Auth: two headers on every request — X-Api-Key and X-Api-Secret — plus
hotel_id as a query parameter. No request signing, no OAuth, no bearer
token scheme; this is the one and only authentication method the
document defines. Never log X-Api-Secret, or the full header dict.
"""
import logging
import time
import uuid
from datetime import date

import httpx

from app.core.config import settings

logger = logging.getLogger("sri_apartment_os")

DEFAULT_TIMEOUT_SECONDS = 15.0
MAX_RETRIES = 3
RETRYABLE_STATUS_CODES = {429, 500, 502, 503, 504}


class ZotelNotConfiguredError(Exception):
    """Raised when ZOTEL_BASE_URL/ZOTEL_API_KEY/ZOTEL_API_SECRET/
    ZOTEL_HOTEL_ID are not all set — same honesty convention as
    GatewayNotConfiguredError (Razorpay) and EmailNotConfiguredError
    (SMTP) elsewhere in this codebase: a clear, typed error instead of a
    confusing downstream failure or a faked success."""


class ZotelAPIError(Exception):
    """Raised for a Zotel API error response (4xx/5xx after retries are
    exhausted), or a malformed/unexpected response body. Carries the HTTP
    status and Zotel's own safe message — never the request headers."""

    def __init__(self, message: str, status_code: int | None = None, category: str | None = None):
        super().__init__(message)
        self.status_code = status_code
        self.category = category  # e.g. "availability", "prices", "bookings" — which call failed


def is_configured() -> bool:
    return bool(settings.zotel_base_url and settings.zotel_api_key and settings.zotel_api_secret and settings.zotel_hotel_id)


def _require_configured() -> None:
    if not is_configured():
        raise ZotelNotConfiguredError(
            "Zotel is not configured. Set ZOTEL_BASE_URL, ZOTEL_API_KEY, ZOTEL_API_SECRET, and "
            "ZOTEL_HOTEL_ID as environment variables (issued by your Zotel account manager)."
        )


def _headers() -> dict:
    # Never returned to callers, never logged in full — only used to build
    # the outgoing request.
    return {
        "X-Api-Key": settings.zotel_api_key,
        "X-Api-Secret": settings.zotel_api_secret,
        "Accept": "application/json",
    }


def _get(path: str, params: dict, category: str) -> dict:
    """Shared GET with retry-with-backoff on the documented transient
    conditions (429 rate limit, 5xx server error) — per the docs' own
    "Best practices: do not poll in a tight loop, implement exponential
    backoff on 429" guidance. Never retries on 4xx client errors other
    than 429, since those won't succeed on retry (bad auth, bad hotel_id,
    validation errors)."""
    _require_configured()
    url = f"{settings.zotel_base_url.rstrip('/')}{path}"
    correlation_id = uuid.uuid4().hex[:12]  # not sent to Zotel (undocumented) — for correlating our own logs only

    last_exc: Exception | None = None
    for attempt in range(1, MAX_RETRIES + 1):
        try:
            with httpx.Client(timeout=DEFAULT_TIMEOUT_SECONDS) as client:
                resp = client.get(url, params=params, headers=_headers())
        except httpx.TimeoutException as e:
            last_exc = e
            logger.warning("Zotel %s request timed out (attempt %d/%d) [cid=%s]", category, attempt, MAX_RETRIES, correlation_id)
            if attempt < MAX_RETRIES:
                time.sleep(2 ** (attempt - 1))
                continue
            raise ZotelAPIError(f"Zotel {category} request timed out after {MAX_RETRIES} attempts", category=category) from e
        except httpx.HTTPError as e:
            raise ZotelAPIError(f"Zotel {category} request failed: {type(e).__name__}", category=category) from e

        if resp.status_code == 200:
            try:
                body = resp.json()
            except ValueError as e:
                raise ZotelAPIError(f"Zotel {category} returned a non-JSON response", status_code=200, category=category) from e
            if not isinstance(body, dict) or "data" not in body:
                raise ZotelAPIError(f"Zotel {category} response missing the documented 'data' envelope field", status_code=200, category=category)
            return body

        if resp.status_code in RETRYABLE_STATUS_CODES and attempt < MAX_RETRIES:
            logger.warning("Zotel %s returned %d (attempt %d/%d) [cid=%s] — retrying", category, resp.status_code, attempt, MAX_RETRIES, correlation_id)
            time.sleep(2 ** (attempt - 1))
            continue

        safe_message = _safe_error_message(resp)
        logger.error("Zotel %s failed: HTTP %d [cid=%s]", category, resp.status_code, correlation_id)
        raise ZotelAPIError(safe_message, status_code=resp.status_code, category=category)

    raise ZotelAPIError(f"Zotel {category} request failed after {MAX_RETRIES} attempts", category=category) from last_exc


def _safe_error_message(resp: httpx.Response) -> str:
    try:
        body = resp.json()
        msg = body.get("message") if isinstance(body, dict) else None
        return msg or f"Zotel returned HTTP {resp.status_code}"
    except ValueError:
        return f"Zotel returned HTTP {resp.status_code} (non-JSON body)"


def get_availability(check_in: date, check_out: date, room_type_id: str | None = None) -> dict:
    params = {"hotel_id": settings.zotel_hotel_id, "checkin": check_in.isoformat(), "checkout": check_out.isoformat()}
    if room_type_id:
        params["room_type_id"] = room_type_id
    return _get("/availability", params, "availability")


def get_prices(check_in: date, check_out: date, room_type_id: str | None = None, rate_plan_id: str | None = None, adults: int = 2, with_campaign: bool = False) -> dict:
    params = {
        "hotel_id": settings.zotel_hotel_id, "checkin": check_in.isoformat(), "checkout": check_out.isoformat(),
        "adults": adults, "with_campaign": str(with_campaign).lower(),
    }
    if room_type_id:
        params["room_type_id"] = room_type_id
    if rate_plan_id:
        params["rate_plan_id"] = rate_plan_id
    return _get("/prices", params, "prices")


def get_coupons(check_in: date, check_out: date) -> dict:
    params = {"hotel_id": settings.zotel_hotel_id, "checkin": check_in.isoformat(), "checkout": check_out.isoformat()}
    return _get("/coupons", params, "coupons")


def get_bookings(status: str | None = None, checkin_from: date | None = None, checkin_to: date | None = None, per_page: int = 20, cursor: str | None = None) -> dict:
    params: dict = {"hotel_id": settings.zotel_hotel_id, "per_page": per_page}
    if status:
        params["status"] = status
    if checkin_from:
        params["checkin_from"] = checkin_from.isoformat()
    if checkin_to:
        params["checkin_to"] = checkin_to.isoformat()
    if cursor:
        params["cursor"] = cursor
    return _get("/bookings", params, "bookings")


def get_all_bookings(status: str | None = None, checkin_from: date | None = None, checkin_to: date | None = None, per_page: int = 100, max_pages: int = 50) -> list[dict]:
    """Follows meta.next_cursor until it's null, per the documented
    pagination contract — never assumes the first page is everything.
    max_pages is a hard safety cap against an unexpected infinite-cursor
    response, not a real-world expectation (Sri Apartment's own booking
    volume will never approach it)."""
    all_records: list[dict] = []
    cursor = None
    for _ in range(max_pages):
        page = get_bookings(status=status, checkin_from=checkin_from, checkin_to=checkin_to, per_page=per_page, cursor=cursor)
        records = page.get("data") or []
        if isinstance(records, dict):
            records = list(records.values())
        all_records.extend(records)
        cursor = (page.get("meta") or {}).get("next_cursor")
        if not cursor:
            break
    return all_records


def get_booking_detail(transaction_id: str) -> dict:
    return _get(f"/bookings/{transaction_id}", {"hotel_id": settings.zotel_hotel_id}, "booking_detail")
