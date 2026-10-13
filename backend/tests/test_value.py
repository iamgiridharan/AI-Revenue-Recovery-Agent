"""
Tests for Phase 2 value-aware recovery scoring.

Covers:
- zero amount / zero recovery probability
- amount and probability validation
- missing, zero and invalid intervention costs
- net recovery arithmetic
- multiple candidates and deterministic ties
- ineligible / policy-blocked candidates
- settled cases receiving no recommendation
- value scoring never authorizing or executing anything
"""
import pytest
from datetime import datetime, timezone

from app.models import (
    Customer,
    Transaction,
    RevenueRiskCase,
    RecoveryAction,
    PolicyDecision,
    AuditEvent,
    CaseStatus,
)
from app.services.value_service import (
    ESTIMATE_LABEL,
    EXECUTABLE_ACTIONS,
    assess_case_value,
    assess_case_value_by_id,
    expected_gross_recovery,
    expected_net_recovery,
    calculate_utility_score,
    validate_amount,
    validate_probability,
    validate_cost,
)


# ============================================================================
# FIXTURES
# ============================================================================


@pytest.fixture(autouse=True)
def _isolate_configured_value_settings(monkeypatch):
    """
    Keep these tests independent of any deployment's configured costs/penalties.

    Value scoring reads ``get_settings().INTERVENTION_COSTS`` / ``UTILITY_PENALTIES``
    when a caller does not pass costs explicitly, so the ambient configuration is
    pinned to empty here and overridden deliberately where a test needs it.
    """
    from app.core import config as core_config

    settings = core_config.get_settings()
    monkeypatch.setattr(core_config, "get_settings", lambda: settings)
    monkeypatch.setattr(settings, "INTERVENTION_COSTS", {})
    monkeypatch.setattr(settings, "UTILITY_PENALTIES", {})


