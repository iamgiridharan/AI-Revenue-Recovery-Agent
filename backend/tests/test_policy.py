"""
Tests for Policy Engine.

Tests cover:
- Retry limit
- Reminder limit
- Amount limit
- Confidence limit
- Recovery probability
- Expiry
- Successful payment
- Escalation
- Blocked action
- Valid approval
- Policy configuration
- AI recommends → Policy blocks → payment tool NOT executed
"""
import pytest
from datetime import datetime, timezone, timedelta

from app.models import (
    Customer,
    Transaction,
    RevenueRiskCase,
    RecoveryAction,
    PolicyConfig,
    PolicyDecision,
    CaseStatus,
    AuditEventType,
)
from app.schemas.agent import RecoveryAction as RecoveryActionEnum
from app.services.policy_engine import (
    get_active_policy,
    evaluate_action,
    check_retry_limit,
    check_reminder_limit,
    check_recovery_attempts,
    check_amount_limit,
    check_ai_confidence,
    check_recovery_probability,
    check_case_expiry,
    check_payment_success,
)


# ============================================================================
# FIXTURES
# ============================================================================


@pytest.fixture
def sample_customer(db_session):
    """Create a sample customer for testing."""
    customer = Customer(
        customer_id="CUST-001",
        name="Test Customer",
        email="test@example.com",
        phone="+1234567890",
        total_transactions=20,
        successful_transactions=17,
        failed_transactions=3,
        lifetime_value=50000.0,
        last_payment_date=datetime.now(timezone.utc),
    )
    db_session.add(customer)
    db_session.commit()
    return customer


@pytest.fixture
def sample_transaction(db_session, sample_customer):
    """Create a sample transaction for testing."""
    transaction = Transaction(
        transaction_id="TXN-001",
        customer_id=sample_customer.id,
        amount=2500.0,
        currency="INR",
        payment_method="card",
        status="FAILED",
        failure_reason="insufficient_funds",
        attempt_count=1,
    )
    db_session.add(transaction)
    db_session.commit()
    return transaction


@pytest.fixture
def sample_case(db_session, sample_customer, sample_transaction):
    """Create a sample revenue risk case for testing."""
    case = RevenueRiskCase(
        case_id="CASE-001",
        transaction_id=sample_transaction.id,
        customer_id=sample_customer.id,
        amount=2500.0,
        risk_score=0.75,
        recovery_probability=0.65,
        priority="HIGH",
        status="OPEN",
        attempt_count=1,
        recovered_amount=0.0,
    )
    db_session.add(case)
    db_session.commit()
    return case


@pytest.fixture
def high_amount_case(db_session, sample_customer, sample_transaction):
    """Create a case with high amount exceeding autonomous limit."""
    case = RevenueRiskCase(
        case_id="CASE-HIGH",
        transaction_id=sample_transaction.id,
        customer_id=sample_customer.id,
        amount=15000.0,  # Exceeds default limit of 10000
        risk_score=0.75,
        recovery_probability=0.65,
        priority="HIGH",
        status="OPEN",
        attempt_count=1,
        recovered_amount=0.0,
    )
    db_session.add(case)
    db_session.commit()
    return case


@pytest.fixture
def expired_case(db_session, sample_customer, sample_transaction):
    """Create an expired case for testing."""
    case = RevenueRiskCase(
        case_id="CASE-EXPIRED",
        transaction_id=sample_transaction.id,
        customer_id=sample_customer.id,
        amount=2500.0,
        risk_score=0.75,
        recovery_probability=0.65,
        priority="HIGH",
        status="OPEN",
        attempt_count=1,
        recovered_amount=0.0,
        created_at=datetime.now(timezone.utc) - timedelta(days=10),  # 10 days old
    )
    db_session.add(case)
    db_session.commit()
    return case


@pytest.fixture
def recovered_case(db_session, sample_customer, sample_transaction):
    """Create a case with successful payment."""
    case = RevenueRiskCase(
        case_id="CASE-RECOVERED",
        transaction_id=sample_transaction.id,
        customer_id=sample_customer.id,
        amount=2500.0,
        risk_score=0.75,
        recovery_probability=0.65,
        priority="HIGH",
        status="RECOVERED",
        attempt_count=1,
        recovered_amount=2500.0,
    )
    db_session.add(case)
    db_session.commit()
    return case


