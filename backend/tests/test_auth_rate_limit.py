"""Regression tests for the Phase 3 P0 / P1 security changes.

Covers:
1. Authentication fails closed: 401 for a missing or invalid key, 503 when
   DEMO_API_KEY is not configured server-side, success with a valid key.
2. The Razorpay webhook is exempt from the shared key (its HMAC is the contract),
   still rejects invalid signatures, and is never rate limited.
3. A policy update writes its audit event in the same transaction, records the
   previous and new values, and never stores the key material.
4. Sensitive routes are rate limited; the exempt webhook route is not.
5. PII is masked in the case-detail response.
6. A terminal case cannot be escalated (L2) and a verified payment records the
   case's own amount as recovered (L3).
"""

import hashlib
import hmac
import json
from datetime import datetime, timezone

import pytest

from app.core.auth import AUTH_ACTOR
from app.core.config import get_settings
from app.core.rate_limit import SENSITIVE_ROUTES, match_route
from app.models import (
    AuditEvent,
    AuditEventType,
    CaseStatus,
    Customer,
    RevenueRiskCase,
    PolicyConfig,
    Transaction,
)
from app.services.agent_tools import escalate_case
from app.services.dashboard_service import mask_email, mask_phone
from app.services.policy_engine import get_active_policy
from app.services.recovery_service import process_payment_success

KEY = "test-demo-api-key"


def _sign(payload: dict, secret: str) -> str:
    body = json.dumps(payload, separators=(",", ":")).encode()
    return hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()


def _seed_case(db_session, case_id="sec-1", status=CaseStatus.OPEN, amount=1000.0):
    customer = Customer(
        customer_id=f"cust-{case_id}",
        name="Test Customer",
        email="anita.rao@example.com",
        phone="+919876543210",
    )
    db_session.add(customer)
    db_session.flush()

    txn = Transaction(
        customer_id=customer.id,
        transaction_id=f"txn-{case_id}",
        amount=amount,
        currency="INR",
        status="FAILED",
        failure_reason="insufficient_funds",
    )
    db_session.add(txn)
    db_session.flush()

    case = RevenueRiskCase(
        case_id=case_id,
        transaction_id=txn.id,
        customer_id=customer.id,
        status=status.value,
        amount=amount,
        attempt_count=1,
        diagnosis='{"action_type": "RETRY", "confidence": 0.8, '
                 '"recovery_probability": 0.7}',
        recommended_action="RETRY",
        created_at=datetime.now(timezone.utc),
    )
    db_session.add(case)
    db_session.commit()
    db_session.refresh(case)
    return case


def _webhook_payload(case_id: str, amount_rupees: float) -> dict:
    return {
        "event": "payment.captured",
        "payment_id": f"pay_{case_id}",
        "payload": {
            "payment": {
                "entity": {
                    "id": f"pay_{case_id}",
                    "amount": int(round(amount_rupees * 100)),
                    "currency": "INR",
                    "notes": {"case_id": case_id},
                    "status": "captured",
                }
            }
        },
    }


# ---------------------------------------------------------------------------
# 1. Authentication fails closed
# ---------------------------------------------------------------------------


class TestAuthentication:
    def test_missing_key_is_rejected(self, anon_client):
        r = anon_client.post(
            "/api/cases/sec-1/recovery", json={"action_type": "RETRY"}
        )
        assert r.status_code == 401
        assert r.json()["success"] is False

    def test_wrong_key_is_rejected(self, anon_client):
        r = anon_client.post(
            "/api/cases/sec-1/recovery",
            json={"action_type": "RETRY"},
            headers={"X-API-Key": "definitely-not-the-key"},
        )
        assert r.status_code == 401

    def test_unconfigured_key_is_503(self, monkeypatch):
        monkeypatch.setattr(get_settings(), "DEMO_API_KEY", "", raising=False)
        from app.core.auth import get_api_key
        from fastapi import HTTPException

        class _Request:
            headers = {}

        with pytest.raises(HTTPException) as exc:
            get_api_key(request=_Request(), credentials=None)
        assert exc.value.status_code == 503

    def test_bearer_header_is_accepted(self, anon_client, db_session):
        _seed_case(db_session)
        r = anon_client.post(
            "/api/cases/sec-1/recovery",
            json={"action_type": "RETRY"},
            headers={"Authorization": f"Bearer {KEY}"},
        )
        # Past authentication: the policy engine now decides, so the response is
        # never 401. (The exact policy verdict is covered by the recovery tests.)
        assert r.status_code != 401

    def test_protected_routes_require_a_key(self, anon_client):
        for method, path, body in [
            ("post", "/api/agent/diagnose", {"case_id": "sec-1"}),
            ("post", "/api/cases/sec-1/review", {"reviewer": "Asha", "decision": "APPROVE"}),
            ("put", "/api/policies", {"max_retries": 2}),
            ("post", "/api/simulation/run", {"num_transactions": 3}),
            ("post", "/api/cases/sec-1/recovery", {"action_type": "RETRY"}),
        ]:
            response = getattr(anon_client, method)(path, json=body)
            assert response.status_code == 401, f"{method.upper()} {path}"

    def test_reads_stay_public(self, anon_client):
        assert anon_client.get("/api/health").status_code == 200
        assert anon_client.get("/api/cases").status_code == 200


