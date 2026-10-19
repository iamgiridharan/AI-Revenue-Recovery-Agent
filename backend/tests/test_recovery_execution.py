"""
Phase 4 tests: controlled recovery execution and verified webhook settlement.

Covered here:
- POST /api/cases/{case_id}/recovery: the recorded AI recommendation is executed
  only after the Policy Engine authorizes it
- refusals: recovered, closed and escalated (pending human review) cases, and a
  case with no recorded diagnosis
- the amount and the confidence always come from stored records, never the caller
- a Razorpay Test Mode call is labelled RAZORPAY_TEST_MODE; a run without
  credentials is labelled MOCK and is never presented as a payment
- provider failures are recorded as failed actions, not as recoveries
- the webhook settles the case for the executed payment link, refuses an
  authorization that was not captured, and never books a duplicate event twice
- duplicate protection is durable (a delivery recorded by an earlier process is
  still recognised as a duplicate)

No real Razorpay Test Mode API call is made by these tests: the provider client is
replaced with a stub.  Real Test Mode verification requires live credentials.
"""
import hashlib
import hmac
import json
from unittest.mock import MagicMock, patch

import pytest
from sqlalchemy.exc import IntegrityError

from app.models import (
    AuditEvent,
    CaseStatus,
    Customer,
    PolicyConfig,
    PolicyDecision,
    RecoveryAction,
    RevenueRiskCase,
    Transaction,
    WebhookEvent,
)
from app.services.agent_service import diagnose_case

CASE_ID = "CASE-PHASE4-001"
WEBHOOK_SECRET = "phase4_test_webhook_secret"


# ============================================================================
# Fixtures
# ============================================================================


@pytest.fixture(autouse=True)
def no_provider_credentials(monkeypatch):
    """
    Keep the suite away from the real Razorpay API.

    Every test that executes a recovery action gets a stubbed provider client, so
    the result is deterministic and no payment link is created on a real Test Mode
    account even when credentials happen to be configured in .env. Tests that want
    a provider call replace this stub explicitly.
    """
    monkeypatch.setattr(
        "app.services.razorpay_service.get_razorpay_client", lambda: None
    )


@pytest.fixture
def active_policy(db_session):
    """Ensure an active policy configuration exists."""
    existing = db_session.query(PolicyConfig).filter(PolicyConfig.is_active == True).first()  # noqa: E712
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
        description="Phase 4 test policy",
    )
    db_session.add(policy)
    db_session.commit()
    db_session.refresh(policy)
    return policy


