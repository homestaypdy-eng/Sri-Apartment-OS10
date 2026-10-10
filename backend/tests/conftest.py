"""
OS10-TEST-ISOLATION-01 — the ONLY place in this project that decides which
database the test suite talks to.

Why this file exists: this test suite (all 1325+ tests) is written as
integration tests that call SessionLocal()/TestClient(app) directly against
a real Postgres — there is no per-test transaction rollback. Before this
file existed, that meant pytest hit the same database the live app uses
(DATABASE_URL), permanently writing fixture rows into real dev data. An
investigation (WEBSITE-01A) found 523 properties / 516 units / 353 bookings
accumulated that way, most of it in a single day.

The fix here is deliberately NOT "rewrite every test to use a transaction
fixture" (that would touch hundreds of files for no safety gain beyond what
this approach already gives) — it's "make the database boundary itself the
safety mechanism": redirect DATABASE_URL to a dedicated test-only database
before any application code is ever imported, and refuse to run at all if
that redirection can't be proven safe.

ORDERING IS THE WHOLE POINT: app.db.session creates its SQLAlchemy engine
at MODULE IMPORT TIME, bound to whatever app.core.config.settings.database_url
resolves to at that moment. pytest guarantees conftest.py files are
imported before any test module is collected. So as long as the
environment override below runs as the first thing in this file — before
this file (or anything it imports) ever does `import app.xxx` — every
test in the suite gets the test database automatically, with zero changes
to any existing test file.
"""

import os
import sys

# ---------------------------------------------------------------------------
# STEP A — hard safety gate. Runs at conftest.py import time, i.e. before
# pytest imports a single test module. No app.* import has happened yet.
# ---------------------------------------------------------------------------

_dev_url = os.environ.get("DATABASE_URL", "")
_test_url = os.environ.get("TEST_DATABASE_URL")

# Names that must NEVER be the resolved test database, however it got
# configured. Matched case-insensitively against the database name in the
# connection URL (the part after the last "/").
_FORBIDDEN_DB_NAMES = {"sri_apartment_os", "postgres"}


def _fail(message: str) -> None:
    # pytest.exit() aborts the entire test session immediately, before any
    # test runs — this is not a warning, and there is no fallback path.
    import pytest

    pytest.exit(f"\n\nOS10-TEST-ISOLATION-01 SAFETY GUARD ABORTED THE TEST RUN:\n{message}\n", returncode=1)


def _db_name_from_url(url: str) -> str:
    return url.rsplit("/", 1)[-1].split("?", 1)[0]


if not _test_url:
    _fail(
        "TEST_DATABASE_URL is not set.\n"
        "This test suite refuses to run against DATABASE_URL (the development database) — ever.\n"
        "Set TEST_DATABASE_URL to a dedicated test database (see docker-compose.yml's `test-db` service\n"
        "and .env.example) before running pytest."
    )

if _dev_url and _test_url == _dev_url:
    _fail("TEST_DATABASE_URL is identical to DATABASE_URL. They must point at different databases/servers.")

_test_db_name = _db_name_from_url(_test_url)

if _test_db_name.lower() in _FORBIDDEN_DB_NAMES:
    _fail(
        f"TEST_DATABASE_URL resolves to database '{_test_db_name}', which is a known development/system "
        f"database name. Refusing to run tests against it. The test database name must contain 'test'."
    )

if "test" not in _test_db_name.lower():
    _fail(
        f"TEST_DATABASE_URL resolves to database '{_test_db_name}', which does not contain 'test' in its name. "
        f"Requiring an explicit 'test' marker in the database name is the safeguard against a typo'd or "
        f"copy-pasted production/dev URL silently being accepted."
    )

if "@db:" in _test_url or "@localhost:5432" in _test_url:
    _fail(
        f"TEST_DATABASE_URL points at the same host/port ({'db:5432' if '@db:' in _test_url else 'localhost:5432'}) "
        f"as the known development database. A dedicated test database must run on a separate service/port "
        f"(see docker-compose.yml's `test-db` service, which uses host port 5433)."
    )

# Only now — after every check above has passed — redirect the app's
# database configuration. This must happen before the first `import app`
# anywhere (including below in this same file), since app.core.config
# reads DATABASE_URL once, at Settings() construction time.
os.environ["DATABASE_URL"] = _test_url

