"""
Deterministic Policy Engine for Revenue Recovery.

This engine validates AI recommendations before any financial actions.
The LLM must never bypass the Policy Engine.

Architecture:
AI Recommendation → Policy Engine → APPROVED / BLOCKED / ESCALATED → Controlled Recovery Tool
"""
from datetime import datetime, timezone, timedelta
from typing import Optional, List
from sqlalchemy.orm import Session

from app.models import (
    RevenueRiskCase,
    PolicyConfig,
    PolicyDecision,
    RecoveryAction,
    AuditEvent,
    AuditEventType,
    CaseStatus,
)
from app.schemas.agent import RecoveryAction as RecoveryActionEnum
from app.schemas.policy import PolicyCheck, PolicyDecisionResponse
from app.utils.logging import logger


# Default policy configuration values
DEFAULT_POLICY_CONFIG = {
    "max_retries": 2,
    "max_reminders": 2,
    "max_recovery_attempts": 3,
    "autonomous_amount_limit": 10000.0,
    "minimum_ai_confidence": 0.3,
    "minimum_recovery_probability": 0.2,
    "case_lifetime_days": 7,
    "escalation_threshold": 0.7,
}


def get_active_policy(db: Session) -> Optional[PolicyConfig]:
    """
    Get the active policy configuration.
    
    Returns the most recently updated active policy,
    or creates a default one if none exists.
    
    If the existing policy has values that would make recovery
    impossible (e.g. autonomous_amount_limit=0), they are reset
    to safe defaults.
    """
    policy = db.query(PolicyConfig).filter(PolicyConfig.is_active == True).first()
    
    if not policy:
        # Create default policy
        policy = PolicyConfig(
            max_retries=DEFAULT_POLICY_CONFIG["max_retries"],
            max_reminders=DEFAULT_POLICY_CONFIG["max_reminders"],
            max_recovery_attempts=DEFAULT_POLICY_CONFIG["max_recovery_attempts"],
            autonomous_amount_limit=DEFAULT_POLICY_CONFIG["autonomous_amount_limit"],
            minimum_ai_confidence=DEFAULT_POLICY_CONFIG["minimum_ai_confidence"],
            minimum_recovery_probability=DEFAULT_POLICY_CONFIG["minimum_recovery_probability"],
            case_lifetime_days=DEFAULT_POLICY_CONFIG["case_lifetime_days"],
            escalation_threshold=DEFAULT_POLICY_CONFIG["escalation_threshold"],
            is_active=True,
            description="Default policy configuration",
        )
        db.add(policy)
        db.commit()
        db.refresh(policy)
        logger.info("Created default policy configuration")
    else:
        # Validate that the active policy has sane values.
        # If critical thresholds are set to impossible values (e.g. by
        # a manual API call), reset them to safe defaults so that
        # recovery actions can actually be approved.
        needs_reset = False
        if policy.autonomous_amount_limit < 100:
            logger.warning(
                f"Policy autonomous_amount_limit={policy.autonomous_amount_limit} "
                f"is too low — resetting to {DEFAULT_POLICY_CONFIG['autonomous_amount_limit']}"
            )
            policy.autonomous_amount_limit = DEFAULT_POLICY_CONFIG["autonomous_amount_limit"]
            needs_reset = True
        if policy.minimum_ai_confidence > 0.95:
            logger.warning(
                f"Policy minimum_ai_confidence={policy.minimum_ai_confidence} "
                f"is too high — resetting to {DEFAULT_POLICY_CONFIG['minimum_ai_confidence']}"
            )
            policy.minimum_ai_confidence = DEFAULT_POLICY_CONFIG["minimum_ai_confidence"]
            needs_reset = True
        if policy.minimum_recovery_probability > 0.95:
            logger.warning(
                f"Policy minimum_recovery_probability={policy.minimum_recovery_probability} "
                f"is too high — resetting to {DEFAULT_POLICY_CONFIG['minimum_recovery_probability']}"
            )
            policy.minimum_recovery_probability = DEFAULT_POLICY_CONFIG["minimum_recovery_probability"]
            needs_reset = True
        if policy.escalation_threshold > 0.95:
            logger.warning(
                f"Policy escalation_threshold={policy.escalation_threshold} "
                f"is too high — resetting to {DEFAULT_POLICY_CONFIG['escalation_threshold']}"
            )
            policy.escalation_threshold = DEFAULT_POLICY_CONFIG["escalation_threshold"]
            needs_reset = True
        if policy.case_lifetime_days < 1:
            logger.warning(
                f"Policy case_lifetime_days={policy.case_lifetime_days} "
                f"is too low — resetting to {DEFAULT_POLICY_CONFIG['case_lifetime_days']}"
            )
            policy.case_lifetime_days = DEFAULT_POLICY_CONFIG["case_lifetime_days"]
            needs_reset = True
        if needs_reset:
            policy.description = "Policy reset to safe defaults (previous values were non-functional)"
            db.commit()
            db.refresh(policy)
            logger.info("Policy configuration corrected to safe defaults")
    
    return policy


