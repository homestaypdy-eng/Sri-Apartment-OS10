"""Phase 15E-1 (+ Phase 15G addition) — durable storage for automation
authorization state.

Phase 15E-1 created four tables: action_proposals, policy_decisions,
approvals, audit_entries. Phase 15G adds a fifth, `policies` — until
now a Policy was always a caller-supplied, in-memory object (every test
across 15B-15F constructs one directly); production activation requires
an actual persisted row so the real deployed system has something to
load rather than something a script hands it ad hoc. No existing table
is touched or altered. No column is added to bookings, booking_holds,
payments, guests, units, expenses, or any GST/OTA table.

JSON-shaped columns (target, parameters, evaluated_conditions, detail)
are stored as Text containing a JSON string, matching this codebase's
existing convention (see payment_gateway/models.py's `payload` field and
inquiries/models.py's `rate_breakdown` field) rather than introducing a
new JSON/JSONB column type pattern.

These tables are created the same way every other table in this project
is: SQLAlchemy models registering on Base.metadata, applied via
app/db/seed.py's `Base.metadata.create_all(bind=engine)`. This project
has no wired-up Alembic migration system — `alembic.ini` does not exist
anywhere in the repository, and the `alembic/versions` directory is an
unused, empty scaffold. Creating an Alembic migration file here would
never actually run against any real deployment of this project, so none
was created; this is the actual, verified migration mechanism.
"""
from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import DateTime, ForeignKey, Index, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.db.session import Base


class PolicyRow(Base):
    """Phase 15G: exactly one row is ever inserted here in production —
    release_expired_hold — via a controlled, explicit script, never
    through a generic write-any-policy endpoint (none exists). Mirrors
    the Phase 15B Policy schema's fields exactly."""
    __tablename__ = "policies"
    __table_args__ = (
        Index("ix_policies_action_type", "action_type"),
        Index("ix_policies_enabled", "enabled"),
    )

    policy_id: Mapped[str] = mapped_column(String(200), primary_key=True)
    action_type: Mapped[str] = mapped_column(String(100), nullable=False)
    automation_level: Mapped[str] = mapped_column(String(10), nullable=False)
    risk_level: Mapped[str] = mapped_column(String(20), nullable=False)
    conditions: Mapped[str] = mapped_column(Text, nullable=False)  # JSON string (list), empty list for this policy
    required_role: Mapped[str] = mapped_column(String(20), nullable=False)
    requires_approval: Mapped[bool] = mapped_column(nullable=False)
    enabled: Mapped[bool] = mapped_column(nullable=False)
    version: Mapped[int] = mapped_column(nullable=False)
    # Phase 15I: distinguishes a temporary disable (enabled=False,
    # status still ACTIVE) from a permanent retirement (enabled=False,
    # status=RETIRED). Added via additive_migrations.py for the
    # already-deployed production table — see that file's entry — so
    # the existing release_expired_hold row gets 'ACTIVE' by default,
    # which is semantically correct and changes nothing about it.
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="ACTIVE")
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(timezone.utc), nullable=False
    )


class PolicyAuditEntryRow(Base):
    """Phase 15I: dedicated, append-only policy-lifecycle audit trail —
    separate from audit_entries (Phase 15E-1), whose action_id column is
    NOT NULL with a foreign key to action_proposals and therefore cannot
    record policy-level or global-switch-level events that aren't about
    any specific action. No update/delete function exists anywhere in
    governance.py for this table (see that module and its tests); a
    database-level trigger enforcing the same thing is added via
    additive_migrations.py.

    policy_id/policy_version are nullable specifically for
    GLOBAL_AUTOMATION_ENABLED/GLOBAL_AUTOMATION_DISABLED events, which
    are system-level, not about any one policy."""
    __tablename__ = "policy_audit_entries"
    __table_args__ = (
        Index("ix_policy_audit_entries_policy_id", "policy_id"),
        Index("ix_policy_audit_entries_timestamp", "timestamp"),
        Index("ix_policy_audit_entries_event_type", "event_type"),
    )

    policy_audit_id: Mapped[str] = mapped_column(String(200), primary_key=True)
    policy_id: Mapped[str | None] = mapped_column(String(200), nullable=True)
    policy_version: Mapped[int | None] = mapped_column(nullable=True)
    event_type: Mapped[str] = mapped_column(String(40), nullable=False)
    actor: Mapped[str] = mapped_column(String(200), nullable=False)
    timestamp: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    previous_state: Mapped[str | None] = mapped_column(Text, nullable=True)  # JSON string
    new_state: Mapped[str | None] = mapped_column(Text, nullable=True)  # JSON string
    change_reason: Mapped[str | None] = mapped_column(String(500), nullable=True)
    change_hash: Mapped[str] = mapped_column(String(64), nullable=False)