# Guard against a previous test run (or an already-imported module from
# some other conftest/plugin) having imported app.core.config before this
# point ran — if that ever happens the override above is too late, so
# fail loudly rather than silently proceeding against the wrong database.
if "app.core.config" in sys.modules or "app.db.session" in sys.modules:
    _fail(
        "app.core.config or app.db.session was already imported before the test-database override could run. "
        "This means something imported application code before conftest.py's safety gate executed, and the "
        "database-URL redirection may not have taken effect. Refusing to continue rather than risk running "
        "against the development database."
    )

# ---------------------------------------------------------------------------
# STEP B — now safe to import application code. settings.database_url will
# resolve to TEST_DATABASE_URL, and app.db.session's engine/SessionLocal
# will be bound to it.
# ---------------------------------------------------------------------------

import pytest  # noqa: E402
from sqlalchemy import text  # noqa: E402

# ---------------------------------------------------------------------------
# Shared admin test credentials — single source of truth for the test files
# that log in as the seeded bootstrap admin. Each of those files used to
# declare its own `ADMIN_PASSWORD = "Sri@2026"` literal; that silently drifts
# out of sync the moment BOOTSTRAP_ADMIN_PASSWORD changes in .env, producing
# a cascade of 401s (and, once enough accumulate in one run, 429s from the
# real login rate-limiter in app/shared/rate_limit.py). Test files import
# these two names from here (`from conftest import ADMIN_EMAIL,
# ADMIN_PASSWORD`) instead of declaring their own.
# ---------------------------------------------------------------------------
ADMIN_EMAIL = "homestaypdy@gmail.com"
ADMIN_PASSWORD = os.environ.get("BOOTSTRAP_ADMIN_PASSWORD", "")


@pytest.fixture(scope="session", autouse=True)
def _reset_test_database():
    """
    Runs once per test session, before any test. Defense-in-depth: re-checks
    the bound engine (not just the env var string) actually points at the
    test database, then wipes and recreates the schema from scratch via the
    project's existing seed mechanism — so every test run starts from a
    known-clean state instead of accumulating rows run after run.
    """
    from app.db import seed  # seed.run() does Base.metadata.create_all() + apply_indexes()
    # + apply_additive_migrations() + role/permission/admin seeding — reusing the
    # project's existing schema-init mechanism rather than inventing a second one.
    from app.db.session import engine

    # Defense-in-depth re-check against the REAL bound engine, not just the
    # environment variable string checked in STEP A.
    resolved_db_name = engine.url.database or ""
    if resolved_db_name.lower() in _FORBIDDEN_DB_NAMES or "test" not in resolved_db_name.lower():
        _fail(
            f"Defense-in-depth check failed: the SQLAlchemy engine is bound to database "
            f"'{resolved_db_name}', which does not look like a test database. Aborting before "
            f"touching any data."
        )
    if engine.url.host == "db":
        _fail("Defense-in-depth check failed: the SQLAlchemy engine's host is 'db' (the dev database host).")

    # Schema-level reset rather than Base.metadata.drop_all(): this project
    # has (legitimate, by-design) circular foreign keys between
    # booking_holds and gateway_orders — a hold references its gateway
    # order and the order references its hold back, matching how
    # public_booking/router.py actually creates them (order first, flush,
    # hold referencing it, then order updated with hold_id). create_all()
    # handles that cycle fine; SQLAlchemy's drop_all() cannot topologically
    # sort it and raises CircularDependencyError. Dropping the whole
    # schema and recreating it hands the ordering problem to Postgres
    # itself, which resolves it trivially via CASCADE — safe here
    # specifically because this only ever runs against the database this
    # fixture already verified (above, and in the module-level checks) is
    # the dedicated test database, never the dev one.
    with engine.connect() as conn:
        conn.execute(text("DROP SCHEMA public CASCADE;"))
        conn.execute(text("CREATE SCHEMA public;"))
        conn.execute(text("CREATE EXTENSION IF NOT EXISTS btree_gist;"))
        conn.execute(text("CREATE EXTENSION IF NOT EXISTS pgcrypto;"))
        conn.commit()

    seed.run()

    yield
    # No teardown drop: the next session's setup wipes it again anyway, and
    # leaving data in place after a run makes it easy to inspect results of
    # a just-finished test session if needed.


