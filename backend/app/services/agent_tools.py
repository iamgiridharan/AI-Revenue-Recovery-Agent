"""
Controlled Backend Tools for AI Agent.

These tools are the ONLY way the agent interacts with the system.
The agent calls these tools through structured recommendations,
NOT through arbitrary API calls.

Security: The agent never directly executes payment operations.
It recommends actions that go through the Policy Engine (next phase).
"""
from sqlalchemy.orm import Session
from typing import Optional

from app.models import (
    RevenueRiskCase,
    Transaction,
    Customer,
    RecoveryAction,
    AuditEvent,
    CaseStatus,
    AuditEventType,
)
from app.services.action_availability import action_availability
from app.utils.logging import logger


# ============================================================================
# READ-ONLY TOOLS (safe for agent to call)
# ============================================================================


def get_transaction(db: Session, transaction_id: int) -> Optional[dict]:
    """
    Get transaction details by ID.
    
    This is a read-only tool that provides transaction context
    to the agent for diagnosis.
    """
    transaction = db.query(Transaction).filter(Transaction.id == transaction_id).first()
    if not transaction:
        return None
    
    return {
        "id": transaction.id,
        "transaction_id": transaction.transaction_id,
        "amount": transaction.amount,
        "currency": transaction.currency,
        "payment_method": transaction.payment_method,
        "status": transaction.status,
        "failure_reason": transaction.failure_reason,
        "attempt_count": transaction.attempt_count,
        "created_at": transaction.created_at.isoformat() if transaction.created_at else None,
    }


def get_customer_history(db: Session, customer_id: int) -> Optional[dict]:
    """
    Get customer history and statistics.
    
    This is a read-only tool that provides customer context
    to the agent for diagnosis.
    """
    customer = db.query(Customer).filter(Customer.id == customer_id).first()
    if not customer:
        return None
    
    # Get recent transactions
    recent_transactions = (
        db.query(Transaction)
        .filter(Transaction.customer_id == customer_id)
        .order_by(Transaction.created_at.desc())
        .limit(10)
        .all()
    )
    
    return {
        "id": customer.id,
        "customer_id": customer.customer_id,
        "name": customer.name,
        "email": customer.email,
        "total_transactions": customer.total_transactions,
        "successful_transactions": customer.successful_transactions,
        "failed_transactions": customer.failed_transactions,
        "lifetime_value": customer.lifetime_value,
        "last_payment_date": customer.last_payment_date.isoformat() if customer.last_payment_date else None,
        "recent_transactions": [
            {
                "transaction_id": t.transaction_id,
                "amount": t.amount,
                "status": t.status,
                "failure_reason": t.failure_reason,
                "created_at": t.created_at.isoformat() if t.created_at else None,
            }
            for t in recent_transactions
        ],
    }


def get_payment_status(db: Session, case_id: str) -> Optional[dict]:
    """
    Get current payment status for a case.
    
    This is a read-only tool that provides payment status
    to the agent for diagnosis.
    """
    case = db.query(RevenueRiskCase).filter(RevenueRiskCase.case_id == case_id).first()
    if not case:
        return None
    
    return {
        "case_id": case.case_id,
        "amount": case.amount,
        "status": case.status,
        "attempt_count": case.attempt_count,
        "recovered_amount": case.recovered_amount,
        "created_at": case.created_at.isoformat() if case.created_at else None,
        "updated_at": case.updated_at.isoformat() if case.updated_at else None,
    }


def get_recovery_probability(db: Session, case_id: str) -> Optional[dict]:
    """
    Get ML-derived recovery probability for a case.
    
    This is a read-only tool that provides ML predictions
    to the agent for diagnosis.
    """
    case = db.query(RevenueRiskCase).filter(RevenueRiskCase.case_id == case_id).first()
    if not case:
        return None
    
    return {
        "case_id": case.case_id,
        "recovery_probability": case.recovery_probability,
        "risk_score": case.risk_score,
        "priority": case.priority,
    }


