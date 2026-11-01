"""
Tests for the money metrics behind the dashboard.

Covers the cases that make a recovery percentage wrong in practice: an empty
denominator, mixed statuses, a settlement recorded twice, the exact 7-day
boundary, and recovered money whose provenance is verified, simulated or unknown.
"""
from datetime import datetime, timedelta, timezone

import pytest

from app.models import (
    AuditEvent,
    Customer,
    PolicyConfig,
    RecoveryAction,
    RevenueRiskCase,
    Transaction,
)
from app.services.metrics_service import get_revenue_metrics

NOW = datetime(2026, 3, 15, 12, 0, 0, tzinfo=timezone.utc)
WEEK = timedelta(days=7)


# ============================================================================
# Helpers
# ============================================================================

_counter = {"n": 0}


def _next_id() -> int:
    _counter["n"] += 1
    return _counter["n"]


def make_case(
    db_session,
    amount: float,
    *,
    status: str = "OPEN",
    recovered_amount: float = 0.0,
    created_at: datetime = None,
    updated_at: datetime = None,
    case_id: str = None,
) -> RevenueRiskCase:
    """Create a complete case (customer + transaction + case) with set timestamps."""
    n = _next_id()
    customer = Customer(
        customer_id=f"CUST-MET-{n:04d}",
        name=f"Metrics User {n}",
        email=f"met{n}@test.com",
    )
    db_session.add(customer)
    db_session.commit()

    transaction = Transaction(
        transaction_id=f"TXN-MET-{n:04d}",
        customer_id=customer.id,
        amount=amount,
        currency="INR",
        status="FAILED",
        failure_reason="insufficient_funds",
    )
    db_session.add(transaction)
    db_session.commit()

    case = RevenueRiskCase(
        case_id=case_id or f"CASE-MET-{n:04d}",
        transaction_id=transaction.id,
        customer_id=customer.id,
        amount=amount,
        risk_score=0.5,
        recovery_probability=0.6,
        priority="MEDIUM",
        status=status,
        attempt_count=0,
        recovered_amount=recovered_amount,
        created_at=created_at or NOW,
        updated_at=updated_at or NOW,
    )
    db_session.add(case)
    db_session.commit()
    db_session.refresh(case)
    return case


def add_execution_event(db_session, case, *, actor="recovery_service",
                        decision=None, metadata=None):
    event = AuditEvent(
        case_id=case.id,
        event_type="ACTION_EXECUTED",
        actor=actor,
        decision=decision,
        action="CREATE_PAYMENT_LINK",
        result="SUCCESS",
        metadata_=metadata or {},
        timestamp=NOW,
    )
    db_session.add(event)
    db_session.commit()
    return event


# ============================================================================
# Denominators
# ============================================================================

class TestDenominators:
    def test_empty_database_returns_zeroes_not_errors(self, db_session):
        """No cases: every rate is 0.0, nothing divides by zero."""
        metrics = get_revenue_metrics(db_session, now=NOW)
        assert metrics["total_case_value"] == 0.0
        assert metrics["total_recovered"] == 0.0
        assert metrics["outstanding_amount"] == 0.0
        assert metrics["money_recovery_rate"] == 0.0
        assert metrics["count_recovery_rate"] == 0.0
        assert metrics["reconciles"] is True
        assert metrics["provenance"]["verified"] == {"amount": 0.0, "cases": 0}

    def test_cases_with_nothing_recovered(self, db_session):
        """A book with no recoveries still reports a valid 0 % money rate."""
        make_case(db_session, 1000.0, status="OPEN")
        make_case(db_session, 500.0, status="ESCALATED")
        metrics = get_revenue_metrics(db_session, now=NOW)
        assert metrics["total_case_value"] == 1500.0
        assert metrics["total_recovered"] == 0.0
        assert metrics["outstanding_amount"] == 1500.0
        assert metrics["money_recovery_rate"] == 0.0
        assert metrics["count_recovery_rate"] == 0.0
        assert metrics["reconciles"] is True