@pytest.fixture
def customer(db_session):
    customer = Customer(
        customer_id="CUST-PHASE4-001",
        name="Phase Four",
        email="phase4@example.com",
        phone="+919876500000",
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
    txn = Transaction(
        transaction_id="TXN-PHASE4-001",
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
    case = RevenueRiskCase(
        case_id=CASE_ID,
        transaction_id=transaction.id,
        customer_id=customer.id,
        amount=2500.0,
        risk_score=0.75,
        recovery_probability=0.65,
        priority="HIGH",
        status=CaseStatus.OPEN.value,
        attempt_count=0,
        recovered_amount=0.0,
    )
    db_session.add(case)
    db_session.commit()
    db_session.refresh(case)
    return case


def _record_diagnosis(db_session, case_id=CASE_ID, confidence=0.88,
                      recovery_probability=0.82, action="CREATE_PAYMENT_LINK"):
    """
    Record a diagnosis the way the agent service does.

    The audit event is the stored recommendation that the execution endpoint
    reads; the case's recommended_action is set alongside it.
    """
    event = AuditEvent(
        case_id=db_session.query(RevenueRiskCase).filter(
            RevenueRiskCase.case_id == case_id
        ).first().id,
        event_type="DIAGNOSIS_COMPLETED",
        actor="ai_agent",
        decision=action,
        reason="recorded diagnosis",
        confidence=confidence,
        action=action,
        result=action,
        metadata_={"recovery_probability": recovery_probability},
    )
    db_session.add(event)

    case = db_session.query(RevenueRiskCase).filter(
        RevenueRiskCase.case_id == case_id
    ).first()
    case.recommended_action = action
    db_session.commit()
    return event


def _fake_provider_link(payload, link_id="plink_phase4_real"):
    """Build a Razorpay-shaped payment link response."""
    return {
        "id": link_id,
        "short_url": f"https://rzp.io/rzp/{link_id}",
        "status": "created",
        "amount": payload["amount"],
        "currency": payload["currency"],
        "expires_by": 1893456000,
        "created_at": 1790000000,
    }


def _signed_webhook(client, payload, event_id, secret=WEBHOOK_SECRET):
    """Deliver a correctly signed webhook to the handler."""
    body = json.dumps(payload)
    signature = hmac.new(secret.encode(), body.encode(), hashlib.sha256).hexdigest()
    with patch("app.services.razorpay_service.settings") as mock_settings:
        mock_settings.RAZORPAY_WEBHOOK_SECRET = secret
        return client.post(
            "/api/webhooks/razorpay",
            content=body,
            headers={
                "Content-Type": "application/json",
                "X-Razorpay-Signature": signature,
                "X-Razorpay-Event-Id": event_id,
            },
        )


def _captured_event(reference_id=CASE_ID, payment_id="pay_phase4_1", amount_paise=250000):
    return {
        "event": "payment.captured",
        "payload": {
            "payment": {
                "entity": {
                    "id": payment_id,
                    "amount": amount_paise,
                    "currency": "INR",
                    "status": "captured",
                    "captured": True,
                    "notes": {"reference_id": reference_id, "case_id": reference_id},
                }
            }
        },
    }


# ============================================================================
# A. Controlled recovery execution
# ============================================================================


class TestRecoveryExecutionEndpoint:

    def test_executes_recorded_recommendation_and_labels_mock_execution(
        self, client, db_session, risk_case, active_policy
    ):
        """
        Without provider credentials there is no Razorpay call at all. The response
        must say so, and the case must not be presented as paid.
        """
        _record_diagnosis(db_session)

        response = client.post(f"/api/cases/{CASE_ID}/recovery", json={})

        assert response.status_code == 200
        data = response.json()["data"]
        assert data["execution_mode"] == "MOCK"
        assert data["mock"] is True
        assert data["action_type"] == "CREATE_PAYMENT_LINK"
        assert data["amount"] == 2500.0
        # The case is awaiting payment - it is NOT recovered by creating a link.
        assert data["authorization"] == {
            "required": True, "authority": "policy_engine", "decision": "APPROVED",
        }

        db_session.refresh(risk_case)
        assert risk_case.status == CaseStatus.RECOVERY_ATTEMPTED.value
        assert risk_case.attempt_count == 1
        assert risk_case.recovered_amount == 0.0

        action = db_session.query(RecoveryAction).filter(
            RecoveryAction.case_id == risk_case.id
        ).one()
        assert action.policy_result == "APPROVED"
        assert action.execution_status == "SUCCESS"
        assert action.api_reference == data["payment_link_id"]

        audit = db_session.query(AuditEvent).filter(
            AuditEvent.case_id == risk_case.id,
            AuditEvent.event_type == "ACTION_EXECUTED",
        ).one()
        assert audit.metadata_["execution_mode"] == "MOCK"
        assert audit.metadata_["mock"] is True
        assert audit.metadata_["case_reference"] == CASE_ID
        assert "MOCK" in audit.reason

        # No credentials or amounts beyond the transaction amount are exposed.
        body = json.dumps(response.json())
        assert "KEY_SECRET" not in body.upper()
        assert "RAZORPAY_KEY" not in body

    def test_test_mode_call_labels_real_provider_execution(
        self, client, db_session, risk_case, active_policy
    ):
        """
        With a provider client available, the link is a real (Test Mode) Razorpay
        object, is labelled RAZORPAY_TEST_MODE, and carries the case reference both
        as reference_id and in notes so a webhook can be traced back to the case.
        """
        _record_diagnosis(db_session)
        provider = MagicMock()
        provider.payment_link.create.side_effect = lambda payload: _fake_provider_link(payload)

        with patch("app.services.razorpay_service.get_razorpay_client", return_value=provider):
            response = client.post(f"/api/cases/{CASE_ID}/recovery", json={})

        assert response.status_code == 200
        data = response.json()["data"]
        assert data["execution_mode"] == "RAZORPAY_TEST_MODE"
        assert data["mock"] is False
        assert data["payment_link"] == "https://rzp.io/rzp/plink_phase4_real"
        assert data["provider_status"] == "created"

        sent_payload = provider.payment_link.create.call_args[0][0]
        assert sent_payload["reference_id"] == CASE_ID
        assert sent_payload["notes"] == {"reference_id": CASE_ID, "case_id": CASE_ID}
        assert sent_payload["amount"] == 250000  # 2500.00 INR in paise
        assert sent_payload["customer"]["email"] == "phase4@example.com"

    def test_amount_and_confidence_come_from_records_not_the_caller(
        self, client, db_session, risk_case, active_policy
    ):
        """
        A caller cannot raise its own confidence to step over MINIMUM_AI_CONFIDENCE,
        nor change the amount that will be charged.
        """
        _record_diagnosis(db_session, confidence=0.4, recovery_probability=0.5)

        response = client.post(
            f"/api/cases/{CASE_ID}/recovery",
            json={
                "confidence": 0.99,
                "recovery_probability": 0.99,
                "amount": 1.0,
                "payment_link": "https://attacker.example/pay",
            },
        )

        assert response.status_code == 200
        assert response.json()["data"]["amount"] == 2500.0

        decision = db_session.query(PolicyDecision).filter(
            PolicyDecision.case_id == risk_case.id
        ).one()
        assert decision.confidence == 0.4
        assert decision.recovery_probability == 0.5
        assert decision.amount == 2500.0

    def test_requires_a_recorded_diagnosis(self, client, db_session, risk_case, active_policy):
        response = client.post(f"/api/cases/{CASE_ID}/recovery", json={})

        assert response.status_code == 409
        body = response.json()
        assert "no recorded AI diagnosis" in body["error"]["message"]
        assert db_session.query(RecoveryAction).count() == 0

    def test_unknown_case_is_404(self, client, db_session, active_policy):
        response = client.post("/api/cases/CASE-DOES-NOT-EXIST/recovery", json={})

        assert response.status_code == 404

    def test_non_executable_action_is_rejected(
        self, client, db_session, risk_case, active_policy
    ):
        """Only real recovery actions can be executed."""
        _record_diagnosis(db_session)

        response = client.post(
            f"/api/cases/{CASE_ID}/recovery", json={"action_type": "ESCALATE_TO_HUMAN"}
        )

        assert response.status_code == 422

    def test_recorded_escalation_decision_is_not_executable(
        self, client, db_session, risk_case, active_policy
    ):
        """
        A case whose recorded recommendation is 'escalate to human' has nothing to
        execute: the request must be refused rather than guessed at.
        """
        _record_diagnosis(db_session, action="ESCALATE_TO_HUMAN")

        response = client.post(f"/api/cases/{CASE_ID}/recovery", json={})

        assert response.status_code == 409
        assert "not an executable recovery action" in response.json()["error"]["message"]

    @pytest.mark.parametrize("status", [CaseStatus.RECOVERED.value, CaseStatus.CLOSED.value])
    def test_settled_cases_are_not_acted_on_again(
        self, client, db_session, risk_case, active_policy, status
    ):
        """A recovered or closed case must not receive a new payment link."""
        _record_diagnosis(db_session)
        risk_case.status = status
        db_session.commit()

        with patch("app.services.recovery_service.create_payment_link") as provider_call:
            response = client.post(f"/api/cases/{CASE_ID}/recovery", json={})

        assert response.status_code == 409
        assert response.json()["error"]["code"] in ("CASE_ALREADY_RECOVERED", "CASE_CLOSED")
        assert provider_call.called is False

        db_session.refresh(risk_case)
        assert risk_case.status == status
        assert risk_case.attempt_count == 0

    def test_escalated_case_awaiting_human_review_is_refused(
        self, client, db_session, risk_case, active_policy
    ):
        """An escalated case waits for a human: the autonomous flow must stop."""
        _record_diagnosis(db_session)
        risk_case.status = CaseStatus.ESCALATED.value
        db_session.commit()

        with patch("app.services.recovery_service.create_payment_link") as provider_call:
            response = client.post(f"/api/cases/{CASE_ID}/recovery", json={})

        assert response.status_code == 403
        body = response.json()
        assert body["error"]["code"] == "POLICY_BLOCKED"
        assert body["action_executed"] is False
        assert provider_call.called is False

        rules = {check["rule"]: check["passed"] for check in body["policy_decision"]["checks"]}
        assert rules["HUMAN_REVIEW_REQUIRED"] is False

        db_session.refresh(risk_case)
        assert risk_case.status == CaseStatus.ESCALATED.value
        assert risk_case.attempt_count == 0

    def test_low_confidence_diagnosis_escalates_instead_of_executing(
        self, client, db_session, risk_case, active_policy
    ):
        """MINIMUM_AI_CONFIDENCE still gates execution: low confidence escalates."""
        _record_diagnosis(db_session, confidence=0.1, recovery_probability=0.9)

        with patch("app.services.recovery_service.create_payment_link") as provider_call:
            response = client.post(f"/api/cases/{CASE_ID}/recovery", json={})

        assert response.status_code == 403
        assert response.json()["error"]["code"] == "POLICY_ESCALATED"
        assert provider_call.called is False

        db_session.refresh(risk_case)
        assert risk_case.status == CaseStatus.ESCALATED.value
        assert risk_case.attempt_count == 0
        assert risk_case.recovered_amount == 0.0

    def test_high_amount_case_escalates_without_creating_a_link(
        self, client, db_session, risk_case, active_policy
    ):
        """AUTONOMOUS_AMOUNT_LIMIT is enforced before any provider call."""
        _record_diagnosis(db_session)
        risk_case.amount = 50000.0
        db_session.commit()

        with patch("app.services.recovery_service.create_payment_link") as provider_call:
            response = client.post(f"/api/cases/{CASE_ID}/recovery", json={})

        assert response.status_code == 403
        assert response.json()["error"]["code"] == "POLICY_ESCALATED"
        assert provider_call.called is False

    def test_provider_error_is_recorded_as_a_failed_action(
        self, client, db_session, risk_case, active_policy
    ):
        """A provider failure is a failed action, never a recovery."""
        _record_diagnosis(db_session)

        with patch(
            "app.services.recovery_service.create_payment_link",
            side_effect=Exception("Razorpay API error: authentication failed"),
        ):
            response = client.post(f"/api/cases/{CASE_ID}/recovery", json={})

        assert response.status_code == 502
        body = response.json()
        assert body["error"]["code"] == "EXECUTION_FAILED"
        assert "Razorpay API error" in body["error"]["message"]

        db_session.refresh(risk_case)
        assert risk_case.status != CaseStatus.RECOVERED.value
        assert risk_case.recovered_amount == 0.0

        action = db_session.query(RecoveryAction).filter(
            RecoveryAction.case_id == risk_case.id,
            RecoveryAction.execution_status == "FAILED",
        ).one()
        assert action.policy_result == "APPROVED"

        audit = db_session.query(AuditEvent).filter(
            AuditEvent.case_id == risk_case.id,
            AuditEvent.event_type == "ACTION_FAILED",
        ).first()
        assert audit is not None

    def test_diagnose_then_execute_end_to_end(
        self, client, db_session, risk_case, active_policy
    ):
        """The real workflow: diagnose a case, then execute its recommendation."""
        diagnosis = diagnose_case(db_session, CASE_ID)
        assert diagnosis["success"] is True
        assert diagnosis["data"]["recommended_action"] == "CREATE_PAYMENT_LINK"

        response = client.post(f"/api/cases/{CASE_ID}/recovery", json={})

        assert response.status_code == 200
        assert response.json()["data"]["action_type"] == "CREATE_PAYMENT_LINK"
        db_session.refresh(risk_case)
        assert risk_case.attempt_count == 1

    def test_recovery_is_refused_once_attempts_are_exhausted(
        self, client, db_session, risk_case, active_policy
    ):
        """MAX_RECOVERY_ATTEMPTS still bounds execution."""
        _record_diagnosis(db_session)

        for _ in range(3):
            assert client.post(f"/api/cases/{CASE_ID}/recovery", json={}).status_code == 200

        exhausted = client.post(f"/api/cases/{CASE_ID}/recovery", json={})

        assert exhausted.status_code == 403
        assert exhausted.json()["error"]["code"] in ("POLICY_BLOCKED", "POLICY_ESCALATED")
        assert "attempt limit" in exhausted.json()["error"]["message"]


# ============================================================================
# B. Webhook verification, association and settlement
# ============================================================================


class TestWebhookSettlement:

    def _execute_recovery(self, client, db_session):
        """Create the recovery attempt whose payment the webhook should settle."""
        _record_diagnosis(db_session)
        response = client.post(f"/api/cases/{CASE_ID}/recovery", json={})
        assert response.status_code == 200
        return response.json()["data"]

    def test_captured_payment_settles_the_associated_case(
        self, client, db_session, risk_case, active_policy
    ):
        link = self._execute_recovery(client, db_session)

        response = _signed_webhook(client, _captured_event(), "evt_phase4_capture_1")

        assert response.status_code == 200
        result = response.json()["result"]
        assert result["status"] == "RECOVERED"
        assert result["recovered_amount"] == 2500.0

        db_session.refresh(risk_case)
        assert risk_case.status == CaseStatus.RECOVERED.value
        assert risk_case.recovered_amount == 2500.0

        # The delivery is recorded in the durable ledger with its case and amount.
        ledger = db_session.query(WebhookEvent).filter(
            WebhookEvent.event_id == "evt_phase4_capture_1"
        ).one()
        assert ledger.case_id == CASE_ID
        assert ledger.result == "PROCESSED"
        assert ledger.amount == 2500.0
        assert ledger.signature_verified is True
        assert ledger.payment_id == "pay_phase4_1"
        assert ledger.case_id == link["case_reference"] == CASE_ID
        assert ledger.amount == link["amount"] == 2500.0

    def test_authorized_payment_does_not_settle_the_case(
        self, client, db_session, risk_case, active_policy
    ):
        """
        payment.authorized is not a capture. Opening a case as recovered on an
        authorization would book money that may still fail to arrive.
        """
        self._execute_recovery(client, db_session)
        payload = _captured_event()
        payload["event"] = "payment.authorized"
        payload["payload"]["payment"]["entity"]["status"] = "authorized"
        payload["payload"]["payment"]["entity"]["captured"] = False

        response = _signed_webhook(client, payload, "evt_phase4_auth_1")

        assert response.status_code == 200
        body = response.json()
        assert body["settled"] is False
        assert "not captured" in body["message"]

        db_session.refresh(risk_case)
        assert risk_case.status == CaseStatus.RECOVERY_ATTEMPTED.value
        assert risk_case.recovered_amount == 0.0

        ledger = db_session.query(WebhookEvent).filter(
            WebhookEvent.event_id == "evt_phase4_auth_1"
        ).one()
        assert ledger.result == "NOT_CAPTURED"

    def test_duplicate_delivery_does_not_double_count_revenue(
        self, client, db_session, risk_case, active_policy
    ):
        """A redelivered capture must not book the recovered amount twice."""
        self._execute_recovery(client, db_session)

        first = _signed_webhook(client, _captured_event(), "evt_phase4_dup_1")
        second = _signed_webhook(client, _captured_event(), "evt_phase4_dup_1")

        assert first.status_code == 200
        assert second.status_code == 200
        assert second.json()["duplicate"] is True
        assert "Duplicate" in second.json()["message"]

        db_session.refresh(risk_case)
        assert risk_case.recovered_amount == 2500.0
        assert db_session.query(WebhookEvent).filter(
            WebhookEvent.event_id == "evt_phase4_dup_1"
        ).count() == 1

    def test_duplicate_protection_survives_a_restart(
        self, client, db_session, risk_case, active_policy
    ):
        """
        Duplicate detection used to be a process-local ``set``, so a provider
        retry after a restart (or on another worker) was applied a second time.

        The ledger row an earlier process wrote is the only thing identifying the
        delivery here: this test records the delivery without ever letting the
        handler see it, so nothing about it is in this process's memory, and then
        delivers it for real. It must be answered as a duplicate and must leave the
        case untouched.
        """
        self._execute_recovery(client, db_session)
        db_session.add(WebhookEvent(
            event_id="evt_phase4_restart_1",
            event_type="payment.captured",
            payment_id="pay_phase4_restart",
            case_id=CASE_ID,
            amount=2500.0,
            signature_verified=True,
            result="PROCESSED",
        ))
        db_session.commit()

        redelivery = _signed_webhook(client, _captured_event(), "evt_phase4_restart_1")

        assert redelivery.status_code == 200
        assert redelivery.json()["duplicate"] is True
        assert redelivery.json()["original_result"] == "PROCESSED"
        db_session.refresh(risk_case)
        assert risk_case.status == CaseStatus.RECOVERY_ATTEMPTED.value
        assert risk_case.recovered_amount == 0.0

    def test_concurrent_duplicate_is_reported_not_applied(
        self, client, db_session, risk_case, active_policy
    ):
        """Losing the race on the unique event id is a duplicate, not a 500."""
        self._execute_recovery(client, db_session)

        with patch(
            "app.api.webhooks._record_event",
            side_effect=IntegrityError("insert", {}, Exception("duplicate key")),
        ):
            response = _signed_webhook(client, _captured_event(), "evt_phase4_race_1")

        assert response.status_code == 200
        assert response.json()["duplicate"] is True
        db_session.refresh(risk_case)
        assert risk_case.recovered_amount == 2500.0

    def test_mismatched_amount_does_not_settle_and_is_recorded(
        self, client, db_session, risk_case, active_policy
    ):
        """A capture for the wrong amount needs a human, and is never hidden."""
        self._execute_recovery(client, db_session)

        response = _signed_webhook(
            client,
            _captured_event(amount_paise=100000),  # 1000 INR, not 2500
            "evt_phase4_mismatch_1",
        )

        assert response.status_code == 200
        result = response.json()["result"]
        assert result["success"] is False
        assert result["manual_reconciliation_required"] is True

        db_session.refresh(risk_case)
        assert risk_case.status == CaseStatus.RECOVERY_ATTEMPTED.value
        assert risk_case.recovered_amount == 0.0

        audit = db_session.query(AuditEvent).filter(
            AuditEvent.case_id == risk_case.id,
            AuditEvent.decision == "PAYMENT_SUCCESS_AMOUNT_MISMATCH",
        ).first()
        assert audit is not None

    def test_wrong_signature_is_rejected_and_leaves_no_ledger_row(
        self, client, db_session, risk_case, active_policy
    ):
        """An unsigned/invalid delivery changes nothing and stays retryable."""
        self._execute_recovery(client, db_session)
        body = json.dumps(_captured_event())

        with patch("app.services.razorpay_service.settings") as mock_settings:
            mock_settings.RAZORPAY_WEBHOOK_SECRET = WEBHOOK_SECRET
            response = client.post(
                "/api/webhooks/razorpay",
                content=body,
                headers={
                    "Content-Type": "application/json",
                    "X-Razorpay-Signature": "0" * 64,
                    "X-Razorpay-Event-Id": "evt_phase4_badsig_1",
                },
            )

        assert response.status_code == 401
        db_session.refresh(risk_case)
        assert risk_case.status == CaseStatus.RECOVERY_ATTEMPTED.value
        assert risk_case.recovered_amount == 0.0
        assert db_session.query(WebhookEvent).filter(
            WebhookEvent.event_id == "evt_phase4_badsig_1"
        ).first() is None

    def test_late_failure_event_does_not_reopen_a_recovered_case(
        self, client, db_session, risk_case, active_policy
    ):
        """A failure arriving after settlement must not restart recovery."""
        self._execute_recovery(client, db_session)
        _signed_webhook(client, _captured_event(), "evt_phase4_late_ok")

        failure = {
            "event": "payment_link.expired",
            "payload": {
                "payment_link": {
                    "entity": {
                        "id": "plink_phase4_late",
                        "reference_id": CASE_ID,
                        "amount": 250000,
                        "status": "expired",
                        "notes": {},
                    }
                }
            },
        }
        response = _signed_webhook(client, failure, "evt_phase4_late_expire")

        assert response.status_code == 200
        assert response.json()["result"]["ignored"] is True

        db_session.refresh(risk_case)
        assert risk_case.status == CaseStatus.RECOVERED.value
        assert risk_case.recovered_amount == 2500.0