# ============================================================================
# WRITE TOOLS (agent recommends, not executes directly)
# ============================================================================


def create_payment_link(
    db: Session,
    case_id: str,
    amount: float,
    currency: str = "INR",
    reason: str = "",
) -> dict:
    """
    Create a payment link for retry.

    NOTE: this agent-side tool REFUSES and returns no link of any kind.  Payment
    links are only created by the approved path --
    ``app.services.recovery_service.execute_recovery_action`` -- after the Policy
    Engine authorizes the recorded recommendation.

    It previously returned a ``mock.invalid`` link.  That was misleading: the value
    looked like a payment link although no case had been read, no policy decision
    taken, no audit event written and no provider contacted, so a caller (or the
    agent itself) could read a fabricated string as recovery activity.  Removing
    that output keeps the tool callable while making the refusal explicit.
    """
    logger.warning(
        "Refusing agent-tool create_payment_link for case %s: payment links are "
        "created only by the policy-authorized recovery execution path",
        case_id,
    )

    return {
        "success": False,
        "code": "POLICY_AUTHORIZATION_REQUIRED",
        "error": (
            "Agent tools cannot create payment links. The Policy Engine must "
            "authorize the recorded recommendation and execution must go through "
            "POST /api/cases/{case_id}/recovery."
        ),
        "case_id": case_id,
        "amount": amount,
        "currency": currency,
        "reason": reason,
        "execution_mode": "NOT_EXECUTED",
        "action_executed": False,
        "provider_called": False,
        "mock": False,
    }


def send_payment_reminder(
    db: Session,
    case_id: str,
    customer_email: str,
    message: str = "",
) -> dict:
    """
    Agent-side reminder tool: REFUSES and reports that nothing was delivered.

    There is no email/SMS delivery channel in this build, so no reminder can be
    sent.  It previously returned ``success: True`` with "Payment reminder sent
    successfully", which recorded an undelivered message as a sent one.  The
    policy-authorized execution path
    (``recovery_service.execute_recovery_action``) records the same attempt as
    ``NOT_SENT`` (or ``SIMULATED`` for simulation data); this tool reports the
    refusal without touching a case.

    Returns:
        A dict with ``delivered: False`` and ``delivery_status: NOT_SENT``.  It
        never claims that a message was sent.
    """
    logger.warning(
        "Refusing agent-tool send_payment_reminder for case %s: no delivery "
        "channel is configured",
        case_id,
    )

    availability = action_availability("SEND_PAYMENT_REMINDER")

    return {
        "success": False,
        "code": "ACTION_UNAVAILABLE",
        "error": (
            "No payment reminder was sent: this deployment has no email/SMS "
            "delivery channel, so SEND_PAYMENT_REMINDER is unavailable "
            f"({availability['availability']}). The Policy Engine refuses to "
            "authorize it, so nothing is recorded and no reminder budget is spent."
        ),
        "availability": availability,
        "case_id": case_id,
        "recipient": customer_email,
        "requested_message": message,
        "delivery_status": "NOT_SENT",
        "delivered": False,
        "provider_called": False,
        "execution_mode": "NOT_EXECUTED",
        "action_executed": False,
        "mock": False,
    }


def retry_payment(
    db: Session,
    case_id: str,
    payment_method: str = "card",
) -> dict:
    """
    Agent-side retry tool: REFUSES and reports that no retry was executed.

    A retry means creating a new payment link, which is only allowed through
    ``execute_recovery_action`` after the Policy Engine authorizes the recorded
    recommendation.  It previously returned ``success: True`` with "Payment
    retry initiated" although nothing was initiated.

    Returns:
        A dict with ``action_executed: False`` and ``provider_called: False``.
    """
    logger.warning(
        "Refusing agent-tool retry_payment for case %s: retries execute only "
        "through the policy-authorized recovery path",
        case_id,
    )

    return {
        "success": False,
        "code": "POLICY_AUTHORIZATION_REQUIRED",
        "error": (
            "Agent tools cannot retry a payment. The Policy Engine must "
            "authorize the recorded recommendation and execution must go through "
            "POST /api/cases/{case_id}/recovery."
        ),
        "case_id": case_id,
        "payment_method": payment_method,
        "execution_mode": "NOT_EXECUTED",
        "action_executed": False,
        "provider_called": False,
        "mock": False,
    }


