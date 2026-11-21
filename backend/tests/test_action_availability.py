"""
Phase 2.1 — recovery action availability and recommendation ranking.

The value engine used to rank SEND_PAYMENT_REMINDER as the cheapest action even
though this deployment has no email/SMS provider, so it recommended an action it
could never execute.  These tests pin the correction:

- the capability catalog distinguishes executable, simulation-only and
  unavailable actions, and names the missing integration;
- an unavailable action is never ranked or recommended, even when it is cheapest;
- the Policy Engine consults the same catalog, so a recommendation and an
  authorization cannot disagree, and a named reviewer cannot override missing
  integration;
- the reminder functionality itself is preserved: it flips to executable the
  moment a delivery channel is configured.
"""
from datetime import datetime, timezone

import pytest

from app.models import (
    AuditEvent,
    Customer,
    PolicyDecision,
    RevenueRiskCase,
    Transaction,
)
from app.services import action_availability as catalog
from app.services.action_availability import (
    DECISION_ONLY_ACTIONS,
    EXECUTABLE,
    EXECUTOR_ACTIONS,
    KNOWN_ACTIONS,
    SIMULATED_ONLY,
    UNAVAILABLE,
    action_availability,
    executable_actions,
    is_executable,
    simulated_only_actions,
    unavailable_actions,
)
from app.services.dashboard_service import get_case_detail_with_relations
from app.services.policy_engine import evaluate_action
from app.services.value_service import (
    EXECUTABLE_ACTIONS,
    assess_case_value,
)

COSTS = {
    "CREATE_PAYMENT_LINK": 20.0,
    "RETRY": 5.0,
    "SEND_PAYMENT_REMINDER": 1.0,  # cheapest, but not executable here
}


# ============================================================================
# FIXTURES
# ============================================================================


@pytest.fixture
def policy(db_session):
    """An active policy so evaluate_action does not create one mid-test."""
    from app.services.policy_engine import get_active_policy

    return get_active_policy(db_session)


@pytest.fixture
def case(db_session):
    """An open case with a usable probability and a normal amount."""
    customer = Customer(
        customer_id="CUST-AVAIL-001",
        name="Availability Test",
        email="avail@example.com",
    )
    db_session.add(customer)
    db_session.commit()

    transaction = Transaction(
        transaction_id="TXN-AVAIL-001",
        customer_id=customer.id,
        amount=2500.0,
        currency="INR",
        status="FAILED",
        payment_method="card",
        failure_reason="insufficient_funds",
    )
    db_session.add(transaction)
    db_session.commit()

    record = RevenueRiskCase(
        case_id="CASE-AVAIL-001",
        transaction_id=transaction.id,
        customer_id=customer.id,
        amount=2500.0,
        risk_score=0.72,
        recovery_probability=0.65,
        priority="HIGH",
        status="OPEN",
        attempt_count=0,
        recovered_amount=0.0,
        created_at=datetime.now(timezone.utc),
    )
    db_session.add(record)
    db_session.commit()
    db_session.refresh(record)
    return record


# ============================================================================
# 1. THE CATALOG
# ============================================================================


