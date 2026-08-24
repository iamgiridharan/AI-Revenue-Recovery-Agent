"""
Controlled Backend Tools for AI Agent.

These tools are the ONLY way the agent interacts with the system.
The agent calls these tools through structured recommendations,
NOT through arbitrary API calls.

Security: The agent never directly executes payment operations.
It recommends actions that go through the Policy Engine (next phase).
"""
from sqlalchemy.orm import Session
from datetime import datetime, timezone
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
    
    NOTE: This is a MOCK implementation for Phase 4.
    In Phase 5, this will connect to Razorpay Test Mode.
    
    The agent recommends this action, but execution goes through
    the Policy Engine (next phase) before actual API call.
    """
    logger.info(f"MOCK: Creating payment link for case {case_id}, amount {amount} {currency}")
    
    # Mock response - in production this would call Razorpay
    mock_link = f"https://rzp.io/test/pay/{case_id[:8]}"
    
    return {
        "success": True,
        "payment_link": mock_link,
        "amount": amount,
        "currency": currency,
        "expires_in_hours": 24,
        "reason": reason,
        "mock": True,
    }


def send_payment_reminder(
    db: Session,
    case_id: str,
    customer_email: str,
    message: str = "",
) -> dict:
    """
    Send payment reminder to customer.
    
    NOTE: This is a MOCK implementation for Phase 4.
    In Phase 5, this will connect to email/SMS service.
    
    The agent recommends this action, but execution goes through
    the Policy Engine (next phase) before actual API call.
    """
    logger.info(f"MOCK: Sending payment reminder for case {case_id} to {customer_email}")
    
    # Mock response
    return {
        "success": True,
        "message": "Payment reminder sent successfully",
        "recipient": customer_email,
        "mock": True,
    }


def retry_payment(
    db: Session,
    case_id: str,
    payment_method: str = "card",
) -> dict:
    """
    Retry the failed payment.
    
    NOTE: This is a MOCK implementation for Phase 4.
    In Phase 5, this will connect to Razorpay Test Mode.
    
    The agent recommends this action, but execution goes through
    the Policy Engine (next phase) before actual API call.
    """
    logger.info(f"MOCK: Retrying payment for case {case_id} with method {payment_method}")
    
    # Mock response
    return {
        "success": True,
        "message": "Payment retry initiated",
        "payment_method": payment_method,
        "mock": True,
    }


def check_recovery_status(db: Session, case_id: str) -> dict:
    """
    Check the status of a recovery attempt.
    
    NOTE: This is a MOCK implementation for Phase 4.
    In Phase 5, this will check actual payment status.
    """
    logger.info(f"MOCK: Checking recovery status for case {case_id}")
    
    # Mock response
    return {
        "case_id": case_id,
        "status": "pending",
        "last_checked": datetime.now(timezone.utc).isoformat(),
        "mock": True,
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