@pytest.fixture(autouse=True)
def _disable_external_email_providers_in_tests(monkeypatch):
    """Never let the developer's local email credentials escape in tests.

    Provider-specific tests opt in explicitly with their own monkeypatches
    and mock the HTTP/SMTP transport. The default suite runs with sending
    unconfigured, regardless of credentials present in a developer .env.
    """
    from app.core.config import settings

    monkeypatch.setattr(settings, "email_provider", "gmail_smtp")
    monkeypatch.setattr(settings, "smtp_host", "")
    monkeypatch.setattr(settings, "smtp_user", "")
    monkeypatch.setattr(settings, "smtp_password", "")
    monkeypatch.setattr(settings, "resend_api_key", "")
    monkeypatch.setattr(settings, "resend_from_email", "")


@pytest.fixture(autouse=True)
def _fail_fast_on_real_smtp_connections(monkeypatch):
    """
    Test-only network guard. The separate provider-safety fixture disables
    configured email credentials by default. This additional guard fails
    closed if a test deliberately enables SMTP without mocking its
    transport; SMTP connection attempts cannot reach an external server.

    The separate _disable_external_email_providers_in_tests fixture keeps
    delivery disabled by default. This is defense in depth for tests that
    explicitly configure SMTP; provider-specific tests mock their
    transports, and IPv4/IPv6 preference tests use loopback sockets only.

    Any test that wants to observe a successful "send" continues to do so
    via its own explicit patch("...send_email", ...) (or equivalent),
    which replaces the whole function and never reaches smtplib at all —
    this fixture only affects the branch where send_email() is genuinely
    allowed to run for real.
    """
    import smtplib

    class _FastFailSMTP:
        def __init__(self, *args, **kwargs):
            raise OSError("[test-environment] Connection refused — real SMTP is intentionally fast-failed in tests, see tests/conftest.py")

    monkeypatch.setattr(smtplib, "SMTP", _FastFailSMTP)

    # notifications.service._PreferIPv6SMTP (the IPv6-first connection
    # optimization) subclasses the REAL smtplib.SMTP at import time, and
    # only overrides _get_socket with its own raw socket.socket()/.connect()
    # calls — it never calls smtplib.SMTP(...) by name, so the patch above
    # does not reach it. Patch its _get_socket directly for the identical
    # fast-fail guarantee, so send_email() still can't make a real,
    # multi-second network connection attempt in any test, through either
    # code path.
    from app.modules.notifications.service import _PreferIPv6SMTP

    def _fast_fail_get_socket(self, host, port, timeout):
        raise OSError("[test-environment] Connection refused — real SMTP is intentionally fast-failed in tests, see tests/conftest.py")

    monkeypatch.setattr(_PreferIPv6SMTP, "_get_socket", _fast_fail_get_socket)


@pytest.fixture(autouse=True)
def _isolate_public_booking_rate_limit_buckets():
    """Give each test a fresh anonymous-booking rate-limit window.

    Public rate limiting is intentionally persisted in PostgreSQL and stays
    enabled in the application. TestClient gives every test the same peer
    address, however, so the suite's many independent hold scenarios shared
    one production-shaped bucket and began returning 429 after ten requests.
    Clear only the test database's rate-limit rows before each test; tests
    that verify the limiter still exercise multiple requests within their
    own test and retain the real configured limits.
    """
    from app.db.session import SessionLocal
    from app.modules.public_booking.models import PublicRateLimitBucket

    db = SessionLocal()
    try:
        db.query(PublicRateLimitBucket).delete(synchronize_session=False)
        db.commit()
    finally:
        db.close()


@pytest.fixture(autouse=True)
def _safe_fake_razorpay_configuration(monkeypatch):
    """Provide deterministic fake gateway configuration without network IO.

    Successful hold tests replace the provider function themselves. The
    defaults below let the route reach those mocks; explicit
    "not configured" tests still override these settings. Any test that
    forgets to mock an order/refund call fails closed instead of reaching
    Razorpay.
    """
    from app.core.config import settings

    monkeypatch.setattr(settings, "razorpay_key_id", "rzp_test_os10_mock")
    monkeypatch.setattr(settings, "razorpay_key_secret", "test_key_secret_for_unit_tests")

    monkeypatch.setattr("app.modules.public_booking.router.verify_provider_payment", lambda *args, **kwargs: True)

    # Keep the provider's own configuration checks intact while making any
    # missed request mock fail before it can leave the test process.
    import requests

    def _provider_http_must_be_mocked(*_args, **_kwargs):
        raise AssertionError("Razorpay HTTP call must be mocked in tests")

    monkeypatch.setattr(requests, "post", _provider_http_must_be_mocked)
    monkeypatch.setattr(requests, "get", _provider_http_must_be_mocked)