# ---------------------------------------------------------------------------
# 2. Webhook exemption
# ---------------------------------------------------------------------------


class TestWebhook:
    def test_valid_signature_works_without_a_key(self, anon_client, db_session, monkeypatch):
        monkeypatch.setattr(
            get_settings(), "RAZORPAY_WEBHOOK_SECRET", "whsec_test", raising=False
        )
        _seed_case(db_session, "sec-w1", amount=750.0)
        payload = _webhook_payload("sec-w1", 750.0)
        r = anon_client.post(
            "/api/webhooks/razorpay",
            json=payload,
            headers={"X-Razorpay-Signature": _sign(payload, "whsec_test")},
        )
        assert r.status_code == 200, r.text

    def test_invalid_signature_is_rejected(self, anon_client, db_session, monkeypatch):
        monkeypatch.setattr(
            get_settings(), "RAZORPAY_WEBHOOK_SECRET", "whsec_test", raising=False
        )
        _seed_case(db_session, "sec-w2", amount=750.0)
        payload = _webhook_payload("sec-w2", 750.0)
        r = anon_client.post(
            "/api/webhooks/razorpay",
            json=payload,
            headers={"X-Razorpay-Signature": "deadbeef"},
        )
        assert r.status_code == 401

    def test_webhook_is_never_rate_limited(self, anon_client, db_session, monkeypatch):
        monkeypatch.setattr(
            get_settings(), "RAZORPAY_WEBHOOK_SECRET", "whsec_test", raising=False
        )
        _seed_case(db_session, "sec-w3", amount=750.0)
        payload = _webhook_payload("sec-w3", 750.0)
        signature = _sign(payload, "whsec_test")

        codes = [
            anon_client.post(
                "/api/webhooks/razorpay",
                json=payload,
                headers={"X-Razorpay-Signature": signature},
            ).status_code
            for _ in range(len(SENSITIVE_ROUTES) * 5 + 20)
        ]
        assert 429 not in codes


# ---------------------------------------------------------------------------
# 3. Policy change is audited atomically
# ---------------------------------------------------------------------------


class TestPolicyAudit:
    @pytest.fixture(autouse=True)
    def _restore_policy_rows(self, db_session):
        """Remove the policy rows these tests commit.

        The suite shares one in-memory database for the whole session, and the
        endpoint commits.  Leaving a policy row behind would change what
        ``get_active_policy`` returns for later tests.
        """
        yield
        db_session.query(PolicyConfig).delete()
        db_session.commit()

    def test_update_records_previous_and_new_values(self, client, db_session):
        policy = get_active_policy(db_session)
        previous_max_retries = policy.max_retries
        new_max_retries = previous_max_retries + 1

        r = client.put("/api/policies", json={"max_retries": new_max_retries})
        assert r.status_code == 200

        try:
            self._assert_audit_event(db_session, previous_max_retries, new_max_retries)
        finally:
            # Restore the shared configuration: this test commits, and the
            # in-memory database is shared across the session.
            client.put("/api/policies", json={"max_retries": previous_max_retries})

    @staticmethod
    def _assert_audit_event(db_session, previous_max_retries, new_max_retries):
        events = (
            db_session.query(AuditEvent)
            .filter(AuditEvent.event_type == AuditEventType.POLICY_CONFIG_CHANGED.value)
            .all()
        )
        assert len(events) == 1
        event = events[0]
        assert event.actor == AUTH_ACTOR
        assert event.decision == "UPDATED"
        assert event.metadata_["previous_values"]["max_retries"] == previous_max_retries
        assert event.metadata_["new_values"]["max_retries"] == new_max_retries
        assert event.metadata_["changed_fields"] == ["max_retries"]
        assert event.metadata_["changed_at"]
        # The key itself is never written to the audit trail.
        assert KEY not in json.dumps(event.metadata_)

    def test_failed_update_writes_no_success_audit(self, client, db_session, monkeypatch):
        """If the audit write fails, the policy change rolls back with it."""
        import app.api.policy as policy_api

        policy = get_active_policy(db_session)
        previous_max_retries = policy.max_retries

        def _boom(*args, **kwargs):
            raise RuntimeError("simulated database failure")

        # Make constructing the audit event fail: the whole unit of work must
        # roll back rather than leave the policy changed but unaudited.
        monkeypatch.setattr(policy_api, "AuditEvent", _boom)

        r = client.put(
            "/api/policies", json={"max_retries": previous_max_retries + 3}
        )
        assert r.status_code == 500
        assert r.json()["success"] is False
        # The error body never carries the internal exception text.
        assert "simulated database failure" not in r.text

        events = (
            db_session.query(AuditEvent)
            .filter(AuditEvent.event_type == AuditEventType.POLICY_CONFIG_CHANGED.value)
            .all()
        )
        assert events == []

        # The rolled-back change is gone too: a failed update changes nothing.
        db_session.expire_all()
        assert get_active_policy(db_session).max_retries == previous_max_retries


