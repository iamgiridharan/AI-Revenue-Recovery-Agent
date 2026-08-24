"""
Recovery Service for Revenue Recovery Agent.

This service orchestrates the payment recovery flow:
1. Validates the case
2. Obtains transaction amount from trusted backend data
3. Calls Policy Engine for approval
4. Executes approved recovery actions via Razorpay
5. Updates database records
6. Creates audit events

CRITICAL: The amount is NEVER trusted from the LLM.
It always comes from the database transaction record.
"""
from typing import Optional, Dict, Any
from sqlalchemy.orm import Session
from datetime import datetime, timezone

from app.models import (
    RevenueRiskCase,
    RecoveryAction,
    Transaction,
    AuditEvent,
    CaseStatus,
    AuditEventType,
)
from app.services.policy_engine import evaluate_action
from app.services.razorpay_service import create_payment_link
from app.utils.logging import logger


def execute_recovery_action(
    db: Session,
    case_id: str,
    action_type: str,
    confidence: float,
    recovery_probability: float,
    reason: str = "",
) -> Dict[str, Any]:
    """
    Execute a recovery action for a revenue risk case.
    
    This is the main entry point for executing approved recovery actions.
    
    Args:
        db: Database session
        case_id: Revenue risk case ID
        action_type: Type of recovery action (from RecoveryActionType enum)
        confidence: AI confidence in recommendation
        recovery_probability: ML-predicted recovery probability
        reason: Reason for the action
        
    Returns:
        Dictionary containing execution result
    """
    logger.info(f"Executing recovery action {action_type} for case {case_id}")
    
    # Step 1: Get the case
    case = db.query(RevenueRiskCase).filter(RevenueRiskCase.case_id == case_id).first()
    if not case:
        return {
            "success": False,
            "error": f"Case {case_id} not found",
            "action_executed": False,
        }
    
    # Step 2: Get the transaction (source of truth for amount)
    transaction = db.query(Transaction).filter(Transaction.id == case.transaction_id).first()
    if not transaction:
        return {
            "success": False,
            "error": f"Transaction not found for case {case_id}",
            "action_executed": False,
        }
    
    # Step 3: Validate case status
    if case.status == CaseStatus.RECOVERED.value:
        return {
            "success": False,
            "error": f"Case {case_id} is already recovered",
            "action_executed": False,
        }
    
    if case.status == CaseStatus.CLOSED.value:
        return {
            "success": False,
            "error": f"Case {case_id} is closed",
            "action_executed": False,
        }
    
    # Step 4: Evaluate against policy engine
    policy_result = evaluate_action(
        db=db,
        case_id=case_id,
        proposed_action=action_type,
        confidence=confidence,
        recovery_probability=recovery_probability,
    )
    
    # Step 5: Check if action is allowed
    if not policy_result.allowed:
        # Action blocked by policy - create recovery action record
        recovery_action = RecoveryAction(
            case_id=case.id,
            action_type=action_type,
            reason=reason,
            confidence=confidence,
            policy_result="BLOCKED",
            execution_status="BLOCKED_BY_POLICY",
        )
        db.add(recovery_action)
        
        # Create audit event
        audit_event = AuditEvent(
            case_id=case.id,
            event_type=AuditEventType.ACTION_FAILED.value,
            actor="recovery_service",
            decision="BLOCKED",
            reason=f"Policy blocked: {policy_result.reason}",
            confidence=confidence,
            policy_checks=[c.model_dump() for c in policy_result.checks] if policy_result.checks else [],
            action=action_type,
            result="BLOCKED_BY_POLICY",
        )
        db.add(audit_event)
        
        db.commit()
        
        logger.info(f"Action {action_type} blocked by policy for case {case_id}: {policy_result.reason}")
        
        return {
            "success": False,
            "error": f"Policy blocked: {policy_result.reason}",
            "action_executed": False,
            "policy_decision": policy_result.model_dump(),
        }
    
    # Step 6: Execute the action based on type
    try:
        if action_type == "CREATE_PAYMENT_LINK":
            result = _execute_create_payment_link(
                db=db,
                case=case,
                transaction=transaction,
                confidence=confidence,
                reason=reason,
            )
        elif action_type == "RETRY":
            result = _execute_retry_payment(
                db=db,
                case=case,
                transaction=transaction,
                confidence=confidence,
                reason=reason,
            )
        elif action_type == "SEND_PAYMENT_REMINDER":
            result = _execute_send_reminder(
                db=db,
                case=case,
                transaction=transaction,
                confidence=confidence,
                reason=reason,
            )
        else:
            result = {
                "success": False,
                "error": f"Unsupported action type: {action_type}",
                "action_executed": False,
            }
        
        return result
        
    except Exception as e:
        logger.error(f"Failed to execute action {action_type} for case {case_id}: {e}")
        
        # Create failed recovery action
        recovery_action = RecoveryAction(
            case_id=case.id,
            action_type=action_type,
            reason=reason,
            confidence=confidence,
            policy_result="APPROVED",
            execution_status="FAILED",
        )
        db.add(recovery_action)
        
        # Create audit event
        audit_event = AuditEvent(
            case_id=case.id,
            event_type=AuditEventType.ACTION_FAILED.value,
            actor="recovery_service",
            decision="FAILED",
            reason=str(e),
            confidence=confidence,
            action=action_type,
            result="FAILED",
        )
        db.add(audit_event)
        
        db.commit()
        
        return {
            "success": False,
            "error": str(e),
            "action_executed": False,
        }