def check_recovery_status(db: Session, case_id: str) -> dict:
    """
    Read the recorded recovery status of a case from the database.

    This is a read-only lookup, not a simulated response: it previously returned
    a fabricated ``status: "pending"`` (a value the case model does not even
    have) with ``mock: True``.  A missing case is reported as not found instead
    of inventing a status.
    """
    case = db.query(RevenueRiskCase).filter(RevenueRiskCase.case_id == case_id).first()
    if not case:
        return {
            "success": False,
            "code": "CASE_NOT_FOUND",
            "error": f"Case {case_id} not found",
            "case_id": case_id,
            "mock": False,
        }

    return {
        "success": True,
        "case_id": case.case_id,
        "status": case.status,
        "attempt_count": case.attempt_count,
        "recovered_amount": case.recovered_amount,
        "last_updated": case.updated_at.isoformat() if case.updated_at else None,
        "source": "database",
        "mock": False,
    }


# ============================================================================
# SYSTEM TOOLS (agent uses for case management)
# ============================================================================


def escalate_case(
    db: Session,
    case_id: str,
    reason: str,
    actor: str = "ai_agent",
) -> dict:
    """
    Escalate a case to human review.
    
    This is a safe operation that updates case status
    and creates an audit event.
    """
    case = db.query(RevenueRiskCase).filter(RevenueRiskCase.case_id == case_id).first()
    if not case:
        return {"success": False, "error": f"Case {case_id} not found"}

    # Terminal states are final: a recovered case has settled money that must not
    # be relabelled as escalated, and a closed case has already been resolved by a
    # human.  Escalating either would reopen a finished decision.
    if case.status in (CaseStatus.RECOVERED.value, CaseStatus.CLOSED.value):
        logger.warning(
            f"Refusing to escalate terminal case {case_id} (status {case.status})"
        )
        return {
            "success": False,
            "case_id": case_id,
            "status": case.status,
            "error": (
                f"Case {case_id} is {case.status}; a terminal case cannot be escalated"
            ),
        }

    # Update case status
    case.status = CaseStatus.ESCALATED.value
    db.commit()
    
    # Create audit event
    audit_event = AuditEvent(
        case_id=case.id,
        event_type=AuditEventType.CASE_ESCALATED.value,
        actor=actor,
        decision="ESCALATE_TO_HUMAN",
        reason=reason,
        result="ESCALATED",
    )
    db.add(audit_event)
    db.commit()
    
    logger.info(f"Case {case_id} escalated to human: {reason}")
    
    return {
        "success": True,
        "case_id": case_id,
        "status": "ESCALATED",
        "reason": reason,
    }


def record_audit_event(
    db: Session,
    case_id: str,
    event_type: str,
    actor: str,
    decision: str = "",
    reason: str = "",
    confidence: float = None,
    action: str = "",
    result: str = "",
    metadata: dict = None,
) -> dict:
    """
    Record an audit event for a case.
    
    This is a safe operation that creates an audit trail
    for all agent actions.
    """
    case = db.query(RevenueRiskCase).filter(RevenueRiskCase.case_id == case_id).first()
    if not case:
        return {"success": False, "error": f"Case {case_id} not found"}
    
    audit_event = AuditEvent(
        case_id=case.id,
        event_type=event_type,
        actor=actor,
        decision=decision,
        reason=reason,
        confidence=confidence,
        action=action,
        result=result,
        metadata_=metadata,
    )
    db.add(audit_event)
    db.commit()
    
    logger.info(f"Audit event recorded for case {case_id}: {event_type} by {actor}")
    
    return {
        "success": True,
        "event_type": event_type,
        "actor": actor,
    }
