"""Phase 15B — Autonomous Policy Engine schemas.

Pure data structures for the Phase 15A design's Policy Engine. No
database model, no API schema, no execution-related field — this module
only describes what a proposal, a policy, a condition, and a decision
look like. See policy_engine.py for the deterministic evaluation logic
that consumes these.
"""
from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

AutomationLevel = Literal["L2", "L3", "L4"]
RiskLevel = Literal["LOW", "MEDIUM", "HIGH", "CRITICAL"]
Role = Literal["admin", "staff"]
ConditionOperator = Literal["eq", "ne", "gt", "gte", "lt", "lte", "in"]
PolicyOutcome = Literal["ALLOW", "DENY", "REQUIRE_APPROVAL"]
ApprovalRequirement = Literal["none", "single_admin", "dual_admin"]

# The full future lifecycle (Phase 15A §10). Only "PROPOSED" is actually
# reachable by anything in this phase — the rest exist so ActionProposal's
# `status` field can be typed correctly now without inventing a smaller,
# throwaway enum that a later phase would have to widen.
ProposalStatus = Literal[
    "PROPOSED", "APPROVAL_REQUIRED", "APPROVED", "EXECUTING", "SUCCEEDED",
    "FAILED", "ROLLED_BACK", "REJECTED", "EXPIRED", "CANCELLED",
]


class Condition(BaseModel):
    field: str
    operator: ConditionOperator
    value: Any


class Policy(BaseModel):
    policy_id: str
    action_type: str
    automation_level: AutomationLevel
    risk_level: RiskLevel
    conditions: list[Condition] = []
    required_role: Role
    requires_approval: bool
    enabled: bool
    version: int


class ActionProposal(BaseModel):
    action_id: str
    action_type: str
    policy_id: str
    risk_level: RiskLevel
    automation_level: AutomationLevel
    actor: str
    target: dict[str, Any]
    parameters: dict[str, Any]
    idempotency_key: str
    approval_requirement: ApprovalRequirement
    timestamp: datetime
    status: ProposalStatus
    proposal_hash: str


class EvaluatedCondition(BaseModel):
    field: str
    operator: ConditionOperator
    value: Any
    actual: Any
    data_present: bool
    passed: bool


class PolicyDecision(BaseModel):
    decision_id: str
    action_id: str
    policy_id: str
    policy_version: int | None
    outcome: PolicyOutcome
    reason: str
    evaluated_conditions: list[EvaluatedCondition]
    evaluated_at: datetime


# --- Phase 15C: closed target/parameter shapes for registered actions ---
#
# Each registered action_type gets its own closed (extra="forbid")
# target/parameters schema here. These are structural-validation shapes
# only — they never carry guest PII, payment data, or a financial
# figure; the action just identifies WHAT to act on, never duplicates
# the target entity's own data.


class ReleaseExpiredHoldTarget(BaseModel):
    """Identifies exactly one existing BookingHold row by id. No guest
    name/phone/email, no pricing/GST fields — those already live on the
    BookingHold row itself (public_booking/models.py) and are never
    copied into an action's target."""
    model_config = ConfigDict(extra="forbid")
    entity_type: Literal["booking_hold"]
    entity_id: str


class ReleaseExpiredHoldParameters(BaseModel):
    """Releasing an expired hold is a pure state-machine transition
    (pending_payment -> expired, per the existing BookingHold docstring)
    — it needs no parameters beyond the target itself. Closed and empty
    on purpose: any field at all is unexpected and rejected."""
    model_config = ConfigDict(extra="forbid")


# --- Phase 15D: human approval / override records -------------------------
#
# An Approval is a decision RECORD, not a queue entry — it only comes
# into existence once an admin (or, for EXPIRED, the passage of an
# explicit, externally-supplied expires_at) actually renders a decision.
# ActionProposal.status (already defined above, unchanged) is what
# tracks "APPROVAL_REQUIRED" while nothing has happened yet; Approval and
# ActionProposal are deliberately kept as separate concepts, matching
# Phase 15D's own instruction not to build a second status system.
#
# No field here carries a business threshold (no expiry duration is
# computed by this schema or by approval_workflow.py — expires_at is
# always supplied by the caller, or omitted).

