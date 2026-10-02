"""
Production-readiness audit finding: Sri Apartment Homestay operates in
India, but every unqualified `date.today()` / naive `datetime.now()` call
across the backend (overdue-payment checks, "arrivals today"/"departures
today", daily closing defaults, report date-range defaults, promo/
quotation expiry, staff attendance "today") implicitly means "today in
Asia/Kolkata". The base Docker image defaults to UTC, which silently
shifts every one of those calendar-day boundaries by 5.5 hours: from
00:00-05:30 IST, `date.today()` on a UTC container still returns
YESTERDAY's date, while it is already a new day for the actual business.

Fixed by setting `ENV TZ=Asia/Kolkata` (with `tzdata` installed) in
backend/Dockerfile — a container/OS-level fix, not a change to any
business-logic call site. This test pins that fix down so it can never
silently regress (e.g. someone reverting the Dockerfile change, or a base
image update dropping tzdata) without a test failure calling it out.

Deliberately does NOT test every one of the ~24 call sites individually —
they all share the same underlying `date.today()`/`time.localtime()`
mechanism, which is what this test verifies directly.
"""
import time
from datetime import date, datetime, timezone
from zoneinfo import ZoneInfo

IST = ZoneInfo("Asia/Kolkata")


def test_process_local_timezone_is_asia_kolkata():
    """The C-level local timezone the Python process resolves against
    (what date.today()/datetime.now() without an explicit tz use) must be
    IST, not UTC or any other zone — this is what actually makes every
    existing date.today() call site correct without modifying any of them."""
    assert time.tzname[0] in ("IST", "+0530"), (
        f"Process local timezone is {time.tzname!r}, expected India Standard Time. "
        "Check backend/Dockerfile's ENV TZ=Asia/Kolkata and tzdata installation."
    )


def test_date_today_matches_explicit_asia_kolkata_date():
    """The exact property this fix exists for: date.today() (used
    pervasively for overdue checks, dashboard "today" figures, report
    date-range defaults, etc.) must equal the real calendar date in
    Asia/Kolkata at the moment it's called — not the UTC calendar date,
    which differs from it for 5.5 hours out of every 24."""
    expected = datetime.now(IST).date()
    assert date.today() == expected, (
        f"date.today() returned {date.today()} but the actual Asia/Kolkata "
        f"calendar date is {expected} — the container is not running in "
        "Asia/Kolkata local time."
    )


def test_utc_aware_timestamps_are_unaffected_by_the_timezone_fix():
    """Every stored timestamp in this codebase uses the explicit,
    timezone-aware datetime.now(timezone.utc) — never a naive
    datetime.now(). Confirms the container-timezone fix (which only
    changes what a *naive*/unqualified call returns) cannot have shifted
    any stored UTC timestamp: datetime.now(timezone.utc) must still return
    a UTC-aware value regardless of the process's local timezone."""
    now_utc = datetime.now(timezone.utc)
    assert now_utc.tzinfo is not None
    assert now_utc.utcoffset().total_seconds() == 0
    # And it must differ from the naive local now() by the expected IST
    # offset (5 hours 30 minutes), proving the two are genuinely on
    # different clocks rather than one silently being a copy of the other.
    naive_local = datetime.now()
    offset_seconds = (naive_local - now_utc.replace(tzinfo=None)).total_seconds()
    assert abs(offset_seconds - 5.5 * 3600) < 120, (
        f"Expected naive local time to lead UTC by 5h30m (IST), got {offset_seconds}s"
    )