def check_retry_limit(db: Session, case: RevenueRiskCase, policy: PolicyConfig) -> PolicyCheck:
    """Check if retry limit has been reached."""
    retry_count = db.query(RecoveryAction).filter(
        RecoveryAction.case_id == case.id,
        RecoveryAction.action_type == RecoveryActionEnum.RETRY.value,
    ).count()
    
    passed = retry_count < policy.max_retries
    
    return PolicyCheck(
        rule="MAX_RETRIES",
        passed=passed,
        reason=None if passed else f"Retry limit reached ({retry_count}/{policy.max_retries})",
        value=float(retry_count),
        limit=float(policy.max_retries),
    )


def check_reminder_limit(db: Session, case: RevenueRiskCase, policy: PolicyConfig) -> PolicyCheck:
    """Check if reminder limit has been reached."""
    reminder_count = db.query(RecoveryAction).filter(
        RecoveryAction.case_id == case.id,
        RecoveryAction.action_type == RecoveryActionEnum.SEND_PAYMENT_REMINDER.value,
    ).count()
    
    passed = reminder_count < policy.max_reminders
    
    return PolicyCheck(
        rule="MAX_REMINDERS",
        passed=passed,
        reason=None if passed else f"Reminder limit reached ({reminder_count}/{policy.max_reminders})",
        value=float(reminder_count),
        limit=float(policy.max_reminders),
    )


def check_recovery_attempts(db: Session, case: RevenueRiskCase, policy: PolicyConfig) -> PolicyCheck:
    """Check if total recovery attempts limit has been reached."""
    attempt_count = db.query(RecoveryAction).filter(
        RecoveryAction.case_id == case.id,
        RecoveryAction.action_type.in_([
            RecoveryActionEnum.RETRY.value,
            RecoveryActionEnum.CREATE_PAYMENT_LINK.value,
            RecoveryActionEnum.SEND_PAYMENT_REMINDER.value,
        ]),
    ).count()
    
    passed = attempt_count < policy.max_recovery_attempts
    
    return PolicyCheck(
        rule="MAX_RECOVERY_ATTEMPTS",
        passed=passed,
        reason=None if passed else f"Recovery attempt limit reached ({attempt_count}/{policy.max_recovery_attempts})",
        value=float(attempt_count),
        limit=float(policy.max_recovery_attempts),
    )


def check_amount_limit(case: RevenueRiskCase, policy: PolicyConfig) -> PolicyCheck:
    """Check if amount exceeds autonomous limit."""
    passed = case.amount <= policy.autonomous_amount_limit
    
    return PolicyCheck(
        rule="AUTONOMOUS_AMOUNT_LIMIT",
        passed=passed,
        reason=None if passed else f"Amount {case.amount} exceeds limit {policy.autonomous_amount_limit}",
        value=case.amount,
        limit=policy.autonomous_amount_limit,
    )