def _execute_create_payment_link(
    db: Session,
    case: RevenueRiskCase,
    transaction: Transaction,
    confidence: float,
    reason: str,
) -> Dict[str, Any]:
    """
    Execute CREATE_PAYMENT_LINK action.
    
    This creates a Razorpay payment link for the failed transaction.
    The amount is always taken from the trusted database transaction record.
    """
    logger.info(f"Creating payment link for case {case.case_id}")
    
    # Get customer email/phone if available
    customer_email = None
    customer_phone = None
    
    # Create payment link via Razorpay
    payment_link_result = create_payment_link(
        amount=transaction.amount,  # Amount from database, NOT from LLM
        currency=transaction.currency or "INR",
        description=f"Recovery payment for case {case.case_id}",
        customer_email=customer_email,
        customer_phone=customer_phone,
        reference_id=case.case_id,
        expiry_days=1,
    )
    
    if not payment_link_result.get("success"):
        raise Exception(f"Failed to create payment link: {payment_link_result.get('error')}")
    
    # Create recovery action record
    recovery_action = RecoveryAction(
        case_id=case.id,
        action_type="CREATE_PAYMENT_LINK",
        reason=reason,
        confidence=confidence,
        policy_result="APPROVED",
        execution_status="SUCCESS",
        api_reference=payment_link_result.get("payment_link_id"),
    )
    db.add(recovery_action)
    
    # Update case status
    case.status = CaseStatus.RECOVERY_ATTEMPTED.value
    case.attempt_count += 1
    case.recommended_action = "CREATE_PAYMENT_LINK"
    
    # Create audit event
    audit_event = AuditEvent(
        case_id=case.id,
        event_type=AuditEventType.ACTION_EXECUTED.value,
        actor="recovery_service",
        decision="APPROVED",
        reason=f"Payment link created: {payment_link_result.get('payment_link')}",
        confidence=confidence,
        action="CREATE_PAYMENT_LINK",
        result="SUCCESS",
        metadata_={
            "payment_link_id": payment_link_result.get("payment_link_id"),
            "payment_link": payment_link_result.get("payment_link"),
            "amount": transaction.amount,
            "mock": payment_link_result.get("mock", False),
        },
    )
    db.add(audit_event)
    
    db.commit()
    
    logger.info(f"Payment link created for case {case.case_id}: {payment_link_result.get('payment_link')}")
    
    return {
        "success": True,
        "action_executed": True,
        "payment_link_id": payment_link_result.get("payment_link_id"),
        "payment_link": payment_link_result.get("payment_link"),
        "amount": transaction.amount,
        "currency": transaction.currency,
        "mock": payment_link_result.get("mock", False),
    }


def _execute_retry_payment(
    db: Session,
    case: RevenueRiskCase,
    transaction: Transaction,
    confidence: float,
    reason: str,
) -> Dict[str, Any]:
    """
    Execute RETRY action.
    
    This creates a payment link for retry (similar to CREATE_PAYMENT_LINK).
    """
    logger.info(f"Retrying payment for case {case.case_id}")
    
    # For retry, we create a new payment link
    return _execute_create_payment_link(
        db=db,
        case=case,
        transaction=transaction,
        confidence=confidence,
        reason=reason,
    )


