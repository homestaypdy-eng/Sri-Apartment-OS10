"""Phase 16C — deterministic signal -> NOTIFY_STAFF proposal generator.

Connects exactly ONE existing, already-deterministic, already-approved
business signal to the existing NOTIFY_STAFF automation pipeline:
maintenance attention-item signals produced by the Phase 13E decision
engine (decision_engine.py, `_signals_from_attention_items`), sourced
from reports/service.py's `build_attention_items()` — specifically the
maintenance branch, which is a plain if/then rule over an
already-approved threshold (`_ATTENTION_THRESHOLD_HOURS`, already live
in the operational dashboard, not invented here) with no guest-PII and
no financial figure anywhere in its output. Every other attention
category (unpaid_balance, guest_request, low_stock) is deliberately left
unconnected in this phase — unpaid_balance/guest_request both embed a
guest name in their title text, which this phase's own instruction to
reject guest PII rules out for a first connection, and low_stock has no
exact category match in NOTIFY_STAFF's existing vocabulary (mapping it
would mean inventing a category-mapping judgment call, which this phase
does not authorize). A future phase may connect additional signals, each
through its own explicit review.

This module creates PROPOSED ActionProposal rows only. It never
evaluates policy (that is policy_engine.evaluate_policy's job, invoked
later, at approval/execution time — see action_executor.py) and never
calls the executor (execute_notify_staff). Proposal generation is not
authorization, per this phase's own explicit instruction.

Isolation: this module imports one read-only value type from
`app.modules.reports.schemas` (AIStrategistSignal) — a one-way
dependency, automation/ -> reports/, the same direction Phase 16A's
severity vocabulary already implied. It never imports decision_engine.py,
narrative_provider.py, narrative_service.py, or anything Claude-related,
and it never calls out to reports/service.py itself — the caller is
responsible for supplying an already-computed AIStrategistSignal; this
module only decides, deterministically, whether that signal is eligible
and if so builds+persists the resulting proposal. reports/ imports
nothing from automation/ (unchanged, verified by test).

No duplicated hashing or authorization logic: proposal_hash and
idempotency_key are computed via policy_engine.compute_proposal_hash/
compute_idempotency_key (Phase 15B, unmodified), and the actual
create-and-validate step is persistence.persist_proposal (Phase 15E-1,
unmodified), which itself calls action_registry.validate_proposal_structure
(Phase 15C, unmodified). Duplicate protection is the same real,
database-level UniqueConstraint("idempotency_key") persist_proposal
already relies on for every other action type — not a second,
bespoke mechanism.
"""
from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import datetime

from pydantic import ValidationError
from sqlalchemy.orm import Session

from app.modules.automation.persistence import persist_proposal
from app.modules.automation.policy_engine import compute_idempotency_key, compute_proposal_hash
from app.modules.automation.schemas import ActionProposal, NotifyStaffParameters, NotifyStaffTarget
from app.modules.reports.schemas import AIStrategistSignal

# The one signal category this phase connects. Both severities the
# existing attention-item logic can ever produce for maintenance
# (`_ATTENTION_SEVERITY_MAP` in decision_engine.py: critical -> CRITICAL,
# warning -> WARNING) are eligible — INFO/WATCH are never emitted for an
# attention item and are not special-cased here.
_ELIGIBLE_CATEGORY = "maintenance"
_ELIGIBLE_SEVERITIES = frozenset({"WARNING", "CRITICAL"})

_ACTION_TYPE = "NOTIFY_STAFF"
_POLICY_ID = "notify_staff"
_RISK_LEVEL = "MEDIUM"
_AUTOMATION_LEVEL = "L2"


@dataclass(frozen=True)
class ProposalGenerationResult:
    created: bool
    reason: str
    action_id: str | None


def _is_eligible(signal: AIStrategistSignal) -> bool:
    return signal.category == _ELIGIBLE_CATEGORY and signal.severity in _ELIGIBLE_SEVERITIES


def generate_notify_staff_proposal(
    db: Session, signal: AIStrategistSignal, actor: str, now: datetime,
) -> ProposalGenerationResult:
    """Deterministic: the same signal always produces the same target,
    the same parameters, the same proposal_hash, and the same
    idempotency_key — so a second call with an identical signal can never
    create a second real proposal, regardless of how many times or how
    concurrently it is called (persist_proposal's UNIQUE constraint on
    idempotency_key is what actually guarantees this, not anything in
    this function)."""
    if not _is_eligible(signal):
        return ProposalGenerationResult(False, "signal_not_eligible", None)

    target = {"entity_type": "staff_notification", "entity_id": signal.signal_id}
    parameters = {
        "message": signal.statement,
        "category": _ELIGIBLE_CATEGORY,
        "severity": signal.severity,
        "source_id": signal.signal_id,
    }

    try:
        NotifyStaffTarget.model_validate(target)
        NotifyStaffParameters.model_validate(parameters)
    except ValidationError:
        # Structurally unreachable given the construction above, but
        # never trust a shape without checking it — same discipline as
        # every other closed-schema boundary in this package.
        return ProposalGenerationResult(False, "invalid_shape", None)

    proposal_hash = compute_proposal_hash(_ACTION_TYPE, target, parameters)
    idempotency_key = compute_idempotency_key(_ACTION_TYPE, target, proposal_hash)

    proposal = ActionProposal(
        action_id=f"action:{uuid.uuid4().hex}",
        action_type=_ACTION_TYPE,
        policy_id=_POLICY_ID,
        risk_level=_RISK_LEVEL,
        automation_level=_AUTOMATION_LEVEL,
        actor=actor,
        target=target,
        parameters=parameters,
        idempotency_key=idempotency_key,
        approval_requirement="none",
        timestamp=now,
        status="PROPOSED",
        proposal_hash=proposal_hash,
    )

    result = persist_proposal(db, proposal)
    if not result.success:
        return ProposalGenerationResult(False, result.reason, None)
    return ProposalGenerationResult(True, "created", proposal.action_id)
