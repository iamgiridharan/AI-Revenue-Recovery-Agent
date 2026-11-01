"""
Human review of escalated cases.

Covered here:
- GET /api/reviews/queue lists escalated cases with the policy rule that escalated
  each one
- POST /api/cases/{id}/review records a named reviewer's decision
- APPROVE executes the recorded AI recommendation and takes the case out of
  ESCALATED, with the reviewer named in the audit trail
- OVERRIDE executes a different executable action and requires a written reason
- CLOSE / MARK_UNRECOVERABLE close the case without recovering it
- an approval is not a bypass: a hard policy stop (expired case, exhausted attempt
  budget) still refuses, the case stays ESCALATED and the refusal is recorded
- the request can never supply the amount or the confidence
"""
from datetime import datetime, timedelta, timezone

import pytest

from app.models import (
    AuditEvent,
    AuditEventType,
    CaseStatus,
    Customer,
    PolicyConfig,
    PolicyDecision,
    RecoveryAction,
    RevenueRiskCase,
    Transaction,
)

CASE_ID = "CASE-REVIEW-001"


# ============================================================================
# Fixtures
# ============================================================================


@pytest.fixture(autouse=True)
def no_provider_credentials(monkeypatch):
    """Keep the suite away from the real Razorpay API."""
    monkeypatch.setattr(
        "app.services.razorpay_service.get_razorpay_client", lambda: None
    )


@pytest.fixture
def active_policy(db_session):
    """Ensure an active policy configuration exists."""
    existing = (
        db_session.query(PolicyConfig)
        .filter(PolicyConfig.is_active == True)  # noqa: E712
        .first()
    )
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
        is_active=True,
        description="Human review test policy",
    )
    db_session.add(policy)
    db_session.commit()
    db_session.refresh(policy)
    return policy


