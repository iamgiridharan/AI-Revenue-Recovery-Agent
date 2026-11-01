"""
Human review of escalated cases.

An ESCALATED case is one the Policy Engine refused to authorize on its own: the
amount was above ``AUTONOMOUS_AMOUNT_LIMIT``, the AI confidence or recovery
probability was below the configured minimum, or the agent asked for a human.
Once escalated, ``HUMAN_REVIEW_REQUIRED`` blocks every further action, so this
flow is the only way out.

Decisions:

- ``APPROVE``            execute the recorded AI recommendation on the reviewer's
                         authority.
- ``OVERRIDE``           execute a different executable action, with a written
                         reason.
- ``CLOSE``              settle the case outside the recovery flow.
- ``MARK_UNRECOVERABLE`` close it as uncollectable.

An approval is not a bypass.  The only rules a named reviewer releases are the
*autonomy* thresholds the escalation was raised for; hard stops (a settled case,
an expired case, an exhausted retry / reminder / attempt budget) still refuse the
action, and a refusal is recorded against the reviewer rather than the reviewer
being told the action happened.  Each overridden rule is named in the stored policy
decision and in the HUMAN_REVIEW_COMPLETED audit event.
"""
from typing import Any, Dict, List, Optional

from sqlalchemy.orm import Session

from app.models import (
    AuditEvent,
    AuditEventType,
    CaseStatus,
    RevenueRiskCase,
)
from app.schemas.recovery import EXECUTABLE_ACTIONS
from app.schemas.review import ReviewDecision
from app.services.policy_engine import evaluate_action
from app.services.recovery_service import execute_recovery_action, get_recorded_diagnosis
from app.utils.logging import logger


# Failure codes from the review flow mapped to the HTTP status that describes
# them: a refusal is not a server fault.
_REVIEW_FAILURE_STATUS = {
    "CASE_NOT_FOUND": 404,
    "CASE_NOT_ESCALATED": 409,
    "CASE_ALREADY_RECOVERED": 409,
    "CASE_ALREADY_CLOSED": 409,
    "NO_RECORDED_DIAGNOSIS": 409,
    "UNSUPPORTED_ACTION": 400,
    "OVERRIDE_REQUIRED": 400,
    "POLICY_BLOCKED": 409,
    "POLICY_ESCALATED": 409,
    "CASE_CLOSED": 409,
    "TRANSACTION_NOT_FOUND": 409,
    "EXECUTION_FAILED": 502,
}

# Executable execution result fields that may be shown to the caller.  Provider
# secrets are never part of the execution result, so this is a whitelist.
_EXECUTION_FIELDS = (
    "action_type",
    "amount",
    "currency",
    "payment_link_id",
    "payment_link",
    "provider_status",
    "expires_at",
    "execution_mode",
    "mock",
    "case_reference",
    "message",
)


def _fail(code: str, message: str) -> Dict[str, Any]:
    """A review refusal, with the HTTP status that describes it."""
    return {
        "success": False,
        "code": code,
        "error": message,
        "http_status": _REVIEW_FAILURE_STATUS.get(code, 400),
    }


def _latest_escalation(db: Session, case: RevenueRiskCase) -> Optional[AuditEvent]:
    """Return the most recent escalation event for a case, if one was recorded."""
    return (
        db.query(AuditEvent)
        .filter(
            AuditEvent.case_id == case.id,
            AuditEvent.decision == "ESCALATED",
        )
        .order_by(AuditEvent.timestamp.desc(), AuditEvent.id.desc())
        .first()
    )


def _failing_rule(checks: Optional[List[Any]]) -> Optional[Dict[str, Optional[str]]]:
    """Return the first failing policy check, i.e. why the case was escalated."""
    for check in checks or []:
        if isinstance(check, dict) and check.get("passed") is False:
            return {"rule": check.get("rule"), "reason": check.get("reason")}
    return None


def get_review_queue(db: Session, limit: int = 100) -> Dict[str, Any]:
    """
    List the escalated cases awaiting human review.

    Each entry carries the policy rule that escalated it, taken from the recorded
    escalation event - never re-derived by re-running the engine, which would only
    report ``HUMAN_REVIEW_REQUIRED`` for a case that is already escalated.

    This is read-only: it lists what needs a decision, it makes none.
    """
    cases = (
        db.query(RevenueRiskCase)
        .filter(RevenueRiskCase.status == CaseStatus.ESCALATED.value)
        .order_by(RevenueRiskCase.created_at.asc())
        .limit(limit)
        .all()
    )

    items: List[Dict[str, Any]] = []
    total_amount = 0.0

    for case in cases:
        event = _latest_escalation(db, case)
        rule = _failing_rule(event.policy_checks if event else None)
        diagnosis = get_recorded_diagnosis(db, case)
        total_amount += case.amount or 0.0

        items.append({
            "case_id": case.case_id,
            "amount": case.amount,
            "priority": case.priority,
            "status": case.status,
            "created_at": case.created_at.isoformat() if case.created_at else None,
            "recommended_action": ((diagnosis or {}).get("action_type")
                                   or case.recommended_action),
            "confidence": (diagnosis or {}).get("confidence"),
            "recovery_probability": (diagnosis or {}).get("recovery_probability"),
            "has_recorded_diagnosis": diagnosis is not None,
            "escalation": {
                "reason": event.reason if event else None,
                "action": event.action if event else None,
                "actor": event.actor if event else None,
                "at": event.timestamp.isoformat() if event and event.timestamp else None,
                "rule": rule["rule"] if rule else None,
                "rule_reason": rule["reason"] if rule else None,
            },
        })

    return {
        "total": len(items),
        "total_amount_at_risk": round(total_amount, 2),
        "cases": items,
    }