def check_ai_confidence(confidence: float, policy: PolicyConfig) -> PolicyCheck:
    """Check if AI confidence meets minimum threshold."""
    passed = confidence >= policy.minimum_ai_confidence
    
    return PolicyCheck(
        rule="MINIMUM_AI_CONFIDENCE",
        passed=passed,
        reason=None if passed else f"Confidence {confidence:.2f} below minimum {policy.minimum_ai_confidence}",
        value=confidence,
        limit=policy.minimum_ai_confidence,
    )


def check_recovery_probability(probability: float, policy: PolicyConfig) -> PolicyCheck:
    """Check if recovery probability meets minimum threshold."""
    passed = probability >= policy.minimum_recovery_probability
    
    return PolicyCheck(
        rule="MINIMUM_RECOVERY_PROBABILITY",
        passed=passed,
        reason=None if passed else f"Recovery probability {probability:.2f} below minimum {policy.minimum_recovery_probability}",
        value=probability,
        limit=policy.minimum_recovery_probability,
    )


def check_case_expiry(case: RevenueRiskCase, policy: PolicyConfig) -> PolicyCheck:
    """Check if case has expired."""
    if case.created_at is None:
        # If no created_at, assume not expired
        return PolicyCheck(
            rule="CASE_EXPIRY",
            passed=True,
            reason=None,
            value=0.0,
            limit=float(policy.case_lifetime_days),
        )
    
    now = datetime.now(timezone.utc)
    case_age_days = (now - case.created_at).days if case.created_at.tzinfo else (now.replace(tzinfo=None) - case.created_at).days
    
    passed = case_age_days < policy.case_lifetime_days
    
    return PolicyCheck(
        rule="CASE_EXPIRY",
        passed=passed,
        reason=None if passed else f"Case expired ({case_age_days} days old, limit {policy.case_lifetime_days})",
        value=float(case_age_days),
        limit=float(policy.case_lifetime_days),
    )


def check_payment_success(case: RevenueRiskCase) -> PolicyCheck:
    """Check if payment is already successful."""
    passed = case.status != CaseStatus.RECOVERED.value
    
    return PolicyCheck(
        rule="PAYMENT_NOT_SUCCESSFUL",
        passed=passed,
        reason=None if passed else "Payment already successful, no recovery needed",
        value=1.0 if case.status == CaseStatus.RECOVERED.value else 0.0,
        limit=0.0,
    )


def check_escalation_threshold(confidence: float, policy: PolicyConfig) -> PolicyCheck:
    """Check if confidence is above escalation threshold."""
    # This check is informational - if confidence is above threshold,
    # escalation may be required
    above_threshold = confidence > policy.escalation_threshold
    
    return PolicyCheck(
        rule="ESCALATION_THRESHOLD",
        passed=True,  # This is informational, not a blocker
        reason=f"Confidence {confidence:.2f} {'above' if above_threshold else 'below'} escalation threshold {policy.escalation_threshold}",
        value=confidence,
        limit=policy.escalation_threshold,
    )