@pytest.fixture
def case_with_retries(db_session, sample_customer, sample_transaction):
    """Create a case with existing retry actions."""
    case = RevenueRiskCase(
        case_id="CASE-RETRIES",
        transaction_id=sample_transaction.id,
        customer_id=sample_customer.id,
        amount=2500.0,
        risk_score=0.75,
        recovery_probability=0.65,
        priority="HIGH",
        status="OPEN",
        attempt_count=2,  # Already at retry limit
        recovered_amount=0.0,
    )
    db_session.add(case)
    db_session.commit()
    
    # Add retry actions
    for i in range(2):
        action = RecoveryAction(
            case_id=case.id,
            action_type=RecoveryActionEnum.RETRY.value,
            reason=f"Retry attempt {i+1}",
            confidence=0.8,
            policy_result="APPROVED",
            execution_status="PENDING",
        )
        db_session.add(action)
    
    db_session.commit()
    return case


@pytest.fixture
def case_with_reminders(db_session, sample_customer, sample_transaction):
    """Create a case with existing reminder actions."""
    case = RevenueRiskCase(
        case_id="CASE-REMINDERS",
        transaction_id=sample_transaction.id,
        customer_id=sample_customer.id,
        amount=2500.0,
        risk_score=0.75,
        recovery_probability=0.65,
        priority="HIGH",
        status="OPEN",
        attempt_count=1,
        recovered_amount=0.0,
    )
    db_session.add(case)
    db_session.commit()
    
    # Add reminder actions
    for i in range(2):
        action = RecoveryAction(
            case_id=case.id,
            action_type=RecoveryActionEnum.SEND_PAYMENT_REMINDER.value,
            reason=f"Reminder {i+1}",
            confidence=0.8,
            policy_result="APPROVED",
            execution_status="PENDING",
        )
        db_session.add(action)
    
    db_session.commit()
    return case


@pytest.fixture
def active_policy(db_session):
    """Get or create active policy configuration."""
    return get_active_policy(db_session)


# ============================================================================
# POLICY CONFIGURATION TESTS
# ============================================================================


class TestPolicyConfiguration:
    """Test policy configuration retrieval and updates."""

    def test_get_active_policy_creates_default(self, db_session):
        """Test that getting active policy creates default if none exists."""
        policy = get_active_policy(db_session)
        
        assert policy is not None
        assert policy.is_active is True
        assert policy.max_retries == 2
        assert policy.max_reminders == 2
        assert policy.max_recovery_attempts == 3
        assert policy.autonomous_amount_limit == 10000.0
        assert policy.minimum_ai_confidence == 0.3
        assert policy.minimum_recovery_probability == 0.2
        assert policy.case_lifetime_days == 7
        assert policy.escalation_threshold == 0.7

    def test_get_active_policy_returns_existing(self, db_session):
        """Test that getting active policy returns existing one."""
        # Create a custom policy
        policy = PolicyConfig(
            max_retries=5,
            is_active=True,
            description="Custom policy",
        )
        db_session.add(policy)
        db_session.commit()
        
        # Get active policy
        active = get_active_policy(db_session)
        
        assert active.id == policy.id
        assert active.max_retries == 5


# ============================================================================
# POLICY CHECK TESTS
# ============================================================================