# ============================================================================
# Mixed statuses and reconciliation
# ============================================================================

class TestMixedStatuses:
    def test_money_and_count_rates_use_different_denominators(self, db_session):
        """
        2500 open + 1500 recovered = 4000 face value.

        Money-weighted: 1500 / 4000 = 37.5 %.
        Count-based:    1 / 2      = 50.0 %.
        Both are reported, separately named, and must never be conflated.
        """
        make_case(db_session, 2500.0, status="OPEN")
        make_case(db_session, 1500.0, status="RECOVERED", recovered_amount=1500.0)

        metrics = get_revenue_metrics(db_session, now=NOW)
        assert metrics["total_case_value"] == 4000.0
        assert metrics["total_recovered"] == 1500.0
        assert metrics["outstanding_amount"] == 2500.0
        assert metrics["money_recovery_rate"] == 37.5
        assert metrics["count_recovery_rate"] == 50.0
        assert metrics["recovered_cases"] == 1
        assert metrics["case_count"] == 2

    def test_three_money_figures_reconcile(self, db_session):
        """value − recovered = outstanding, and old total_at_risk agrees with it."""
        make_case(db_session, 2500.0, status="OPEN")
        make_case(db_session, 900.0, status="IN_PROGRESS")
        make_case(db_session, 1500.0, status="RECOVERED", recovered_amount=1500.0)
        make_case(db_session, 100.0, status="CLOSED")

        metrics = get_revenue_metrics(db_session, now=NOW)
        assert metrics["total_case_value"] == 5000.0
        assert metrics["total_recovered"] == 1500.0
        assert metrics["outstanding_amount"] == 3500.0
        assert metrics["reconciles"] is True
        assert (
            metrics["total_case_value"] - metrics["total_recovered"]
            == metrics["outstanding_amount"]
        )


# ============================================================================
# No double counting
# ============================================================================

class TestNoDoubleCounting:
    def test_repeated_settlement_events_count_the_money_once(self, db_session):
        """
        Two webhook deliveries for the same payment must not double the figure.

        The amount comes from the case row, never from summing events, so a
        duplicate delivery cannot inflate recovered revenue.
        """
        case = make_case(
            db_session, 1200.0, status="RECOVERED", recovered_amount=1200.0
        )
        for _ in range(2):
            add_execution_event(
                db_session,
                case,
                actor="webhook",
                decision="payment.captured",
                metadata={"payment_id": "pay_123", "event_type": "payment.captured"},
            )

        metrics = get_revenue_metrics(db_session, now=NOW)
        assert metrics["total_recovered"] == 1200.0
        assert metrics["provenance"]["verified"] == {"amount": 1200.0, "cases": 1}

    def test_partial_recovery_is_not_counted_as_full(self, db_session):
        """recovered_amount is what counts, not the case's face value."""
        make_case(db_session, 1000.0, status="RECOVERED", recovered_amount=400.0)
        metrics = get_revenue_metrics(db_session, now=NOW)
        assert metrics["total_recovered"] == 400.0
        assert metrics["outstanding_amount"] == 600.0
        assert metrics["money_recovery_rate"] == 40.0


# ============================================================================
# 7-day windows and boundaries
# ============================================================================

