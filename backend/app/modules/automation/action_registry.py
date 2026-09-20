"""Phase 15C — deterministic Action Registry.

Answers exactly one question: "is this action type known to the system,
and what are its approved structural characteristics?" It never decides
whether a specific proposal is *permitted* (that is the Policy Engine's
job, policy_engine.py, Phase 15B — unchanged, not imported here) and it
never executes anything (no ActionExecutor exists anywhere in this
codebase yet).

Closed by design: only action_type strings explicitly present in
_REGISTRY are known. Everything else — PRICE_CHANGE, REFUND,
PAYMENT_CAPTURE, GST_ADJUSTMENT, BOOKING_CANCEL, SYNC_RATE,
SYNC_AVAILABILITY, UNIT_BLOCK, or any other string — is unregistered and
rejected. This is intentional per the Phase 15A design: a future action
gets added here only after its own explicit design/approval phase, never
implicitly.

RELEASE_EXPIRED_HOLD was the first registered action type (Phase 15C),
because Phase 15A's verification found the BookingHold TTL expiry
(public_booking/models.py, HOLD_STATUSES, "pending_payment -> expired")
to be the one already-existing, already-enforced deterministic rule in
the whole taxonomy. This module does not touch that model, does not
change its TTL, and does not release anything — it only describes the
action's shape.

NOTIFY_STAFF (Phase 16A) is the second: a purely advisory, internal
notification with no booking/payment/pricing/GST/OTA/guest-record write
path at all. Unlike RELEASE_EXPIRED_HOLD, its registry entry originally
shipped (Phase 16A) with enabled=False — an extra layer of caution for a
brand-new action type that had never been activated in any form before,
requiring both this registry entry AND its policy (governance.py, Phase
15I) to be explicitly, separately enabled before it could execute
anywhere. Phase 16B Gate B performed that explicit activation, after all
25 activation-gate checks passed: the persisted policy was enabled via
governance.set_policy_enabled(), and this entry's `enabled` field was
flipped True via a source edit + backend redeploy (the only way to
change it, exactly like GLOBAL_AUTOMATION_ENABLED in policy_engine.py).
"""
from __future__ import annotations

from dataclasses import dataclass

from pydantic import BaseModel, ValidationError

from app.modules.automation.schemas import (
    ActionProposal,
    ApprovalRequirement,
    AutomationLevel,
    NotifyStaffParameters,
    NotifyStaffTarget,
    ReleaseExpiredHoldParameters,
    ReleaseExpiredHoldTarget,
    RiskLevel,
    Role,
)


@dataclass(frozen=True)
class ActionRegistryEntry:
    action_type: str
    domain: str
    risk_level: RiskLevel
    automation_level: AutomationLevel
    required_role: Role
    approval_requirement: ApprovalRequirement
    enabled: bool
    rollback_supported: bool
    idempotency_required: bool
    target_entity_type: str
    target_schema: type[BaseModel]
    parameters_schema: type[BaseModel]
    policy_id: str | None


@dataclass(frozen=True)
class StructuralValidationResult:
    valid: bool
    reason: str | None
    entry: ActionRegistryEntry | None


_REGISTRY: dict[str, ActionRegistryEntry] = {
    "RELEASE_EXPIRED_HOLD": ActionRegistryEntry(
        action_type="RELEASE_EXPIRED_HOLD",
        domain="BOOKING",
        risk_level="LOW",
        automation_level="L2",
        required_role="staff",
        approval_requirement="none",
        enabled=True,
        # No compensating action exists to restore a released hold to
        # its previous state — a wrong release is a lost hold, not
        # corrupted data, recoverable only by the guest starting over.
        # That is a statement about impact severity, not about rollback
        # capability, so this is honestly False, not True — Phase 15A's
        # "acceptable L2 risk profile" note must not be read as "has a
        # rollback."
        rollback_supported=False,
        idempotency_required=True,
        target_entity_type="booking_hold",
        target_schema=ReleaseExpiredHoldTarget,
        parameters_schema=ReleaseExpiredHoldParameters,
        policy_id="pol:release-expired-hold",
    ),
    "NOTIFY_STAFF": ActionRegistryEntry(
        action_type="NOTIFY_STAFF",
        domain="OPERATIONS",
        risk_level="MEDIUM",
        automation_level="L2",
        required_role="staff",
        approval_requirement="none",
        # Phase 16B Gate B: flipped True after all 25 activation-gate
        # checks passed and the persisted notify_staff policy was
        # separately enabled via governance.set_policy_enabled(). Shipped
        # False at first (16A) as an extra layer of caution beyond the
        # policy-level gate alone — see module docstring.
        enabled=True,
        # A wrong/unwanted notification is not corrected via a
        # compensating ActionProposal — no such action is defined.
        rollback_supported=False,
        idempotency_required=True,
        target_entity_type="staff_notification",
        target_schema=NotifyStaffTarget,
        parameters_schema=NotifyStaffParameters,
        policy_id="notify_staff",
    ),
}


def is_registered(action_type: str) -> bool:
    return action_type in _REGISTRY


def get_registry_entry(action_type: str) -> ActionRegistryEntry | None:
    return _REGISTRY.get(action_type)


def list_registered_action_types() -> list[str]:
    return sorted(_REGISTRY.keys())


def validate_proposal_structure(proposal: ActionProposal) -> StructuralValidationResult:
    """Structural validation only: is this a known, enabled action type,
    and do target/parameters match its closed shape? This function never
    calls evaluate_policy() and never returns anything resembling an
    authorization outcome — a `valid=True` result here means "this
    proposal is well-formed," nothing more. It still has to go through
    the Policy Engine (Phase 15B, unmodified) to learn whether it's
    actually permitted."""
    entry = get_registry_entry(proposal.action_type)
    if entry is None:
        return StructuralValidationResult(False, "action_type_not_registered", None)
    if not entry.enabled:
        return StructuralValidationResult(False, "action_type_disabled", entry)
    if proposal.risk_level != entry.risk_level:
        return StructuralValidationResult(False, "risk_level_mismatch", entry)
    if proposal.automation_level != entry.automation_level:
        return StructuralValidationResult(False, "automation_level_mismatch", entry)

    try:
        target = entry.target_schema.model_validate(proposal.target)
    except ValidationError:
        return StructuralValidationResult(False, "target_invalid", entry)
    if getattr(target, "entity_type", None) != entry.target_entity_type:
        return StructuralValidationResult(False, "target_entity_type_mismatch", entry)

    try:
        entry.parameters_schema.model_validate(proposal.parameters)
    except ValidationError:
        return StructuralValidationResult(False, "parameters_invalid", entry)

    return StructuralValidationResult(True, None, entry)
