"""
Tests for Phase 7: Dashboard and Audit API endpoints.

Tests cover:
1. Dashboard stats endpoint
2. Chart data endpoints
3. Case detail with relations
4. Audit events listing
5. Edge cases (empty database, invalid case_id)
"""
import pytest
from datetime import datetime, timezone, timedelta

from app.models import (
    Customer,
    Transaction,
    RevenueRiskCase,
    RecoveryAction,
    AuditEvent,
    PolicyConfig,
    CaseStatus,
)


# ============================================================================
# Fixtures
# ============================================================================

@pytest.fixture
def customer(db_session):
    c = Customer(
        customer_id="CUST-DASH-001",
        name="Dashboard User",
        email="dash@test.com",
        total_transactions=5,
        successful_transactions=3,
        failed_transactions=2,
        lifetime_value=3000.0,
    )
    db_session.add(c)
    db_session.commit()
    db_session.refresh(c)
    return c


@pytest.fixture
def transaction(db_session, customer):
    t = Transaction(
        transaction_id="TXN-DASH-001",
        customer_id=customer.id,
        amount=2500.0,
        currency="INR",
        payment_method="card",
        status="FAILED",
        failure_reason="insufficient_funds",
        attempt_count=1,
    )
    db_session.add(t)
    db_session.commit()
    db_session.refresh(t)
    return t