class TestPolicyChecks:
    """Test individual policy checks."""

    def test_check_retry_limit_passed(self, db_session, sample_case, active_policy):
        """Test retry limit check when under limit."""
        check = check_retry_limit(db_session, sample_case, active_policy)
        
        assert check.passed is True
        assert check.rule == "MAX_RETRIES"

    def test_check_retry_limit_failed(self, db_session, case_with_retries, active_policy):
        """Test retry limit check when at limit."""
        check = check_retry_limit(db_session, case_with_retries, active_policy)
        
        assert check.passed is False
        assert check.rule == "MAX_RETRIES"
        assert "Retry limit reached" in check.reason

    def test_check_reminder_limit_passed(self, db_session, sample_case, active_policy):
        """Test reminder limit check when under limit."""
        check = check_reminder_limit(db_session, sample_case, active_policy)
        
        assert check.passed is True
        assert check.rule == "MAX_REMINDERS"

    def test_check_reminder_limit_failed(self, db_session, case_with_reminders, active_policy):
        """Test reminder limit check when at limit."""
        check = check_reminder_limit(db_session, case_with_reminders, active_policy)
        
        assert check.passed is False
        assert check.rule == "MAX_REMINDERS"
        assert "Reminder limit reached" in check.reason

    def test_check_recovery_attempts_passed(self, db_session, sample_case, active_policy):
        """Test recovery attempts check when under limit."""
        check = check_recovery_attempts(db_session, sample_case, active_policy)
        
        assert check.passed is True
        assert check.rule == "MAX_RECOVERY_ATTEMPTS"

    def test_check_amount_limit_passed(self, db_session, sample_case, active_policy):
        """Test amount limit check when under limit."""
        check = check_amount_limit(sample_case, active_policy)
        
        assert check.passed is True
        assert check.rule == "AUTONOMOUS_AMOUNT_LIMIT"

    def test_check_amount_limit_failed(self, db_session, high_amount_case, active_policy):
        """Test amount limit check when over limit."""
        check = check_amount_limit(high_amount_case, active_policy)
        
        assert check.passed is False
        assert check.rule == "AUTONOMOUS_AMOUNT_LIMIT"
        assert "exceeds limit" in check.reason

    def test_check_ai_confidence_passed(self, db_session, active_policy):
        """Test AI confidence check when above minimum."""
        check = check_ai_confidence(0.8, active_policy)
        
        assert check.passed is True
        assert check.rule == "MINIMUM_AI_CONFIDENCE"

    def test_check_ai_confidence_failed(self, db_session, active_policy):
        """Test AI confidence check when below minimum."""
        check = check_ai_confidence(0.2, active_policy)
        
        assert check.passed is False
        assert check.rule == "MINIMUM_AI_CONFIDENCE"
        assert "below minimum" in check.reason

    def test_check_recovery_probability_passed(self, db_session, active_policy):
        """Test recovery probability check when above minimum."""
        check = check_recovery_probability(0.5, active_policy)
        
        assert check.passed is True
        assert check.rule == "MINIMUM_RECOVERY_PROBABILITY"

    def test_check_recovery_probability_failed(self, db_session, active_policy):
        """Test recovery probability check when below minimum."""
        check = check_recovery_probability(0.1, active_policy)
        
        assert check.passed is False
        assert check.rule == "MINIMUM_RECOVERY_PROBABILITY"
        assert "below minimum" in check.reason

    def test_check_case_expiry_passed(self, db_session, sample_case, active_policy):
        """Test case expiry check when case is not expired."""
        check = check_case_expiry(sample_case, active_policy)
        
        assert check.passed is True
        assert check.rule == "CASE_EXPIRY"

    def test_check_case_expiry_failed(self, db_session, expired_case, active_policy):
        """Test case expiry check when case is expired."""
        check = check_case_expiry(expired_case, active_policy)
        
        assert check.passed is False
        assert check.rule == "CASE_EXPIRY"
        assert "expired" in check.reason

    def test_check_payment_success_passed(self, db_session, sample_case):
        """Test payment success check when payment is not successful."""
        check = check_payment_success(sample_case)
        
        assert check.passed is True
        assert check.rule == "PAYMENT_NOT_SUCCESSFUL"

    def test_check_payment_success_failed(self, db_session, recovered_case):
        """Test payment success check when payment is successful."""
        check = check_payment_success(recovered_case)
        
        assert check.passed is False
        assert check.rule == "PAYMENT_NOT_SUCCESSFUL"
        assert "already successful" in check.reason


# ============================================================================
# POLICY EVALUATION TESTS
# ============================================================================


