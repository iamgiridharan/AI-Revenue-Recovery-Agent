import pytest
from datetime import datetime, timezone
from app.models import (
    Customer,
    Transaction,
    RevenueRiskCase,
    RecoveryAction,
    AuditEvent,
    CaseStatus,
    CasePriority,
    TransactionStatus,
    RecoveryActionType,
    RecoveryOutcome,
    AuditEventType,
)
from tests.conftest import db_session


class TestEnums:
    """Test controlled enum values."""

    def test_case_status_values(self):
        """Verify all case status values are defined."""
        expected = {"OPEN", "IN_PROGRESS", "RECOVERY_ATTEMPTED", "RECOVERED", "FAILED", "ESCALATED", "CLOSED"}
        actual = {status.value for status in CaseStatus}
        assert actual == expected

    def test_case_priority_values(self):
        """Verify all priority values are defined."""
        expected = {"LOW", "MEDIUM", "HIGH", "CRITICAL"}
        actual = {p.value for p in CasePriority}
        assert actual == expected

    def test_transaction_status_values(self):
        """Verify all transaction status values are defined."""
        expected = {"PENDING", "SUCCESS", "FAILED", "REFUNDED", "CANCELLED"}
        actual = {s.value for s in TransactionStatus}
        assert actual == expected

    def test_recovery_action_type_values(self):
        """Verify all recovery action type values are defined."""
        expected = {
            "NO_ACTION", "RETRY", "CREATE_PAYMENT_LINK",
            "SEND_PAYMENT_REMINDER", "WAIT_AND_RETRY",
            "ESCALATE_TO_HUMAN", "MARK_UNRECOVERABLE"
        }
        actual = {a.value for a in RecoveryActionType}
        assert actual == expected

    def test_recovery_outcome_values(self):
        """Verify all recovery outcome values are defined."""
        expected = {"SUCCESS", "FAILED", "PENDING", "BLOCKED_BY_POLICY", "ESCALATED", "EXPIRED"}
        actual = {o.value for o in RecoveryOutcome}
        assert actual == expected

    def test_audit_event_type_values(self):
        """Verify all audit event type values are defined."""
        expected = {
            "CASE_CREATED", "CASE_UPDATED", "RISK_ASSESSED",
            "DIAGNOSIS_COMPLETED", "ACTION_RECOMMENDED",
            "POLICY_CHECKED", "ACTION_EXECUTED", "ACTION_FAILED",
            "CASE_ESCALATED", "CASE_CLOSED"
        }
        actual = {e.value for e in AuditEventType}
        assert actual == expected


class TestCustomerModel:
    """Test Customer model creation and fields."""

    def test_create_customer(self, db_session):
        """Test creating a basic customer."""
        customer = Customer(
            customer_id="CUST-001",
            name="Test Customer",
            email="test@example.com",
            phone="+1234567890",
        )
        db_session.add(customer)
        db_session.commit()

        assert customer.id is not None
        assert customer.customer_id == "CUST-001"
        assert customer.name == "Test Customer"
        assert customer.email == "test@example.com"
        assert customer.total_transactions == 0
        assert customer.successful_transactions == 0
        assert customer.failed_transactions == 0
        assert customer.lifetime_value == 0.0
        assert customer.created_at is not None
        assert customer.updated_at is not None

    def test_customer_unique_constraint(self, db_session):
        """Test that customer_id must be unique."""
        customer1 = Customer(customer_id="CUST-001", name="Customer 1", email="c1@example.com")
        customer2 = Customer(customer_id="CUST-001", name="Customer 2", email="c2@example.com")

        db_session.add(customer1)
        db_session.commit()

        db_session.add(customer2)
        with pytest.raises(Exception):
            db_session.commit()

    def test_customer_repr(self, db_session):
        """Test customer string representation."""
        customer = Customer(customer_id="CUST-001", name="Test Customer", email="test@example.com")
        db_session.add(customer)
        db_session.commit()

        assert "CUST-001" in repr(customer)
        assert "Test Customer" in repr(customer)


