"""
Tests for Phase 6: Razorpay Test Mode Payment Recovery.

Tests cover:
1. Payment link creation (mock mode)
2. Webhook signature verification
3. Webhook event parsing
4. Duplicate webhook protection
5. Recovery service orchestration
6. Policy-blocked recovery action
7. Successful payment flow
8. Failed payment flow
9. Audit event creation
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
    PolicyConfig,
    PolicyDecision,
    CaseStatus,
    AuditEventType,
)


# ============================================================================
# Fixtures
# ============================================================================

@pytest.fixture
def customer(db_session):
    """Create a test customer."""
    customer = Customer(
        customer_id="CUST-TEST-001",
        name="Test User",
        email="test@example.com",
        phone="+919876543210",
        total_transactions=10,
        successful_transactions=8,
        failed_transactions=2,
        lifetime_value=5000.0,
    )
    db_session.add(customer)
    db_session.commit()
    db_session.refresh(customer)
    return customer


@pytest.fixture
def transaction(db_session, customer):
    """Create a test transaction."""
    txn = Transaction(
        transaction_id="TXN-TEST-001",
        customer_id=customer.id,
        amount=2500.0,
        currency="INR",
        payment_method="card",
        status="FAILED",
        failure_reason="insufficient_funds",
        attempt_count=1,
    )
    db_session.add(txn)
    db_session.commit()
    db_session.refresh(txn)
    return txn


@pytest.fixture
def risk_case(db_session, customer, transaction):
    """Create a test revenue risk case."""
    case = RevenueRiskCase(
        case_id="CASE-TEST-001",
        transaction_id=transaction.id,
        customer_id=customer.id,
        amount=2500.0,
        risk_score=0.75,
        recovery_probability=0.65,
        priority="HIGH",
        status="OPEN",
        attempt_count=0,
        recovered_amount=0.0,
    )
    db_session.add(case)
    db_session.commit()
    db_session.refresh(case)
    return case


@pytest.fixture
def active_policy(db_session):
    """Ensure an active policy config exists."""
    existing = db_session.query(PolicyConfig).filter(PolicyConfig.is_active == True).first()
    if existing:
        return existing
    policy = PolicyConfig(
        max_retries=2,
        max_reminders=2,
        max_recovery_attempts=3,
        autonomous_amount_limit=10000.0,
        minimum_ai_confidence=0.3,
        minimum_recovery_probability=0.2,
        case_lifetime_days=7,
        escalation_threshold=0.7,
        is_active=True,
        description="Test policy",
    )
    db_session.add(policy)
    db_session.commit()
    db_session.refresh(policy)
    return policy


# ============================================================================
# Razorpay Service Tests
# ============================================================================

class TestRazorpayService:
    """Tests for Razorpay payment link creation."""

    def test_create_mock_payment_link(self):
        """Test mock payment link creation when Razorpay is not configured."""
        from app.services.razorpay_service import create_payment_link

        with patch("app.services.razorpay_service.get_razorpay_client", return_value=None):
            result = create_payment_link(
                amount=5000.0,
                currency="INR",
                description="Test payment",
                reference_id="CASE-TEST-001",
            )

        assert result["success"] is True
        assert result["payment_link_id"].startswith("plink_")
        assert result["amount"] == 5000.0
        assert result["currency"] == "INR"
        assert result["mock"] is True
        assert result["reference_id"] == "CASE-TEST-001"

    def test_create_mock_payment_link_default_reference(self):
        """Test mock payment link with auto-generated reference."""
        from app.services.razorpay_service import create_payment_link

        with patch("app.services.razorpay_service.get_razorpay_client", return_value=None):
            result = create_payment_link(amount=1000.0)

        assert result["success"] is True
        assert result["reference_id"].startswith("recovery_")

    def test_verify_webhook_signature_no_secret(self):
        """
        Test webhook signature verification fails CLOSED when no secret is set.

        A missing secret used to skip verification entirely, which let anyone
        mark a case as recovered with an arbitrary amount.
        """
        import hashlib
        import hmac
        from app.services.razorpay_service import verify_webhook_signature

        with patch("app.services.razorpay_service.settings") as mock_settings:
            mock_settings.RAZORPAY_WEBHOOK_SECRET = ""
            assert verify_webhook_signature("payload", "sig") is False
            assert verify_webhook_signature("payload", "") is False

            # With a secret configured the normal verification path is used.
            mock_settings.RAZORPAY_WEBHOOK_SECRET = "test_webhook_secret"
            signature = hmac.new(
                b"test_webhook_secret", b"payload", hashlib.sha256
            ).hexdigest()
            assert verify_webhook_signature("payload", signature) is True
            assert verify_webhook_signature("payload", "wrong") is False

    def test_verify_webhook_signature_valid(self):
        """Test valid webhook signature verification."""
        import hmac
        import hashlib
        from app.services.razorpay_service import verify_webhook_signature

        secret = "test_webhook_secret"
        payload = '{"event":"payment.captured"}'
        expected_sig = hmac.new(
            secret.encode("utf-8"),
            payload.encode("utf-8"),
            hashlib.sha256
        ).hexdigest()

        result = verify_webhook_signature(payload, expected_sig, secret=secret)
        assert result is True

    def test_verify_webhook_signature_invalid(self):
        """Test invalid webhook signature verification."""
        from app.services.razorpay_service import verify_webhook_signature

        result = verify_webhook_signature(
            "payload",
            "invalid_signature",
            secret="test_secret",
        )
        assert result is False

    def test_parse_webhook_event_payment_captured(self):
        """Test parsing a payment.captured webhook event."""
        from app.services.razorpay_service import parse_webhook_event

        payload = {
            "event": "payment.captured",
            "payload": {
                "payment": {
                    "entity": {
                        "id": "pay_test123",
                        "order_id": "order_test123",
                        "amount": 250000,  # 2500 INR in paise
                        "currency": "INR",
                        "status": "captured",
                        "method": "card",
                        "email": "test@example.com",
                        "contact": "+919876543210",
                        "created_at": 1690000000,
                        "captured": True,
                        "description": "Test payment",
                        "notes": {"reference_id": "CASE-TEST-001"},
                    }
                }
            }
        }

        result = parse_webhook_event(payload)

        assert result["event_type"] == "payment.captured"
        assert result["payment_id"] == "pay_test123"
        assert result["amount"] == 2500.0  # Converted from paise
        assert result["currency"] == "INR"
        assert result["status"] == "captured"
        assert result["notes"]["reference_id"] == "CASE-TEST-001"

    def test_parse_webhook_event_payment_failed(self):
        """Test parsing a payment.failed webhook event."""
        from app.services.razorpay_service import parse_webhook_event

        payload = {
            "event": "payment.failed",
            "payload": {
                "payment": {
                    "entity": {
                        "id": "pay_test456",
                        "amount": 100000,
                        "currency": "INR",
                        "status": "failed",
                        "notes": {"reference_id": "CASE-TEST-002"},
                    }
                }
            }
        }

        result = parse_webhook_event(payload)

        assert result["event_type"] == "payment.failed"
        assert result["payment_id"] == "pay_test456"
        assert result["amount"] == 1000.0


# ============================================================================
# Recovery Service Tests
# ============================================================================

class TestRecoveryService:
    """Tests for recovery service orchestration."""

    def test_execute_recovery_creates_payment_link(self, db_session, risk_case, active_policy):
        """Test that executing a recovery action creates a payment link."""
        from app.services.recovery_service import execute_recovery_action

        result = execute_recovery_action(
            db=db_session,
            case_id="CASE-TEST-001",
            action_type="CREATE_PAYMENT_LINK",
            confidence=0.85,
            recovery_probability=0.75,
            reason="Test recovery",
        )

        assert result["success"] is True
        assert result["action_executed"] is True
        assert result["payment_link_id"] is not None
        assert result["amount"] == 2500.0

        # Verify case status updated
        case = db_session.query(RevenueRiskCase).filter(
            RevenueRiskCase.case_id == "CASE-TEST-001"
        ).first()
        assert case.status == CaseStatus.RECOVERY_ATTEMPTED.value
        assert case.attempt_count == 1

        # Verify recovery action created
        action = db_session.query(RecoveryAction).filter(
            RecoveryAction.case_id == case.id
        ).first()
        assert action is not None
        assert action.action_type == "CREATE_PAYMENT_LINK"
        assert action.policy_result == "APPROVED"
        assert action.execution_status == "SUCCESS"

        # Verify audit events created (policy engine creates one, then recovery service)
        audits = db_session.query(AuditEvent).filter(
            AuditEvent.case_id == case.id
        ).all()
        assert len(audits) >= 2
        event_types = [a.event_type for a in audits]
        assert AuditEventType.POLICY_CHECKED.value in event_types
        assert AuditEventType.ACTION_EXECUTED.value in event_types

    def test_execute_recovery_case_not_found(self, db_session):
        """Test recovery action when case doesn't exist."""
        from app.services.recovery_service import execute_recovery_action

        result = execute_recovery_action(
            db=db_session,
            case_id="NONEXISTENT",
            action_type="CREATE_PAYMENT_LINK",
            confidence=0.85,
            recovery_probability=0.75,
        )

        assert result["success"] is False
        assert "not found" in result["error"]
        assert result["action_executed"] is False

    def test_execute_recovery_already_recovered(self, db_session, risk_case, active_policy):
        """Test recovery action when case is already recovered."""
        from app.services.recovery_service import execute_recovery_action

        risk_case.status = CaseStatus.RECOVERED.value
        db_session.commit()

        result = execute_recovery_action(
            db=db_session,
            case_id="CASE-TEST-001",
            action_type="CREATE_PAYMENT_LINK",
            confidence=0.85,
            recovery_probability=0.75,
        )

        assert result["success"] is False
        assert "already recovered" in result["error"]

    def test_execute_recovery_policy_blocked(self, db_session, risk_case, active_policy):
        """Test recovery action blocked by policy engine."""
        from app.services.recovery_service import execute_recovery_action

        # Set amount above limit
        risk_case.amount = 50000.0  # Above 10000 limit
        db_session.commit()

        result = execute_recovery_action(
            db=db_session,
            case_id="CASE-TEST-001",
            action_type="CREATE_PAYMENT_LINK",
            confidence=0.85,
            recovery_probability=0.75,
        )

        assert result["success"] is False
        assert result["action_executed"] is False
        # Amounts above the autonomous limit require human review, i.e. ESCALATED
        assert result["escalated"] is True
        assert "Policy escalated" in result["error"]

        # Verify the escalated recovery action was created and the case is visible
        case = db_session.query(RevenueRiskCase).filter(
            RevenueRiskCase.case_id == "CASE-TEST-001"
        ).first()
        assert case.status == CaseStatus.ESCALATED.value
        action = db_session.query(RecoveryAction).filter(
            RecoveryAction.case_id == case.id
        ).first()
        assert action is not None
        assert action.policy_result == "ESCALATED"
        assert action.execution_status == "ESCALATED"

    def test_execute_recovery_send_reminder(self, db_session, risk_case, active_policy):
        """Test SEND_PAYMENT_REMINDER action."""
        from app.services.recovery_service import execute_recovery_action

        result = execute_recovery_action(
            db=db_session,
            case_id="CASE-TEST-001",
            action_type="SEND_PAYMENT_REMINDER",
            confidence=0.75,
            recovery_probability=0.65,
            reason="Gentle reminder",
        )

        assert result["success"] is True
        assert result["action_executed"] is True

    def test_process_payment_success(self, db_session, risk_case):
        """Test processing a successful payment from webhook."""
        from app.services.recovery_service import process_payment_success

        result = process_payment_success(
            db=db_session,
            case_id="CASE-TEST-001",
            payment_id="pay_success123",
            amount=2500.0,
        )

        assert result["success"] is True
        assert result["status"] == "RECOVERED"
        assert result["recovered_amount"] == 2500.0

        # Verify case updated
        case = db_session.query(RevenueRiskCase).filter(
            RevenueRiskCase.case_id == "CASE-TEST-001"
        ).first()
        assert case.status == CaseStatus.RECOVERED.value
        assert case.recovered_amount == 2500.0

        # Verify audit event
        audit = db_session.query(AuditEvent).filter(
            AuditEvent.case_id == case.id,
            AuditEvent.actor == "webhook",
        ).first()
        assert audit is not None
        assert "PAYMENT_SUCCESS" in audit.decision

    def test_process_payment_failure(self, db_session, risk_case):
        """Test processing a failed payment from webhook."""
        from app.services.recovery_service import process_payment_failure

        result = process_payment_failure(
            db=db_session,
            case_id="CASE-TEST-001",
            payment_id="pay_fail456",
            failure_reason="card_declined",
        )

        assert result["success"] is True
        assert result["status"] == "OPEN"

        # Verify case reopened
        case = db_session.query(RevenueRiskCase).filter(
            RevenueRiskCase.case_id == "CASE-TEST-001"
        ).first()
        assert case.status == CaseStatus.OPEN.value

    def test_process_payment_success_case_not_found(self, db_session):
        """Test processing payment success for nonexistent case."""
        from app.services.recovery_service import process_payment_success

        result = process_payment_success(
            db=db_session,
            case_id="NONEXISTENT",
            payment_id="pay_123",
            amount=1000.0,
        )

        assert result["success"] is False
        assert "not found" in result["error"]


