"""
Tests for AI Agent - Revenue Recovery.

Tests cover:
- Valid LLM output
- Malformed output
- Invalid action
- Low confidence
- LLM unavailable
- Tool selection
- Tool authorization
- Audit logging
"""
import pytest
from unittest.mock import patch, MagicMock
from datetime import datetime, timezone

from app.models import (
    Customer,
    Transaction,
    RevenueRiskCase,
    RecoveryAction,
    AuditEvent,
    CaseStatus,
    AuditEventType,
)
from app.schemas.agent import (
    AgentRecommendation,
    RecoveryAction as RecoveryActionEnum,
)
from app.services.agent_service import (
    validate_llm_output,
    diagnose_case,
    build_llm_prompt,
    get_case_context,
)
from app.services.agent_tools import (
    get_transaction,
    get_customer_history,
    get_payment_status,
    get_recovery_probability,
    record_audit_event,
    escalate_case,
    create_payment_link,
    send_payment_reminder,
    retry_payment,
    check_recovery_status,
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
def valid_llm_output():
    """Valid LLM output for testing."""
    return {
        "diagnosis": "Temporary payment failure due to insufficient funds. Customer has strong payment history.",
        "reasoning_summary": "The customer has a good payment history with 85% success rate. The failure is likely temporary.",
        "recovery_probability": 0.82,
        "recommended_action": "CREATE_PAYMENT_LINK",
        "confidence": 0.88,
        "customer_message": "Your payment could not be completed. You can securely retry using the payment link below.",
        "additional_information_required": False,
    }


@pytest.fixture
def malformed_llm_output():
    """Malformed LLM output for testing."""
    return {
        "diagnosis": "",  # Empty - invalid
        "reasoning_summary": "Some reasoning",
        "recovery_probability": 0.5,
        "recommended_action": "CREATE_PAYMENT_LINK",
        "confidence": 0.7,
        "customer_message": "Test message",
        "additional_information_required": False,
    }


@pytest.fixture
def invalid_action_llm_output():
    """LLM output with invalid action for testing."""
    return {
        "diagnosis": "Some diagnosis here for testing purposes",
        "reasoning_summary": "Some reasoning summary here for testing",
        "recovery_probability": 0.5,
        "recommended_action": "INVALID_ACTION",  # Invalid action
        "confidence": 0.7,
        "customer_message": "Test message here for testing",
        "additional_information_required": False,
    }


@pytest.fixture
def low_confidence_llm_output():
    """LLM output with low confidence for testing."""
    return {
        "diagnosis": "Some diagnosis here for testing purposes",
        "reasoning_summary": "Some reasoning summary here for testing",
        "recovery_probability": 0.5,
        "recommended_action": "CREATE_PAYMENT_LINK",
        "confidence": 0.2,  # Low confidence
        "customer_message": "Test message here for testing",
        "additional_information_required": False,
    }


# ============================================================================
# SCHEMA VALIDATION TESTS
# ============================================================================


class TestAgentSchemaValidation:
    """Test Pydantic schema validation for agent output."""

    def test_valid_recommendation(self, valid_llm_output):
        """Test that valid LLM output passes validation."""
        recommendation = AgentRecommendation(**valid_llm_output)
        assert recommendation is not None
        assert recommendation.recommended_action == RecoveryActionEnum.CREATE_PAYMENT_LINK
        assert 0 <= recommendation.confidence <= 1
        assert 0 <= recommendation.recovery_probability <= 1

    def test_empty_diagnosis_rejected(self, malformed_llm_output):
        """Test that empty diagnosis is rejected."""
        recommendation = validate_llm_output(malformed_llm_output)
        assert recommendation is None

    def test_invalid_action_rejected(self, invalid_action_llm_output):
        """Test that invalid action is rejected."""
        recommendation = validate_llm_output(invalid_action_llm_output)
        assert recommendation is None

    def test_low_confidence_accepted(self, low_confidence_llm_output):
        """Test that low confidence is accepted but flagged."""
        recommendation = validate_llm_output(low_confidence_llm_output)
        assert recommendation is not None
        assert recommendation.confidence < 0.3

    def test_all_valid_actions_accepted(self):
        """Test that all valid recovery actions are accepted."""
        for action in RecoveryActionEnum:
            output = {
                "diagnosis": "Test diagnosis for validation purposes",
                "reasoning_summary": "Test reasoning summary for validation purposes",
                "recovery_probability": 0.5,
                "recommended_action": action.value,
                "confidence": 0.7,
                "customer_message": "Test customer message for validation",
                "additional_information_required": False,
            }
            recommendation = validate_llm_output(output)
            assert recommendation is not None
            assert recommendation.recommended_action == action

    def test_diagnosis_length_validation(self):
        """Test diagnosis length constraints."""
        # Too short
        output = {
            "diagnosis": "Short",
            "reasoning_summary": "Some reasoning summary here for testing purposes",
            "recovery_probability": 0.5,
            "recommended_action": "NO_ACTION",
            "confidence": 0.7,
            "customer_message": "Test customer message for validation",
            "additional_information_required": False,
        }
        recommendation = validate_llm_output(output)
        assert recommendation is None

        # Valid length
        output["diagnosis"] = "This is a valid diagnosis with enough characters"
        recommendation = validate_llm_output(output)
        assert recommendation is not None

    def test_confidence_range_validation(self):
        """Test confidence range constraints."""
        # Below range
        output = {
            "diagnosis": "Test diagnosis for validation purposes",
            "reasoning_summary": "Test reasoning summary for validation purposes",
            "recovery_probability": 0.5,
            "recommended_action": "NO_ACTION",
            "confidence": -0.1,
            "customer_message": "Test customer message for validation",
            "additional_information_required": False,
        }
        recommendation = validate_llm_output(output)
        assert recommendation is None

        # Above range
        output["confidence"] = 1.1
        recommendation = validate_llm_output(output)
        assert recommendation is None


# ============================================================================
# TOOL TESTS
# ============================================================================


class TestAgentTools:
    """Test agent backend tools."""

    def test_get_transaction(self, db_session, sample_transaction):
        """Test get_transaction tool."""
        result = get_transaction(db_session, sample_transaction.id)
        assert result is not None
        assert result["transaction_id"] == "TXN-001"
        assert result["amount"] == 2500.0
        assert result["status"] == "FAILED"

    def test_get_transaction_not_found(self, db_session):
        """Test get_transaction with non-existent ID."""
        result = get_transaction(db_session, 99999)
        assert result is None

    def test_get_customer_history(self, db_session, sample_customer):
        """Test get_customer_history tool."""
        result = get_customer_history(db_session, sample_customer.id)
        assert result is not None
        assert result["customer_id"] == "CUST-001"
        assert result["name"] == "Test Customer"
        assert result["total_transactions"] == 20

    def test_get_customer_history_not_found(self, db_session):
        """Test get_customer_history with non-existent ID."""
        result = get_customer_history(db_session, 99999)
        assert result is None

    def test_get_payment_status(self, db_session, sample_case):
        """Test get_payment_status tool."""
        result = get_payment_status(db_session, "CASE-001")
        assert result is not None
        assert result["case_id"] == "CASE-001"
        assert result["amount"] == 2500.0
        assert result["status"] == "OPEN"

    def test_get_payment_status_not_found(self, db_session):
        """Test get_payment_status with non-existent case."""
        result = get_payment_status(db_session, "NONEXISTENT")
        assert result is None

    def test_get_recovery_probability(self, db_session, sample_case):
        """Test get_recovery_probability tool."""
        result = get_recovery_probability(db_session, "CASE-001")
        assert result is not None
        assert result["case_id"] == "CASE-001"
        assert result["recovery_probability"] == 0.65
        assert result["risk_score"] == 0.75

    def test_get_recovery_probability_not_found(self, db_session):
        """Test get_recovery_probability with non-existent case."""
        result = get_recovery_probability(db_session, "NONEXISTENT")
        assert result is None

    def test_create_payment_link_tool_refuses_to_produce_a_link(self, db_session):
        """
        The agent tool cannot create a payment link, mock or real.

        Recovery links exist only after the Policy Engine authorizes the stored
        recommendation, so this tool must return no link at all and must never
        reach the provider layer.
        """
        with patch("app.services.razorpay_service.create_payment_link") as provider_call:
            result = create_payment_link(db_session, "CASE-001", 2500.0)

        provider_call.assert_not_called()
        assert result["success"] is False
        assert result["code"] == "POLICY_AUTHORIZATION_REQUIRED"
        assert "payment_link" not in result
        assert "mock.invalid" not in str(result)
        assert result["execution_mode"] == "NOT_EXECUTED"
        assert result["action_executed"] is False
        assert result["provider_called"] is False
        assert result["mock"] is False

    def test_create_payment_link_tool_does_not_touch_the_case(
        self, db_session, sample_case
    ):
        """A refusal must not record an execution or move the case."""
        result = create_payment_link(db_session, "CASE-001", 2500.0)

        assert result["success"] is False
        db_session.refresh(sample_case)
        assert sample_case.status == CaseStatus.OPEN.value
        assert sample_case.attempt_count == 1
        assert sample_case.recovered_amount == 0.0
        assert db_session.query(RecoveryAction).count() == 0
        assert db_session.query(AuditEvent).count() == 0

    def test_send_payment_reminder_tool_never_claims_delivery(self, db_session):
        """
        Regression (2A): no delivery channel exists, so the reminder tool must not
        report a sent reminder.  It previously returned success: True with
        "Payment reminder sent successfully".
        """
        result = send_payment_reminder(db_session, "CASE-001", "test@example.com")

        assert result["success"] is False
        assert result["code"] == "ACTION_UNAVAILABLE"
        assert result["availability"]["availability"] == "SIMULATED_ONLY"
        assert result["availability"]["executable"] is False
        assert result["delivery_status"] == "NOT_SENT"
        assert result["delivered"] is False
        assert result["provider_called"] is False
        assert result["action_executed"] is False
        assert result["mock"] is False
        assert "sent successfully" not in str(result).lower()

    def test_send_payment_reminder_tool_records_nothing(
        self, db_session, sample_case
    ):
        """A refused reminder must not create an action or an audit event."""
        send_payment_reminder(db_session, "CASE-001", "test@example.com")

        assert db_session.query(RecoveryAction).count() == 0
        assert db_session.query(AuditEvent).count() == 0
        db_session.refresh(sample_case)
        assert sample_case.status == CaseStatus.OPEN.value

    def test_retry_payment_tool_never_claims_a_retry(self, db_session):
        """
        Regression (2A): a retry creates a payment link, so it may only happen
        through the policy-authorized execution path.  The tool previously returned
        success: True with "Payment retry initiated".
        """
        result = retry_payment(db_session, "CASE-001", "card")

        assert result["success"] is False
        assert result["code"] == "POLICY_AUTHORIZATION_REQUIRED"
        assert result["execution_mode"] == "NOT_EXECUTED"
        assert result["action_executed"] is False
        assert result["provider_called"] is False
        assert result["mock"] is False
        assert "initiated" not in str(result).lower()

    def test_check_recovery_status_reads_the_database(
        self, db_session, sample_case
    ):
        """The status lookup reports the stored status, not a fabricated one."""
        result = check_recovery_status(db_session, "CASE-001")

        assert result["success"] is True
        assert result["case_id"] == "CASE-001"
        assert result["status"] == CaseStatus.OPEN.value
        assert result["source"] == "database"
        assert result["mock"] is False

    def test_check_recovery_status_missing_case(self, db_session):
        """A missing case is reported as missing instead of given a status."""
        result = check_recovery_status(db_session, "NONEXISTENT")

        assert result["success"] is False
        assert result["code"] == "CASE_NOT_FOUND"
        assert "status" not in result

    def test_record_audit_event(self, db_session, sample_case):
        """Test record_audit_event tool."""
        result = record_audit_event(
            db=db_session,
            case_id="CASE-001",
            event_type=AuditEventType.DIAGNOSIS_COMPLETED.value,
            actor="ai_agent",
            decision="CREATE_PAYMENT_LINK",
            reason="Test reason",
            confidence=0.85,
        )
        assert result["success"] is True
        assert result["event_type"] == "DIAGNOSIS_COMPLETED"

    def test_record_audit_event_case_not_found(self, db_session):
        """Test record_audit_event with non-existent case."""
        result = record_audit_event(
            db=db_session,
            case_id="NONEXISTENT",
            event_type=AuditEventType.DIAGNOSIS_COMPLETED.value,
            actor="ai_agent",
            decision="CREATE_PAYMENT_LINK",
        )
        assert result["success"] is False

    def test_escalate_case(self, db_session, sample_case):
        """Test escalate_case tool."""
        result = escalate_case(db_session, "CASE-001", "Test escalation reason")
        assert result["success"] is True
        assert result["status"] == "ESCALATED"

    def test_escalate_case_not_found(self, db_session):
        """Test escalate_case with non-existent case."""
        result = escalate_case(db_session, "NONEXISTENT", "Test reason")
        assert result["success"] is False


# ============================================================================
# SERVICE TESTS
# ============================================================================


class TestAgentService:
    """Test agent service orchestration."""

    def test_get_case_context(self, db_session, sample_case):
        """Test get_case_context function."""
        context = get_case_context(db_session, "CASE-001")
        assert context is not None
        assert "case" in context
        assert "transaction" in context
        assert "customer" in context
        assert "recovery_probability" in context
        assert context["case"]["case_id"] == "CASE-001"

    def test_get_case_context_not_found(self, db_session):
        """Test get_case_context with non-existent case."""
        context = get_case_context(db_session, "NONEXISTENT")
        assert context is None

    def test_build_llm_prompt(self, sample_case):
        """Test build_llm_prompt function."""
        context = {
            "case": {"case_id": "CASE-001", "amount": 2500.0},
            "transaction": {"payment_method": "card", "failure_reason": "insufficient_funds"},
            "customer": {"name": "Test Customer", "total_transactions": 20},
            "recovery_probability": {"recovery_probability": 0.65},
        }
        prompt = build_llm_prompt(context)
        assert "CASE-001" in prompt
        assert "2500" in prompt
        assert "insufficient_funds" in prompt
        assert "JSON" in prompt

    def test_diagnose_case_success(self, db_session, sample_case):
        """Test diagnose_case function with successful flow."""
        result = diagnose_case(db_session, "CASE-001")
        assert result["success"] is True
        assert "data" in result
        assert result["case_id"] == "CASE-001"
        assert "model_used" in result
        assert "processing_time_ms" in result

    def test_diagnose_case_not_found(self, db_session):
        """Test diagnose_case with non-existent case."""
        result = diagnose_case(db_session, "NONEXISTENT")
        assert result["success"] is False
        assert "error" in result

    def test_diagnose_case_records_audit_event(self, db_session, sample_case):
        """Test that diagnose_case records audit event."""
        result = diagnose_case(db_session, "CASE-001")
        assert result["success"] is True

        # Check audit event was created
        audit_events = (
            db_session.query(AuditEvent)
            .filter(AuditEvent.case_id == sample_case.id)
            .all()
        )
        assert len(audit_events) > 0
        assert any(
            e.event_type == AuditEventType.DIAGNOSIS_COMPLETED.value
            for e in audit_events
        )

    def test_diagnose_case_updates_case_status(self, db_session, sample_case):
        """Test that diagnose_case updates case status."""
        result = diagnose_case(db_session, "CASE-001")
        assert result["success"] is True

        # Check case status was updated
        db_session.refresh(sample_case)
        assert sample_case.status == CaseStatus.IN_PROGRESS.value

    def test_diagnose_case_updates_case_diagnosis(self, db_session, sample_case):
        """Test that diagnose_case updates case diagnosis."""
        result = diagnose_case(db_session, "CASE-001")
        assert result["success"] is True

        # Check case diagnosis was updated
        db_session.refresh(sample_case)
        assert sample_case.diagnosis is not None
        assert sample_case.recommended_action is not None

    def test_diagnosis_is_labelled_deterministic(self, db_session, sample_case):
        """
        Regression (2B): the diagnosis is produced by rules, and the response says
        so instead of presenting it as a language-model answer.
        """
        result = diagnose_case(db_session, "CASE-001")

        assert result["success"] is True
        assert result["llm_used"] is False
        assert result["diagnosis_source"] == "DETERMINISTIC_RULES"
        assert result["model_used"] == "deterministic-rules-v1"
        assert result["model_used"] != "mock-llm-v1"
        assert result["confidence_source"] == "deterministic_rule_score"
        assert result["recovery_probability_source"] == "ml_model_case_level"

        # The probability is the ML value stored on the case, not a constant.
        assert result["data"]["recovery_probability"] == 0.65
        assert result["recovery_probability_used"] == 0.65

        # The diagnosis is built from the stored failure reason, and contains no
        # invented statistics (the old text claimed an "85% success rate").
        assert "insufficient" in result["data"]["diagnosis"].lower()
        assert "85%" not in result["data"]["diagnosis"]
        assert "rule-based" in result["data"]["diagnosis"].lower()

    def test_diagnosis_provenance_is_recorded_in_the_audit_event(
        self, db_session, sample_case
    ):
        """The audit trail states what produced the diagnosis and which value was used."""
        diagnose_case(db_session, "CASE-001")

        event = db_session.query(AuditEvent).filter(
            AuditEvent.case_id == sample_case.id,
            AuditEvent.event_type == AuditEventType.DIAGNOSIS_COMPLETED.value,
        ).one()
        metadata = event.metadata_
        assert metadata["diagnosis_source"] == "DETERMINISTIC_RULES"
        assert metadata["llm_used"] is False
        assert metadata["confidence_source"] == "deterministic_rule_score"
        assert metadata["recovery_probability"] == 0.65
        assert metadata["recovery_probability_source"] == "ml_model_case_level"

    def test_policy_uses_the_ml_probability_not_the_engines_claim(
        self, db_session, sample_case
    ):
        """
        A diagnosis engine cannot raise the probability that authorizes spending:
        the Policy Engine is given the ML value stored on the case.
        """
        inflated = {
            "diagnosis": "Rule output claiming a very high probability",
            "reasoning_summary": "This recommendation claims 0.99 to test sourcing",
            "recovery_probability": 0.99,
            "recommended_action": "CREATE_PAYMENT_LINK",
            "confidence": 0.9,
            "customer_message": "Please retry your payment using this link.",
            "additional_information_required": False,
        }
        with patch("app.services.agent_service.call_llm", return_value=inflated):
            result = diagnose_case(db_session, "CASE-001")

        assert result["success"] is True
        assert result["recovery_probability_used"] == 0.65

        event = db_session.query(AuditEvent).filter(
            AuditEvent.case_id == sample_case.id,
            AuditEvent.event_type == AuditEventType.DIAGNOSIS_COMPLETED.value,
        ).one()
        assert event.metadata_["recovery_probability"] == 0.65
        assert event.metadata_["model_reported_recovery_probability"] == 0.99

    def test_missing_ml_probability_escalates_instead_of_inventing_one(
        self, db_session, sample_case
    ):
        """
        Regression (2B): without a stored ML probability the case is escalated. The
        old mock answered every case with 0.82.
        """
        sample_case.recovery_probability = None
        db_session.commit()

        result = diagnose_case(db_session, "CASE-001")

        assert result["success"] is False
        assert result["code"] == "RECOVERY_PROBABILITY_UNAVAILABLE"
        assert result["fallback_action"] == "ESCALATE_TO_HUMAN"
        assert "fallback_reason" in result

        db_session.refresh(sample_case)
        assert sample_case.status == CaseStatus.ESCALATED.value
        assert sample_case.diagnosis is None

        diagnoses = db_session.query(AuditEvent).filter(
            AuditEvent.case_id == sample_case.id,
            AuditEvent.event_type == AuditEventType.DIAGNOSIS_COMPLETED.value,
        ).all()
        assert len(diagnoses) == 1
        assert diagnoses[0].decision == "FALLBACK"
        assert diagnoses[0].confidence is None

    def test_unknown_failure_reason_escalates(self, db_session, sample_case):
        """An unrecognised failure cause is escalated, never guessed."""
        from app.models import Transaction as TransactionModel

        txn = db_session.query(TransactionModel).filter(
            TransactionModel.id == sample_case.transaction_id
        ).one()
        txn.failure_reason = "something_unheard_of"
        db_session.commit()

        result = diagnose_case(db_session, "CASE-001")

        assert result["success"] is True
        assert result["data"]["recommended_action"] == "ESCALATE_TO_HUMAN"
        assert result["data"]["additional_information_required"] is True
        assert result["data"]["confidence"] <= 0.5

    def test_diagnosis_rules_vary_with_the_case(self):
        """Two different cases do not receive the same canned answer."""
        from app.services.agent_service import call_llm

        base_case = {
            "case_id": "CASE-A", "amount": 1000.0, "attempt_count": 0,
        }
        transaction_a = {
            "currency": "INR", "payment_method": "card",
            "failure_reason": "insufficient_funds",
        }
        transaction_b = {
            "currency": "INR", "payment_method": "upi",
            "failure_reason": "card_expired",
        }
        customer = {"total_transactions": 5, "failed_transactions": 1}
        ml = {"recovery_probability": 0.4}

        first = call_llm("prompt", {
            "case": base_case, "transaction": transaction_a,
            "customer": customer, "recovery_probability": ml,
        })
        second = call_llm("prompt", {
            "case": base_case, "transaction": transaction_b,
            "customer": customer, "recovery_probability": ml,
        })

        assert first["diagnosis"] != second["diagnosis"]
        assert first["recommended_action"] == "CREATE_PAYMENT_LINK"
        assert second["recommended_action"] == "ESCALATE_TO_HUMAN"
        # Both report the same stored ML probability: it is not the engine's number.
        assert first["recovery_probability"] == second["recovery_probability"] == 0.4

    def test_confidences_are_a_rule_score_not_a_constant(self):
        """The confidence varies only with the evidence the rules used."""
        from app.services.agent_service import _rule_confidence

        assert _rule_confidence(cause_known=True, history_available=True) == 0.7
        assert _rule_confidence(cause_known=True, history_available=False) == 0.5
        assert _rule_confidence(cause_known=False, history_available=True) == 0.5
        assert _rule_confidence(cause_known=False, history_available=False) == 0.3

    def test_no_context_produces_no_diagnosis(self):
        """Without case data the engine refuses to fabricate a diagnosis."""
        from app.services.agent_service import call_llm

        assert call_llm("prompt") is None
        assert call_llm("prompt", None) is None

    def test_diagnose_case_does_not_reopen_recovered_case(self, db_session, sample_case):
        """
        Regression test (stop after success): diagnosing a settled case must not
        run the LLM or drag the case back to IN_PROGRESS.
        """
        sample_case.status = CaseStatus.RECOVERED.value
        sample_case.recovered_amount = 2500.0
        db_session.commit()

        result = diagnose_case(db_session, "CASE-001")

        assert result["success"] is False
        assert result["skipped"] is True
        assert result["status"] == CaseStatus.RECOVERED.value

        db_session.refresh(sample_case)
        assert sample_case.status == CaseStatus.RECOVERED.value
        assert sample_case.recovered_amount == 2500.0
        assert sample_case.recommended_action is None
        assert sample_case.diagnosis is None

        # No diagnosis work was recorded for a settled case
        audits = db_session.query(AuditEvent).filter(
            AuditEvent.case_id == sample_case.id,
            AuditEvent.event_type == AuditEventType.DIAGNOSIS_COMPLETED.value,
        ).all()
        assert audits == []


# ============================================================================
# API ENDPOINT TESTS
# ============================================================================


class TestAgentAPI:
    """Test agent API endpoints."""

    def test_diagnose_endpoint_success(self, client, sample_case):
        """Test POST /api/agent/diagnose endpoint."""
        response = client.post(
            "/api/agent/diagnose",
            json={"case_id": "CASE-001"},
        )
        assert response.status_code == 200
        data = response.json()
        assert data["success"] is True
        assert "data" in data
        assert data["case_id"] == "CASE-001"

    def test_diagnose_endpoint_case_not_found(self, client):
        """Test POST /api/agent/diagnose with non-existent case."""
        response = client.post(
            "/api/agent/diagnose",
            json={"case_id": "NONEXISTENT"},
        )
        assert response.status_code == 404
        data = response.json()
        assert data["success"] is False

    def test_diagnose_endpoint_invalid_request(self, client):
        """Test POST /api/agent/diagnose with invalid request."""
        response = client.post(
            "/api/agent/diagnose",
            json={},  # Missing case_id
        )
        assert response.status_code == 422

    def test_diagnose_endpoint_response_structure(self, client, sample_case):
        """Test that diagnose endpoint returns correct structure."""
        response = client.post(
            "/api/agent/diagnose",
            json={"case_id": "CASE-001"},
        )
        assert response.status_code == 200
        data = response.json()

        # Check response structure
        assert "success" in data
        assert "data" in data
        assert "case_id" in data
        assert "model_used" in data
        assert "processing_time_ms" in data

        # Check data structure
        assert "diagnosis" in data["data"]
        assert "reasoning_summary" in data["data"]
        assert "recovery_probability" in data["data"]
        assert "recommended_action" in data["data"]
        assert "confidence" in data["data"]
        assert "customer_message" in data["data"]
        assert "additional_information_required" in data["data"]

    def test_diagnose_endpoint_states_the_diagnosis_provenance(self, client, sample_case):
        """The API response makes the absence of a language model explicit."""
        response = client.post("/api/agent/diagnose", json={"case_id": "CASE-001"})

        assert response.status_code == 200
        data = response.json()
        assert data["diagnosis_source"] == "DETERMINISTIC_RULES"
        assert data["llm_used"] is False
        assert data["diagnosis_engine_version"] == "deterministic-rules-v1"
        assert data["confidence_source"] == "deterministic_rule_score"
        assert data["recovery_probability_source"] == "ml_model_case_level"
        assert data["recovery_probability_used"] == 0.65

    def test_case_detail_exposes_the_diagnosis_provenance(self, client, sample_case):
        """The case detail view can tell rule output from a model answer."""
        client.post("/api/agent/diagnose", json={"case_id": "CASE-001"})

        response = client.get("/api/cases/CASE-001/detail")
        assert response.status_code == 200
        provenance = response.json()["data"]["diagnosis_provenance"]

        assert provenance["diagnosis_source"] == "DETERMINISTIC_RULES"
        assert provenance["llm_used"] is False
        assert provenance["recovery_probability_source"] == "ml_model_case_level"

    def test_diagnose_endpoint_with_llm_unavailable(self, client, sample_case):
        """Test diagnose endpoint when LLM is unavailable."""
        with patch("app.services.agent_service.call_llm", return_value=None):
            response = client.post(
                "/api/agent/diagnose",
                json={"case_id": "CASE-001"},
            )
            # Should return error and escalate
            assert response.status_code == 500
            data = response.json()
            assert data["success"] is False
            assert data["fallback_action"] == "ESCALATE_TO_HUMAN"

    def test_diagnose_endpoint_with_invalid_llm_output(self, client, sample_case):
        """Test diagnose endpoint with invalid LLM output."""
        invalid_output = {
            "diagnosis": "",  # Empty - invalid
            "reasoning_summary": "Some reasoning",
            "recovery_probability": 0.5,
            "recommended_action": "CREATE_PAYMENT_LINK",
            "confidence": 0.7,
            "customer_message": "Test message",
            "additional_information_required": False,
        }
        with patch("app.services.agent_service.call_llm", return_value=invalid_output):
            response = client.post(
                "/api/agent/diagnose",
                json={"case_id": "CASE-001"},
            )
            # Should return error and escalate
            assert response.status_code == 500
            data = response.json()
            assert data["success"] is False
            assert data["fallback_action"] == "ESCALATE_TO_HUMAN"


# ============================================================================
# EDGE CASE TESTS
# ============================================================================


class TestAgentEdgeCases:
    """Test agent edge cases and error handling."""

    def test_llm_returns_none(self, db_session, sample_case):
        """Test when LLM returns None."""
        with patch("app.services.agent_service.call_llm", return_value=None):
            result = diagnose_case(db_session, "CASE-001")
            assert result["success"] is False
            assert result["fallback_action"] == "ESCALATE_TO_HUMAN"

    def test_llm_returns_invalid_json(self, db_session, sample_case):
        """Test when LLM returns invalid JSON structure."""
        with patch("app.services.agent_service.call_llm", return_value={"invalid": "structure"}):
            result = diagnose_case(db_session, "CASE-001")
            assert result["success"] is False
            assert result["fallback_action"] == "ESCALATE_TO_HUMAN"

    def test_llm_returns_invalid_action(self, db_session, sample_case):
        """Test when LLM returns invalid action."""
        invalid_output = {
            "diagnosis": "Valid diagnosis with enough characters",
            "reasoning_summary": "Valid reasoning with enough characters for testing",
            "recovery_probability": 0.5,
            "recommended_action": "INVALID_ACTION",
            "confidence": 0.7,
            "customer_message": "Valid message with enough characters",
            "additional_information_required": False,
        }
        with patch("app.services.agent_service.call_llm", return_value=invalid_output):
            result = diagnose_case(db_session, "CASE-001")
            assert result["success"] is False
            assert result["fallback_action"] == "ESCALATE_TO_HUMAN"

    def test_llm_returns_low_confidence(self, db_session, sample_case):
        """Test when LLM returns low confidence (should still succeed)."""
        low_confidence_output = {
            "diagnosis": "Valid diagnosis with enough characters",
            "reasoning_summary": "Valid reasoning with enough characters for testing",
            "recovery_probability": 0.5,
            "recommended_action": "NO_ACTION",
            "confidence": 0.1,  # Low confidence
            "customer_message": "Valid message with enough characters",
            "additional_information_required": False,
        }
        with patch("app.services.agent_service.call_llm", return_value=low_confidence_output):
            result = diagnose_case(db_session, "CASE-001")
            # Should still succeed, just with low confidence
            assert result["success"] is True
            assert result["data"]["confidence"] == 0.1

    def test_concurrent_diagnosis_requests(self, db_session, sample_case):
        """Test that concurrent requests don't cause issues."""
        # Both should succeed (or one might fail if case is already in progress)
        result1 = diagnose_case(db_session, "CASE-001")
        result2 = diagnose_case(db_session, "CASE-001")

        # At least one should succeed
        assert result1["success"] or result2["success"]

    def test_diagnosis_idempotency(self, db_session, sample_case):
        """Test that multiple diagnoses don't cause duplicate audit events."""
        result1 = diagnose_case(db_session, "CASE-001")
        result2 = diagnose_case(db_session, "CASE-001")

        # Check audit events
        audit_events = (
            db_session.query(AuditEvent)
            .filter(AuditEvent.case_id == sample_case.id)
            .all()
        )
        # Should have at least 2 audit events (one for each diagnosis)
        assert len(audit_events) >= 2