class TestTransactionModel:
    """Test Transaction model creation and relationships."""

    def test_create_transaction(self, db_session):
        """Test creating a transaction with customer relationship."""
        customer = Customer(customer_id="CUST-001", name="Test", email="test@example.com")
        db_session.add(customer)
        db_session.commit()

        transaction = Transaction(
            transaction_id="TXN-001",
            customer_id=customer.id,
            amount=99.99,
            currency="INR",
            payment_method="card",
            status="FAILED",
            failure_reason="Insufficient funds",
        )
        db_session.add(transaction)
        db_session.commit()

        assert transaction.id is not None
        assert transaction.transaction_id == "TXN-001"
        assert transaction.amount == 99.99
        assert transaction.status == "FAILED"
        assert transaction.customer_id == customer.id

    def test_transaction_customer_relationship(self, db_session):
        """Test transaction-customer relationship."""
        customer = Customer(customer_id="CUST-001", name="Test", email="test@example.com")
        db_session.add(customer)
        db_session.commit()

        transaction = Transaction(
            transaction_id="TXN-001",
            customer_id=customer.id,
            amount=50.0,
            currency="INR",
            status="SUCCESS",
        )
        db_session.add(transaction)
        db_session.commit()

        assert transaction.customer.customer_id == "CUST-001"

    def test_customer_transactions_relationship(self, db_session):
        """Test customer-transactions relationship."""
        customer = Customer(customer_id="CUST-001", name="Test", email="test@example.com")
        db_session.add(customer)
        db_session.commit()

        txn1 = Transaction(transaction_id="TXN-001", customer_id=customer.id, amount=10.0, currency="INR", status="SUCCESS")
        txn2 = Transaction(transaction_id="TXN-002", customer_id=customer.id, amount=20.0, currency="INR", status="FAILED")
        db_session.add_all([txn1, txn2])
        db_session.commit()

        # Refresh customer to get updated relationships
        db_session.refresh(customer)
        transactions = customer.transactions.all()
        assert len(transactions) == 2


class TestRevenueRiskCaseModel:
    """Test RevenueRiskCase model creation and relationships."""

    def test_create_case(self, db_session):
        """Test creating a revenue risk case."""
        customer = Customer(customer_id="CUST-001", name="Test", email="test@example.com")
        db_session.add(customer)
        db_session.commit()

        transaction = Transaction(
            transaction_id="TXN-001",
            customer_id=customer.id,
            amount=100.0,
            currency="INR",
            status="FAILED",
        )
        db_session.add(transaction)
        db_session.commit()

        case = RevenueRiskCase(
            case_id="CASE-001",
            transaction_id=transaction.id,
            customer_id=customer.id,
            amount=100.0,
            risk_score=0.75,
            recovery_probability=0.6,
            priority="HIGH",
            status="OPEN",
        )
        db_session.add(case)
        db_session.commit()

        assert case.id is not None
        assert case.case_id == "CASE-001"
        assert case.risk_score == 0.75
        assert case.status == "OPEN"
        assert case.priority == "HIGH"

    def test_case_default_values(self, db_session):
        """Test default values for case fields."""
        customer = Customer(customer_id="CUST-001", name="Test", email="test@example.com")
        db_session.add(customer)
        db_session.commit()

        transaction = Transaction(
            transaction_id="TXN-001",
            customer_id=customer.id,
            amount=100.0,
            currency="INR",
            status="FAILED",
        )
        db_session.add(transaction)
        db_session.commit()

        case = RevenueRiskCase(
            case_id="CASE-001",
            transaction_id=transaction.id,
            customer_id=customer.id,
            amount=100.0,
        )
        db_session.add(case)
        db_session.commit()

        assert case.status == "OPEN"
        assert case.priority == "MEDIUM"
        assert case.attempt_count == 0
        assert case.recovered_amount == 0.0

    def test_case_relationships(self, db_session):
        """Test case relationships with transaction and customer."""
        customer = Customer(customer_id="CUST-001", name="Test", email="test@example.com")
        db_session.add(customer)
        db_session.commit()

        transaction = Transaction(
            transaction_id="TXN-001",
            customer_id=customer.id,
            amount=100.0,
            currency="INR",
            status="FAILED",
        )
        db_session.add(transaction)
        db_session.commit()

        case = RevenueRiskCase(
            case_id="CASE-001",
            transaction_id=transaction.id,
            customer_id=customer.id,
            amount=100.0,
        )
        db_session.add(case)
        db_session.commit()

        assert case.transaction.transaction_id == "TXN-001"
        assert case.customer.customer_id == "CUST-001"


