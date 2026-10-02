"""Schema-only PostgreSQL migration entrypoint.

Run with ``python -m app.db.migrate`` before application startup. This module
imports ORM metadata but never imports the FastAPI application, seed/bootstrap
workflow, scheduler, or external-provider integrations. Any failure propagates
to the process and returns a non-zero exit status.
"""

from sqlalchemy import text
from sqlalchemy.engine import Engine

# Populate Base.metadata without importing app.main or starting application
# lifespan behavior. This registry contains model imports only.
from app.db import model_registry as _model_registry  # noqa: F401
from app.db.additive_migrations import apply_additive_migrations
from app.db.indexes import apply_indexes
from app.db.session import Base, engine


def run(target_engine: Engine | None = None) -> None:
    """Apply PostgreSQL schema changes in one transaction where supported."""
    target = target_engine or engine
    if target.dialect.name != "postgresql":
        raise RuntimeError("The schema migration command requires PostgreSQL.")

    # The current DDL set (extensions, create_all, standard indexes, ALTERs,
    # constraints, functions and triggers) is transactional on PostgreSQL.
    # A single transaction prevents partially applied schema on failure.
    with target.begin() as connection:
        connection.execute(text("CREATE EXTENSION IF NOT EXISTS btree_gist"))
        connection.execute(text("CREATE EXTENSION IF NOT EXISTS pgcrypto"))
        Base.metadata.create_all(bind=connection)
        apply_indexes(connection)
        apply_additive_migrations(connection)

    print("Database schema migration complete.")


if __name__ == "__main__":
    run()
