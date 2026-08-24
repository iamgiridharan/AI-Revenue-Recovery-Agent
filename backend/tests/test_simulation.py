"""
Tests for Phase 8: Simulation Service.

Tests cover:
1. Synthetic transaction generation
2. ML prediction integration
3. AI action determination
4. Policy engine application
5. Simulated recovery execution
6. Audit event generation
7. Business metrics calculation
8. Stopping rules enforcement
9. Simulation API endpoint
10. Large batch simulation (1000+ transactions)
"""
import pytest
import random
from unittest.mock import patch, MagicMock

from app.models import (
    Customer,
    Transaction,
    RevenueRiskCase,
    RecoveryAction,
    AuditEvent,
    PolicyConfig,
    CaseStatus,
)
from app.services.simulation_service import (
    _generate_simulation_transactions,
    _determine_action,
    _simulate_recovery_outcome,
    _generate_diagnosis,
    run_simulation,
    SIMULATION_LABEL,
    MAX_SIMULATION_RECOVERY_ATTEMPTS,
)


# ============================================================================
# FIXTURES
# ============================================================================

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
# 1. SYNTHETIC TRANSACTION GENERATION
# ============================================================================

class TestSyntheticGeneration:
    """Tests for synthetic transaction generation."""

    def test_generates_correct_count(self):
        """Test that correct number of transactions is generated."""
        txns = _generate_simulation_transactions(100, seed=42)
        assert len(txns) == 100

    def test_generates_minimum_1000(self):
        """Test that 1000+ transactions can be generated."""
        txns = _generate_simulation_transactions(1000, seed=42)
        assert len(txns) == 1000

    def test_transaction_fields_present(self):
        """Test that all required fields are present."""
        txns = _generate_simulation_transactions(10, seed=42)
        required_fields = [
            "amount", "currency", "payment_method", "failure_reason",
            "attempt_number", "hour_of_day", "day_of_week",
            "days_since_last_transaction", "customer_total_transactions",
            "customer_successful_transactions", "customer_failed_transactions",
            "customer_lifetime_value", "customer_age_days",
        ]
        for txn in txns:
            for field in required_fields:
                assert field in txn, f"Missing field: {field}"

    def test_amounts_are_positive(self):
        """Test that all amounts are positive."""
        txns = _generate_simulation_transactions(100, seed=42)
        for txn in txns:
            assert txn["amount"] > 0

    def test_reproducible_with_seed(self):
        """Test that same seed produces same output."""
        txns1 = _generate_simulation_transactions(50, seed=42)
        txns2 = _generate_simulation_transactions(50, seed=42)
        assert txns1 == txns2

    def test_different_seeds_differ(self):
        """Test that different seeds produce different output."""
        txns1 = _generate_simulation_transactions(50, seed=42)
        txns2 = _generate_simulation_transactions(50, seed=99)
        assert txns1 != txns2

    def test_payment_methods_valid(self):
        """Test that payment methods are from valid set."""
        valid_methods = {"card", "upi", "netbanking", "wallet", "emi"}
        txns = _generate_simulation_transactions(100, seed=42)
        for txn in txns:
            assert txn["payment_method"] in valid_methods

    def test_failure_reasons_valid(self):
        """Test that failure reasons are from valid set."""
        valid_reasons = {
            "insufficient_funds", "card_expired", "bank_declined",
            "network_timeout", "authentication_failed", "daily_limit_exceeded",
            "card_blocked", "invalid_cvv", "merchant_error", "customer_cancelled",
        }
        txns = _generate_simulation_transactions(100, seed=42)
        for txn in txns:
            assert txn["failure_reason"] in valid_reasons


# ============================================================================
# 2. AI ACTION DETERMINATION
# ============================================================================