@pytest.fixture
def risk_case(db_session, customer, transaction):
    case = RevenueRiskCase(
        case_id="CASE-DASH-001",
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
def recovered_case(db_session, customer, transaction):
    case = RevenueRiskCase(
        case_id="CASE-DASH-002",
        transaction_id=transaction.id,
        customer_id=customer.id,
        amount=1500.0,
        risk_score=0.40,
        recovery_probability=0.80,
        priority="MEDIUM",
        status="RECOVERED",
        attempt_count=1,
        recovered_amount=1500.0,
    )
    db_session.add(case)
    db_session.commit()
    db_session.refresh(case)
    return case


@pytest.fixture
def policy(db_session):
    existing = db_session.query(PolicyConfig).filter(PolicyConfig.is_active == True).first()
    if existing:
        return existing
    p = PolicyConfig(max_retries=2, max_reminders=2, max_recovery_attempts=3,
                     autonomous_amount_limit=10000.0, minimum_ai_confidence=0.3,
                     minimum_recovery_probability=0.2, case_lifetime_days=7,
                     escalation_threshold=0.7, is_active=True)
    db_session.add(p)
    db_session.commit()
    db_session.refresh(p)
    return p


# ============================================================================
# Dashboard Stats Tests
# ============================================================================

class TestDashboardStats:
    def test_stats_empty_db(self, client):
        """Stats endpoint returns valid response even with empty database."""
        response = client.get("/api/dashboard/stats")
        assert response.status_code == 200
        data = response.json()
        assert data["success"] is True
        assert data["data"]["total_cases"] == 0
        assert data["data"]["total_at_risk"] == 0
        assert data["data"]["total_recovered"] == 0

    def test_stats_with_cases(self, client, risk_case, recovered_case, policy):
        """Stats endpoint returns correct aggregate data."""
        response = client.get("/api/dashboard/stats")
        assert response.status_code == 200
        data = response.json()["data"]
        assert data["total_cases"] == 2
        assert data["total_at_risk"] == 2500.0  # Only OPEN case
        assert data["total_recovered"] == 1500.0
        assert data["awaiting_action"] >= 1
        assert data["total_customers"] >= 1

    def test_stats_status_breakdown(self, client, risk_case, recovered_case, policy):
        """Stats include status breakdown."""
        response = client.get("/api/dashboard/stats")
        data = response.json()["data"]
        assert "status_breakdown" in data
        assert data["status_breakdown"].get("OPEN", 0) >= 1
        assert data["status_breakdown"].get("RECOVERED", 0) >= 1


# ============================================================================
# Chart Endpoints Tests
# ============================================================================

class TestDashboardCharts:
    def test_status_chart(self, client, risk_case, policy):
        """Status chart returns data."""
        response = client.get("/api/dashboard/charts/status")
        assert response.status_code == 200
        data = response.json()
        assert data["success"] is True
        assert isinstance(data["data"], list)

    def test_priority_chart(self, client, risk_case, policy):
        """Priority chart returns data."""
        response = client.get("/api/dashboard/charts/priority")
        assert response.status_code == 200
        data = response.json()
        assert data["success"] is True

    def test_actions_chart(self, client, policy):
        """Actions chart returns data."""
        response = client.get("/api/dashboard/charts/actions")
        assert response.status_code == 200
        data = response.json()
        assert data["success"] is True

    def test_daily_cases_chart(self, client, policy):
        """Daily cases chart returns data."""
        response = client.get("/api/dashboard/charts/daily-cases?days=7")
        assert response.status_code == 200
        data = response.json()
        assert data["success"] is True

    def test_daily_recovered_chart(self, client, policy):
        """Daily recovered chart returns data."""
        response = client.get("/api/dashboard/charts/daily-recovered?days=7")
        assert response.status_code == 200
        data = response.json()
        assert data["success"] is True


# ============================================================================
# Case Detail with Relations Tests
# ============================================================================

class TestCaseDetailFull:
    def test_detail_not_found(self, client):
        """Returns error for nonexistent case."""
        response = client.get("/api/cases/NONEXISTENT/detail")
        assert response.status_code == 200
        data = response.json()
        assert data["success"] is False

    def test_detail_returns_full_data(self, client, risk_case, policy):
        """Returns case with customer, transaction, actions, and audit."""
        response = client.get("/api/cases/CASE-DASH-001/detail")
        assert response.status_code == 200
        data = response.json()
        assert data["success"] is True
        assert "case" in data["data"]
        assert "customer" in data["data"]
        assert "transaction" in data["data"]
        assert "recovery_actions" in data["data"]
        assert "audit_events" in data["data"]
        assert data["data"]["case"]["case_id"] == "CASE-DASH-001"
        assert data["data"]["customer"]["name"] == "Dashboard User"
        assert data["data"]["transaction"]["amount"] == 2500.0

    def test_detail_includes_audit_events(self, client, db_session, risk_case, policy):
        """Detail includes audit events when they exist."""
        audit_event = AuditEvent(
            case_id=risk_case.id,
            event_type="CASE_CREATED",
            actor="system",
            decision="CREATED",
        )
        db_session.add(audit_event)
        db_session.commit()

        response = client.get("/api/cases/CASE-DASH-001/detail")
        data = response.json()
        assert data["success"] is True
        assert len(data["data"]["audit_events"]) >= 1


# ============================================================================
# Audit Events Endpoint Tests
# ============================================================================

class TestAuditEvents:
    def test_audit_empty(self, client):
        """Audit events returns empty list for empty database."""
        response = client.get("/api/audit")
        assert response.status_code == 200
        data = response.json()
        assert data["success"] is True
        assert data["data"] == []
        assert data["pagination"]["total"] == 0

    def test_audit_with_events(self, client, db_session, risk_case, policy):
        """Audit events returns events from database."""
        for i in range(3):
            audit = AuditEvent(
                case_id=risk_case.id,
                event_type="POLICY_CHECKED",
                actor="policy_engine",
                decision="APPROVED",
                reason=f"Check {i}",
            )
            db_session.add(audit)
        db_session.commit()

        response = client.get("/api/audit")
        assert response.status_code == 200
        data = response.json()
        assert data["success"] is True
        assert data["pagination"]["total"] == 3
        assert len(data["data"]) == 3

    def test_audit_pagination(self, client, db_session, risk_case, policy):
        """Audit events supports pagination."""
        for i in range(5):
            db_session.add(AuditEvent(
                case_id=risk_case.id,
                event_type="ACTION_EXECUTED",
                actor="recovery_service",
                decision="APPROVED",
            ))
        db_session.commit()

        response = client.get("/api/audit?page=1&page_size=2")
        data = response.json()
        assert data["pagination"]["total"] == 5
        assert data["pagination"]["total_pages"] == 3
        assert len(data["data"]) == 2

    def test_audit_filter_by_actor(self, client, db_session, risk_case, policy):
        """Audit events supports actor filter."""
        db_session.add(AuditEvent(
            case_id=risk_case.id, event_type="POLICY_CHECKED",
            actor="policy_engine", decision="APPROVED",
        ))
        db_session.add(AuditEvent(
            case_id=risk_case.id, event_type="ACTION_EXECUTED",
            actor="recovery_service", decision="APPROVED",
        ))
        db_session.commit()

        response = client.get("/api/audit?actor=policy_engine")
        data = response.json()
        assert data["pagination"]["total"] == 1
        assert data["data"][0]["actor"] == "policy_engine"

    def test_audit_filter_by_event_type(self, client, db_session, risk_case, policy):
        """Audit events supports event_type filter."""
        db_session.add(AuditEvent(
            case_id=risk_case.id, event_type="POLICY_CHECKED",
            actor="policy_engine", decision="APPROVED",
        ))
        db_session.add(AuditEvent(
            case_id=risk_case.id, event_type="ACTION_EXECUTED",
            actor="recovery_service", decision="APPROVED",
        ))
        db_session.commit()

        response = client.get("/api/audit?event_type=ACTION_EXECUTED")
        data = response.json()
        assert data["pagination"]["total"] == 1
        assert data["data"][0]["event_type"] == "ACTION_EXECUTED"

    def test_audit_events_have_case_id_string(self, client, db_session, risk_case, policy):
        """Audit events include case_id as string, not integer."""
        db_session.add(AuditEvent(
            case_id=risk_case.id, event_type="POLICY_CHECKED",
            actor="policy_engine", decision="APPROVED",
        ))
        db_session.commit()

        response = client.get("/api/audit")
        data = response.json()
        assert len(data["data"]) == 1
        assert data["data"][0]["case_id"] == "CASE-DASH-001"
        assert isinstance(data["data"][0]["case_id"], str)