# ============================================================================
# Webhook Handler Tests
# ============================================================================

class TestWebhookHandler:
    """Tests for the Razorpay webhook endpoint."""

    def test_webhook_invalid_json(self, client):
        """Test webhook with invalid JSON payload."""
        response = client.post(
            "/api/webhooks/razorpay",
            content="not json",
            headers={"Content-Type": "application/json"},
        )
        assert response.status_code == 400

    def test_webhook_invalid_signature(self, client):
        """Test webhook with invalid signature (when secret is configured)."""
        import json

        payload = {
            "event": "payment.captured",
            "payload": {
                "payment": {
                    "entity": {
                        "id": "pay_test",
                        "amount": 100000,
                        "notes": {"reference_id": "CASE-001"},
                    }
                }
            }
        }

        with patch("app.api.webhooks.verify_webhook_signature", return_value=False):
            response = client.post(
                "/api/webhooks/razorpay",
                content=json.dumps(payload),
                headers={
                    "Content-Type": "application/json",
                    "X-Razorpay-Signature": "invalid",
                },
            )

        assert response.status_code == 401

    def test_webhook_success_event(self, client, db_session, risk_case):
        """Test webhook processing for successful payment event."""
        import json

        payload = {
            "event": "payment.captured",
            "payload": {
                "payment": {
                    "entity": {
                        "id": "pay_captured123",
                        "amount": 250000,
                        "currency": "INR",
                        "status": "captured",
                        "notes": {"reference_id": "CASE-TEST-001"},
                    }
                }
            }
        }

        with patch("app.api.webhooks.verify_webhook_signature", return_value=True):
            response = client.post(
                "/api/webhooks/razorpay",
                content=json.dumps(payload),
                headers={
                    "Content-Type": "application/json",
                    "X-Razorpay-Signature": "valid",
                },
            )

        assert response.status_code == 200
        data = response.json()
        assert data["success"] is True
        assert data["result"]["success"] is True
        assert data["result"]["status"] == "RECOVERED"

    def test_webhook_failure_event(self, client, db_session, risk_case):
        """Test webhook processing for failed payment event."""
        import json

        payload = {
            "event": "payment.failed",
            "payload": {
                "payment": {
                    "entity": {
                        "id": "pay_failed456",
                        "amount": 100000,
                        "currency": "INR",
                        "status": "failed",
                        "notes": {"reference_id": "CASE-TEST-001"},
                    }
                }
            }
        }

        with patch("app.api.webhooks.verify_webhook_signature", return_value=True):
            response = client.post(
                "/api/webhooks/razorpay",
                content=json.dumps(payload),
                headers={
                    "Content-Type": "application/json",
                    "X-Razorpay-Signature": "valid",
                },
            )

        assert response.status_code == 200
        data = response.json()
        assert data["success"] is True
        assert data["result"]["status"] == "OPEN"

    def test_webhook_duplicate_event(self, client, db_session, risk_case):
        """Test duplicate webhook event is ignored."""
        import json
        from app.api.webhooks import _processed_events

        payload = {
            "event": "payment.captured",
            "payload": {
                "payment": {
                    "entity": {
                        "id": "pay_dup789",
                        "amount": 250000,
                        "status": "captured",
                        "created_at": 1690000000,
                        "notes": {"reference_id": "CASE-TEST-001"},
                    }
                }
            }
        }

        with patch("app.api.webhooks.verify_webhook_signature", return_value=True):
            # First request
            response1 = client.post(
                "/api/webhooks/razorpay",
                content=json.dumps(payload),
                headers={
                    "Content-Type": "application/json",
                    "X-Razorpay-Signature": "valid",
                },
            )
            assert response1.status_code == 200

            # Duplicate request
            response2 = client.post(
                "/api/webhooks/razorpay",
                content=json.dumps(payload),
                headers={
                    "Content-Type": "application/json",
                    "X-Razorpay-Signature": "valid",
                },
            )
            assert response2.status_code == 200
            data2 = response2.json()
            assert "Duplicate" in data2["message"]

    def test_webhook_no_reference_id(self, client, db_session, risk_case):
        """Test webhook with no reference_id in notes."""
        import json

        payload = {
            "event": "payment.captured",
            "payload": {
                "payment": {
                    "entity": {
                        "id": "pay_noref",
                        "amount": 100000,
                        "status": "captured",
                        "notes": {},
                    }
                }
            }
        }

        with patch("app.api.webhooks.verify_webhook_signature", return_value=True):
            response = client.post(
                "/api/webhooks/razorpay",
                content=json.dumps(payload),
                headers={
                    "Content-Type": "application/json",
                    "X-Razorpay-Signature": "valid",
                },
            )

        assert response.status_code == 200
        data = response.json()
        assert data["success"] is True
        assert "no case reference found" in data["message"]

    def test_webhook_unknown_event_type(self, client, db_session, risk_case):
        """Test webhook with unhandled event type."""
        import json

        payload = {
            "event": "refund.created",
            "payload": {
                "payment": {
                    "entity": {
                        "id": "pay_unknown",
                        "amount": 100000,
                        "notes": {},
                    }
                }
            }
        }

        with patch("app.api.webhooks.verify_webhook_signature", return_value=True):
            response = client.post(
                "/api/webhooks/razorpay",
                content=json.dumps(payload),
                headers={
                    "Content-Type": "application/json",
                    "X-Razorpay-Signature": "valid",
                },
            )

        assert response.status_code == 200
        data = response.json()
        assert data["success"] is True
        assert "refund.created" in data["message"]