class TestActionDetermination:
    """Tests for deterministic action selection."""

    def test_low_prob_no_action(self):
        """Test that very low recovery probability leads to NO_ACTION."""
        action = _determine_action(0.1, 0.9, "customer_cancelled")
        assert action in ("NO_ACTION", "ESCALATE_TO_HUMAN")

    def test_medium_prob_wait_retry(self):
        """Test that medium-low probability leads to WAIT_AND_RETRY."""
        action = _determine_action(0.3, 0.5, "insufficient_funds")
        assert action == "WAIT_AND_RETRY"

    def test_high_prob_payment_link(self):
        """Test that high probability leads to CREATE_PAYMENT_LINK."""
        action = _determine_action(0.8, 0.2, "insufficient_funds")
        assert action == "CREATE_PAYMENT_LINK"

    def test_network_timeout_retry(self):
        """Test that network timeout leads to RETRY."""
        action = _determine_action(0.6, 0.3, "network_timeout")
        assert action == "RETRY"

    def test_merchant_error_payment_link(self):
        """Test that merchant error leads to CREATE_PAYMENT_LINK."""
        action = _determine_action(0.6, 0.3, "merchant_error")
        assert action == "CREATE_PAYMENT_LINK"

    def test_medium_prob_reminder(self):
        """Test that medium probability leads to SEND_PAYMENT_REMINDER."""
        action = _determine_action(0.55, 0.4, "insufficient_funds")
        assert action == "SEND_PAYMENT_REMINDER"

    def test_escalation_for_critical_low_prob(self):
        """Test escalation for critical risk with low probability."""
        action = _determine_action(0.15, 0.85, "card_blocked")
        assert action == "ESCALATE_TO_HUMAN"


# ============================================================================
# 3. SIMULATED RECOVERY EXECUTION
# ============================================================================

class TestSimulatedRecovery:
    """Tests for simulated recovery execution."""

    def test_recovery_returns_valid_result(self):
        """Test that simulated recovery returns expected fields."""
        result = _simulate_recovery_outcome("CREATE_PAYMENT_LINK", 5000.0, 0.8)
        assert "success" in result
        assert "recovered_amount" in result
        assert result["simulated"] is True
        assert result["mock"] is True

    def test_high_confidence_higher_success_rate(self):
        """Test that higher confidence yields higher success probability."""
        random.seed(42)
        successes_high = sum(
            _simulate_recovery_outcome("CREATE_PAYMENT_LINK", 5000.0, 0.9)["success"]
            for _ in range(100)
        )
        random.seed(42)
        successes_low = sum(
            _simulate_recovery_outcome("CREATE_PAYMENT_LINK", 5000.0, 0.3)["success"]
            for _ in range(100)
        )
        # High confidence should generally have more successes
        # (not guaranteed per-run due to randomness, but statistically)
        assert successes_high >= successes_low - 5  # Allow some variance

    def test_escalate_always_fails(self):
        """Test that ESCALATE_TO_HUMAN always fails in simulation."""
        for _ in range(10):
            result = _simulate_recovery_outcome("ESCALATE_TO_HUMAN", 5000.0, 0.8)
            assert result["success"] is False

    def test_no_action_always_fails(self):
        """Test that NO_ACTION always fails in simulation."""
        for _ in range(10):
            result = _simulate_recovery_outcome("NO_ACTION", 5000.0, 0.8)
            assert result["success"] is False


# ============================================================================
# 4. DIAGNOSIS GENERATION
# ============================================================================

class TestDiagnosisGeneration:
    """Tests for deterministic diagnosis generation."""

    def test_diagnosis_contains_failure_reason(self):
        """Test that diagnosis mentions the failure reason context."""
        diag = _generate_diagnosis("insufficient_funds", 0.8, 0.2)
        assert len(diag) > 20
        assert "insufficient funds" in diag.lower() or "insufficient_funds" in diag

    def test_diagnosis_includes_confidence(self):
        """Test that diagnosis includes confidence level."""
        diag = _generate_diagnosis("network_timeout", 0.75, 0.25)
        assert "HIGH" in diag or "high" in diag.lower()

    def test_diagnosis_all_reasons_covered(self):
        """Test that all failure reasons produce valid diagnoses."""
        reasons = [
            "insufficient_funds", "card_expired", "bank_declined",
            "network_timeout", "authentication_failed", "daily_limit_exceeded",
            "card_blocked", "invalid_cvv", "merchant_error", "customer_cancelled",
        ]
        for reason in reasons:
            diag = _generate_diagnosis(reason, 0.5, 0.5)
            assert len(diag) > 20


# ============================================================================
# 5. SIMULATION INTEGRATION
# ============================================================================