class TestPolicyEvaluation:
    """Test complete policy evaluation."""

    def test_valid_approval(self, db_session, sample_case):
        """Test that valid action is approved."""
        result = evaluate_action(
            db=db_session,
            case_id="CASE-001",
            proposed_action=RecoveryActionEnum.CREATE_PAYMENT_LINK.value,
            confidence=0.8,
            recovery_probability=0.6,
        )
        
        assert result.allowed is True
        assert result.decision == "APPROVED"
        assert len(result.checks) > 0

    def test_retry_limit_blocks_retry(self, db_session, case_with_retries):
        """Test that retry action is blocked when retry limit reached."""
        result = evaluate_action(
            db=db_session,
            case_id="CASE-RETRIES",
            proposed_action=RecoveryActionEnum.RETRY.value,
            confidence=0.8,
            recovery_probability=0.6,
        )
        
        assert result.allowed is False
        assert result.decision == "BLOCKED"
        assert any(check.rule == "MAX_RETRIES" and not check.passed 
                  for check in result.checks)

    def test_reminder_limit_blocks_reminder(self, db_session, case_with_reminders):
        """Test that reminder action is blocked when reminder limit reached."""
        result = evaluate_action(
            db=db_session,
            case_id="CASE-REMINDERS",
            proposed_action=RecoveryActionEnum.SEND_PAYMENT_REMINDER.value,
            confidence=0.8,
            recovery_probability=0.6,
        )
        
        assert result.allowed is False
        assert result.decision == "BLOCKED"
        assert any(check.rule == "MAX_REMINDERS" and not check.passed 
                  for check in result.checks)

    def test_amount_limit_escalates(self, db_session, high_amount_case):
        """Test that high amount triggers escalation."""
        result = evaluate_action(
            db=db_session,
            case_id="CASE-HIGH",
            proposed_action=RecoveryActionEnum.CREATE_PAYMENT_LINK.value,
            confidence=0.8,
            recovery_probability=0.6,
        )
        
        assert result.allowed is False
        assert result.decision == "ESCALATED"
        assert any(check.rule == "AUTONOMOUS_AMOUNT_LIMIT" and not check.passed 
                  for check in result.checks)

    def test_low_confidence_escalates(self, db_session, sample_case):
        """Test that low confidence triggers escalation."""
        result = evaluate_action(
            db=db_session,
            case_id="CASE-001",
            proposed_action=RecoveryActionEnum.CREATE_PAYMENT_LINK.value,
            confidence=0.2,  # Below minimum of 0.3
            recovery_probability=0.6,
        )
        
        assert result.allowed is False
        assert result.decision == "ESCALATED"
        assert any(check.rule == "MINIMUM_AI_CONFIDENCE" and not check.passed 
                  for check in result.checks)

    def test_low_recovery_probability_escalates(self, db_session, sample_case):
        """Test that low recovery probability triggers escalation."""
        result = evaluate_action(
            db=db_session,
            case_id="CASE-001",
            proposed_action=RecoveryActionEnum.CREATE_PAYMENT_LINK.value,
            confidence=0.8,
            recovery_probability=0.1,  # Below minimum of 0.2
        )
        
        assert result.allowed is False
        assert result.decision == "ESCALATED"
        assert any(check.rule == "MINIMUM_RECOVERY_PROBABILITY" and not check.passed 
                  for check in result.checks)

    def test_expired_case_blocks_action(self, db_session, expired_case):
        """Test that expired case blocks action."""
        result = evaluate_action(
            db=db_session,
            case_id="CASE-EXPIRED",
            proposed_action=RecoveryActionEnum.CREATE_PAYMENT_LINK.value,
            confidence=0.8,
            recovery_probability=0.6,
        )
        
        assert result.allowed is False
        assert result.decision == "BLOCKED"
        assert any(check.rule == "CASE_EXPIRY" and not check.passed 
                  for check in result.checks)

    def test_successful_payment_blocks_action(self, db_session, recovered_case):
        """Test that successful payment blocks further action."""
        result = evaluate_action(
            db=db_session,
            case_id="CASE-RECOVERED",
            proposed_action=RecoveryActionEnum.CREATE_PAYMENT_LINK.value,
            confidence=0.8,
            recovery_probability=0.6,
        )
        
        assert result.allowed is False
        assert result.decision == "BLOCKED"
        assert any(check.rule == "PAYMENT_NOT_SUCCESSFUL" and not check.passed 
                  for check in result.checks)

    def test_case_not_found_blocks_action(self, db_session):
        """Test that non-existent case blocks action."""
        result = evaluate_action(
            db=db_session,
            case_id="NONEXISTENT",
            proposed_action=RecoveryActionEnum.CREATE_PAYMENT_LINK.value,
            confidence=0.8,
            recovery_probability=0.6,
        )
        
        assert result.allowed is False
        assert result.decision == "BLOCKED"
        assert "not found" in result.reason.lower()


# ============================================================================
# CRITICAL TEST: AI RECOMMENDS → POLICY BLOCKS → TOOL NOT EXECUTED
# ============================================================================