# ============================================================================
# End-to-End Recovery Flow Tests
# ============================================================================

class TestEndToEndRecovery:
    """End-to-end tests for the full recovery flow."""

    def test_full_recovery_flow(self, db_session, risk_case, active_policy):
        """Test complete flow: policy approved -> recovery action -> payment link -> webhook success."""
        from app.services.recovery_service import (
            execute_recovery_action,
            process_payment_success,
        )

        # Step 1: Execute recovery action (creates payment link)
        exec_result = execute_recovery_action(
            db=db_session,
            case_id="CASE-TEST-001",
            action_type="CREATE_PAYMENT_LINK",
            confidence=0.85,
            recovery_probability=0.75,
            reason="AI recommended",
        )
        assert exec_result["success"] is True
        payment_link_id = exec_result["payment_link_id"]

        # Step 2: Simulate webhook success
        webhook_result = process_payment_success(
            db=db_session,
            case_id="CASE-TEST-001",
            payment_id="pay_success_e2e",
            amount=2500.0,
        )
        assert webhook_result["success"] is True
        assert webhook_result["status"] == "RECOVERED"
        assert webhook_result["recovered_amount"] == 2500.0

        # Step 3: Verify final state
        case = db_session.query(RevenueRiskCase).filter(
            RevenueRiskCase.case_id == "CASE-TEST-001"
        ).first()
        assert case.status == CaseStatus.RECOVERED.value
        assert case.recovered_amount == 2500.0
        assert case.attempt_count == 1

        # Step 4: Verify audit trail
        audits = db_session.query(AuditEvent).filter(
            AuditEvent.case_id == case.id
        ).all()
        event_types = [a.event_type for a in audits]
        assert AuditEventType.ACTION_EXECUTED.value in event_types

    def test_policy_blocks_action_prevents_payment(self, db_session, risk_case, active_policy):
        """Test that policy blocking prevents payment execution."""
        from app.services.recovery_service import execute_recovery_action

        # Set low confidence to trigger policy block
        result = execute_recovery_action(
            db=db_session,
            case_id="CASE-TEST-001",
            action_type="CREATE_PAYMENT_LINK",
            confidence=0.1,  # Below 0.3 minimum
            recovery_probability=0.05,  # Below 0.2 minimum
        )

        # Verify the action was escalated (low confidence needs human review)
        assert result["success"] is False
        assert result["action_executed"] is False
        assert result["escalated"] is True
        assert "Policy escalated" in result["error"]

        # Verify no payment link was created
        assert "payment_link_id" not in result

        # The case must be visibly escalated for human review, not silently blocked
        case = db_session.query(RevenueRiskCase).filter(
            RevenueRiskCase.case_id == "CASE-TEST-001"
        ).first()
        assert case.status == CaseStatus.ESCALATED.value

        # Verify audit events: policy engine records ESCALATED, recovery service records the escalation
        policy_audit = db_session.query(AuditEvent).filter(
            AuditEvent.case_id == case.id,
            AuditEvent.event_type == AuditEventType.POLICY_CHECKED.value,
        ).first()
        assert policy_audit is not None
        assert policy_audit.result == "ESCALATED"

        recovery_audit = db_session.query(AuditEvent).filter(
            AuditEvent.case_id == case.id,
            AuditEvent.event_type == AuditEventType.CASE_ESCALATED.value,
        ).first()
        assert recovery_audit is not None
        assert recovery_audit.actor == "recovery_service"
        assert recovery_audit.result == "ESCALATED"

    def test_retry_limit_enforcement(self, db_session, risk_case, active_policy):
        """Test that the retry limit (max_retries) is enforced by policy."""
        from app.services.recovery_service import execute_recovery_action

        # max_retries=2: two RETRY actions are allowed.
        for i in range(2):
            result = execute_recovery_action(
                db=db_session,
                case_id="CASE-TEST-001",
                action_type="RETRY",
                confidence=0.85,
                recovery_probability=0.75,
                reason=f"Retry {i+1}",
            )
            assert result["success"] is True, f"Retry {i+1} should succeed"

        # The third retry is blocked by MAX_RETRIES (not by the total attempt cap),
        # which was previously impossible because retries were recorded as
        # CREATE_PAYMENT_LINK and the retry counter therefore always saw zero.
        result = execute_recovery_action(
            db=db_session,
            case_id="CASE-TEST-001",
            action_type="RETRY",
            confidence=0.85,
            recovery_probability=0.75,
            reason="Retry 3 - should be blocked",
        )

        assert result["success"] is False
        assert result["action_executed"] is False
        assert "Policy blocked" in result["error"]
        assert "Retry limit reached" in result["error"]

        # RETRY actions must be recorded as RETRY for the policy rule to see them.
        actions = db_session.query(RecoveryAction).filter(
            RecoveryAction.case_id == risk_case.id
        ).all()
        executed_labels = {
            a.action_type for a in actions if a.execution_status == "SUCCESS"
        }
        assert executed_labels == {"RETRY"}