def _record_review(
    db: Session,
    case: RevenueRiskCase,
    reviewer: str,
    decision: ReviewDecision,
    outcome: str,
    reason: str,
    action: Optional[str] = None,
    decision_response: Any = None,
    extra: Optional[Dict[str, Any]] = None,
) -> None:
    """Record a HUMAN_REVIEW_COMPLETED audit event attributed to the reviewer."""
    overridden = [
        check.rule for check in (decision_response.checks if decision_response else [])
        if not check.passed
    ]
    metadata: Dict[str, Any] = {
        "reviewer": reviewer,
        "review_decision": decision.value,
        "overridden_rules": overridden,
    }
    if extra:
        metadata.update(extra)

    db.add(AuditEvent(
        case_id=case.id,
        event_type=AuditEventType.HUMAN_REVIEW_COMPLETED.value,
        actor=reviewer,
        decision=outcome,
        reason=reason,
        policy_checks=(
            [check.model_dump() for check in decision_response.checks]
            if decision_response else None
        ),
        action=action,
        result=outcome,
        metadata_=metadata,
    ))


def _execution_payload(execution: Dict[str, Any]) -> Dict[str, Any]:
    """The caller-visible part of an execution result."""
    return {field: execution.get(field) for field in _EXECUTION_FIELDS}