class TestPeriodWindows:
    def test_settlement_exactly_seven_days_ago_is_in_the_current_window(self, db_session):
        """The current window is closed on the left and open on the right."""
        make_case(
            db_session,
            700.0,
            status="RECOVERED",
            recovered_amount=700.0,
            updated_at=NOW - WEEK,
        )
        metrics = get_revenue_metrics(db_session, now=NOW)
        assert metrics["recovered_7d"] == 700.0
        assert metrics["recovered_previous_7d"] == 0.0

    def test_settlement_one_second_earlier_falls_into_the_previous_window(self, db_session):
        make_case(
            db_session,
            650.0,
            status="RECOVERED",
            recovered_amount=650.0,
            updated_at=NOW - WEEK - timedelta(seconds=1),
        )
        metrics = get_revenue_metrics(db_session, now=NOW)
        assert metrics["recovered_7d"] == 0.0
        assert metrics["recovered_previous_7d"] == 650.0

    def test_settlement_older_than_two_weeks_is_in_neither_window(self, db_session):
        make_case(
            db_session,
            500.0,
            status="RECOVERED",
            recovered_amount=500.0,
            updated_at=NOW - timedelta(days=20),
        )
        metrics = get_revenue_metrics(db_session, now=NOW)
        assert metrics["recovered_7d"] == 0.0
        assert metrics["recovered_previous_7d"] == 0.0
        # …but it still counts in the all-time totals.
        assert metrics["total_recovered"] == 500.0

    def test_at_risk_counts_only_unsettled_cases_created_in_the_window(self, db_session):
        """At risk = money that became at risk in the period and is still open."""
        make_case(db_session, 100.0, status="OPEN", created_at=NOW - timedelta(days=1))
        make_case(db_session, 200.0, status="OPEN", created_at=NOW - WEEK)
        # Settled inside the window: no longer at risk.
        make_case(
            db_session,
            400.0,
            status="RECOVERED",
            recovered_amount=400.0,
            created_at=NOW - timedelta(days=2),
            updated_at=NOW - timedelta(days=1),
        )
        # Created before the window: belongs to the previous period at most.
        make_case(db_session, 800.0, status="OPEN", created_at=NOW - timedelta(days=9))

        metrics = get_revenue_metrics(db_session, now=NOW)
        assert metrics["at_risk_7d"] == 300.0
        assert metrics["at_risk_previous_7d"] == 800.0

    def test_deltas_compare_the_two_windows(self, db_session):
        """Current minus previous, absolute and percentage."""
        make_case(
            db_session, 300.0, status="RECOVERED", recovered_amount=300.0,
            updated_at=NOW - timedelta(days=1),
        )
        make_case(
            db_session, 100.0, status="RECOVERED", recovered_amount=100.0,
            updated_at=NOW - timedelta(days=8),
        )
        metrics = get_revenue_metrics(db_session, now=NOW)
        assert metrics["recovered_7d"] == 300.0
        assert metrics["recovered_previous_7d"] == 100.0
        assert metrics["recovered_delta_7d"] == 200.0
        assert metrics["recovered_delta_pct_7d"] == 200.0

    def test_delta_percentage_is_none_when_previous_period_is_empty(self, db_session):
        """No percentage change from nothing: None, not 0 %, not infinity."""
        make_case(
            db_session, 300.0, status="RECOVERED", recovered_amount=300.0,
            updated_at=NOW - timedelta(days=1),
        )
        metrics = get_revenue_metrics(db_session, now=NOW)
        assert metrics["recovered_delta_pct_7d"] is None
        assert metrics["at_risk_delta_pct_7d"] is None


# ============================================================================
# Provenance labelling
# ============================================================================