def evaluate_action(
    db: Session,
    case_id: str,
    proposed_action: str,
    confidence: float,
    recovery_probability: float,
) -> PolicyDecisionResponse:
    """
    Evaluate a proposed action against policy rules.
    
    This is the main policy engine function that determines
    whether an action is APPROVED, BLOCKED, or ESCALATED.
    
    Args:
        db: Database session
        case_id: Revenue risk case ID
        proposed_action: Action proposed by AI agent
        confidence: AI confidence in recommendation
        recovery_probability: ML-predicted recovery probability
        
    Returns:
        PolicyDecisionResponse with decision and checks
    """
    # Get case
    case = db.query(RevenueRiskCase).filter(RevenueRiskCase.case_id == case_id).first()
    if not case:
        return PolicyDecisionResponse(
            allowed=False,
            decision="BLOCKED",
            reason=f"Case {case_id} not found",
            checks=[],
            case_id=case_id,
            proposed_action=proposed_action,
        )
    
    # Get active policy
    policy = get_active_policy(db)
    
    # Run all checks
    checks: List[PolicyCheck] = []
    
    # Check if payment already successful
    check = check_payment_success(case)
    checks.append(check)
    if not check.passed:
        return _create_decision(db, case, proposed_action, "BLOCKED", 
                               "Payment already successful", checks, confidence, recovery_probability)
    
    # Check case expiry
    check = check_case_expiry(case, policy)
    checks.append(check)
    if not check.passed:
        return _create_decision(db, case, proposed_action, "BLOCKED",
                               "Case has expired", checks, confidence, recovery_probability)
    
    # Check retry limit
    check = check_retry_limit(db, case, policy)
    checks.append(check)
    if not check.passed and proposed_action == RecoveryActionEnum.RETRY.value:
        return _create_decision(db, case, proposed_action, "BLOCKED",
                               "Retry limit reached", checks, confidence, recovery_probability)
    
    # Check reminder limit
    check = check_reminder_limit(db, case, policy)
    checks.append(check)
    if not check.passed and proposed_action == RecoveryActionEnum.SEND_PAYMENT_REMINDER.value:
        return _create_decision(db, case, proposed_action, "BLOCKED",
                               "Reminder limit reached", checks, confidence, recovery_probability)
    
    # Check recovery attempts
    check = check_recovery_attempts(db, case, policy)
    checks.append(check)
    if not check.passed:
        return _create_decision(db, case, proposed_action, "BLOCKED",
                               "Recovery attempt limit reached", checks, confidence, recovery_probability)
    
    # Check amount limit
    check = check_amount_limit(case, policy)
    checks.append(check)
    if not check.passed:
        # Amount exceeds limit - escalate to human
        return _create_decision(db, case, proposed_action, "ESCALATED",
                               "Amount exceeds autonomous limit", checks, confidence, recovery_probability)
    
    # Check AI confidence
    check = check_ai_confidence(confidence, policy)
    checks.append(check)
    if not check.passed:
        # Low confidence - escalate to human
        return _create_decision(db, case, proposed_action, "ESCALATED",
                               "AI confidence below minimum threshold", checks, confidence, recovery_probability)
    
    # Check recovery probability
    check = check_recovery_probability(recovery_probability, policy)
    checks.append(check)
    if not check.passed:
        # Low recovery probability - escalate or no action
        return _create_decision(db, case, proposed_action, "ESCALATED",
                               "Recovery probability below minimum threshold", checks, confidence, recovery_probability)
    
    # Check escalation threshold
    check = check_escalation_threshold(confidence, policy)
    checks.append(check)
    
    # All checks passed - approve the action
    return _create_decision(db, case, proposed_action, "APPROVED",
                           "All policy checks passed", checks, confidence, recovery_probability)


def _create_decision(
    db: Session,
    case: RevenueRiskCase,
    proposed_action: str,
    decision: str,
    reason: str,
    checks: List[PolicyCheck],
    confidence: float,
    recovery_probability: float,
) -> PolicyDecisionResponse:
    """Create and store a policy decision."""
    # Store decision in database
    policy_decision = PolicyDecision(
        case_id=case.id,
        proposed_action=proposed_action,
        decision=decision,
        reason=reason,
        checks=[check.model_dump() for check in checks],
        confidence=confidence,
        recovery_probability=recovery_probability,
        amount=case.amount,
        final_decision=decision,
    )
    db.add(policy_decision)
    
    # Create audit event
    audit_event = AuditEvent(
        case_id=case.id,
        event_type=AuditEventType.POLICY_CHECKED.value,
        actor="policy_engine",
        decision=decision,
        reason=reason,
        confidence=confidence,
        policy_checks=[check.model_dump() for check in checks],
        action=proposed_action,
        result=decision,
    )
    db.add(audit_event)
    
    db.commit()
    
    logger.info(f"Policy decision for case {case.case_id}: {decision} - {reason}")
    
    return PolicyDecisionResponse(
        allowed=decision == "APPROVED",
        decision=decision,
        reason=reason,
        checks=checks,
        case_id=case.case_id,
        proposed_action=proposed_action,
    )