def review_case(
    db: Session,
    case_id: str,
    reviewer: str,
    decision: ReviewDecision,
    action_type: Optional[str] = None,
    note: str = "",
) -> Dict[str, Any]:
    """
    Record and act on a human review decision for an escalated case.

    Returns a result whose ``success`` flag is the truth about what happened: an
    approval whose action the Policy Engine still refuses is a refusal, not a
    silent no-op, and the case stays ESCALATED in that case.
    """
    reviewer = (reviewer or "").strip()
    note = (note or "").strip()

    case = (
        db.query(RevenueRiskCase)
        .filter(RevenueRiskCase.case_id == case_id)
        .first()
    )
    if case is None:
        return _fail("CASE_NOT_FOUND", f"Revenue risk case '{case_id}' not found")

    if decision in (ReviewDecision.CLOSE, ReviewDecision.MARK_UNRECOVERABLE):
        return _close_case(db, case, reviewer, decision, note)

    # APPROVE / OVERRIDE execute an action, so they only make sense for a case that
    # is actually waiting for a human decision.
    if case.status != CaseStatus.ESCALATED.value:
        return _fail(
            "CASE_NOT_ESCALATED",
            f"Case {case_id} is {case.status}, not ESCALATED: there is no pending "
            f"human decision to approve or override",
        )

    diagnosis = get_recorded_diagnosis(db, case)
    if diagnosis is None:
        return _fail(
            "NO_RECORDED_DIAGNOSIS",
            f"Case {case_id} has no recorded AI diagnosis, so there is no "
            f"recommendation to approve and no recorded confidence to authorize. "
            f"Run POST /api/agent/diagnose first, or close the case.",
        )

    recorded_action = diagnosis["action_type"]
    action = action_type or recorded_action

    if action not in EXECUTABLE_ACTIONS:
        return _fail(
            "UNSUPPORTED_ACTION",
            f"'{action}' is a decision, not an executable recovery action; choose "
            f"one of: {', '.join(sorted(EXECUTABLE_ACTIONS))}",
        )

    if decision == ReviewDecision.APPROVE and action_type and action_type != recorded_action:
        return _fail(
            "OVERRIDE_REQUIRED",
            f"Action '{action_type}' differs from the recorded recommendation "
            f"'{recorded_action}'; use decision OVERRIDE with a note to authorize a "
            f"different action",
        )

    # Read-only pre-check before the case leaves ESCALATED: a hard policy stop is
    # not overridable by an approval, so it must be refused *before* the gate opens.
    # The authoritative, persisted decision is the one recorded by the execution.
    precheck = evaluate_action(
        db=db,
        case_id=case.case_id,
        proposed_action=action,
        confidence=diagnosis["confidence"],
        recovery_probability=diagnosis["recovery_probability"],
        persist=False,
        authorized_by=reviewer,
    )

    if not precheck.allowed:
        reason = f"Review refused by policy: {precheck.reason}"
        _record_review(
            db, case, reviewer, decision, "REJECTED_BY_POLICY", reason,
            action=action, decision_response=precheck,
        )
        db.commit()
        logger.info(f"Human review {decision.value} by {reviewer} refused for case {case_id}: {precheck.reason}")
        return {
            "success": False,
            "code": "POLICY_BLOCKED",
            "http_status": _REVIEW_FAILURE_STATUS["POLICY_BLOCKED"],
            "error": (
                f"Policy still refuses '{action}' for case {case_id}: "
                f"{precheck.reason}. The case stays ESCALATED. A hard policy stop "
                f"(settled or expired case, exhausted attempt budget) is not "
                f"overridable by review."
            ),
            "case_id": case_id,
            "reviewer": reviewer,
            "decision": decision.value,
            "action_type": action,
            "case_status": case.status,
            "reviewed": True,
            "policy_decision": precheck.model_dump(),
        }

    # The reviewer is the human the HUMAN_REVIEW_REQUIRED gate waits for, so the
    # case leaves ESCALATED now.  The status change and the audit event are
    # committed before execution so the decision survives a provider failure.
    case.status = CaseStatus.IN_PROGRESS.value
    _record_review(
        db, case, reviewer, decision, "APPROVED",
        note or f"{decision.value} by {reviewer}: {action}",
        action=action,
        decision_response=precheck,
        extra={"recorded_action": recorded_action},
    )
    db.commit()

    execution_reason = f"Human review {decision.value} by {reviewer}"
    if note:
        execution_reason += f": {note}"

    execution = execute_recovery_action(
        db=db,
        case_id=case.case_id,
        action_type=action,
        confidence=diagnosis["confidence"],
        recovery_probability=diagnosis["recovery_probability"],
        reason=execution_reason,
        human_authorized_by=reviewer,
    )

    db.refresh(case)

    if not execution.get("success"):
        code = execution.get("code", "EXECUTION_FAILED")
        logger.warning(
            f"Human review {decision.value} by {reviewer}: execution of {action} "
            f"failed for case {case_id} ({code})"
        )
        return {
            "success": False,
            "code": code,
            "http_status": _REVIEW_FAILURE_STATUS.get(code, 409),
            "error": execution.get("error", "Recovery execution failed"),
            "case_id": case_id,
            "reviewer": reviewer,
            "decision": decision.value,
            "action_type": action,
            "case_status": case.status,
            "reviewed": True,
            "policy_decision": execution.get("policy_decision"),
        }

    logger.info(
        f"Human review {decision.value} by {reviewer} executed {action} for case {case_id}"
    )

    return {
        "success": True,
        "data": {
            "case_id": case.case_id,
            "case_status": case.status,
            "reviewer": reviewer,
            "decision": decision.value,
            "note": note,
            "recorded_action": recorded_action,
            "action_type": action,
            "policy_decision": precheck.model_dump(),
            "execution": _execution_payload(execution),
        },
    }


def _close_case(
    db: Session,
    case: RevenueRiskCase,
    reviewer: str,
    decision: ReviewDecision,
    note: str,
) -> Dict[str, Any]:
    """
    Close a case by human decision.

    Closing is allowed for any unsettled case (an escalated one is the normal
    case), but never for a RECOVERED case: settled money must not be relabelled as
    closed, which would silently drop it from the recovered totals.
    """
    if case.status == CaseStatus.RECOVERED.value:
        return _fail(
            "CASE_ALREADY_RECOVERED",
            f"Case {case.case_id} is RECOVERED for "
            f"{case.recovered_amount or 0.0}; a settled case is not closed",
        )
    if case.status == CaseStatus.CLOSED.value:
        return _fail(
            "CASE_ALREADY_CLOSED",
            f"Case {case.case_id} is already CLOSED",
        )

    previous_status = case.status
    case.status = CaseStatus.CLOSED.value

    default_reason = (
        "Marked uncollectable by reviewer" if decision == ReviewDecision.MARK_UNRECOVERABLE
        else "Closed by reviewer outside the recovery flow"
    )
    reason = note or default_reason

    _record_review(
        db, case, reviewer, decision, decision.value, reason,
        extra={"previous_status": previous_status},
    )
    db.commit()

    logger.info(
        f"Human review {decision.value} by {reviewer} closed case {case.case_id} "
        f"(was {previous_status})"
    )

    return {
        "success": True,
        "data": {
            "case_id": case.case_id,
            "case_status": case.status,
            "previous_status": previous_status,
            "reviewer": reviewer,
            "decision": decision.value,
            "note": reason,
            "recovered_amount": case.recovered_amount or 0.0,
        },
    }