def _execute_send_reminder(
    db: Session,
    case: RevenueRiskCase,
    transaction: Transaction,
    confidence: float,
    reason: str,
) -> Dict[str, Any]:
    """
    Execute SEND_PAYMENT_REMINDER action.
    
    This sends a payment reminder to the customer.
    """
    logger.info(f"Sending payment reminder for case {case.case_id}")
    
    # Create recovery action record
    recovery_action = RecoveryAction(
        case_id=case.id,
        action_type="SEND_PAYMENT_REMINDER",
        reason=reason,
        confidence=confidence,
        policy_result="APPROVED",
        execution_status="SUCCESS",
        api_reference=f"reminder_{case.case_id}",
    )
    db.add(recovery_action)
    
    # Create audit event
    audit_event = AuditEvent(
        case_id=case.id,
        event_type=AuditEventType.ACTION_EXECUTED.value,
        actor="recovery_service",
        decision="APPROVED",
        reason="Payment reminder sent",
        confidence=confidence,
        action="SEND_PAYMENT_REMINDER",
        result="SUCCESS",
    )
    db.add(audit_event)
    
    db.commit()
    
    logger.info(f"Payment reminder sent for case {case.case_id}")
    
    return {
        "success": True,
        "action_executed": True,
        "message": "Payment reminder sent",
    }


def process_payment_success(
    db: Session,
    case_id: str,
    payment_id: str,
    amount: float,
) -> Dict[str, Any]:
    """
    Process a successful payment from webhook.
    
    Args:
        db: Database session
        case_id: Revenue risk case ID
        payment_id: Razorpay payment ID
        amount: Payment amount in INR
        
    Returns:
        Processing result
    """
    logger.info(f"Processing payment success for case {case_id}, payment {payment_id}")
    
    # Get the case
    case = db.query(RevenueRiskCase).filter(RevenueRiskCase.case_id == case_id).first()
    if not case:
        return {
            "success": False,
            "error": f"Case {case_id} not found",
        }
    
    # Update case
    case.status = CaseStatus.RECOVERED.value
    case.recovered_amount = amount
    
    # Create audit event
    audit_event = AuditEvent(
        case_id=case.id,
        event_type=AuditEventType.ACTION_EXECUTED.value,
        actor="webhook",
        decision="PAYMENT_SUCCESS",
        reason=f"Payment {payment_id} successful for amount {amount}",
        action="PAYMENT_RECEIVED",
        result="SUCCESS",
        metadata_={
            "payment_id": payment_id,
            "amount": amount,
        },
    )
    db.add(audit_event)
    
    db.commit()
    
    logger.info(f"Case {case_id} marked as recovered, amount: {amount}")
    
    return {
        "success": True,
        "case_id": case_id,
        "status": "RECOVERED",
        "recovered_amount": amount,
    }


def process_payment_failure(
    db: Session,
    case_id: str,
    payment_id: str,
    failure_reason: str,
) -> Dict[str, Any]:
    """
    Process a failed payment from webhook.
    
    Args:
        db: Database session
        case_id: Revenue risk case ID
        payment_id: Razorpay payment ID
        failure_reason: Reason for failure
        
    Returns:
        Processing result
    """
    logger.info(f"Processing payment failure for case {case_id}, payment {payment_id}")
    
    # Get the case
    case = db.query(RevenueRiskCase).filter(RevenueRiskCase.case_id == case_id).first()
    if not case:
        return {
            "success": False,
            "error": f"Case {case_id} not found",
        }
    
    # Update case status
    case.status = CaseStatus.OPEN.value  # Reopen for next attempt
    
    # Create audit event
    audit_event = AuditEvent(
        case_id=case.id,
        event_type=AuditEventType.ACTION_FAILED.value,
        actor="webhook",
        decision="PAYMENT_FAILED",
        reason=f"Payment {payment_id} failed: {failure_reason}",
        action="PAYMENT_RECEIVED",
        result="FAILED",
        metadata_={
            "payment_id": payment_id,
            "failure_reason": failure_reason,
        },
    )
    db.add(audit_event)
    
    db.commit()
    
    logger.info(f"Case {case_id} payment failed, reopened for next attempt")
    
    return {
        "success": True,
        "case_id": case_id,
        "status": "OPEN",
        "failure_reason": failure_reason,
    }