ApprovalDecision = Literal["APPROVED", "REJECTED", "EXPIRED", "REVOKED"]


class Approval(BaseModel):
    approval_id: str
    action_id: str
    proposal_hash: str
    policy_id: str
    policy_version: int
    approver: str
    decision: ApprovalDecision
    created_at: datetime
    expires_at: datetime | None = None
    reason: str | None = None


# --- Phase 15E-1: append-only audit trail ----------------------------------
#
# Execution-related event types are defined here for future phases'
# schema stability, but nothing in Phase 15E-1 emits them — there is no
# executor anywhere in this codebase yet.

AuditEventType = Literal[
    "PROPOSED", "POLICY_EVALUATED", "APPROVAL_REQUESTED", "APPROVED", "REJECTED",
    "EXPIRED", "REVOKED", "CANCELLED", "EXECUTION_STARTED", "EXECUTION_SUCCEEDED",
    "EXECUTION_FAILED", "ROLLED_BACK", "KILL_SWITCH_BLOCKED",
]


class AuditEntry(BaseModel):
    audit_id: str
    action_id: str
    event_type: AuditEventType
    actor: str
    timestamp: datetime
    detail: dict[str, Any] = {}


# --- Phase 15I: policy lifecycle governance ---------------------------------
#
# A dedicated, closed event vocabulary for policy/global-switch history —
# deliberately separate from AuditEventType above, since audit_entries'
# action_id is NOT NULL with a foreign key to action_proposals and
# structurally cannot represent an event that isn't about one specific
# action (see governance.py's module docstring for the full rationale).

PolicyStatus = Literal["ACTIVE", "RETIRED"]

PolicyLifecycleEventType = Literal[
    "POLICY_CREATED", "POLICY_UPDATED", "POLICY_ENABLED", "POLICY_DISABLED",
    "POLICY_VERSION_BUMPED", "POLICY_RETIRED",
    "GLOBAL_AUTOMATION_ENABLED", "GLOBAL_AUTOMATION_DISABLED",
    "POLICY_AUDIT_BASELINE_ESTABLISHED",
]


class PolicyAuditEntry(BaseModel):
    policy_audit_id: str
    policy_id: str | None
    policy_version: int | None
    event_type: PolicyLifecycleEventType
    actor: str
    timestamp: datetime
    previous_state: dict[str, Any] | None
    new_state: dict[str, Any] | None
    change_reason: str | None
    change_hash: str


# --- Phase 16A: NOTIFY_STAFF closed target/parameter shapes --------------
#
# Mirrors the Phase 15C pattern (ReleaseExpiredHoldTarget/Parameters):
# closed (extra="forbid"), no guest PII, no financial data, no arbitrary
# metadata. severity values intentionally match Phase 13E's existing
# AIStrategistSignal vocabulary (INFO/WATCH/WARNING/CRITICAL) for
# consistency when a notification is generated from a 13E signal — but
# this module never imports reports/schemas.py to get it, keeping the
# automation package's existing decoupling from the AI Strategist
# package intact (same principle already applied to narrative isolation).

NotifyStaffCategory = Literal[
    "operational", "attention", "guest_request", "maintenance", "financial_review", "system",
]
NotifyStaffSeverity = Literal["INFO", "WATCH", "WARNING", "CRITICAL"]


class NotifyStaffTarget(BaseModel):
    model_config = ConfigDict(extra="forbid")
    entity_type: Literal["staff_notification"]
    entity_id: str


class NotifyStaffParameters(BaseModel):
    """message must already be a finished, deterministic statement —
    this schema does not compute or invent one; whoever constructs the
    ActionProposal is responsible for sourcing it from an existing
    deterministic signal (Phase 13E) and never inventing new content."""
    model_config = ConfigDict(extra="forbid")
    message: str = Field(max_length=500)
    category: NotifyStaffCategory
    severity: NotifyStaffSeverity
    source_id: str