# ============================================================================
# Audit Trail Tests
# ============================================================================

class TestAuditTrail:
    """Tests for audit event creation."""

    def test_recovery_creates_audit_event(self, db_session, risk_case, active_policy):
        """Test that recovery action creates proper audit events."""
        from app.services.recovery_service import execute_recovery_action

        execute_recovery_action(
            db=db_session,
            case_id="CASE-TEST-001",
            action_type="CREATE_PAYMENT_LINK",
            confidence=0.85,
            recovery_probability=0.75,
            reason="Audit test",
        )

        case = db_session.query(RevenueRiskCase).filter(
            RevenueRiskCase.case_id == "CASE-TEST-001"
        ).first()

        audits = db_session.query(AuditEvent).filter(
            AuditEvent.case_id == case.id
        ).all()

        assert len(audits) >= 1

        # Check for recovery action audit
        action_audit = next(
            (a for a in audits if a.event_type == AuditEventType.ACTION_EXECUTED.value),
            None,
        )
        assert action_audit is not None
        assert action_audit.actor == "recovery_service"
        assert action_audit.action == "CREATE_PAYMENT_LINK"
        assert action_audit.result == "SUCCESS"
        assert action_audit.metadata_ is not None
        assert "payment_link_id" in action_audit.metadata_

    def test_policy_blocked_creates_audit_event(self, db_session, risk_case, active_policy):
        """Test that policy block creates audit event."""
        from app.services.recovery_service import execute_recovery_action

        risk_case.amount = 50000.0  # Above limit
        db_session.commit()

        result = execute_recovery_action(
            db=db_session,
            case_id="CASE-TEST-001",
            action_type="CREATE_PAYMENT_LINK",
            confidence=0.85,
            recovery_probability=0.75,
        )

        # The action should be escalated (amount exceeds limit), not blocked
        assert result["success"] is False
        assert result["escalated"] is True

        case = db_session.query(RevenueRiskCase).filter(
            RevenueRiskCase.case_id == "CASE-TEST-001"
        ).first()
        assert case.status == CaseStatus.ESCALATED.value

        # Check for both the escalated recovery action and its audit event
        action = db_session.query(RecoveryAction).filter(
            RecoveryAction.case_id == case.id
        ).first()
        assert action.policy_result == "ESCALATED"
        assert action.execution_status == "ESCALATED"

        # Policy engine also creates its own audit event
        policy_audit = db_session.query(AuditEvent).filter(
            AuditEvent.case_id == case.id,
            AuditEvent.event_type == AuditEventType.POLICY_CHECKED.value,
        ).first()
        assert policy_audit is not None

        recovery_audit = db_session.query(AuditEvent).filter(
            AuditEvent.case_id == case.id,
            AuditEvent.event_type == AuditEventType.CASE_ESCALATED.value,
        ).first()
        assert recovery_audit is not None
        assert recovery_audit.result == "ESCALATED"

    def test_webhook_creates_audit_event(self, db_session, risk_case):
        """Test that webhook processing creates audit events."""
        from app.services.recovery_service import process_payment_success

        process_payment_success(
            db=db_session,
            case_id="CASE-TEST-001",
            payment_id="pay_audit_test",
            amount=2500.0,
        )

        case = db_session.query(RevenueRiskCase).filter(
            RevenueRiskCase.case_id == "CASE-TEST-001"
        ).first()

        audit = db_session.query(AuditEvent).filter(
            AuditEvent.case_id == case.id,
            AuditEvent.actor == "webhook",
        ).first()

        assert audit is not None
        assert audit.decision == "PAYMENT_SUCCESS"
        assert audit.metadata_ is not None
        assert audit.metadata_["payment_id"] == "pay_audit_test"
        assert audit.metadata_["amount"] == 2500.0


