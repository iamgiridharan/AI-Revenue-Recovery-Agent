"""
AI Agent Service for Revenue Recovery.

This service orchestrates the AI agent workflow:
1. Gather context about the case
2. Call LLM for diagnosis and recommendation
3. Validate structured output
4. Check policy engine
5. Record audit events
6. Return validated recommendation or escalation

The agent NEVER directly executes payment operations.
It produces structured recommendations that go through
the Policy Engine.
"""
import json
import time
from typing import Optional
from sqlalchemy.orm import Session

from app.models import RevenueRiskCase, CaseStatus, AuditEventType
from app.schemas.agent import (
    AgentRecommendation,
    RecoveryAction,
)
from app.services.agent_tools import (
    get_transaction,
    get_customer_history,
    get_payment_status,
    get_recovery_probability,
    record_audit_event,
    escalate_case,
)
from app.services.policy_engine import evaluate_action
from app.utils.logging import logger


# Valid recovery actions for validation
VALID_ACTIONS = {action.value for action in RecoveryAction}


def get_case_context(db: Session, case_id: str) -> Optional[dict]:
    """
    Gather all context about a case for the LLM.
    
    This includes:
    - Case details
    - Transaction details
    - Customer history
    - ML predictions
    """
    case = db.query(RevenueRiskCase).filter(RevenueRiskCase.case_id == case_id).first()
    if not case:
        return None
    
    transaction = get_transaction(db, case.transaction_id)
    customer = get_customer_history(db, case.customer_id)
    recovery_prob = get_recovery_probability(db, case_id)
    
    return {
        "case": {
            "case_id": case.case_id,
            "amount": case.amount,
            "status": case.status,
            "priority": case.priority,
            "attempt_count": case.attempt_count,
            "recovered_amount": case.recovered_amount,
            "created_at": case.created_at.isoformat() if case.created_at else None,
        },
        "transaction": transaction,
        "customer": customer,
        "recovery_probability": recovery_prob,
    }


def build_llm_prompt(context: dict) -> str:
    """
    Build a prompt for the LLM based on case context.
    
    This creates a structured prompt that guides the LLM
    to produce valid structured output.
    """
    case = context.get("case", {})
    transaction = context.get("transaction", {})
    customer = context.get("customer", {})
    recovery_prob = context.get("recovery_probability", {})
    
    prompt = f"""You are an AI Revenue Recovery Agent. Analyze the following failed payment case and provide a diagnosis and recovery recommendation.

## Case Details
- Case ID: {case.get('case_id', 'Unknown')}
- Amount: {case.get('amount', 0)} INR
- Status: {case.get('status', 'Unknown')}
- Priority: {case.get('priority', 'Unknown')}
- Attempt Count: {case.get('attempt_count', 0)}

## Transaction Details
- Transaction ID: {transaction.get('transaction_id', 'Unknown')}
- Payment Method: {transaction.get('payment_method', 'Unknown')}
- Failure Reason: {transaction.get('failure_reason', 'Unknown')}
- Amount: {transaction.get('amount', 0)} {transaction.get('currency', 'INR')}

## Customer Information
- Name: {customer.get('name', 'Unknown')}
- Total Transactions: {customer.get('total_transactions', 0)}
- Successful Transactions: {customer.get('successful_transactions', 0)}
- Failed Transactions: {customer.get('failed_transactions', 0)}
- Lifetime Value: {customer.get('lifetime_value', 0)} INR

## ML Predictions
- Recovery Probability: {recovery_prob.get('recovery_probability', 'Unknown')}
- Risk Score: {recovery_prob.get('risk_score', 'Unknown')}

## Instructions
Based on this information, provide your analysis in the following JSON format:
{{
    "diagnosis": "Your diagnosis of the payment failure (10-500 chars)",
    "reasoning_summary": "Summary of your reasoning (20-1000 chars)",
    "recovery_probability": 0.0 to 1.0,
    "recommended_action": "One of: NO_ACTION, RETRY, CREATE_PAYMENT_LINK, SEND_PAYMENT_REMINDER, WAIT_AND_RETRY, ESCALATE_TO_HUMAN, MARK_UNRECOVERABLE",
    "confidence": 0.0 to 1.0,
    "customer_message": "Message to show the customer (10-500 chars)",
    "additional_information_required": true or false
}}

Provide ONLY the JSON response, no other text."""
    
    return prompt


def call_llm(prompt: str) -> Optional[dict]:
    """
    Call the LLM API for diagnosis and recommendation.
    
    This is a MOCK implementation for Phase 4.
    In production, this would call the actual LLM API.
    
    Returns:
        Parsed JSON response from LLM, or None on failure
    """
    logger.info("Calling LLM for case diagnosis...")
    
    # Mock LLM response for Phase 4
    # In production, this would call OpenAI/Anthropic/etc.
    mock_response = {
        "diagnosis": "Temporary payment failure due to insufficient funds. Customer has strong payment history with 85% success rate.",
        "reasoning_summary": "The customer has a good payment history with mostly successful transactions. The failure reason indicates a temporary issue (insufficient funds) which is often recoverable. The customer's lifetime value suggests they are worth investing recovery effort in.",
        "recovery_probability": 0.82,
        "recommended_action": "CREATE_PAYMENT_LINK",
        "confidence": 0.88,
        "customer_message": "Your payment could not be completed due to insufficient funds. You can securely retry using the payment link below.",
        "additional_information_required": False,
    }
    
    logger.info("LLM call completed (mock)")
    return mock_response