class TestCapabilityCatalog:
    """Every action type is classified with a reason, not just a yes/no."""

    def test_executable_actions_are_reported_as_executable(self):
        assert executable_actions() == ("CREATE_PAYMENT_LINK", "RETRY")
        for action in executable_actions():
            entry = action_availability(action)
            assert entry["availability"] == EXECUTABLE
            assert entry["executable"] is True
            assert entry["reason"].startswith("executable")

    def test_reminder_is_simulation_only_while_no_provider_exists(self):
        entry = action_availability("SEND_PAYMENT_REMINDER")

        assert entry["availability"] == SIMULATED_ONLY
        assert entry["executable"] is False
        assert entry["simulated_only"] is True
        assert entry["available_in_simulation"] is True
        assert entry["required_integration"].startswith("reminder_delivery_provider")
        assert "no reminder delivery channel" in entry["reason"]
        # A real case is told the truth about what would happen.
        assert "NOT_SENT" in entry["reason"]
        assert simulated_only_actions() == ("SEND_PAYMENT_REMINDER",)
        assert "SEND_PAYMENT_REMINDER" not in executable_actions()

    def test_reminder_becomes_executable_once_a_channel_is_configured(self, monkeypatch):
        """The reminder is preserved: configuring a channel makes it executable."""
        monkeypatch.setattr(
            catalog, "reminder_delivery_channel", lambda: "smtp-test-channel"
        )

        entry = action_availability("SEND_PAYMENT_REMINDER")

        assert entry["availability"] == EXECUTABLE
        assert entry["executable"] is True
        assert "smtp-test-channel" in entry["reason"]
        assert "SEND_PAYMENT_REMINDER" in executable_actions()
        assert simulated_only_actions() == ()

    def test_decision_only_actions_are_unavailable(self):
        for action in DECISION_ONLY_ACTIONS:
            entry = action_availability(action)
            assert entry["availability"] == UNAVAILABLE
            assert entry["executable"] is False
            assert "decision" in entry["reason"]

    def test_unknown_action_is_unavailable(self):
        entry = action_availability("TELEPORT_MONEY")

        assert entry["availability"] == UNAVAILABLE
        assert entry["executable"] is False
        assert "not a known recovery action type" in entry["reason"]

    def test_catalog_covers_every_known_action(self):
        entries = catalog.availability_catalog()

        assert [e["action"] for e in entries] == list(KNOWN_ACTIONS)
        assert all(e["reason"] for e in entries)
        assert unavailable_actions() == ()


# ============================================================================
# 2. RECOMMENDATION RANKING
# ============================================================================


class TestRankingExcludesUnavailableActions:
    def test_reminder_is_never_recommended_even_when_it_is_cheapest(self, db_session, case):
        assessment = assess_case_value(db_session, case, costs=COSTS)

        ranked = [c["action"] for c in assessment["candidates"]]
        assert "SEND_PAYMENT_REMINDER" not in ranked
        assert assessment["recommended_action"] in EXECUTABLE_ACTIONS
        assert assessment["executable_actions"] == list(EXECUTABLE_ACTIONS)

        unavailable = {u["action"]: u for u in assessment["unavailable_actions"]}
        reminder = unavailable["SEND_PAYMENT_REMINDER"]
        assert reminder["availability"] == SIMULATED_ONLY
        assert reminder["required_integration"].startswith("reminder_delivery_provider")
        assert "no reminder delivery channel" in reminder["reason"]
        # The cheapest action is the one that is skipped.
        assert COSTS["SEND_PAYMENT_REMINDER"] < min(
            COSTS[c["action"]] for c in assessment["candidates"]
        )

    def test_assessment_reports_the_whole_catalog(self, db_session, case):
        assessment = assess_case_value(db_session, case, costs=COSTS)

        reported = {e["action"]: e for e in assessment["action_availability"]}
        assert set(reported) == set(KNOWN_ACTIONS)
        assert reported["SEND_PAYMENT_REMINDER"]["executable"] is False
        assert reported["CREATE_PAYMENT_LINK"]["executable"] is True
        assert assessment["availability_source"] == "app.services.action_availability"

    def test_simulated_case_reminder_is_described_as_simulation_only(self, db_session, case):
        case.case_id = "SIM-CASE-AVAIL-000001"
        db_session.commit()

        assessment = assess_case_value(db_session, case, costs=COSTS)

        unavailable = {u["action"]: u for u in assessment["unavailable_actions"]}
        reminder = unavailable["SEND_PAYMENT_REMINDER"]
        assert reminder["available_in_simulation"] is True
        assert "SIMULATED" in reminder["reason"]
        assert assessment["recommended_action"] in EXECUTABLE_ACTIONS

    def test_agent_recommendations_are_always_executable(self):
        """No rule may recommend a recovery action this deployment cannot run."""
        from app.services.agent_service import ACTION_BY_CAUSE

        recommended_recovery_actions = {
            action for action in ACTION_BY_CAUSE.values()
            if action in EXECUTOR_ACTIONS
        }
        assert recommended_recovery_actions, "the rules must recommend something"
        for action in recommended_recovery_actions:
            assert is_executable(action), f"{action} is recommended but not executable"
        assert "SEND_PAYMENT_REMINDER" not in recommended_recovery_actions


# ============================================================================
# 3. POLICY ENGINE AGREEMENT
# ============================================================================