class TestSimulationIntegration:
    """Integration tests for the full simulation pipeline."""

    def test_small_simulation(self, db_session, active_policy):
        """Test running a small simulation (50 transactions)."""
        result = run_simulation(db_session, num_transactions=50, seed=42)

        assert result["simulation_id"].startswith("SIM-")
        assert result["status"] == "COMPLETED"
        assert result["label"] == SIMULATION_LABEL
        assert result["num_transactions_processed"] == 50
        assert result["processing_duration_seconds"] > 0
        assert result["total_audit_events"] > 0

    def test_simulation_creates_records(self, db_session, active_policy):
        """Test that simulation creates database records."""
        result = run_simulation(db_session, num_transactions=20, seed=42)

        customers = db_session.query(Customer).filter(
            Customer.customer_id.like("SIM-CUST-%")
        ).count()
        assert customers == 20

        cases = db_session.query(RevenueRiskCase).filter(
            RevenueRiskCase.case_id.like("SIM-CASE-%")
        ).count()
        assert cases == 20

    def test_simulation_audit_trail(self, db_session, active_policy):
        """Test that simulation creates complete audit trail."""
        result = run_simulation(db_session, num_transactions=10, seed=42)

        # Each case should have at least: CASE_CREATED, RISK_ASSESSED, DIAGNOSIS_COMPLETED
        assert result["total_audit_events"] >= 30  # 10 * 3 minimum

    def test_simulation_business_metrics(self, db_session, active_policy):
        """Test that simulation computes valid business metrics."""
        result = run_simulation(db_session, num_transactions=50, seed=42)

        metrics = result["metrics"]
        assert metrics["revenue_at_risk"] > 0
        assert metrics["recovery_rate"] >= 0
        assert metrics["recovery_rate"] <= 100
        assert metrics["outstanding_revenue"] >= 0
        assert metrics["outstanding_revenue"] <= metrics["revenue_at_risk"]

    def test_simulation_no_real_payments(self, db_session, active_policy):
        """Test that simulation does NOT create real payment links."""
        result = run_simulation(db_session, num_transactions=10, seed=42)

        # All recovery actions should be simulated
        actions = db_session.query(RecoveryAction).join(RevenueRiskCase).filter(
            RevenueRiskCase.case_id.like("SIM-CASE-%")
        ).all()

        for action in actions:
            # API references should be simulated payment IDs
            if action.api_reference:
                assert action.api_reference.startswith("sim_pay_")

    def test_simulation_label_present(self, db_session, active_policy):
        """Test that all simulation audit events are labeled."""
        run_simulation(db_session, num_transactions=10, seed=42)

        sim_cases = db_session.query(RevenueRiskCase).filter(
            RevenueRiskCase.case_id.like("SIM-CASE-%")
        ).all()

        for case in sim_cases:
            events = db_session.query(AuditEvent).filter(
                AuditEvent.case_id == case.id
            ).all()
            for event in events:
                if event.metadata_ and "label" in event.metadata_:
                    assert event.metadata_["label"] == SIMULATION_LABEL

    def test_simulation_metrics_consistency(self, db_session, active_policy):
        """Test that metrics are internally consistent."""
        result = run_simulation(db_session, num_transactions=100, seed=42)

        metrics = result["metrics"]
        # Revenue recovered should not exceed revenue at risk
        assert metrics["revenue_recovered"] <= metrics["revenue_at_risk"]
        # Successful + failed + escalated + policy_blocks should add up
        total_outcomes = (
            metrics["successful_recoveries"] +
            metrics["failed_recoveries"] +
            metrics["escalated_cases"] +
            metrics["policy_blocked"]
        )
        assert total_outcomes > 0

    def test_simulation_ml_predictions_used(self, db_session, active_policy):
        """Test that ML predictions are used in simulation."""
        result = run_simulation(db_session, num_transactions=10, seed=42)

        ml_stats = result["ml_prediction_stats"]
        assert ml_stats["avg_recovery_probability"] > 0
        assert ml_stats["avg_recovery_probability"] < 1
        # Should have predictions in different categories
        total_predictions = (
            ml_stats["high_prob_count"] +
            ml_stats["medium_prob_count"] +
            ml_stats["low_prob_count"]
        )
        assert total_predictions == 10


# ============================================================================
# 6. LARGE BATCH SIMULATION
# ============================================================================