@pytest.fixture
def value_customer(db_session):
    customer = Customer(
        customer_id="CUST-VALUE-001",
        name="Value Test Customer",
        email="value@example.com",
        phone="+919876543210",
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
def value_transaction(db_session, value_customer):
    transaction = Transaction(
        transaction_id="TXN-VALUE-001",
        customer_id=value_customer.id,
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


def _make_case(db_session, customer, transaction, **overrides):
    defaults = dict(
        case_id="CASE-VALUE-001",
        transaction_id=transaction.id,
        customer_id=customer.id,
        amount=2500.0,
        risk_score=0.2,
        recovery_probability=0.8,
        priority="HIGH",
        status=CaseStatus.OPEN.value,
        attempt_count=0,
        recovered_amount=0.0,
    )
    defaults.update(overrides)
    case = RevenueRiskCase(**defaults)
    db_session.add(case)
    db_session.commit()
    db_session.refresh(case)
    return case


@pytest.fixture
def value_case(db_session, value_customer, value_transaction):
    """Open case: amount 2500, recovery probability 0.8, nothing blocking."""
    return _make_case(db_session, value_customer, value_transaction)


#: Costs used by the arithmetic tests (explicitly configured, never assumed).
COSTS = {
    "CREATE_PAYMENT_LINK": 20.0,
    "RETRY": 5.0,
    "SEND_PAYMENT_REMINDER": 1.0,
}


# ============================================================================
# 1. ZERO AMOUNT / ZERO PROBABILITY
# ============================================================================


class TestZeroValues:
    """Zero amount and zero recovery probability are handled explicitly."""

    def test_zero_amount(self, db_session, value_customer, value_transaction):
        case = _make_case(
            db_session, value_customer, value_transaction,
            case_id="CASE-VALUE-ZERO-AMOUNT", amount=0.0, recovery_probability=0.8,
        )

        assessment = assess_case_value(db_session, case, costs=COSTS)

        assert assessment["inputs"]["payment_amount"] == 0.0
        assert assessment["expected_gross_recovery"] == 0.0
        assert assessment["intervention_cost"] == 1.0  # cheapest eligible candidate
        assert assessment["expected_net_recovery"] == -1.0
        # Every candidate is still ranked and remains inspectable
        assert len(assessment["candidates"]) == len(EXECUTABLE_ACTIONS)

    def test_zero_amount_with_zero_cost(self, db_session, value_customer, value_transaction):
        case = _make_case(
            db_session, value_customer, value_transaction,
            case_id="CASE-VALUE-ZERO-AMOUNT-2", amount=0.0, recovery_probability=0.8,
        )
        zero_costs = {action: 0.0 for action in EXECUTABLE_ACTIONS}

        assessment = assess_case_value(db_session, case, costs=zero_costs)

        assert assessment["expected_gross_recovery"] == 0.0
        assert assessment["expected_net_recovery"] == 0.0
        assert assessment["utility_score"] == 0.0

    def test_zero_probability_is_not_ranked(self, db_session, value_customer, value_transaction):
        """A zero-probability case yields no recommendation - nothing is invented."""
        case = _make_case(
            db_session, value_customer, value_transaction,
            case_id="CASE-VALUE-ZERO-PROB", recovery_probability=0.0,
        )

        assessment = assess_case_value(db_session, case, costs=COSTS)

        assert assessment["expected_gross_recovery"] == 0.0
        # No eligible candidate => no cost can be attributed => net not computed
        assert assessment["expected_net_recovery"] is None
        assert assessment["utility_score"] is None
        assert assessment["candidates"] == []
        assert assessment["recommended_action"] is None
        assert len(assessment["excluded_candidates"]) == len(EXECUTABLE_ACTIONS)
        for excluded in assessment["excluded_candidates"]:
            assert excluded["eligible"] is False
            assert "policy" in excluded["eligibility_reason"].lower()
            assert excluded["policy_precheck_decision"] == "ESCALATED"

    def test_missing_probability_is_not_invented(self, db_session, value_customer, value_transaction):
        """Without a stored probability no estimate is produced."""
        case = _make_case(
            db_session, value_customer, value_transaction,
            case_id="CASE-VALUE-NO-PROB", recovery_probability=None,
        )

        assessment = assess_case_value(db_session, case, costs=COSTS)

        assert assessment["inputs"]["recovery_probability"] is None
        assert assessment["inputs"]["recovery_probability_source"] == "unavailable"
        assert assessment["expected_gross_recovery"] is None
        assert assessment["expected_net_recovery"] is None
        assert assessment["utility_score"] is None


# ============================================================================
# 2. VALIDATION
# ============================================================================


class TestValidation:
    """Amounts, probabilities and costs are validated, not coerced."""

    def test_amount_validation(self):
        assert validate_amount(0) == 0.0
        assert validate_amount(2500.5) == 2500.5

        for invalid in (-1, -0.01, "2500", None, float("nan"), float("inf"), True):
            with pytest.raises(ValueError):
                validate_amount(invalid)

    def test_probability_validation(self):
        assert validate_probability(0) == 0.0
        assert validate_probability(1) == 1.0
        assert validate_probability(0.35) == 0.35

        for invalid in (-0.01, 1.01, 2, "0.5", None, float("nan"), False):
            with pytest.raises(ValueError):
                validate_probability(invalid)

    def test_cost_validation(self):
        assert validate_cost(0) == 0.0
        assert validate_cost(12.5) == 12.5
        assert validate_cost(None) is None  # unknown stays unknown

        for invalid in (-1, "free", float("nan"), True):
            with pytest.raises(ValueError):
                validate_cost(invalid, "RETRY")

    def test_assessment_rejects_invalid_case_probability(self, db_session, value_customer, value_transaction):
        case = _make_case(
            db_session, value_customer, value_transaction,
            case_id="CASE-VALUE-BAD-PROB", recovery_probability=1.4,
        )
        with pytest.raises(ValueError):
            assess_case_value(db_session, case)

    def test_assessment_rejects_negative_case_amount(self, db_session, value_customer, value_transaction):
        case = _make_case(
            db_session, value_customer, value_transaction,
            case_id="CASE-VALUE-BAD-AMOUNT", amount=-100.0,
        )
        with pytest.raises(ValueError):
            assess_case_value(db_session, case)


# ============================================================================
# 3. INTERVENTION COSTS
# ============================================================================


class TestInterventionCosts:
    """Missing, zero and invalid costs are distinguished."""

    def test_missing_cost_leaves_net_not_computed(self, db_session, value_case):
        """No cost configured anywhere => net recovery is NOT assumed to be gross."""
        assessment = assess_case_value(db_session, value_case)

        assert assessment["intervention_cost"] is None
        assert assessment["intervention_cost_source"] == "not_configured"
        assert assessment["inputs"]["costs_configured_for"] == []
        assert assessment["expected_gross_recovery"] == 2000.0
        assert assessment["expected_net_recovery"] is None
        assert assessment["utility_score"] is None
        assert assessment["ranking_basis"].startswith("expected_gross_recovery")

    def test_zero_cost_is_honoured_and_labelled(self, db_session, value_case):
        zero_costs = {action: 0.0 for action in EXECUTABLE_ACTIONS}

        assessment = assess_case_value(db_session, value_case, costs=zero_costs)

        assert assessment["intervention_cost"] == 0.0
        assert assessment["intervention_cost_source"] == "configured_template"
        assert assessment["expected_net_recovery"] == assessment["expected_gross_recovery"]
        assert assessment["expected_net_recovery"] == 2000.0

    def test_invalid_cost_is_rejected(self, db_session, value_case):
        with pytest.raises(ValueError):
            assess_case_value(db_session, value_case, costs={"CREATE_PAYMENT_LINK": -5.0})
        with pytest.raises(ValueError):
            assess_case_value(db_session, value_case, costs={"CREATE_PAYMENT_LINK": "free"})

    def test_invalid_penalty_is_rejected(self, db_session, value_case):
        with pytest.raises(ValueError):
            assess_case_value(db_session, value_case, penalties={"RETRY": -1.0})

    def test_deployment_configured_costs_are_used(self, monkeypatch, db_session, value_case):
        """Costs supplied by deployment configuration are applied and labelled."""
        from app.core import config as core_config

        settings = core_config.get_settings()
        monkeypatch.setattr(core_config, "get_settings", lambda: settings)
        monkeypatch.setattr(settings, "INTERVENTION_COSTS", {"CREATE_PAYMENT_LINK": 7.5})

        assessment = assess_case_value(db_session, value_case)

        by_action = {c["action"]: c for c in assessment["candidates"]}
        assert by_action["CREATE_PAYMENT_LINK"]["intervention_cost"] == 7.5
        assert by_action["CREATE_PAYMENT_LINK"]["intervention_cost_source"] == "configured_template"
        assert by_action["CREATE_PAYMENT_LINK"]["expected_net_recovery"] == 1992.5
        # Actions without a configured cost stay explicitly unknown
        assert by_action["RETRY"]["intervention_cost"] is None
        assert by_action["RETRY"]["intervention_cost_source"] == "not_configured"

    def test_configured_cost_and_penalty_are_reported_separately(self, db_session, value_case):
        assessment = assess_case_value(
            db_session, value_case, costs=COSTS, penalties={"RETRY": 3.0},
        )

        assert assessment["inputs"]["costs_configured_for"] == sorted(COSTS.keys())
        assert assessment["inputs"]["penalties_configured_for"] == ["RETRY"]
        assert assessment["penalties"]["applied_to"] == (
            "utility_score only (never to expected_net_recovery)"
        )


# ============================================================================
# 4. NET RECOVERY ARITHMETIC
# ============================================================================


class TestNetRecoveryArithmetic:
    """gross = amount x probability; net = gross - cost; utility = net - penalties."""

    def test_formula_helpers(self):
        assert expected_gross_recovery(2500.0, 0.8) == 2000.0
        assert expected_gross_recovery(2500.0, 0.3333) == 833.25
        assert expected_net_recovery(2000.0, 20.0) == 1980.0
        assert expected_net_recovery(2000.0, None) is None
        assert expected_net_recovery(2000.0, 0.0) == 2000.0
        assert calculate_utility_score(1980.0, 5.0) == 1975.0
        assert calculate_utility_score(None, 5.0) is None

    def test_candidate_arithmetic_is_per_action(self, db_session, value_case):
        assessment = assess_case_value(db_session, value_case, costs=COSTS)

        by_action = {c["action"]: c for c in assessment["candidates"]}
        assert set(by_action) == set(EXECUTABLE_ACTIONS)

        for action, candidate in by_action.items():
            assert candidate["expected_gross_recovery"] == 2000.0  # same case-level probability
            assert candidate["intervention_cost"] == COSTS[action]
            assert candidate["expected_net_recovery"] == round(2000.0 - COSTS[action], 2)
            assert candidate["utility_score"] == candidate["expected_net_recovery"]

    def test_gross_cost_and_net_stay_separately_inspectable(self, db_session, value_case):
        assessment = assess_case_value(db_session, value_case, costs=COSTS)

        assert assessment["expected_gross_recovery"] == 2000.0
        assert assessment["intervention_cost"] == 1.0
        assert assessment["expected_net_recovery"] == 1999.0
        assert assessment["expected_net_recovery"] == (
            assessment["expected_gross_recovery"] - assessment["intervention_cost"]
        )

    def test_ranking_is_cost_driven_because_model_is_not_action_specific(self, db_session, value_case):
        """
        With one case-level probability the model cannot prefer an action, so the
        cheapest eligible action wins.  This is the documented consequence of the
        missing action-specific probabilities, asserted here on purpose.
        """
        assessment = assess_case_value(db_session, value_case, costs=COSTS)

        assert assessment["inputs"]["recovery_probability_is_action_specific"] is False
        assert assessment["recommended_action"] == "SEND_PAYMENT_REMINDER"
        assert [c["action"] for c in assessment["candidates"]] == [
            "SEND_PAYMENT_REMINDER", "RETRY", "CREATE_PAYMENT_LINK",
        ]

    def test_caller_supplied_action_probabilities_are_labelled(self, db_session, value_case):
        assessment = assess_case_value(
            db_session,
            value_case,
            costs=COSTS,
            action_probabilities={"CREATE_PAYMENT_LINK": 0.9},
        )

        assert assessment["inputs"]["recovery_probability_is_action_specific"] is True
        by_action = {c["action"]: c for c in assessment["candidates"]}
        create = by_action["CREATE_PAYMENT_LINK"]
        assert create["probability"] == 0.9
        assert create["probability_source"] == "caller_supplied"
        # Actions without an override fall back to the case-level probability
        assert by_action["RETRY"]["probability_source"] == "case_level_ml_model"


# ============================================================================
# 5. MULTIPLE CANDIDATES AND TIES
# ============================================================================


class TestCandidatesAndTies:
    """Ranking is deterministic, including for ties."""

    def test_ties_use_declared_action_order(self, db_session, value_case):
        equal_costs = {action: 0.0 for action in EXECUTABLE_ACTIONS}

        assessment = assess_case_value(db_session, value_case, costs=equal_costs)

        assert [c["action"] for c in assessment["candidates"]] == list(EXECUTABLE_ACTIONS)
        assert [c["rank"] for c in assessment["candidates"]] == [1, 2, 3]
        assert assessment["recommended_action"] == EXECUTABLE_ACTIONS[0]

    def test_ranking_is_deterministic_across_calls(self, db_session, value_case):
        first = assess_case_value(db_session, value_case, costs=COSTS)
        second = assess_case_value(db_session, value_case, costs=COSTS)

        assert first == second

    def test_only_executable_actions_are_ranked(self, db_session, value_case):
        assessment = assess_case_value(db_session, value_case, costs=COSTS)

        ranked = {c["action"] for c in assessment["candidates"]}
        assert ranked == set(EXECUTABLE_ACTIONS)
        assert "NO_ACTION" not in ranked
        assert "ESCALATE_TO_HUMAN" not in ranked
        assert "WAIT_AND_RETRY" not in ranked  # no executor exists for it

    def test_higher_value_case_ranks_the_same_but_is_worth_more(self, db_session, value_customer, value_transaction):
        """Cross-case comparison: the estimate scales with amount x probability."""
        small = _make_case(
            db_session, value_customer, value_transaction,
            case_id="CASE-VALUE-SMALL", amount=100.0, recovery_probability=0.5,
        )
        large = _make_case(
            db_session, value_customer, value_transaction,
            case_id="CASE-VALUE-LARGE", amount=9000.0, recovery_probability=0.5,
        )

        small_value = assess_case_value(db_session, small, costs=COSTS)["expected_gross_recovery"]
        large_value = assess_case_value(db_session, large, costs=COSTS)["expected_gross_recovery"]

        assert small_value == 50.0
        assert large_value == 4500.0


# ============================================================================
# 6. INELIGIBLE / POLICY-BLOCKED CANDIDATES
# ============================================================================


class TestEligibility:
    """Ineligible candidates are excluded from the ranking, with reasons."""

    def test_retry_excluded_when_retry_limit_reached(self, db_session, value_case):
        for _ in range(2):  # default max_retries = 2
            db_session.add(RecoveryAction(
                case_id=value_case.id,
                action_type="RETRY",
                reason="previous retry",
                confidence=0.8,
                policy_result="APPROVED",
                execution_status="SUCCESS",
            ))
        db_session.commit()

        assessment = assess_case_value(db_session, value_case, costs=COSTS)

        ranked = {c["action"] for c in assessment["candidates"]}
        excluded = {e["action"]: e for e in assessment["excluded_candidates"]}
        assert "RETRY" not in ranked
        assert "Retry limit reached" in excluded["RETRY"]["eligibility_reason"]
        assert excluded["RETRY"]["policy_precheck_decision"] == "BLOCKED"
        assert "CREATE_PAYMENT_LINK" in ranked

    def test_all_candidates_excluded_when_policy_escalates(self, db_session, value_customer, value_transaction):
        case = _make_case(
            db_session, value_customer, value_transaction,
            case_id="CASE-VALUE-HIGH-AMOUNT", amount=50000.0,  # above the 10,000 limit
        )

        assessment = assess_case_value(db_session, case, costs=COSTS)

        assert assessment["candidates"] == []
        assert assessment["recommended_action"] is None
        assert assessment["expected_net_recovery"] is None
        assert len(assessment["excluded_candidates"]) == len(EXECUTABLE_ACTIONS)
        for excluded in assessment["excluded_candidates"]:
            assert excluded["policy_precheck_decision"] == "ESCALATED"
            assert "human review" in excluded["eligibility_reason"]
            assert excluded["expected_net_recovery"] is None

    def test_excluded_candidates_are_not_recommended(self, db_session, value_customer, value_transaction):
        case = _make_case(
            db_session, value_customer, value_transaction,
            case_id="CASE-VALUE-ESCALATED", recovery_probability=0.1,  # below thresholds
        )

        assessment = assess_case_value(db_session, case, costs=COSTS)

        assert assessment["recommended_action"] is None
        assert all(c["eligible"] for c in assessment["candidates"]) is True
        assert assessment["candidates"] == []


# ============================================================================
# 7. SETTLED CASES
# ============================================================================


class TestTerminalCases:
    """Recovered / closed cases receive no further recovery recommendation."""

    @pytest.mark.parametrize("status", [CaseStatus.RECOVERED.value, CaseStatus.CLOSED.value])
    def test_terminal_case_has_no_candidates(self, db_session, value_customer, value_transaction, status):
        case = _make_case(
            db_session, value_customer, value_transaction,
            case_id=f"CASE-VALUE-{status}", status=status, recovered_amount=2500.0,
        )

        assessment = assess_case_value(db_session, case, costs=COSTS)

        assert assessment["candidates"] == []
        assert assessment["recommended_action"] is None
        assert assessment["expected_net_recovery"] is None
        assert len(assessment["excluded_candidates"]) == len(EXECUTABLE_ACTIONS)
        for excluded in assessment["excluded_candidates"]:
            assert "terminal" in excluded["eligibility_reason"]
            assert status in excluded["eligibility_reason"]

    def test_assessment_does_not_replace_actual_recovered_revenue(self, db_session, value_customer, value_transaction):
        """Estimates and actual recovered revenue are kept apart."""
        case = _make_case(
            db_session, value_customer, value_transaction,
            case_id="CASE-VALUE-ACTUAL", status=CaseStatus.OPEN.value, recovered_amount=0.0,
        )

        assessment = assess_case_value(db_session, case, costs=COSTS)

        assert assessment["estimate_label"] == ESTIMATE_LABEL
        assert assessment["expected_gross_recovery"] is not None
        assert "recovered_amount" not in assessment
        assert any("case.recovered_amount" in line for line in assessment["limitations"])


# ============================================================================
# 8. VALUE SCORING NEVER AUTHORIZES OR EXECUTES
# ============================================================================


class TestNeverAuthorizes:
    """Ranking is advisory; the Policy Engine stays the authority."""

    def test_assessment_writes_no_decisions_audits_or_actions(self, db_session, value_case):
        before = (
            db_session.query(PolicyDecision).count(),
            db_session.query(AuditEvent).count(),
            db_session.query(RecoveryAction).count(),
        )

        assessment = assess_case_value(db_session, value_case, costs=COSTS)

        after = (
            db_session.query(PolicyDecision).count(),
            db_session.query(AuditEvent).count(),
            db_session.query(RecoveryAction).count(),
        )
        assert before == after, "value assessment must not persist policy decisions or actions"
        assert assessment["recommended_action"] is not None
        assert assessment["authorization"] == {
            "required": True,
            "authority": "policy_engine",
            "note": (
                "value scoring ranks eligible candidates only; it never "
                "authorizes or executes an action. The Policy Engine is "
                "consulted again (and recorded) when an action is executed."
            ),
        }

    def test_execution_still_records_policy_authorization(self, db_session, value_case):
        """
        The recommendation from value scoring cannot act on its own: executing it
        goes through the Policy Engine and is recorded as a decision.
        """
        from app.services.recovery_service import execute_recovery_action

        assessment = assess_case_value(db_session, value_case, costs=COSTS)
        recommended = assessment["recommended_action"]

        # Nothing has been recorded yet by the assessment itself
        assert db_session.query(PolicyDecision).count() == 0

        result = execute_recovery_action(
            db=db_session,
            case_id=value_case.case_id,
            action_type=recommended,
            confidence=0.85,
            recovery_probability=0.8,
        )

        assert result["action_executed"] is True
        decisions = db_session.query(PolicyDecision).all()
        assert len(decisions) == 1
        assert decisions[0].proposed_action == recommended
        assert decisions[0].decision == "APPROVED"

    def test_assessment_uses_non_persisting_policy_precheck(self, db_session, value_case):
        """The eligibility pre-filter calls the real engine without persisting."""
        from app.services import policy_engine

        calls = []
        original = policy_engine.evaluate_action

        def spy(*args, **kwargs):
            calls.append(kwargs.get("persist"))
            return original(*args, **kwargs)

        policy_engine.evaluate_action = spy
        try:
            assess_case_value(db_session, value_case, costs=COSTS)
        finally:
            policy_engine.evaluate_action = original

        assert calls, "eligibility must consult the policy engine"
        assert all(persist is False for persist in calls)


# ============================================================================
# API INTEGRATION
# ============================================================================


class TestValueApi:
    """The assessment is exposed through the API and the case-detail response."""

    def test_value_endpoint(self, client, db_session, value_case):
        response = client.get(f"/api/cases/{value_case.case_id}/value")

        assert response.status_code == 200
        body = response.json()
        assert body["success"] is True

        data = body["data"]
        assert data["case_id"] == value_case.case_id
        assert data["estimate_label"] == "ESTIMATE"
        assert set(data["formula"]) == {
            "expected_gross_recovery", "expected_net_recovery", "utility_score",
        }
        assert data["expected_gross_recovery"] == 2000.0
        assert data["expected_net_recovery"] is None  # no cost configured
        assert data["inputs"]["payment_amount"] == 2500.0
        assert data["inputs"]["recovery_probability"] == 0.8
        assert data["excluded_candidates"] == []
        assert data["limitations"]

    def test_value_endpoint_unknown_case(self, client):
        response = client.get("/api/cases/CASE-DOES-NOT-EXIST/value")

        assert response.status_code == 404
        assert response.json()["success"] is False

    def test_value_endpoint_is_read_only(self, client, db_session, value_case):
        before = (
            db_session.query(PolicyDecision).count(),
            db_session.query(AuditEvent).count(),
            db_session.query(RecoveryAction).count(),
        )

        assert client.get(f"/api/cases/{value_case.case_id}/value").status_code == 200

        after = (
            db_session.query(PolicyDecision).count(),
            db_session.query(AuditEvent).count(),
            db_session.query(RecoveryAction).count(),
        )
        assert before == after

    def test_case_detail_includes_value_assessment(self, client, db_session, value_case):
        response = client.get(f"/api/cases/{value_case.case_id}/detail")

        assert response.status_code == 200
        data = response.json()["data"]
        assert "value_assessment" in data
        assert data["value_assessment"]["case_id"] == value_case.case_id
        # The assessment must not leak into actual recovered revenue
        assert data["case"]["recovered_amount"] == 0.0

    def test_value_endpoint_reports_malformed_stored_value(self, client, db_session, value_customer, value_transaction):
        """A bad stored probability yields a clear 422, not a 500."""
        case = _make_case(
            db_session, value_customer, value_transaction,
            case_id="CASE-VALUE-MALFORMED", recovery_probability=1.4,
        )

        response = client.get(f"/api/cases/{case.case_id}/value")

        assert response.status_code == 422
        body = response.json()
        assert body["success"] is False
        assert "probability must be within [0, 1]" in body["error"]["message"]

    def test_case_detail_survives_malformed_stored_value(self, client, db_session, value_customer, value_transaction):
        """The detail view still renders, reporting why the estimate is missing."""
        case = _make_case(
            db_session, value_customer, value_transaction,
            case_id="CASE-VALUE-MALFORMED-2", recovery_probability=1.4,
        )

        response = client.get(f"/api/cases/{case.case_id}/detail")

        assert response.status_code == 200
        data = response.json()["data"]
        assert data["value_assessment"] is None
        assert "probability must be within [0, 1]" in data["value_assessment_error"]
        assert data["case"]["case_id"] == case.case_id

    def test_service_lookup_by_id(self, db_session, value_case):
        assert assess_case_value_by_id(db_session, value_case.case_id)["case_id"] == value_case.case_id
        assert assess_case_value_by_id(db_session, "MISSING") is None
