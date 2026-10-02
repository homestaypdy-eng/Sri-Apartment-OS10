"""Integration coverage for the schema-only migration entrypoint.

All database work is constrained to an isolated temporary schema inside the
dedicated pytest database; the fixture asserts that database identity before
creating anything.
"""
import os
import subprocess
import sys
import uuid

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, text
from sqlalchemy.orm import Session

from app.core.config import settings
from app.db.migrate import run
from app.db.session import Base, engine
from app.main import app
from app.modules.settings.models import BusinessSettings


def _assert_isolated_test_database():
    assert engine.dialect.name == "postgresql"
    assert engine.url.database == "sri_apartment_os_test"
    assert engine.url.host not in {"db", "localhost"}


def test_migration_first_and_second_run_are_schema_only_and_keep_guards():
    _assert_isolated_test_database()
    schema = f"migration_test_{uuid.uuid4().hex}"
    with engine.begin() as connection:
        connection.execute(text(f'CREATE SCHEMA "{schema}"'))

    isolated_engine = create_engine(
        engine.url,
        connect_args={"options": f"-c search_path={schema},public"},
        future=True,
    ).execution_options(schema_translate_map={None: schema})
    try:
        with isolated_engine.connect() as connection:
            assert connection.execute(text("SELECT current_schema()")).scalar_one() == schema
        # Model creation represents a pre-existing installation. Remove
        # selected post-bootstrap additions so the additive path must restore
        # them, and remove the rate-limit table so create_all must add it.
        Base.metadata.create_all(bind=isolated_engine)
        with Session(isolated_engine) as db:
            db.add(BusinessSettings())
            db.commit()
            db.execute(text(
                "UPDATE business_settings SET secondary_contact_email = NULL, "
                "secondary_whatsapp_number = NULL"
            ))
            db.commit()

        with isolated_engine.begin() as connection:
            for column in (
                "cancellation_at", "cancellation_checkin_at", "cancellation_policy_percent",
                "cancellation_refund_target", "cancellation_refund_source", "cancellation_requires_review",
            ):
                connection.execute(text(f"ALTER TABLE bookings DROP COLUMN {column} CASCADE"))
            connection.execute(text("ALTER TABLE customer_accounts DROP COLUMN guest_id CASCADE"))
            connection.execute(text("ALTER TABLE customer_accounts DROP COLUMN phone_verified_at CASCADE"))
            connection.execute(text("DROP TABLE public_rate_limit_buckets"))

        before = isolated_engine.connect()
        try:
            before_snapshot = before.execute(text(
                "SELECT company_name, contact_email, secondary_contact_email, "
                "secondary_whatsapp_number FROM business_settings"
            )).all()
            assert len(before_snapshot) == 1
            assert before_snapshot[0][2:] == (None, None)
            # This engine is intentionally pointed at an isolated schema,
            # while pytest's session fixture has already bootstrapped the
            # public test schema. Compare rows before/after instead of
            # assuming globally empty bootstrap tables.
            bootstrap_counts = tuple(
                before.execute(text(f"SELECT COUNT(*) FROM {table}")).scalar_one()
                for table in ("users", "roles", "permissions")
            )
        finally:
            before.close()

        run(isolated_engine)
        run(isolated_engine)

        with isolated_engine.connect() as connection:
            after_snapshot = connection.execute(text(
                "SELECT company_name, contact_email, secondary_contact_email, "
                "secondary_whatsapp_number FROM business_settings"
            )).all()
            assert after_snapshot == before_snapshot
            assert tuple(
                connection.execute(text(f"SELECT COUNT(*) FROM {table}")).scalar_one()
                for table in ("users", "roles", "permissions")
            ) == bootstrap_counts

            required_tables = {
                row[0] for row in connection.execute(text(
                    "SELECT table_name FROM information_schema.tables "
                    "WHERE table_schema = current_schema() AND table_type = 'BASE TABLE'"
                ))
            }
            assert {
                "booking_guests", "conversations", "conversation_messages", "expense_payments",
                "public_rate_limit_buckets", "customer_accounts", "guest_documents", "gateway_orders",
            } <= required_tables

            required_columns = {
                row[0] for row in connection.execute(text(
                    "SELECT column_name FROM information_schema.columns "
                    "WHERE table_schema = current_schema() AND table_name = 'bookings'"
                ))
            }
            assert {
                "cancellation_at", "cancellation_checkin_at", "cancellation_policy_percent",
                "cancellation_refund_target", "cancellation_refund_source", "cancellation_requires_review",
                "stay_range",
            } <= required_columns
            assert connection.execute(text(
                "SELECT to_regclass('public_rate_limit_buckets') IS NOT NULL"
            )).scalar_one()
            assert connection.execute(text(
                "SELECT COUNT(*) FROM pg_constraint WHERE conrelid = 'bookings'::regclass "
                "AND conname = 'no_overlapping_bookings' AND contype = 'x'"
            )).scalar_one() == 1
            assert connection.execute(text(
                "SELECT COUNT(*) FROM pg_constraint WHERE conrelid = 'booking_holds'::regclass "
                "AND conname = 'no_overlapping_holds' AND contype = 'x'"
            )).scalar_one() == 1
            assert connection.execute(text(
                "SELECT COUNT(*) FROM pg_trigger WHERE tgrelid = 'policy_audit_entries'::regclass "
                "AND tgname = 'trg_policy_audit_append_only' AND NOT tgisinternal"
            )).scalar_one() == 1
            assert connection.execute(text(
                "SELECT COUNT(*) FROM pg_indexes WHERE schemaname = current_schema() "
                "AND indexname = 'ux_customer_accounts_guest_id'"
            )).scalar_one() == 1
            assert connection.execute(text(
                "SELECT COUNT(*) FROM pg_indexes WHERE schemaname = current_schema() "
                "AND indexname IN ('ux_payments_idempotency_key', 'ux_refunds_idempotency_key', "
                "'ux_refunds_gateway_refund_id')"
            )).scalar_one() == 3

        assert settings.enable_scheduler is False
        with engine.connect() as connection:
            public_data_snapshot = {
                table: connection.execute(text(f"SELECT COUNT(*) FROM {table}")).scalar_one()
                for table in ("properties", "units", "bookings", "payments", "users", "roles", "permissions")
            }
            settings_snapshot = connection.execute(text(
                "SELECT company_name, contact_email, secondary_contact_email, secondary_whatsapp_number "
                "FROM business_settings ORDER BY id"
            )).all()
        migration_env = os.environ.copy()
        migration_env["DATABASE_URL"] = engine.url.render_as_string(hide_password=False)
        migration_env["ENABLE_SCHEDULER"] = "false"
        migration_cwd = os.path.dirname(os.path.dirname(__file__))
        for _ in range(2):
            migration_result = subprocess.run(
                [sys.executable, "-m", "app.db.migrate"],
                cwd=migration_cwd,
                env=migration_env,
                capture_output=True,
                text=True,
                timeout=60,
            )
            assert migration_result.returncode == 0, "schema-only migration CLI failed"
            assert "Database schema migration complete." in migration_result.stdout

        with engine.connect() as connection:
            assert {
                table: connection.execute(text(f"SELECT COUNT(*) FROM {table}")).scalar_one()
                for table in public_data_snapshot
            } == public_data_snapshot
            assert connection.execute(text(
                "SELECT company_name, contact_email, secondary_contact_email, secondary_whatsapp_number "
                "FROM business_settings ORDER BY id"
            )).all() == settings_snapshot

        with TestClient(app) as client:
            assert client.get("/health").status_code == 200
            assert getattr(app.state, "scheduler", None) is None
    finally:
        isolated_engine.dispose()
        with engine.begin() as connection:
            connection.execute(text(f'DROP SCHEMA IF EXISTS "{schema}" CASCADE'))


def test_migration_cli_failure_exits_nonzero_without_startup():
    _assert_isolated_test_database()
    env = os.environ.copy()
    env.update({
        "DATABASE_URL": "postgresql+psycopg://test:test@127.0.0.1:1/sri_apartment_os_test?connect_timeout=1",
        "ENABLE_SCHEDULER": "false",
        "ENVIRONMENT": "test",
    })
    result = subprocess.run(
        [sys.executable, "-m", "app.db.migrate"],
        cwd=os.path.dirname(os.path.dirname(__file__)),
        env=env,
        capture_output=True,
        text=True,
        timeout=10,
    )
    assert result.returncode != 0
    assert "Database schema migration complete" not in result.stdout
    assert "uvicorn" not in result.stdout.lower()
    assert "uvicorn" not in result.stderr.lower()