@pytest.fixture
def customer(db_session):
    customer = Customer(
        customer_id="CUST-REVIEW-001",
        name="Review Customer",
        email="review@example.com",
        phone="+919876500001",
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
    """Above the ₹10,000 autonomous limit, so policy escalates this case."""
    txn = Transaction(
        transaction_id="TXN-REVIEW-001",
        customer_id=customer.id,
        amount=15000.0,
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


# ============================================================================
# Helpers
# ============================================================================


def _make_case(
    db_session,
    customer,
    transaction,
    case_id=CASE_ID,
    status=CaseStatus.ESCALATED.value,
    age_days=0,
):
    case = RevenueRiskCase(
        case_id=case_id,
        transaction_id=transaction.id,
        customer_id=customer.id,
        amount=transaction.amount,
        risk_score=0.75,
        recovery_probability=0.82,
        priority="HIGH",
        status=status,
        attempt_count=0,
        recovered_amount=0.0,
    )
    if age_days:
        case.created_at = datetime.now(timezone.utc) - timedelta(days=age_days)
    db_session.add(case)
    db_session.commit()
    db_session.refresh(case)
    return case


def _record_diagnosis(db_session, case, action="CREATE_PAYMENT_LINK",
                      confidence=0.88, probability=0.82):
    db_session.add(AuditEvent(
        case_id=case.id,
        event_type=AuditEventType.DIAGNOSIS_COMPLETED.value,
        actor="ai_agent",
        decision=action,
        reason="Test diagnosis",
        confidence=confidence,
        action=action,
        result="SUCCESS",
        metadata_={"recovery_probability": probability},
    ))
    db_session.commit()


def _record_escalation(db_session, case, action="CREATE_PAYMENT_LINK"):
    db_session.add(AuditEvent(
        case_id=case.id,
        event_type=AuditEventType.CASE_ESCALATED.value,
        actor="recovery_service",
        decision="ESCALATED",
        reason="Policy escalated: Amount exceeds autonomous limit",
        confidence=0.88,
        policy_checks=[{
            "rule": "AUTONOMOUS_AMOUNT_LIMIT",
            "passed": False,
            "reason": f"Amount {case.amount} exceeds limit 10000.0",
            "value": case.amount,
            "limit": 10000.0,
        }],
        action=action,
        result="ESCALATED",
    ))
    db_session.commit()


def _escalated_case(db_session, customer, transaction, **kwargs):
    """An escalated case with a recorded AI diagnosis and escalation event."""
    case = _make_case(db_session, customer, transaction, **kwargs)
    _record_diagnosis(db_session, case)
    _record_escalation(db_session, case)
    return case


def _refresh_case(db_session, case_id=CASE_ID):
    db_session.expire_all()
    return (
        db_session.query(RevenueRiskCase)
        .filter(RevenueRiskCase.case_id == case_id)
        .first()
    )


def _review_events(db_session, case):
    return (
        db_session.query(AuditEvent)
        .filter(
            AuditEvent.case_id == case.id,
            AuditEvent.event_type == AuditEventType.HUMAN_REVIEW_COMPLETED.value,
        )
        .order_by(AuditEvent.id.asc())
        .all()
    )


# ============================================================================
# APPROVE
# ============================================================================


class TestApprove:
    def test_approve_executes_recorded_recommendation(
        self, client, db_session, active_policy, customer, transaction
    ):
        case = _escalated_case(db_session, customer, transaction)

        response = client.post(f"/api/cases/{CASE_ID}/review", json={
            "reviewer": "Asha Reviewer",
            "decision": "APPROVE",
            "note": "Verified the customer history by phone",
        })

        assert response.status_code == 200
        data = response.json()["data"]
        assert data["decision"] == "APPROVE"
        assert data["reviewer"] == "Asha Reviewer"
        assert data["action_type"] == "CREATE_PAYMENT_LINK"
        assert data["case_status"] == "RECOVERY_ATTEMPTED"
        # No Razorpay credentials in tests: no provider call, clearly labelled.
        assert data["execution"]["execution_mode"] == "MOCK"
        assert data["execution"]["mock"] is True
        # The amount is the transaction amount, never anything the reviewer sent.
        assert data["execution"]["amount"] == 15000.0
        # The autonomy threshold the escalation was raised for is named as
        # overridden by this reviewer.
        assert "AUTONOMOUS_AMOUNT_LIMIT" in data["policy_decision"]["reason"]
        assert "Asha Reviewer" in data["policy_decision"]["reason"]

        stored = _refresh_case(db_session)
        assert stored.status == "RECOVERY_ATTEMPTED"
        assert stored.attempt_count == 1

        events = _review_events(db_session, stored)
        assert len(events) == 1
        assert events[0].actor == "Asha Reviewer"
        assert events[0].decision == "APPROVED"
        assert events[0].action == "CREATE_PAYMENT_LINK"
        assert events[0].metadata_["reviewer"] == "Asha Reviewer"
        assert "AUTONOMOUS_AMOUNT_LIMIT" in events[0].metadata_["overridden_rules"]

        # The authoritative persisted policy decision names the human authorizer.
        decision = (
            db_session.query(PolicyDecision)
            .filter(PolicyDecision.case_id == stored.id)
            .order_by(PolicyDecision.id.desc())
            .first()
        )
        assert decision.decision == "APPROVED"
        assert "Asha Reviewer" in decision.reason

        actions = (
            db_session.query(RecoveryAction)
            .filter(RecoveryAction.case_id == stored.id)
            .all()
        )
        assert [a.action_type for a in actions] == ["CREATE_PAYMENT_LINK"]
        assert actions[0].execution_status == "SUCCESS"

    def test_approved_case_cannot_be_reviewed_twice(
        self, client, db_session, active_policy, customer, transaction
    ):
        _escalated_case(db_session, customer, transaction)
        first = client.post(f"/api/cases/{CASE_ID}/review", json={
            "reviewer": "Asha Reviewer", "decision": "APPROVE",
        })
        assert first.status_code == 200

        second = client.post(f"/api/cases/{CASE_ID}/review", json={
            "reviewer": "Asha Reviewer", "decision": "APPROVE",
        })
        assert second.status_code == 409
        assert second.json()["error"]["code"] == "CASE_NOT_ESCALATED"

    def test_approve_refuses_a_different_action(
        self, client, db_session, active_policy, customer, transaction
    ):
        _escalated_case(db_session, customer, transaction)

        response = client.post(f"/api/cases/{CASE_ID}/review", json={
            "reviewer": "Asha Reviewer",
            "decision": "APPROVE",
            "action_type": "RETRY",
            "note": "wants a retry",
        })

        assert response.status_code == 400
        assert response.json()["error"]["code"] == "OVERRIDE_REQUIRED"
        assert _refresh_case(db_session).status == "ESCALATED"


# ============================================================================
# OVERRIDE
# ============================================================================


class TestOverride:
    def test_override_executes_a_different_action(
        self, client, db_session, active_policy, customer, transaction
    ):
        _escalated_case(db_session, customer, transaction)

        response = client.post(f"/api/cases/{CASE_ID}/review", json={
            "reviewer": "Ravi Ops",
            "decision": "OVERRIDE",
            "action_type": "RETRY",
            "note": "Card was replaced; retrying is cheaper than a fresh link",
        })

        assert response.status_code == 200
        data = response.json()["data"]
        assert data["recorded_action"] == "CREATE_PAYMENT_LINK"
        assert data["action_type"] == "RETRY"
        assert data["case_status"] == "RECOVERY_ATTEMPTED"

        stored = _refresh_case(db_session)
        actions = (
            db_session.query(RecoveryAction)
            .filter(RecoveryAction.case_id == stored.id)
            .all()
        )
        assert [a.action_type for a in actions] == ["RETRY"]
        events = _review_events(db_session, stored)
        assert events[0].decision == "APPROVED"
        assert events[0].metadata_["review_decision"] == "OVERRIDE"
        assert events[0].actor == "Ravi Ops"

    def test_override_requires_action_and_reason(
        self, client, db_session, active_policy, customer, transaction
    ):
        _escalated_case(db_session, customer, transaction)

        no_action = client.post(f"/api/cases/{CASE_ID}/review", json={
            "reviewer": "Ravi Ops", "decision": "OVERRIDE", "note": "no action",
        })
        assert no_action.status_code == 422

        no_note = client.post(f"/api/cases/{CASE_ID}/review", json={
            "reviewer": "Ravi Ops", "decision": "OVERRIDE", "action_type": "RETRY",
        })
        assert no_note.status_code == 422

        assert _refresh_case(db_session).status == "ESCALATED"

    def test_override_cannot_supply_the_amount(
        self, client, db_session, active_policy, customer, transaction
    ):
        """The request schema has no amount field; extra input is ignored."""
        _escalated_case(db_session, customer, transaction)

        response = client.post(f"/api/cases/{CASE_ID}/review", json={
            "reviewer": "Ravi Ops",
            "decision": "OVERRIDE",
            "action_type": "CREATE_PAYMENT_LINK",
            "note": "approve the same action with a smaller amount",
            "amount": 1.0,
            "confidence": 0.99,
        })

        assert response.status_code == 200
        assert response.json()["data"]["execution"]["amount"] == 15000.0


# ============================================================================
# Approval is not a bypass
# ============================================================================


class TestHardStopsStillApply:
    def test_expired_case_stays_escalated(
        self, client, db_session, active_policy, customer, transaction
    ):
        _escalated_case(db_session, customer, transaction, age_days=30)

        response = client.post(f"/api/cases/{CASE_ID}/review", json={
            "reviewer": "Asha Reviewer", "decision": "APPROVE",
        })

        assert response.status_code == 409
        body = response.json()
        assert body["error"]["code"] == "POLICY_BLOCKED"
        assert "expired" in body["error"]["message"].lower()
        assert body["case_status"] == "ESCALATED"

        stored = _refresh_case(db_session)
        assert stored.status == "ESCALATED"
        assert stored.attempt_count == 0

        # The refusal is recorded against the reviewer, not silently dropped.
        events = _review_events(db_session, stored)
        assert len(events) == 1
        assert events[0].actor == "Asha Reviewer"
        assert events[0].decision == "REJECTED_BY_POLICY"

    def test_exhausted_attempt_budget_stays_escalated(
        self, client, db_session, active_policy, customer, transaction
    ):
        case = _escalated_case(db_session, customer, transaction)
        for _ in range(3):  # policy.max_recovery_attempts == 3
            db_session.add(RecoveryAction(
                case_id=case.id,
                action_type="CREATE_PAYMENT_LINK",
                reason="earlier attempt",
                confidence=0.9,
                policy_result="APPROVED",
                execution_status="SUCCESS",
            ))
        db_session.commit()

        response = client.post(f"/api/cases/{CASE_ID}/review", json={
            "reviewer": "Ravi Ops",
            "decision": "OVERRIDE",
            "action_type": "RETRY",
            "note": "trying once more",
        })

        assert response.status_code == 409
        assert response.json()["error"]["code"] == "POLICY_BLOCKED"
        assert _refresh_case(db_session).status == "ESCALATED"


# ============================================================================
# CLOSE / MARK_UNRECOVERABLE
# ============================================================================


class TestClose:
    def test_close_settles_the_case(
        self, client, db_session, active_policy, customer, transaction
    ):
        _escalated_case(db_session, customer, transaction)

        response = client.post(f"/api/cases/{CASE_ID}/review", json={
            "reviewer": "Asha Reviewer",
            "decision": "CLOSE",
            "note": "Customer paid through another channel",
        })

        assert response.status_code == 200
        data = response.json()["data"]
        assert data["case_status"] == "CLOSED"
        assert data["previous_status"] == "ESCALATED"
        assert data["recovered_amount"] == 0.0

        stored = _refresh_case(db_session)
        assert stored.status == "CLOSED"
        events = _review_events(db_session, stored)
        assert events[0].decision == "CLOSE"
        assert events[0].actor == "Asha Reviewer"

        # A closed case is out of the recovery flow for good.
        execution = client.post(f"/api/cases/{CASE_ID}/recovery", json={})
        assert execution.status_code == 409

    def test_mark_unrecoverable_requires_a_reason(
        self, client, db_session, active_policy, customer, transaction
    ):
        _escalated_case(db_session, customer, transaction)

        no_reason = client.post(f"/api/cases/{CASE_ID}/review", json={
            "reviewer": "Asha Reviewer", "decision": "MARK_UNRECOVERABLE",
        })
        assert no_reason.status_code == 422

        with_reason = client.post(f"/api/cases/{CASE_ID}/review", json={
            "reviewer": "Asha Reviewer",
            "decision": "MARK_UNRECOVERABLE",
            "note": "Customer is uncontactable; write-off approved by finance",
        })
        assert with_reason.status_code == 200
        assert with_reason.json()["data"]["case_status"] == "CLOSED"
        assert _refresh_case(db_session).status == "CLOSED"

    def test_recovered_case_is_never_closed(
        self, client, db_session, active_policy, customer, transaction
    ):
        case = _make_case(db_session, customer, transaction)
        case.status = CaseStatus.RECOVERED.value
        case.recovered_amount = 15000.0
        db_session.commit()

        response = client.post(f"/api/cases/{CASE_ID}/review", json={
            "reviewer": "Asha Reviewer", "decision": "CLOSE", "note": "cleanup",
        })

        assert response.status_code == 409
        assert response.json()["error"]["code"] == "CASE_ALREADY_RECOVERED"
        stored = _refresh_case(db_session)
        assert stored.status == "RECOVERED"
        assert stored.recovered_amount == 15000.0


# ============================================================================
# Guards and the queue
# ============================================================================


class TestGuards:
    def test_review_requires_a_named_reviewer(
        self, client, db_session, active_policy, customer, transaction
    ):
        _escalated_case(db_session, customer, transaction)

        missing = client.post(f"/api/cases/{CASE_ID}/review", json={
            "decision": "CLOSE",
        })
        assert missing.status_code == 422

        blank = client.post(f"/api/cases/{CASE_ID}/review", json={
            "reviewer": "   ", "decision": "CLOSE",
        })
        assert blank.status_code == 422
        assert _refresh_case(db_session).status == "ESCALATED"

    def test_non_escalated_case_is_refused(
        self, client, db_session, active_policy, customer, transaction
    ):
        case = _make_case(db_session, customer, transaction, status=CaseStatus.OPEN.value)
        _record_diagnosis(db_session, case)

        response = client.post(f"/api/cases/{CASE_ID}/review", json={
            "reviewer": "Asha Reviewer", "decision": "APPROVE",
        })

        assert response.status_code == 409
        assert response.json()["error"]["code"] == "CASE_NOT_ESCALATED"

    def test_approve_needs_a_recorded_diagnosis(
        self, client, db_session, active_policy, customer, transaction
    ):
        _make_case(db_session, customer, transaction)

        response = client.post(f"/api/cases/{CASE_ID}/review", json={
            "reviewer": "Asha Reviewer", "decision": "APPROVE",
        })

        assert response.status_code == 409
        assert response.json()["error"]["code"] == "NO_RECORDED_DIAGNOSIS"

    def test_unknown_case_is_not_found(self, client, db_session, active_policy):
        response = client.post("/api/cases/DOES-NOT-EXIST/review", json={
            "reviewer": "Asha Reviewer", "decision": "CLOSE",
        })
        assert response.status_code == 404
        assert response.json()["error"]["code"] == "CASE_NOT_FOUND"


class TestReviewQueue:
    def test_lists_escalated_cases_with_the_blocking_rule(
        self, client, db_session, active_policy, customer, transaction
    ):
        _escalated_case(db_session, customer, transaction)
        _make_case(
            db_session, customer, transaction,
            case_id="CASE-REVIEW-OPEN", status=CaseStatus.OPEN.value,
        )

        response = client.get("/api/reviews/queue")

        assert response.status_code == 200
        data = response.json()["data"]
        assert data["total"] == 1
        assert data["total_amount_at_risk"] == 15000.0
        item = data["cases"][0]
        assert item["case_id"] == CASE_ID
        assert item["recommended_action"] == "CREATE_PAYMENT_LINK"
        assert item["confidence"] == 0.88
        assert item["has_recorded_diagnosis"] is True
        assert item["escalation"]["rule"] == "AUTONOMOUS_AMOUNT_LIMIT"
        assert "exceeds limit" in item["escalation"]["rule_reason"]

    def test_empty_queue(self, client, db_session, active_policy):
        response = client.get("/api/reviews/queue")
        assert response.status_code == 200
        data = response.json()["data"]
        assert data["total"] == 0
        assert data["cases"] == []

    def test_queue_is_read_only(
        self, client, db_session, active_policy, customer, transaction
    ):
        _escalated_case(db_session, customer, transaction)
        client.get("/api/reviews/queue")
        assert _refresh_case(db_session).status == "ESCALATED"