# ============================================================================
# Webhook signature, idempotency and state-transition guards
# ============================================================================


def _captured_payload(payment_id: str, amount_paise: int = 250000) -> dict:
    """Build a minimal payment.captured webhook payload for the test case."""
    return {
        "event": "payment.captured",
        "payload": {
            "payment": {
                "entity": {
                    "id": payment_id,
                    "amount": amount_paise,
                    "currency": "INR",
                    "status": "captured",
                    "notes": {"reference_id": "CASE-TEST-001"},
                }
            }
        },
    }


class TestWebhookSecurityAndIdempotency:
    """Regression tests for webhook fail-closed behaviour, retry safety and state guards."""

    def test_webhook_rejected_when_no_secret_configured(self, client, db_session, risk_case):
        """
        An unsigned webhook must be rejected (401) when no webhook secret is
        configured - otherwise anyone could mark a case recovered.
        """
        import json

        payload = _captured_payload("pay_unsigned_1")

        with patch("app.services.razorpay_service.settings") as mock_settings:
            mock_settings.RAZORPAY_WEBHOOK_SECRET = ""
            response = client.post(
                "/api/webhooks/razorpay",
                content=json.dumps(payload),
                headers={"Content-Type": "application/json"},
            )

        assert response.status_code == 401

        case = db_session.query(RevenueRiskCase).filter(
            RevenueRiskCase.case_id == "CASE-TEST-001"
        ).first()
        assert case.status == "OPEN"
        assert case.recovered_amount == 0.0

    def test_failed_delivery_remains_retryable(self, client, db_session, risk_case):
        """
        Regression test: an event must only be marked as processed AFTER it has
        been handled.  Previously a 500 response still recorded the event, so the
        provider's retry was discarded as a duplicate and the payment was lost.
        """
        import json
        import app.api.webhooks as webhooks

        webhooks._processed_events.clear()
        payload = _captured_payload("pay_retry_1")

        with patch("app.api.webhooks.verify_webhook_signature", return_value=True), \
             patch(
                 "app.api.webhooks.process_payment_success",
                 side_effect=RuntimeError("transient database failure"),
             ):
            first = client.post(
                "/api/webhooks/razorpay",
                content=json.dumps(payload),
                headers={
                    "Content-Type": "application/json",
                    "X-Razorpay-Signature": "valid",
                    "X-Razorpay-Event-Id": "evt_retry_1",
                },
            )
        assert first.status_code == 500
        assert "evt_retry_1" not in webhooks._processed_events

        with patch("app.api.webhooks.verify_webhook_signature", return_value=True):
            second = client.post(
                "/api/webhooks/razorpay",
                content=json.dumps(payload),
                headers={
                    "Content-Type": "application/json",
                    "X-Razorpay-Signature": "valid",
                    "X-Razorpay-Event-Id": "evt_retry_1",
                },
            )

        assert second.status_code == 200
        assert "Duplicate" not in second.json()["message"]
        db_session.refresh(risk_case)
        assert risk_case.status == CaseStatus.RECOVERED.value
        assert risk_case.recovered_amount == 2500.0

    def test_provider_event_id_header_is_honoured(self, client, db_session, risk_case):
        """Two distinct deliveries of the same payload are not collapsed."""
        import json
        import app.api.webhooks as webhooks

        webhooks._processed_events.clear()
        payload = _captured_payload("pay_event_id_1")

        with patch("app.api.webhooks.verify_webhook_signature", return_value=True):
            first = client.post(
                "/api/webhooks/razorpay",
                content=json.dumps(payload),
                headers={
                    "Content-Type": "application/json",
                    "X-Razorpay-Signature": "valid",
                    "X-Razorpay-Event-Id": "evt_distinct_1",
                },
            )
            second = client.post(
                "/api/webhooks/razorpay",
                content=json.dumps(payload),
                headers={
                    "Content-Type": "application/json",
                    "X-Razorpay-Signature": "valid",
                    "X-Razorpay-Event-Id": "evt_distinct_2",
                },
            )

        assert first.json()["message"] == "Payment success processed"
        # The second delivery is a different provider event: it reaches the
        # handler (and is then ignored by the already-recovered guard), rather
        # than being dropped as a duplicate of the first.
        assert second.json()["message"] == "Payment success processed"
        assert second.json()["result"]["duplicate"] is True

        # The same event id delivered twice is still deduplicated.
        with patch("app.api.webhooks.verify_webhook_signature", return_value=True):
            third = client.post(
                "/api/webhooks/razorpay",
                content=json.dumps(payload),
                headers={
                    "Content-Type": "application/json",
                    "X-Razorpay-Signature": "valid",
                    "X-Razorpay-Event-Id": "evt_distinct_2",
                },
            )
        assert "Duplicate" in third.json()["message"]

    def test_payment_link_paid_maps_to_case(self, client, db_session, risk_case):
        """
        Regression test: payment_link.paid carries the case reference on the
        payment_link entity, so it must mark the case recovered.
        """
        import json
        import app.api.webhooks as webhooks

        webhooks._processed_events.clear()
        payload = {
            "event": "payment_link.paid",
            "payload": {
                "payment_link": {
                    "entity": {
                        "id": "plink_paid_1",
                        "reference_id": "CASE-TEST-001",
                        "amount": 250000,
                        "status": "paid",
                        "notes": {},
                    }
                },
                "payment": {
                    "entity": {
                        "id": "pay_link_paid_1",
                        "amount": 250000,
                        "status": "captured",
                        "notes": {},
                    }
                },
            },
        }

        with patch("app.api.webhooks.verify_webhook_signature", return_value=True):
            response = client.post(
                "/api/webhooks/razorpay",
                content=json.dumps(payload),
                headers={
                    "Content-Type": "application/json",
                    "X-Razorpay-Signature": "valid",
                },
            )

        assert response.status_code == 200
        assert response.json()["result"]["status"] == "RECOVERED"

        db_session.refresh(risk_case)
        assert risk_case.status == CaseStatus.RECOVERED.value
        assert risk_case.recovered_amount == 2500.0

    def test_late_failure_does_not_reopen_recovered_case(self, client, db_session, risk_case):
        """A failure event arriving after recovery must not reopen the case."""
        import json
        import app.api.webhooks as webhooks

        webhooks._processed_events.clear()
        risk_case.status = CaseStatus.RECOVERED.value
        risk_case.recovered_amount = 2500.0
        db_session.commit()

        payload = {
            "event": "payment.failed",
            "payload": {
                "payment": {
                    "entity": {
                        "id": "pay_late_fail_1",
                        "amount": 250000,
                        "status": "failed",
                        "notes": {"reference_id": "CASE-TEST-001"},
                    }
                }
            },
        }

        with patch("app.api.webhooks.verify_webhook_signature", return_value=True):
            response = client.post(
                "/api/webhooks/razorpay",
                content=json.dumps(payload),
                headers={
                    "Content-Type": "application/json",
                    "X-Razorpay-Signature": "valid",
                },
            )

        assert response.status_code == 200
        assert response.json()["result"]["ignored"] is True

        db_session.refresh(risk_case)
        assert risk_case.status == CaseStatus.RECOVERED.value
        assert risk_case.recovered_amount == 2500.0

    def test_success_on_closed_case_is_not_resurrected(self, client, db_session, risk_case):
        """A closed case must not be silently marked recovered by a late capture."""
        import json
        import app.api.webhooks as webhooks

        webhooks._processed_events.clear()
        risk_case.status = CaseStatus.CLOSED.value
        db_session.commit()

        with patch("app.api.webhooks.verify_webhook_signature", return_value=True):
            response = client.post(
                "/api/webhooks/razorpay",
                content=json.dumps(_captured_payload("pay_closed_1")),
                headers={
                    "Content-Type": "application/json",
                    "X-Razorpay-Signature": "valid",
                },
            )

        assert response.status_code == 200
        result = response.json()["result"]
        assert result["success"] is False
        assert "manual reconciliation" in result["error"]

        db_session.refresh(risk_case)
        assert risk_case.status == CaseStatus.CLOSED.value
        assert risk_case.recovered_amount == 0.0

        audit = db_session.query(AuditEvent).filter(
            AuditEvent.case_id == risk_case.id,
            AuditEvent.decision == "PAYMENT_SUCCESS_REJECTED",
        ).first()
        assert audit is not None

    def test_duplicate_success_does_not_overwrite_recovered_amount(
        self, client, db_session, risk_case
    ):
        """A repeat capture with a different amount must not overwrite the record."""
        import json
        import app.api.webhooks as webhooks

        webhooks._processed_events.clear()
        risk_case.status = CaseStatus.RECOVERED.value
        risk_case.recovered_amount = 2500.0
        db_session.commit()

        with patch("app.api.webhooks.verify_webhook_signature", return_value=True):
            response = client.post(
                "/api/webhooks/razorpay",
                content=json.dumps(_captured_payload("pay_mismatch_1", amount_paise=99999900)),
                headers={
                    "Content-Type": "application/json",
                    "X-Razorpay-Signature": "valid",
                },
            )

        assert response.status_code == 200
        result = response.json()["result"]
        assert result["success"] is False
        assert result["recovered_amount"] == 2500.0

        db_session.refresh(risk_case)
        assert risk_case.recovered_amount == 2500.0
        assert risk_case.status == CaseStatus.RECOVERED.value

        audit = db_session.query(AuditEvent).filter(
            AuditEvent.case_id == risk_case.id,
            AuditEvent.decision == "PAYMENT_SUCCESS_AMOUNT_MISMATCH",
        ).first()
        assert audit is not None