def validate_llm_output(raw_output: dict) -> Optional[AgentRecommendation]:
    """
    Validate LLM output against the structured schema.
    
    This ensures:
    1. All required fields are present
    2. Fields have correct types and ranges
    3. Action is from the controlled list
    4. Confidence is within valid range
    
    Returns:
        Validated AgentRecommendation, or None if invalid
    """
    try:
        # Validate using Pydantic schema
        recommendation = AgentRecommendation(**raw_output)
        
        # Additional validation
        if recommendation.recommended_action.value not in VALID_ACTIONS:
            logger.warning(f"Invalid action: {recommendation.recommended_action}")
            return None
        
        if recommendation.confidence < 0.3:
            logger.warning(f"Low confidence: {recommendation.confidence}")
            # Still valid, but flagged
        
        return recommendation
        
    except Exception as e:
        logger.error(f"LLM output validation failed: {e}")
        return None


def diagnose_case(db: Session, case_id: str) -> dict:
    """
    Main agent workflow: diagnose a revenue risk case.
    
    This orchestrates:
    1. Gather context
    2. Call LLM
    3. Validate output
    4. Record audit events
    5. Return recommendation
    
    Returns:
        Dict with recommendation or error
    """
    start_time = time.time()
    
    # Step 1: Gather context
    logger.info(f"Gathering context for case {case_id}")
    context = get_case_context(db, case_id)
    if not context:
        return {
            "success": False,
            "error": {"message": f"Case {case_id} not found"},
            "case_id": case_id,
        }
    
    # Step 2: Build prompt and call LLM
    prompt = build_llm_prompt(context)
    raw_llm_output = call_llm(prompt)
    
    if not raw_llm_output:
        # LLM unavailable - safe fallback
        logger.warning("LLM unavailable, using safe fallback")
        record_audit_event(
            db=db,
            case_id=case_id,
            event_type=AuditEventType.DIAGNOSIS_COMPLETED.value,
            actor="ai_agent",
            decision="FALLBACK",
            reason="LLM unavailable, using safe fallback",
            action="ESCALATE_TO_HUMAN",
        )
        escalate_case(db, case_id, "LLM unavailable, requires human review")
        
        processing_time = (time.time() - start_time) * 1000
        return {
            "success": False,
            "error": {"message": "LLM unavailable, case escalated to human"},
            "case_id": case_id,
            "fallback_action": "ESCALATE_TO_HUMAN",
            "processing_time_ms": processing_time,
        }
    
    # Step 3: Validate LLM output
    recommendation = validate_llm_output(raw_llm_output)
    
    if not recommendation:
        # Invalid output - safe fallback
        logger.warning("Invalid LLM output, using safe fallback")
        record_audit_event(
            db=db,
            case_id=case_id,
            event_type=AuditEventType.DIAGNOSIS_COMPLETED.value,
            actor="ai_agent",
            decision="FALLBACK",
            reason="Invalid LLM output, using safe fallback",
            action="ESCALATE_TO_HUMAN",
        )
        escalate_case(db, case_id, "Invalid AI output, requires human review")
        
        processing_time = (time.time() - start_time) * 1000
        return {
            "success": False,
            "error": {"message": "Invalid AI output, case escalated to human"},
            "case_id": case_id,
            "fallback_action": "ESCALATE_TO_HUMAN",
            "processing_time_ms": processing_time,
        }
    
    # Step 4: Check policy engine
    logger.info(f"Checking policy for case {case_id}")
    policy_result = evaluate_action(
        db=db,
        case_id=case_id,
        proposed_action=recommendation.recommended_action.value,
        confidence=recommendation.confidence,
        recovery_probability=recommendation.recovery_probability,
    )
    
    # Step 5: Record audit event
    record_audit_event(
        db=db,
        case_id=case_id,
        event_type=AuditEventType.DIAGNOSIS_COMPLETED.value,
        actor="ai_agent",
        decision=recommendation.recommended_action.value,
        reason=recommendation.reasoning_summary,
        confidence=recommendation.confidence,
        action=recommendation.recommended_action.value,
        metadata={
            "diagnosis": recommendation.diagnosis,
            "recovery_probability": recommendation.recovery_probability,
            "customer_message": recommendation.customer_message,
            "policy_decision": policy_result.decision,
            "policy_reason": policy_result.reason,
        },
    )
    
    # Step 6: Update case with diagnosis
    case = db.query(RevenueRiskCase).filter(RevenueRiskCase.case_id == case_id).first()
    if case:
        case.diagnosis = recommendation.diagnosis
        case.recommended_action = recommendation.recommended_action.value
        case.status = CaseStatus.IN_PROGRESS.value
        db.commit()
    
    processing_time = (time.time() - start_time) * 1000
    
    logger.info(
        f"Case {case_id} diagnosed: {recommendation.recommended_action.value} "
        f"(confidence: {recommendation.confidence:.2f}) "
        f"Policy: {policy_result.decision}"
    )
    
    return {
        "success": True,
        "data": recommendation.model_dump(),
        "case_id": case_id,
        "model_used": "mock-llm-v1",
        "processing_time_ms": processing_time,
        "policy_decision": policy_result.model_dump(),
    }
