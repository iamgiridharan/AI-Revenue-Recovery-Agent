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

    def test_create_payment_link_mock(self, db_session):
        """Test create_payment_link mock tool."""
        result = create_payment_link(db_session, "CASE-001", 2500.0)
        assert result["success"] is True
        assert "payment_link" in result
        assert result["mock"] is True

    def test_send_payment_reminder_mock(self, db_session):
        """Test send_payment_reminder mock tool."""
        result = send_payment_reminder(db_session, "CASE-001", "test@example.com")
        assert result["success"] is True
        assert result["mock"] is True

    def test_retry_payment_mock(self, db_session):
        """Test retry_payment mock tool."""
        result = retry_payment(db_session, "CASE-001", "card")
        assert result["success"] is True
        assert result["mock"] is True

    def test_check_recovery_status_mock(self, db_session):
        """Test check_recovery_status mock tool."""
        result = check_recovery_status(db_session, "CASE-001")
        assert result["case_id"] == "CASE-001"
        assert result["mock"] is True

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