class TestPolicyAgreesWithTheCatalog:
    def test_policy_refuses_an_unavailable_action(self, db_session, case, policy):
        result = evaluate_action(
            db=db_session,
            case_id=case.case_id,
            proposed_action="SEND_PAYMENT_REMINDER",
            confidence=0.8,
            recovery_probability=0.65,
        )

        assert result.allowed is False
        assert result.decision == "BLOCKED"
        assert result.code == "ACTION_UNAVAILABLE"
        assert result.action_availability["availability"] == SIMULATED_ONLY
        assert result.action_availability["executable"] is False
        assert "not executable in this deployment" in result.reason
        rule = next(c for c in result.checks if c.rule == "ACTION_AVAILABILITY")
        assert rule.passed is False

        # The refusal is stored with its code and availability, so it can be
        # explained later even after the deployment changes.
        audit = db_session.query(AuditEvent).filter(
            AuditEvent.case_id == case.id,
            AuditEvent.event_type == "POLICY_CHECKED",
        ).one()
        assert audit.decision == "BLOCKED"
        assert audit.metadata_["code"] == "ACTION_UNAVAILABLE"
        assert audit.metadata_["action_availability"]["executable"] is False

    def test_a_named_reviewer_cannot_override_missing_integration(self, db_session, case, policy):
        result = evaluate_action(
            db=db_session,
            case_id=case.case_id,
            proposed_action="SEND_PAYMENT_REMINDER",
            confidence=0.8,
            recovery_probability=0.65,
            authorized_by="Asha Reviewer",
        )

        assert result.allowed is False
        assert result.code == "ACTION_UNAVAILABLE"
        assert result.decision == "BLOCKED"

    def test_ranking_and_authorization_cannot_disagree(self, db_session, case, policy):
        for action in EXECUTOR_ACTIONS:
            ranked = action in executable_actions()
            decision = evaluate_action(
                db=db_session,
                case_id=case.case_id,
                proposed_action=action,
                confidence=0.65,
                recovery_probability=0.65,
                persist=False,
            )
            assert decision.allowed is ranked, (
                f"{action}: ranking executable={ranked} but policy "
                f"decision={decision.decision}"
            )

    def test_simulation_data_may_run_a_simulation_only_action(self, db_session, case, policy):
        """SIMULATED_ONLY means exactly that: the simulation pipeline can run it."""
        case.case_id = "SIM-CASE-AVAIL-000001"
        db_session.commit()

        result = evaluate_action(
            db=db_session,
            case_id=case.case_id,
            proposed_action="SEND_PAYMENT_REMINDER",
            confidence=0.65,
            recovery_probability=0.65,
            persist=False,
        )

        assert result.allowed is True
        # Authorized for simulation, and still never presented as executable.
        assert result.action_availability["availability"] == SIMULATED_ONLY
        assert result.action_availability["executable"] is False
        assert result.action_availability["available_in_simulation"] is True

    def test_policy_approval_carries_the_availability_it_checked(self, db_session, case, policy):
        result = evaluate_action(
            db=db_session,
            case_id=case.case_id,
            proposed_action="CREATE_PAYMENT_LINK",
            confidence=0.65,
            recovery_probability=0.65,
            persist=False,
        )

        assert result.allowed is True
        assert result.action_availability["availability"] == EXECUTABLE
        assert result.action_availability["executable"] is True
        assert result.code is None

    def test_no_policy_rows_are_written_by_a_read_only_precheck(self, db_session, case, policy):
        evaluate_action(
            db=db_session,
            case_id=case.case_id,
            proposed_action="SEND_PAYMENT_REMINDER",
            confidence=0.8,
            recovery_probability=0.65,
            persist=False,
        )

        assert db_session.query(PolicyDecision).count() == 0


# ============================================================================
# 4. THE CASE VIEW EXPLAINS UNAVAILABILITY
# ============================================================================


class TestCaseDetailReportsAvailability:
    def test_case_detail_includes_the_availability_catalog(self, db_session, case):
        detail = get_case_detail_with_relations(db_session, case.case_id)

        entries = {e["action"]: e for e in detail["action_availability"]}
        assert set(entries) == set(KNOWN_ACTIONS)
        reminder = entries["SEND_PAYMENT_REMINDER"]
        assert reminder["executable"] is False
        assert reminder["availability"] == SIMULATED_ONLY
        assert reminder["required_integration"].startswith("reminder_delivery_provider")
        assert entries["RETRY"]["executable"] is True