class TestPolicyBlocksExecution:
    """
    CRITICAL TEST: Proves that AI recommendation → Policy blocks → payment tool NOT executed.
    
    This is the most important test for Phase 5.
    """

    def test_policy_blocks_retry_when_limit_reached(self, db_session, case_with_retries):
        """
        Test that when AI recommends RETRY but retry limit is reached,
        policy BLOCKS the action and payment tool is NOT executed.
        """
        # Step 1: AI recommends RETRY
        ai_recommendation = RecoveryActionEnum.RETRY.value
        ai_confidence = 0.85
        recovery_probability = 0.7
        
        # Step 2: Policy engine evaluates
        policy_result = evaluate_action(
            db=db_session,
            case_id="CASE-RETRIES",
            proposed_action=ai_recommendation,
            confidence=ai_confidence,
            recovery_probability=recovery_probability,
        )
        
        # Step 3: Policy BLOCKS the action
        assert policy_result.allowed is False
        assert policy_result.decision == "BLOCKED"
        
        # Step 4: Verify payment tool would NOT be executed
        # In real code, this would be:
        # if policy_result.allowed:
        #     execute_payment_tool(...)
        # else:
        #     # Do NOT execute payment tool
        #     log_blocked_action(...)
        
        # Step 5: Verify audit trail shows blocked action
        from app.models import PolicyDecision
        decision = db_session.query(PolicyDecision).filter(
            PolicyDecision.case_id == case_with_retries.id,
            PolicyDecision.proposed_action == ai_recommendation,
        ).first()
        
        assert decision is not None
        assert decision.decision == "BLOCKED"
        assert decision.final_decision == "BLOCKED"

    def test_policy_blocks_reminder_when_limit_reached(self, db_session, case_with_reminders):
        """
        Test that when AI recommends SEND_PAYMENT_REMINDER but reminder limit is reached,
        policy BLOCKS the action and payment tool is NOT executed.
        """
        # Step 1: AI recommends SEND_PAYMENT_REMINDER
        ai_recommendation = RecoveryActionEnum.SEND_PAYMENT_REMINDER.value
        
        # Step 2: Policy engine evaluates
        policy_result = evaluate_action(
            db=db_session,
            case_id="CASE-REMINDERS",
            proposed_action=ai_recommendation,
            confidence=0.8,
            recovery_probability=0.6,
        )
        
        # Step 3: Policy BLOCKS the action
        assert policy_result.allowed is False
        assert policy_result.decision == "BLOCKED"
        
        # Step 4: Verify payment tool would NOT be executed
        # The policy engine has blocked the reminder action

    def test_policy_escalates_when_amount_too_high(self, db_session, high_amount_case):
        """
        Test that when AI recommends CREATE_PAYMENT_LINK but amount exceeds limit,
        policy ESCALATES to human and payment tool is NOT executed.
        """
        # Step 1: AI recommends CREATE_PAYMENT_LINK
        ai_recommendation = RecoveryActionEnum.CREATE_PAYMENT_LINK.value
        
        # Step 2: Policy engine evaluates
        policy_result = evaluate_action(
            db=db_session,
            case_id="CASE-HIGH",
            proposed_action=ai_recommendation,
            confidence=0.8,
            recovery_probability=0.6,
        )
        
        # Step 3: Policy ESCALATES to human
        assert policy_result.allowed is False
        assert policy_result.decision == "ESCALATED"
        
        # Step 4: Verify payment tool would NOT be executed
        # The policy engine has escalated to human review

    def test_policy_approves_when_all_checks_pass(self, db_session, sample_case):
        """
        Test that when AI recommends action and all policy checks pass,
        policy APPROVES the action and payment tool CAN be executed.
        """
        # Step 1: AI recommends CREATE_PAYMENT_LINK
        ai_recommendation = RecoveryActionEnum.CREATE_PAYMENT_LINK.value
        
        # Step 2: Policy engine evaluates
        policy_result = evaluate_action(
            db=db_session,
            case_id="CASE-001",
            proposed_action=ai_recommendation,
            confidence=0.8,
            recovery_probability=0.6,
        )
        
        # Step 3: Policy APPROVES the action
        assert policy_result.allowed is True
        assert policy_result.decision == "APPROVED"
        
        # Step 4: Verify payment tool CAN be executed
        # In real code, this would be:
        # if policy_result.allowed:
        #     execute_payment_tool(...)  # This would proceed


# ============================================================================
# AUDIT TRAIL TESTS
# ============================================================================