class TestRecoveryActionModel:
    """Test RecoveryAction model creation and relationships."""

    def test_create_action(self, db_session):
        """Test creating a recovery action."""
        customer = Customer(customer_id="CUST-001", name="Test", email="test@example.com")
        db_session.add(customer)
        db_session.commit()

        transaction = Transaction(
            transaction_id="TXN-001",
            customer_id=customer.id,
            amount=100.0,
            currency="INR",
            status="FAILED",
        )
        db_session.add(transaction)
        db_session.commit()

        case = RevenueRiskCase(
            case_id="CASE-001",
            transaction_id=transaction.id,
            customer_id=customer.id,
            amount=100.0,
        )
        db_session.add(case)
        db_session.commit()

        action = RecoveryAction(
            case_id=case.id,
            action_type="RETRY",
            reason="High recovery probability",
            confidence=0.8,
            policy_result="APPROVED",
            execution_status="PENDING",
        )
        db_session.add(action)
        db_session.commit()

        assert action.id is not None
        assert action.action_type == "RETRY"
        assert action.confidence == 0.8

    def test_case_recovery_actions_relationship(self, db_session):
        """Test case-recovery_actions relationship."""
        customer = Customer(customer_id="CUST-001", name="Test", email="test@example.com")
        db_session.add(customer)
        db_session.commit()

        transaction = Transaction(
            transaction_id="TXN-001",
            customer_id=customer.id,
            amount=100.0,
            currency="INR",
            status="FAILED",
        )
        db_session.add(transaction)
        db_session.commit()

        case = RevenueRiskCase(
            case_id="CASE-001",
            transaction_id=transaction.id,
            customer_id=customer.id,
            amount=100.0,
        )
        db_session.add(case)
        db_session.commit()

        action1 = RecoveryAction(case_id=case.id, action_type="RETRY", reason="First attempt")
        action2 = RecoveryAction(case_id=case.id, action_type="SEND_PAYMENT_REMINDER", reason="Second attempt")
        db_session.add_all([action1, action2])
        db_session.commit()

        db_session.refresh(case)
        actions = case.recovery_actions.all()
        assert len(actions) == 2


class TestAuditEventModel:
    """Test AuditEvent model creation and relationships."""

    def test_create_audit_event(self, db_session):
        """Test creating an audit event."""
        customer = Customer(customer_id="CUST-001", name="Test", email="test@example.com")
        db_session.add(customer)
        db_session.commit()

        transaction = Transaction(
            transaction_id="TXN-001",
            customer_id=customer.id,
            amount=100.0,
            currency="INR",
            status="FAILED",
        )
        db_session.add(transaction)
        db_session.commit()

        case = RevenueRiskCase(
            case_id="CASE-001",
            transaction_id=transaction.id,
            customer_id=customer.id,
            amount=100.0,
        )
        db_session.add(case)
        db_session.commit()

        event = AuditEvent(
            case_id=case.id,
            event_type="CASE_CREATED",
            actor="system",
            decision="Case created for failed transaction",
            confidence=None,
        )
        db_session.add(event)
        db_session.commit()

        assert event.id is not None
        assert event.event_type == "CASE_CREATED"
        assert event.actor == "system"

    def test_case_audit_events_relationship(self, db_session):
        """Test case-audit_events relationship."""
        customer = Customer(customer_id="CUST-001", name="Test", email="test@example.com")
        db_session.add(customer)
        db_session.commit()

        transaction = Transaction(
            transaction_id="TXN-001",
            customer_id=customer.id,
            amount=100.0,
            currency="INR",
            status="FAILED",
        )
        db_session.add(transaction)
        db_session.commit()

        case = RevenueRiskCase(
            case_id="CASE-001",
            transaction_id=transaction.id,
            customer_id=customer.id,
            amount=100.0,
        )
        db_session.add(case)
        db_session.commit()

        event1 = AuditEvent(case_id=case.id, event_type="CASE_CREATED", actor="system")
        event2 = AuditEvent(case_id=case.id, event_type="RISK_ASSESSED", actor="ml_model")
        db_session.add_all([event1, event2])
        db_session.commit()

        db_session.refresh(case)
        events = case.audit_events.all()
        assert len(events) == 2