class TestCapturedAmountVerification:
    """
    A success webhook only settles a case when it carries a positive amount that
    matches the amount that was due.

    Regression tests for two confirmed gaps:
      1. a capture event with a missing/zero amount marked the case RECOVERED
         with recovered_amount 0.0, and
      2. a partial capture (e.g. 100 of 2500) settled the case, terminating
         recovery on the remaining balance.
    Both previously stopped all further recovery on an unpaid debt.
    """

    def test_non_positive_capture_does_not_settle_case(self, client, db_session, risk_case):
        """A zero-amount capture must not mark the case recovered."""
        import json
        import app.api.webhooks as webhooks

        webhooks._processed_events.clear()

        with patch("app.api.webhooks.verify_webhook_signature", return_value=True):
            response = client.post(
                "/api/webhooks/razorpay",
                content=json.dumps(_captured_payload("pay_zero_1", amount_paise=0)),
                headers={
                    "Content-Type": "application/json",
                    "X-Razorpay-Signature": "valid",
                },
            )

        assert response.status_code == 200
        result = response.json()["result"]
        assert result["success"] is False
        assert result["rejected"] is True

        db_session.refresh(risk_case)
        assert risk_case.status == CaseStatus.OPEN.value
        assert risk_case.recovered_amount == 0.0

        audit = db_session.query(AuditEvent).filter(
            AuditEvent.case_id == risk_case.id,
            AuditEvent.decision == "PAYMENT_SUCCESS_AMOUNT_UNVERIFIED",
        ).first()
        assert audit is not None
        assert audit.result == "REJECTED"

        # The unverified event must not consume the case: a valid capture for the
        # full amount still settles it.
        with patch("app.api.webhooks.verify_webhook_signature", return_value=True):
            retry = client.post(
                "/api/webhooks/razorpay",
                content=json.dumps(_captured_payload("pay_zero_2")),
                headers={
                    "Content-Type": "application/json",
                    "X-Razorpay-Signature": "valid",
                },
            )

        assert retry.json()["result"]["status"] == "RECOVERED"
        db_session.refresh(risk_case)
        assert risk_case.status == CaseStatus.RECOVERED.value
        assert risk_case.recovered_amount == 2500.0

    def test_partial_capture_requires_manual_reconciliation(self, client, db_session, risk_case):
        """A capture for less than the case amount must not settle the case."""
        import json
        import app.api.webhooks as webhooks

        webhooks._processed_events.clear()

        with patch("app.api.webhooks.verify_webhook_signature", return_value=True):
            response = client.post(
                "/api/webhooks/razorpay",
                content=json.dumps(_captured_payload("pay_partial_1", amount_paise=10000)),
                headers={
                    "Content-Type": "application/json",
                    "X-Razorpay-Signature": "valid",
                },
            )

        assert response.status_code == 200
        result = response.json()["result"]
        assert result["success"] is False
        assert result["manual_reconciliation_required"] is True

        db_session.refresh(risk_case)
        assert risk_case.status == CaseStatus.OPEN.value
        assert risk_case.recovered_amount == 0.0

        audit = db_session.query(AuditEvent).filter(
            AuditEvent.case_id == risk_case.id,
            AuditEvent.decision == "PAYMENT_SUCCESS_AMOUNT_MISMATCH",
        ).first()
        assert audit is not None
        assert audit.metadata_["amount"] == 100.0
        assert audit.metadata_["case_amount"] == 2500.0

        # Recovery on the outstanding balance is still possible.
        with patch("app.api.webhooks.verify_webhook_signature", return_value=True):
            retry = client.post(
                "/api/webhooks/razorpay",
                content=json.dumps(_captured_payload("pay_partial_2")),
                headers={
                    "Content-Type": "application/json",
                    "X-Razorpay-Signature": "valid",
                },
            )

        assert retry.json()["result"]["status"] == "RECOVERED"

    def test_wrong_signature_changes_no_state(self, client, db_session, risk_case, monkeypatch):
        """
        With a webhook secret configured, a delivery signed with the wrong key
        must change no payment or recovery state - and must not be recorded as
        processed, so the provider's legitimate retry is still handled.
        """
        import hashlib
        import hmac
        import json

        import app.api.webhooks as webhooks
        import app.services.razorpay_service as razorpay_service

        secret = "test_webhook_secret"
        monkeypatch.setattr(
            razorpay_service.settings, "RAZORPAY_WEBHOOK_SECRET", secret, raising=False
        )
        webhooks._processed_events.clear()

        body = json.dumps(_captured_payload("pay_badsig_1"))
        response = client.post(
            "/api/webhooks/razorpay",
            content=body,
            headers={
                "Content-Type": "application/json",
                "X-Razorpay-Signature": "0" * 64,
                "X-Razorpay-Event-Id": "evt_badsig_1",
            },
        )

        assert response.status_code == 401

        db_session.refresh(risk_case)
        assert risk_case.status == CaseStatus.OPEN.value
        assert risk_case.recovered_amount == 0.0
        assert db_session.query(AuditEvent).filter(
            AuditEvent.case_id == risk_case.id
        ).count() == 0
        assert "evt_badsig_1" not in webhooks._processed_events

        # The same delivery, correctly signed, is still processed.
        signature = hmac.new(
            secret.encode("utf-8"), body.encode("utf-8"), hashlib.sha256
        ).hexdigest()
        accepted = client.post(
            "/api/webhooks/razorpay",
            content=body,
            headers={
                "Content-Type": "application/json",
                "X-Razorpay-Signature": signature,
                "X-Razorpay-Event-Id": "evt_badsig_1",
            },
        )

        assert accepted.status_code == 200
        assert accepted.json()["result"]["status"] == "RECOVERED"
        db_session.refresh(risk_case)
        assert risk_case.status == CaseStatus.RECOVERED.value
        assert risk_case.recovered_amount == 2500.0