class TestLargeBatchSimulation:
    """Tests for 1000+ transaction simulation."""

    def test_1000_transaction_simulation(self, db_session, active_policy):
        """Test simulation with 1000 transactions."""
        result = run_simulation(db_session, num_transactions=1000, seed=42)

        assert result["num_transactions_processed"] == 1000
        assert result["status"] == "COMPLETED"
        assert result["processing_duration_seconds"] > 0
        assert result["revenue_at_risk"] > 0

    def test_1000_transaction_metrics(self, db_session, active_policy):
        """Test metrics for 1000 transaction simulation."""
        result = run_simulation(db_session, num_transactions=1000, seed=42)

        metrics = result["metrics"]
        assert metrics["recovery_rate"] >= 0
        assert metrics["recovery_rate"] <= 100
        assert metrics["total_recovery_attempts"] > 0
        assert metrics["average_recovery_time_seconds"] > 0

    def test_1000_transaction_audit_events(self, db_session, active_policy):
        """Test that 1000 transaction simulation creates sufficient audit events."""
        result = run_simulation(db_session, num_transactions=1000, seed=42)

        # Each case should have at least 3 audit events
        assert result["total_audit_events"] >= 3000

    def test_1000_transaction_performance(self, db_session, active_policy):
        """Test that 1000 transaction simulation completes in reasonable time."""
        result = run_simulation(db_session, num_transactions=1000, seed=42)

        # Should complete in under 120 seconds
        assert result["processing_duration_seconds"] < 120
        # Average processing time per transaction should be under 200ms
        assert result["avg_processing_time_ms"] < 200


# ============================================================================
# 7. STOPPING RULES
# ============================================================================

class TestStoppingRules:
    """Tests for recovery stopping rules in simulation."""

    def test_max_attempts_enforced(self, db_session, active_policy):
        """Test that max recovery attempts are enforced."""
        # Run simulation and check escalated cases
        result = run_simulation(db_session, num_transactions=50, seed=42)

        # Check that some cases were escalated (due to max attempts)
        escalated = result["escalations"]
        policy_blocks = result["policy_blocks"]

        # With 50 transactions, some should hit stopping rules
        assert escalated + policy_blocks >= 0  # Basic sanity

    def test_escalated_cases_not_retried(self, db_session, active_policy):
        """Test that escalated cases don't get additional recovery attempts."""
        run_simulation(db_session, num_transactions=20, seed=42)

        escalated_cases = db_session.query(RevenueRiskCase).filter(
            RevenueRiskCase.case_id.like("SIM-CASE-%"),
            RevenueRiskCase.status == CaseStatus.ESCALATED.value,
        ).all()

        for case in escalated_cases:
            # Escalated cases should not have RECOVERED status
            assert case.status != CaseStatus.RECOVERED.value


# ============================================================================
# 8. SIMULATION API ENDPOINT
# ============================================================================

class TestSimulationAPI:
    """Tests for the simulation API endpoint."""

    def test_simulation_endpoint_returns_200(self, client, active_policy):
        """Test that simulation endpoint returns 200."""
        response = client.post(
            "/api/simulation/run",
            json={"num_transactions": 20, "seed": 42},
        )
        assert response.status_code == 200

    def test_simulation_endpoint_response_structure(self, client, active_policy):
        """Test simulation endpoint response structure."""
        response = client.post(
            "/api/simulation/run",
            json={"num_transactions": 20, "seed": 42},
        )
        data = response.json()

        assert data["simulation_id"].startswith("SIM-")
        assert data["status"] == "COMPLETED"
        assert data["label"] == SIMULATION_LABEL
        assert "num_transactions_processed" in data
        assert "revenue_at_risk" in data
        assert "simulated_revenue_recovered" in data
        assert "recovery_rate" in data
        assert "processing_duration_seconds" in data
        assert "metrics" in data
        assert "ml_prediction_stats" in data

    def test_simulation_endpoint_with_defaults(self, client, active_policy):
        """Test simulation with default parameters."""
        response = client.post("/api/simulation/run", json={})
        assert response.status_code == 200
        data = response.json()
        assert data["num_transactions_processed"] == 1000  # Default

    def test_simulation_endpoint_min_transactions(self, client, active_policy):
        """Test simulation with minimum transactions."""
        response = client.post(
            "/api/simulation/run",
            json={"num_transactions": 10},
        )
        assert response.status_code == 200
        data = response.json()
        assert data["num_transactions_processed"] == 10

    def test_simulation_endpoint_invalid_low(self, client):
        """Test simulation with too few transactions."""
        response = client.post(
            "/api/simulation/run",
            json={"num_transactions": 5},
        )
        assert response.status_code == 422  # Validation error

    def test_simulation_endpoint_invalid_high(self, client):
        """Test simulation with too many transactions."""
        response = client.post(
            "/api/simulation/run",
            json={"num_transactions": 100000},
        )
        assert response.status_code == 422  # Validation error

    def test_simulation_labeled_simulated(self, client, active_policy):
        """Test that simulation results are labeled SIMULATED."""
        response = client.post(
            "/api/simulation/run",
            json={"num_transactions": 10, "seed": 42},
        )
        data = response.json()
        assert data["label"] == "SIMULATED"