class TestProvenance:
    def test_webhook_settlement_is_verified(self, db_session):
        case = make_case(db_session, 800.0, status="RECOVERED", recovered_amount=800.0)
        add_execution_event(
            db_session, case, actor="webhook", decision="payment_link.paid",
            metadata={"payment_id": "plink_1", "event_type": "payment_link.paid"},
        )
        provenance = get_revenue_metrics(db_session, now=NOW)["provenance"]
        assert provenance["verified"] == {"amount": 800.0, "cases": 1}
        assert provenance["simulated"] == {"amount": 0.0, "cases": 0}

    def test_simulation_metadata_marks_revenue_simulated(self, db_session):
        case = make_case(
            db_session, 450.0, status="RECOVERED", recovered_amount=450.0,
            case_id="CASE-MANUAL-1",
        )
        add_execution_event(
            db_session, case, actor="recovery_service",
            metadata={"simulation_id": "abc123"},
        )
        provenance = get_revenue_metrics(db_session, now=NOW)["provenance"]
        assert provenance["simulated"] == {"amount": 450.0, "cases": 1}
        assert provenance["verified"] == {"amount": 0.0, "cases": 0}

    def test_simulation_case_id_prefix_marks_revenue_simulated(self, db_session):
        make_case(
            db_session, 320.0, status="RECOVERED", recovered_amount=320.0,
            case_id="SIM-CASE-ABC123-000001",
        )
        provenance = get_revenue_metrics(db_session, now=NOW)["provenance"]
        assert provenance["simulated"] == {"amount": 320.0, "cases": 1}

    def test_settlement_without_evidence_is_unattributed(self, db_session):
        """Money booked with no audit trail is shown as unknown, not as verified."""
        make_case(db_session, 999.0, status="RECOVERED", recovered_amount=999.0)
        provenance = get_revenue_metrics(db_session, now=NOW)["provenance"]
        assert provenance["unattributed"] == {"amount": 999.0, "cases": 1}

    def test_provenance_amounts_sum_to_total_recovered(self, db_session):
        verified = make_case(db_session, 500.0, status="RECOVERED", recovered_amount=500.0)
        add_execution_event(
            db_session, verified, actor="webhook", decision="payment.captured",
            metadata={"event_type": "payment.captured"},
        )
        make_case(
            db_session, 300.0, status="RECOVERED", recovered_amount=300.0,
            case_id="SIM-CASE-XYZ-000002",
        )
        make_case(db_session, 200.0, status="RECOVERED", recovered_amount=200.0)

        metrics = get_revenue_metrics(db_session, now=NOW)
        provenance = metrics["provenance"]
        total = round(
            sum(provenance[b]["amount"] for b in ("verified", "simulated", "unattributed")), 2
        )
        assert total == metrics["total_recovered"] == 1000.0


# ============================================================================
# Endpoint
# ============================================================================

class TestDashboardExposure:
    @pytest.fixture
    def policy(self, db_session):
        existing = (
            db_session.query(PolicyConfig).filter(PolicyConfig.is_active == True).first()
        )
        if existing:
            return existing
        p = PolicyConfig(
            max_retries=2, max_reminders=2, max_recovery_attempts=3,
            autonomous_amount_limit=10000.0, minimum_ai_confidence=0.3,
            minimum_recovery_probability=0.2, case_lifetime_days=7, is_active=True,
        )
        db_session.add(p)
        db_session.commit()
        return p

    def test_stats_endpoint_exposes_both_rates_and_provenance(self, client, db_session, policy):
        make_case(db_session, 2500.0, status="OPEN")
        make_case(db_session, 1500.0, status="RECOVERED", recovered_amount=1500.0)

        response = client.get("/api/dashboard/stats")
        assert response.status_code == 200
        data = response.json()["data"]

        # Both rates, explicitly named.
        assert data["recovery_rate"] == 50.0          # count-based, kept for callers
        assert data["count_recovery_rate"] == 50.0
        assert data["money_recovery_rate"] == 37.5

        assert data["total_case_value"] == 4000.0
        assert data["outstanding_amount"] == 2500.0
        assert data["reconciles"] is True
        assert set(data["provenance"]) == {"verified", "simulated", "unattributed"}
        assert "recovered_7d" in data and "at_risk_7d" in data
        assert "recovered_delta_7d" in data and "recovered_delta_pct_7d" in data

    def test_stats_still_reports_legacy_totals(self, client, db_session, policy):
        """Existing keys keep their old meaning so old callers do not break."""
        make_case(db_session, 2500.0, status="OPEN")
        make_case(db_session, 1500.0, status="RECOVERED", recovered_amount=1500.0)

        data = client.get("/api/dashboard/stats").json()["data"]
        assert data["total_at_risk"] == 2500.0
        assert data["total_recovered"] == 1500.0
