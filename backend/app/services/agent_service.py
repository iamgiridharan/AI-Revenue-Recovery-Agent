"""
AI Agent Service for Revenue Recovery.

This service orchestrates the AI agent workflow:
1. Gather context about the case
2. Produce a diagnosis and recommendation
3. Validate structured output
4. Check policy engine
5. Record audit events
6. Return validated recommendation or escalation

The agent NEVER directly executes payment operations.
It produces structured recommendations that go through
the Policy Engine.

NO LANGUAGE MODEL RUNS IN THIS BUILD.  The diagnosis is produced by a
deterministic, rule-based engine (``call_llm`` below).  Every response, audit
event and UI view therefore reports the diagnosis provenance
(``DIAGNOSIS_SOURCE`` / ``LLM_USED``) instead of presenting rule output as an LLM
answer.
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


# ============================================================================
# DIAGNOSIS PROVENANCE
# ============================================================================
# These constants describe how a diagnosis was produced and are reported by the
# API, recorded in audit events and displayed in the UI.  A real LLM integration
# must replace ``call_llm`` and these constants together: a response must never
# claim an engine that did not run.
DIAGNOSIS_SOURCE = "DETERMINISTIC_RULES"
DIAGNOSIS_ENGINE_VERSION = "deterministic-rules-v1"
LLM_USED = False
#: Confidence is a documented rule score (see ``_rule_confidence``), not a model
#: probability and not a language-model judgement.
CONFIDENCE_SOURCE = "deterministic_rule_score"
#: The recovery probability is the ML model's case-level output stored on the
#: case.  It is never invented by the diagnosis engine.
PROBABILITY_SOURCE = "ml_model_case_level"

# Valid recovery actions for validation
VALID_ACTIONS = {action.value for action in RecoveryAction}

# ============================================================================
# DETERMINISTIC DIAGNOSIS RULES
# ============================================================================
#: Stored failure reason -> (category, human-readable description).  Only these
#: reasons are recognised; anything else is classified as unknown, which
#: escalates instead of guessing a cause.
FAILURE_CAUSES = {
    "insufficient_funds": ("insufficient_funds", "the account had insufficient funds"),
    "daily_limit_exceeded": ("daily_limit_exceeded", "the daily payment limit was exceeded"),
    "card_expired": ("card_expired", "the card on file has expired"),
    "card_blocked": ("card_blocked", "the issuing bank blocked the card"),
    "bank_declined": ("bank_declined", "the issuing bank declined the payment"),
    "invalid_cvv": ("invalid_cvv", "the security code was rejected"),
    "network_timeout": ("network_timeout", "the payment timed out in transit"),
    "authentication_failed": ("authentication_failed", "customer authentication (3DS) failed"),
    "merchant_error": ("merchant_error", "the merchant payment configuration rejected it"),
    "customer_cancelled": ("customer_cancelled", "the customer cancelled the payment"),
}

#: Deterministic action per failure category.  Causes the customer must resolve
#: themselves (expired or blocked card, failed authentication, withdrawn payment)
#: go to a human instead of being retried automatically; an unknown cause always
#: escalates.  SEND_PAYMENT_REMINDER is deliberately never chosen while no
#: delivery channel exists.
ACTION_BY_CAUSE = {
    "insufficient_funds": "CREATE_PAYMENT_LINK",
    "daily_limit_exceeded": "CREATE_PAYMENT_LINK",
    "merchant_error": "CREATE_PAYMENT_LINK",
    "network_timeout": "RETRY",
    "bank_declined": "RETRY",
    "card_expired": "ESCALATE_TO_HUMAN",
    "card_blocked": "ESCALATE_TO_HUMAN",
    "invalid_cvv": "ESCALATE_TO_HUMAN",
    "authentication_failed": "ESCALATE_TO_HUMAN",
    "customer_cancelled": "NO_ACTION",
}

#: Attempt count at which the deterministic engine stops recommending automated
#: retries and hands the case to a human.
MAX_AUTOMATED_ATTEMPTS = 2

#: Category used when the stored failure reason is missing or unrecognised.
UNKNOWN_CAUSE = "unknown"
UNKNOWN_CAUSE_DESCRIPTION = "the stored failure reason is missing or not recognised"


def classify_failure(failure_reason: Optional[str]) -> tuple[str, str]:
    """Map a stored failure reason to ``(category, description)``.

    Unrecognised reasons return ``(UNKNOWN_CAUSE, ...)`` rather than a guessed
    cause.
    """
    key = (failure_reason or "").strip().lower()
    return FAILURE_CAUSES.get(key, (UNKNOWN_CAUSE, UNKNOWN_CAUSE_DESCRIPTION))


def _usable_probability(ml_prediction: Optional[dict]) -> Optional[float]:
    """Return the stored ML recovery probability if it is usable, else None.

    ``None`` means "no usable model output", which escalates the case: it is
    never replaced by a made-up number.
    """
    value = (ml_prediction or {}).get("recovery_probability")
    if value is None or isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    number = float(value)
    if number < 0.0 or number > 1.0:
        return None
    return number


def _rule_confidence(cause_known: bool, history_available: bool) -> float:
    """
    Confidence in the deterministic diagnosis, as a documented rule score.

    This is NOT a model probability and NOT a language-model judgement: it grows
    only with the evidence the rule actually used (a recognised failure cause and
    a recorded customer history), so it is reproducible from the case row.  It is
    capped below 1.0 because a rule table cannot express full certainty.
    """
    score = 0.5 if cause_known else 0.3
    if history_available:
        score += 0.2
    return round(min(score, 0.9), 2)


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


def call_llm(prompt: str, context: Optional[dict] = None) -> Optional[dict]:
    """
    Produce a structured diagnosis for one case.

    NO LANGUAGE MODEL IS CALLED HERE.  This is the deterministic, rule-based
    engine that stands in for an LLM in this build:

    * the failure cause comes from the stored ``failure_reason`` (or is reported
      as unknown, which escalates);
    * the recovery probability is the ML model's case-level output stored on the
      case - it is never invented;
    * the confidence is a documented rule score (``_rule_confidence``);
    * the diagnosis text is composed only from stored case/transaction values.

    It previously returned a fixed diagnosis, ``0.82`` probability and ``0.88``
    confidence for every case (including an "85% success rate" no data produced)
    and ``model_used: mock-llm-v1``, which represented fabricated constants as an
    LLM answer.  Those constants are gone.

    A real LLM integration replaces this function (and the DIAGNOSIS_* /
    LLM_USED / CONFIDENCE_SOURCE constants) and must keep returning the same
    schema so ``validate_llm_output`` and the Policy Engine keep working.

    Args:
        prompt: The rendered prompt (unused by the rule engine; kept so a real
            LLM can consume it).
        context: Case context from ``get_case_context``.  Required: without it
            no diagnosis can be derived, and None is returned rather than a
            fabricated one.

    Returns:
        Parsed JSON-ready dict matching ``AgentRecommendation``, or None when the
        context is missing.
    """
    if not context:
        logger.error(
            "Diagnosis requested without case context; refusing to produce a "
            "diagnosis that is not derived from stored data"
        )
        return None

    case = context.get("case") or {}
    transaction = context.get("transaction") or {}
    customer = context.get("customer") or {}
    ml = context.get("recovery_probability") or {}

    probability = ml.get("recovery_probability")
    if probability is None or isinstance(probability, bool) or not isinstance(
        probability, (int, float)
    ) or not 0.0 <= float(probability) <= 1.0:
        logger.error(
            "No usable ML recovery probability for case %s; refusing to invent one",
            case.get("case_id"),
        )
        return None
    probability = float(probability)

    failure_reason = transaction.get("failure_reason")
    cause, cause_description = classify_failure(failure_reason)
    cause_known = cause != UNKNOWN_CAUSE

    total_transactions = int(customer.get("total_transactions") or 0)
    failed_transactions = int(customer.get("failed_transactions") or 0)
    history_available = total_transactions > 0

    attempt_count = int(case.get("attempt_count") or 0)
    amount = case.get("amount") or 0
    currency = transaction.get("currency") or "INR"
    method = transaction.get("payment_method") or "unknown method"

    # Deterministic recommendation: a recognised cause maps to its documented
    # action, an exhausted retry budget goes to a human, and an unknown cause
    # always escalates.  No reminders are recommended while no delivery channel
    # exists (see recovery_service).
    if not cause_known:
        recommended_action = "ESCALATE_TO_HUMAN"
    elif attempt_count >= MAX_AUTOMATED_ATTEMPTS:
        recommended_action = "ESCALATE_TO_HUMAN"
    else:
        recommended_action = ACTION_BY_CAUSE.get(cause, "ESCALATE_TO_HUMAN")

    confidence = _rule_confidence(cause_known, history_available)

    diagnosis = (
        f"Payment of {currency} {amount} via {method} failed because "
        f"{cause_description} (recorded failure reason: {failure_reason or 'none'}). "
        f"This is a rule-based diagnosis, not a language-model answer."
    )

    reasoning_summary = (
        f"Deterministic diagnosis: the stored failure reason maps to the "
        f"'{cause}' category, which this deployment handles with "
        f"{recommended_action}. The ML model's case-level recovery probability "
        f"({probability:.2f}) and the recorded customer history "
        f"({total_transactions} transactions, {failed_transactions} failed) were "
        f"used as inputs. Confidence ({confidence:.2f}) is a documented rule "
        f"score, not a model output."
    )

    if recommended_action in ("CREATE_PAYMENT_LINK", "RETRY"):
        customer_message = (
            f"Your payment of {currency} {amount} could not be completed because "
            f"{cause_description}. You can securely retry using the payment link "
            f"below."
        )
    else:
        customer_message = (
            f"Your payment of {currency} {amount} could not be completed because "
            f"{cause_description}. Our team is reviewing the case and will "
            f"contact you with the next step."
        )

    logger.info(
        "Deterministic diagnosis produced for case %s (cause=%s, action=%s)",
        case.get("case_id"),
        cause,
        recommended_action,
    )

    return {
        "diagnosis": diagnosis,
        "reasoning_summary": reasoning_summary,
        "recovery_probability": probability,
        "recommended_action": recommended_action,
        "confidence": confidence,
        "customer_message": customer_message,
        "additional_information_required": not cause_known,
    }


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
    
    # Step 1b: Stop after success (or closure).
    # Diagnosing a settled case would burn an LLM call and overwrite the case
    # status/recommendation of a case that must not be acted on again.
    case_status = context["case"]["status"]
    if case_status in (CaseStatus.RECOVERED.value, CaseStatus.CLOSED.value):
        logger.info(f"Case {case_id} is {case_status}; skipping diagnosis (settled case)")
        return {
            "success": False,
            "error": {
                "message": f"Case {case_id} is {case_status}; no further recovery action"
            },
            "case_id": case_id,
            "status": case_status,
            "skipped": True,
        }

    # Step 1c: The recovery probability must be the ML model's stored output.
    # Without a usable value there is no evidence to authorize against, so the
    # case is escalated to a human instead of using an invented probability.
    ml_probability = _usable_probability(context.get("recovery_probability"))
    if ml_probability is None:
        logger.warning(
            f"Case {case_id} has no usable ML recovery probability; escalating "
            f"instead of inventing one"
        )
        record_audit_event(
            db=db,
            case_id=case_id,
            event_type=AuditEventType.DIAGNOSIS_COMPLETED.value,
            actor="ai_agent",
            decision="FALLBACK",
            reason="No usable ML recovery probability recorded; requires human review",
            action="ESCALATE_TO_HUMAN",
        )
        escalate_case(
            db, case_id,
            "No usable ML recovery probability recorded; requires human review",
        )

        processing_time = (time.time() - start_time) * 1000
        return {
            "success": False,
            "code": "RECOVERY_PROBABILITY_UNAVAILABLE",
            "error": {
                "message": (
                    "No usable ML recovery probability is recorded for this case; "
                    "the case was escalated to a human instead of authorizing an "
                    "action on an invented number"
                )
            },
            "case_id": case_id,
            "fallback_action": "ESCALATE_TO_HUMAN",
            "fallback_reason": "RECOVERY_PROBABILITY_UNAVAILABLE",
            "processing_time_ms": processing_time,
        }

    # Step 2: Build the prompt and produce the diagnosis (deterministic in this
    # build; see call_llm). The context is passed so the diagnosis is derived
    # from stored data rather than from constants.
    prompt = build_llm_prompt(context)
    raw_llm_output = call_llm(prompt, context)
    
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
    
    # Step 4: Check policy engine.
    #
    # The recovery probability handed to the Policy Engine is the ML model's
    # stored case-level value (validated above), not whatever number the
    # diagnosis text claims: an engine must not be able to raise the probability
    # that authorizes spending.  Confidence is the diagnosis engine's own
    # validated score, and its source is recorded with the decision.
    logger.info(f"Checking policy for case {case_id}")
    policy_result = evaluate_action(
        db=db,
        case_id=case_id,
        proposed_action=recommendation.recommended_action.value,
        confidence=recommendation.confidence,
        recovery_probability=ml_probability,
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
            # Recorded so downstream consumers (case detail, UI badges) can state
            # what produced this diagnosis and which number the policy decision
            # actually used.
            "diagnosis_source": DIAGNOSIS_SOURCE,
            "diagnosis_engine_version": DIAGNOSIS_ENGINE_VERSION,
            "llm_used": LLM_USED,
            "confidence_source": CONFIDENCE_SOURCE,
            "recovery_probability": ml_probability,
            "recovery_probability_source": PROBABILITY_SOURCE,
            "model_reported_recovery_probability": recommendation.recovery_probability,
            "customer_message": recommendation.customer_message,
            "policy_decision": policy_result.decision,
            "policy_reason": policy_result.reason,
        },
    )
    
    # Step 6: Update case with diagnosis
    case = db.query(RevenueRiskCase).filter(RevenueRiskCase.case_id == case_id).first()
    if case:
        # Do not drag a settled case back into the active workflow.
        if case.status not in (CaseStatus.RECOVERED.value, CaseStatus.CLOSED.value,
                               CaseStatus.ESCALATED.value):
            case.status = CaseStatus.IN_PROGRESS.value
        case.diagnosis = recommendation.diagnosis
        case.recommended_action = recommendation.recommended_action.value
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
        # Provenance of the diagnosis.  ``model_used`` is kept for existing
        # callers but now names the deterministic rule engine, and llm_used=false
        # makes it explicit that no language model ran.
        "model_used": DIAGNOSIS_ENGINE_VERSION,
        "diagnosis_source": DIAGNOSIS_SOURCE,
        "diagnosis_engine_version": DIAGNOSIS_ENGINE_VERSION,
        "llm_used": LLM_USED,
        "confidence_source": CONFIDENCE_SOURCE,
        "recovery_probability_source": PROBABILITY_SOURCE,
        "recovery_probability_used": ml_probability,
        "processing_time_ms": processing_time,
        "policy_decision": policy_result.model_dump(),
    }