# ---------------------------------------------------------------------------
# 4. Rate limiting
# ---------------------------------------------------------------------------


class TestRateLimiting:
    def test_sensitive_route_is_limited(self, anon_client, db_session):
        _seed_case(db_session)
        limit, _window = SENSITIVE_ROUTES["/api/cases/{case_id}/recovery"]

        codes = [
            anon_client.post(
                "/api/cases/sec-1/recovery",
                json={"action_type": "RETRY"},
                headers={"X-API-Key": "wrong-key"},
            ).status_code
            for _ in range(limit + 3)
        ]
        assert codes[0] == 401
        assert codes[-1] == 429

    def test_route_matching_uses_path_segments(self):
        assert match_route("/api/cases/abc/recovery") is not None
        assert match_route("/api/simulation/run") is not None
        assert match_route("/api/policies") is not None
        assert match_route("/api/webhooks/razorpay") is None
        assert match_route("/api/cases/abc") is None
        assert match_route("/api/cases/abc/recovery/extra") is None


# ---------------------------------------------------------------------------
# 5. PII masking
# ---------------------------------------------------------------------------


class TestPiiMasking:
    def test_helpers_mask_email_and_phone(self):
        assert mask_email("anita.rao@example.com") == "a*******o@example.com"
        assert mask_email(None) == ""
        assert mask_phone("+919876543210") == "9198****3210"
        assert mask_phone(None) == ""

    def test_case_detail_masks_customer_contact(self, client, db_session):
        _seed_case(db_session, "pii-1")
        r = client.get("/api/cases/pii-1/detail")
        assert r.status_code == 200
        customer = r.json()["data"]["customer"]
        assert customer["email"] == "a*******o@example.com"
        assert customer["phone"] == "9198****3210"
        assert "anita.rao@example.com" not in r.text
        assert "9876543210" not in r.text


# ---------------------------------------------------------------------------
# 6. Small safeguards: terminal escalation guard (L2), recovered amount (L3)
# ---------------------------------------------------------------------------


class TestTerminalStateSafeguards:
    def test_recovered_case_cannot_be_escalated(self, db_session):
        case = _seed_case(db_session, "term-1", status=CaseStatus.RECOVERED)
        result = escalate_case(db_session, "term-1", "test reason")
        assert result["success"] is False

        db_session.refresh(case)
        assert case.status == CaseStatus.RECOVERED.value

    def test_closed_case_cannot_be_escalated(self, db_session):
        case = _seed_case(db_session, "term-2", status=CaseStatus.CLOSED)
        result = escalate_case(db_session, "term-2", "test reason")
        assert result["success"] is False

        db_session.refresh(case)
        assert case.status == CaseStatus.CLOSED.value

    def test_open_case_still_escalates(self, db_session):
        _seed_case(db_session, "term-3", status=CaseStatus.OPEN)
        result = escalate_case(db_session, "term-3", "test reason")
        assert result["success"] is True

    def test_verified_payment_records_case_amount(self, db_session):
        case = _seed_case(db_session, "l3-1", amount=1234.56)
        result = process_payment_success(
            db=db_session,
            case_id="l3-1",
            payment_id="pay_l3_1",
            amount=1234.56,
        )
        assert result["success"] is True

        db_session.refresh(case)
        assert case.status == CaseStatus.RECOVERED.value
        assert case.recovered_amount == 1234.56

    def test_mismatched_amount_does_not_settle(self, db_session):
        case = _seed_case(db_session, "l3-2", amount=1000.0)
        result = process_payment_success(
            db=db_session,
            case_id="l3-2",
            payment_id="pay_l3_2",
            amount=100.0,
        )
        assert result["success"] is False

        db_session.refresh(case)
        assert case.status != CaseStatus.RECOVERED.value
        assert case.recovered_amount == 0.0