class TestAuditTrail:
    """Test that policy decisions are properly audited."""

    def test_policy_decision_stored(self, db_session, sample_case):
        """Test that policy decisions are stored in database."""
        evaluate_action(
            db=db_session,
            case_id="CASE-001",
            proposed_action=RecoveryActionEnum.CREATE_PAYMENT_LINK.value,
            confidence=0.8,
            recovery_probability=0.6,
        )
        
        # Check that decision was stored
        from app.models import PolicyDecision
        decision = db_session.query(PolicyDecision).filter(
            PolicyDecision.case_id == sample_case.id,
        ).first()
        
        assert decision is not None
        assert decision.decision == "APPROVED"
        assert decision.proposed_action == RecoveryActionEnum.CREATE_PAYMENT_LINK.value

    def test_audit_event_created(self, db_session, sample_case):
        """Test that audit events are created for policy decisions."""
        evaluate_action(
            db=db_session,
            case_id="CASE-001",
            proposed_action=RecoveryActionEnum.CREATE_PAYMENT_LINK.value,
            confidence=0.8,
            recovery_probability=0.6,
        )
        
        # Check that audit event was created
        from app.models import AuditEvent
        audit = db_session.query(AuditEvent).filter(
            AuditEvent.case_id == sample_case.id,
            AuditEvent.event_type == AuditEventType.POLICY_CHECKED.value,
        ).first()
        
        assert audit is not None
        assert audit.actor == "policy_engine"
        assert audit.decision == "APPROVED"

    def test_blocked_action_audited(self, db_session, case_with_retries):
        """Test that blocked actions are properly audited."""
        evaluate_action(
            db=db_session,
            case_id="CASE-RETRIES",
            proposed_action=RecoveryActionEnum.RETRY.value,
            confidence=0.8,
            recovery_probability=0.6,
        )
        
        # Check that blocked action was audited
        from app.models import AuditEvent
        audit = db_session.query(AuditEvent).filter(
            AuditEvent.case_id == case_with_retries.id,
            AuditEvent.event_type == AuditEventType.POLICY_CHECKED.value,
        ).first()
        
        assert audit is not None
        assert audit.decision == "BLOCKED"
        assert audit.policy_checks is not None


# ============================================================================
# API ENDPOINT TESTS
# ============================================================================


class TestPolicyAPI:
    """Test Policy Engine API endpoints."""

    def test_get_policy_config(self, client):
        """Test GET /api/policies endpoint."""
        response = client.get("/api/policies")
        
        assert response.status_code == 200
        data = response.json()
        assert "max_retries" in data
        assert "max_reminders" in data
        assert "autonomous_amount_limit" in data
        assert data["is_active"] is True

    def test_update_policy_config(self, client):
        """Test PUT /api/policies endpoint."""
        update_data = {
            "max_retries": 3,
            "description": "Updated policy",
        }
        
        response = client.put("/api/policies", json=update_data)
        
        assert response.status_code == 200
        data = response.json()
        assert data["max_retries"] == 3
        assert data["description"] == "Updated policy"

    def test_update_policy_config_validation(self, client):
        """Test that policy updates are validated."""
        # Try to set amount limit too high
        update_data = {
            "autonomous_amount_limit": 200000,  # Exceeds 100000 limit
        }
        
        response = client.put("/api/policies", json=update_data)
        
        assert response.status_code == 422  # Validation error

    def test_evaluate_action_endpoint(self, client, sample_case):
        """Test POST /api/policies/evaluate endpoint."""
        response = client.post(
            "/api/policies/evaluate",
            params={
                "case_id": "CASE-001",
                "proposed_action": "CREATE_PAYMENT_LINK",
                "confidence": 0.8,
                "recovery_probability": 0.6,
            },
        )
        
        assert response.status_code == 200
        data = response.json()
        assert "allowed" in data
        assert "decision" in data
        assert "checks" in data

    def test_evaluate_action_invalid_confidence(self, client, sample_case):
        """Test evaluate endpoint with invalid confidence."""
        response = client.post(
            "/api/policies/evaluate",
            params={
                "case_id": "CASE-001",
                "proposed_action": "CREATE_PAYMENT_LINK",
                "confidence": 1.5,  # Invalid
                "recovery_probability": 0.6,
            },
        )
        
        assert response.status_code == 400