class ActionProposalRow(Base):
    __tablename__ = "action_proposals"
    __table_args__ = (
        UniqueConstraint("idempotency_key", name="uq_action_proposals_idempotency_key"),
        Index("ix_action_proposals_policy_id", "policy_id"),
        Index("ix_action_proposals_status", "status"),
    )

    # action_id is the primary key itself — the uniqueness this project
    # was asked to enforce IS the primary key constraint, no separate
    # surrogate id column needed.
    action_id: Mapped[str] = mapped_column(String(200), primary_key=True)
    action_type: Mapped[str] = mapped_column(String(100), nullable=False)
    policy_id: Mapped[str] = mapped_column(String(200), nullable=False)
    risk_level: Mapped[str] = mapped_column(String(20), nullable=False)
    automation_level: Mapped[str] = mapped_column(String(10), nullable=False)
    actor: Mapped[str] = mapped_column(String(200), nullable=False)
    target: Mapped[str] = mapped_column(Text, nullable=False)  # JSON string, closed 15C schema only
    parameters: Mapped[str] = mapped_column(Text, nullable=False)  # JSON string, closed 15C schema only
    idempotency_key: Mapped[str] = mapped_column(String(200), nullable=False)
    approval_requirement: Mapped[str] = mapped_column(String(20), nullable=False)
    proposal_hash: Mapped[str] = mapped_column(String(64), nullable=False)  # sha256 hex digest
    status: Mapped[str] = mapped_column(String(30), nullable=False)
    timestamp: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(timezone.utc), nullable=False
    )


class PolicyDecisionRow(Base):
    __tablename__ = "policy_decisions"
    __table_args__ = (
        Index("ix_policy_decisions_action_id", "action_id"),
        Index("ix_policy_decisions_policy_id", "policy_id"),
    )

    decision_id: Mapped[str] = mapped_column(String(200), primary_key=True)
    action_id: Mapped[str] = mapped_column(String(200), ForeignKey("action_proposals.action_id"), nullable=False)
    policy_id: Mapped[str] = mapped_column(String(200), nullable=False)
    policy_version: Mapped[int | None] = mapped_column(nullable=True)
    outcome: Mapped[str] = mapped_column(String(20), nullable=False)
    reason: Mapped[str] = mapped_column(String(200), nullable=False)
    evaluated_conditions: Mapped[str] = mapped_column(Text, nullable=False)  # JSON string (list)
    evaluated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class ApprovalRow(Base):
    """One row per action_id. action_id is UNIQUE — the initial
    APPROVED/REJECTED write is a real, atomic, database-enforced
    compare-and-swap (a second concurrent INSERT for the same action_id
    fails the unique constraint, full stop, no read-then-write race
    possible). Revocation and expiry are modeled as conditional UPDATEs
    (`WHERE decision = 'APPROVED'`) against this same row rather than a
    new row — the record is never deleted, only ever transitioned, so
    "expired/revoked approval remains persisted" holds by construction:
    nothing here has a delete path at all.

    `status` intentionally mirrors `decision` — Phase 15D's pure Approval
    model doesn't distinguish the two, and no test or instruction defines
    a separate "current vs superseded" concept for this phase, so this
    column exists for literal schema completeness (as requested) without
    inventing new semantics beyond what `decision` already captures.
    """
    __tablename__ = "approvals"
    __table_args__ = (
        UniqueConstraint("action_id", name="uq_approvals_action_id"),
        Index("ix_approvals_action_id", "action_id"),
    )

    approval_id: Mapped[str] = mapped_column(String(200), primary_key=True)
    action_id: Mapped[str] = mapped_column(String(200), ForeignKey("action_proposals.action_id"), nullable=False)
    proposal_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    policy_id: Mapped[str] = mapped_column(String(200), nullable=False)
    policy_version: Mapped[int] = mapped_column(nullable=False)
    approver: Mapped[str] = mapped_column(String(200), nullable=False)
    decision: Mapped[str] = mapped_column(String(20), nullable=False)
    status: Mapped[str] = mapped_column(String(20), nullable=False)
    reason: Mapped[str | None] = mapped_column(String(500), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class AuditEntryRow(Base):
    """Append-only: no update_audit_entry / delete_audit_entry function
    exists anywhere in this module or persistence.py — there is no code
    path in the automation service capable of modifying or removing a
    row here once written. A database-level trigger/rule enforcing this
    at the SQL layer was not added in 15E-1: this project has no
    migration mechanism to deploy custom DDL beyond
    Base.metadata.create_all() (see module docstring), so a trigger
    defined only in a migration file that never runs would be a false
    guarantee. Application-level enforcement (the missing code path) is
    what's actually in place; this limitation is reported explicitly
    rather than silently assumed away.
    """
    __tablename__ = "audit_entries"
    __table_args__ = (
        Index("ix_audit_entries_action_id", "action_id"),
        Index("ix_audit_entries_timestamp", "timestamp"),
    )

    audit_id: Mapped[str] = mapped_column(String(200), primary_key=True)
    action_id: Mapped[str] = mapped_column(String(200), ForeignKey("action_proposals.action_id"), nullable=False)
    event_type: Mapped[str] = mapped_column(String(40), nullable=False)
    actor: Mapped[str] = mapped_column(String(200), nullable=False)
    timestamp: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    detail: Mapped[str] = mapped_column(Text, nullable=False)  # JSON string
