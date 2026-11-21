import pytest
from types import SimpleNamespace

from tests.conftest import db_session
from app.models import Customer, Transaction, RevenueRiskCase
import app.api.health as health_module


def create_test_data(db_session):
    """Helper to create test data for API tests."""
    customer = Customer(
        customer_id="CUST-001",
        name="Test Customer",
        email="test@example.com",
    )
    db_session.add(customer)
    db_session.commit()

    transaction = Transaction(
        transaction_id="TXN-001",
        customer_id=customer.id,
        amount=100.0,
        currency="INR",
        status="FAILED",
        failure_reason="Insufficient funds",
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

    return customer, transaction, case


class TestHealthEndpoint:
    """Test health check endpoint."""

    def test_health_check(self, client):
        """Test GET /api/health returns success."""
        response = client.get("/api/health")
        assert response.status_code == 200
        data = response.json()
        assert data["success"] is True
        assert data["data"]["status"] == "healthy"
        assert "version" in data["data"]


def _fake_settings(**overrides):
    """Settings stand-in for /config/status; only the fields it reads."""
    values = {
        "RAZORPAY_KEY_ID": "",
        "RAZORPAY_KEY_SECRET": "",
        "RAZORPAY_WEBHOOK_SECRET": "",
        "RAZORPAY_LIVE_KEY_REJECTED": False,
    }
    values.update(overrides)
    return SimpleNamespace(**values)


class TestConfigStatusEndpoint:
    """Test GET /api/config/status (payment mode reporting)."""

    def test_mock_mode_without_keys(self, client, monkeypatch):
        """No credentials means the system runs mocked and says so."""
        monkeypatch.setattr(health_module, "settings", _fake_settings())
        payments = client.get("/api/config/status").json()["data"]["payments"]
        assert payments["mode"] == "MOCK"
        assert payments["razorpay_configured"] is False
        assert payments["key_type"] is None
        assert payments["webhook_configured"] is False

    def test_test_mode_with_full_credentials(self, client, monkeypatch):
        """Test keys + webhook secret are reported as TEST MODE."""
        monkeypatch.setattr(
            health_module,
            "settings",
            _fake_settings(
                RAZORPAY_KEY_ID="rzp_test_keyid_123",
                RAZORPAY_KEY_SECRET="super-secret-value",
                RAZORPAY_WEBHOOK_SECRET="whsec-secret-value",
            ),
        )
        response = client.get("/api/config/status")
        payments = response.json()["data"]["payments"]
        assert payments["mode"] == "TEST_MODE"
        assert payments["razorpay_configured"] is True
        assert payments["key_type"] == "test"
        assert payments["webhook_configured"] is True

        # Only presence flags may leave the server: never the key or the secrets.
        body = response.text
        assert "rzp_test_keyid_123" not in body
        assert "super-secret-value" not in body
        assert "whsec-secret-value" not in body

    def test_partial_credentials_are_reported_unconfigured(self, client, monkeypatch):
        """
        A key id without its secret cannot reach Razorpay.  That is reported as
        UNCONFIGURED (a half-configured deployment), not silently as MOCK, so the
        missing secret is visible instead of hidden behind a working demo label.
        """
        monkeypatch.setattr(
            health_module,
            "settings",
            _fake_settings(RAZORPAY_KEY_ID="rzp_test_keyid_123"),
        )
        payments = client.get("/api/config/status").json()["data"]["payments"]
        assert payments["mode"] == "UNCONFIGURED"
        assert payments["razorpay_configured"] is False
        assert payments["key_type"] == "test"
        assert payments["live_key_rejected"] is False

    def test_unrecognised_key_prefix_is_unconfigured(self, client, monkeypatch):
        """Only a rzp_test_ key pair opens a provider path; anything else is flagged."""
        monkeypatch.setattr(
            health_module,
            "settings",
            _fake_settings(
                RAZORPAY_KEY_ID="pk_unknown_prefix",
                RAZORPAY_KEY_SECRET="super-secret-value",
            ),
        )
        payments = client.get("/api/config/status").json()["data"]["payments"]
        assert payments["mode"] == "UNCONFIGURED"
        assert payments["razorpay_configured"] is False
        assert payments["key_type"] == "unknown"

    def test_webhook_missing_is_reported_separately(self, client, monkeypatch):
        """Real keys without a webhook secret: TEST MODE, but settlement is dead."""
        monkeypatch.setattr(
            health_module,
            "settings",
            _fake_settings(
                RAZORPAY_KEY_ID="rzp_test_keyid_123",
                RAZORPAY_KEY_SECRET="super-secret-value",
            ),
        )
        payments = client.get("/api/config/status").json()["data"]["payments"]
        assert payments["mode"] == "TEST_MODE"
        assert payments["webhook_configured"] is False

    def test_live_keys_are_rejected_not_reported_as_test_mode(self, client, monkeypatch):
        """
        A live key must never be reported as TEST MODE.

        This test previously asserted the opposite: any present key + secret was
        labelled TEST_MODE, which meant a live-configured deployment advertised
        itself as a safe test one.  A live key is now refused and reported as
        LIVE_REJECTED, and no provider can be reached from it.
        """
        monkeypatch.setattr(
            health_module,
            "settings",
            _fake_settings(
                RAZORPAY_KEY_ID="rzp_live_keyid_123",
                RAZORPAY_KEY_SECRET="super-secret-value",
            ),
        )
        response = client.get("/api/config/status")
        payments = response.json()["data"]["payments"]

        assert payments["mode"] == "LIVE_REJECTED"
        assert payments["mode"] != "TEST_MODE"
        assert payments["razorpay_configured"] is False
        assert payments["live_key_rejected"] is True
        # The prefix class is still reported so an operator can tell why.
        assert payments["key_type"] == "live"

        # The key itself and the secret never leave the server.
        assert "rzp_live_keyid_123" not in response.text
        assert "super-secret-value" not in response.text

    def test_live_key_without_secret_is_rejected(self, client, monkeypatch):
        """Refusal does not depend on the secret being present."""
        monkeypatch.setattr(
            health_module,
            "settings",
            _fake_settings(RAZORPAY_KEY_ID="rzp_live_keyid_123"),
        )
        payments = client.get("/api/config/status").json()["data"]["payments"]
        assert payments["mode"] == "LIVE_REJECTED"
        assert payments["live_key_rejected"] is True

    def test_validated_settings_with_live_key_report_live_rejected(self, client, monkeypatch):
        """
        After validation a live key has been blanked and only the refusal flag
        remains.  That state must still report LIVE_REJECTED, never MOCK.
        """
        monkeypatch.setattr(
            health_module,
            "settings",
            _fake_settings(RAZORPAY_LIVE_KEY_REJECTED=True),
        )
        payments = client.get("/api/config/status").json()["data"]["payments"]
        assert payments["mode"] == "LIVE_REJECTED"
        assert payments["razorpay_configured"] is False
        assert payments["live_key_rejected"] is True


class TestListCasesEndpoint:
    """Test GET /api/cases endpoint."""

    def test_list_cases_empty(self, client):
        """Test listing cases when none exist."""
        response = client.get("/api/cases")
        assert response.status_code == 200
        data = response.json()
        assert data["success"] is True
        assert data["data"] == []
        assert data["pagination"]["total"] == 0

    def test_list_cases_with_data(self, client, db_session):
        """Test listing cases with test data."""
        create_test_data(db_session)

        response = client.get("/api/cases")
        assert response.status_code == 200
        data = response.json()
        assert data["success"] is True
        assert len(data["data"]) == 1
        assert data["data"][0]["case_id"] == "CASE-001"
        assert data["pagination"]["total"] == 1

    def test_list_cases_pagination(self, client, db_session):
        """Test pagination parameters."""
        create_test_data(db_session)

        response = client.get("/api/cases?page=1&page_size=10")
        assert response.status_code == 200
        data = response.json()
        assert data["pagination"]["page"] == 1
        assert data["pagination"]["page_size"] == 10

    def test_list_cases_filter_by_status(self, client, db_session):
        """Test filtering cases by status."""
        create_test_data(db_session)

        response = client.get("/api/cases?status=OPEN")
        assert response.status_code == 200
        data = response.json()
        assert len(data["data"]) == 1

        response = client.get("/api/cases?status=CLOSED")
        assert response.status_code == 200
        data = response.json()
        assert len(data["data"]) == 0

    def test_list_cases_filter_by_priority(self, client, db_session):
        """Test filtering cases by priority."""
        create_test_data(db_session)

        response = client.get("/api/cases?priority=HIGH")
        assert response.status_code == 200
        data = response.json()
        assert len(data["data"]) == 1

        response = client.get("/api/cases?priority=LOW")
        assert response.status_code == 200
        data = response.json()
        assert len(data["data"]) == 0

    def test_list_cases_filter_by_risk_score(self, client, db_session):
        """Test filtering cases by risk score range."""
        create_test_data(db_session)

        response = client.get("/api/cases?min_risk_score=0.5")
        assert response.status_code == 200
        data = response.json()
        assert len(data["data"]) == 1

        response = client.get("/api/cases?max_risk_score=0.5")
        assert response.status_code == 200
        data = response.json()
        assert len(data["data"]) == 0

    def test_list_cases_invalid_page(self, client):
        """Test invalid page parameter."""
        response = client.get("/api/cases?page=0")
        assert response.status_code == 422  # Validation error

    def test_list_cases_invalid_page_size(self, client):
        """Test invalid page_size parameter."""
        response = client.get("/api/cases?page_size=0")
        assert response.status_code == 422

        response = client.get("/api/cases?page_size=101")
        assert response.status_code == 422


class TestGetCaseEndpoint:
    """Test GET /api/cases/{case_id} endpoint."""

    def test_get_case_success(self, client, db_session):
        """Test getting a specific case."""
        create_test_data(db_session)

        response = client.get("/api/cases/CASE-001")
        assert response.status_code == 200
        data = response.json()
        assert data["success"] is True
        assert data["data"]["case_id"] == "CASE-001"
        assert data["data"]["amount"] == 100.0
        assert data["data"]["risk_score"] == 0.75

    def test_get_case_not_found(self, client):
        """Test getting a non-existent case."""
        response = client.get("/api/cases/NONEXISTENT")
        assert response.status_code == 404
        data = response.json()
        assert data["success"] is False
        assert "not found" in data["error"]["message"].lower()

    def test_get_case_returns_related_data(self, client, db_session):
        """Test that case response includes all expected fields."""
        create_test_data(db_session)

        response = client.get("/api/cases/CASE-001")
        assert response.status_code == 200
        data = response.json()["data"]

        # Check all expected fields exist
        expected_fields = [
            "id", "case_id", "transaction_id", "customer_id",
            "amount", "risk_score", "recovery_probability",
            "priority", "diagnosis", "recommended_action",
            "status", "attempt_count", "recovered_amount",
            "created_at", "updated_at"
        ]
        for field in expected_fields:
            assert field in data, f"Missing field: {field}"
