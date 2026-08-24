"""
Task 3: ML Inference Integration Verification Tests.

Verifies:
- Saved model can be loaded correctly
- Inference works through FastAPI endpoint
- Pydantic validation works
- Probability/risk thresholds are applied correctly
- Invalid and missing inputs are handled properly
- Existing Phase 1 and Phase 2 APIs continue to work
"""
import pytest
import sys
from pathlib import Path
from typing import Dict, Any
from unittest.mock import patch, MagicMock

import numpy as np

# Add project root to path for ml module imports
_project_root = Path(__file__).resolve().parent.parent.parent
if str(_project_root) not in sys.path:
    sys.path.insert(0, str(_project_root))


# ============================================================================
# FIXTURES
# ============================================================================

@pytest.fixture
def sample_valid_request() -> Dict[str, Any]:
    """Valid ML inference request data."""
    return {
        "amount": 5000.0,
        "payment_method": "card",
        "failure_reason": "insufficient_funds",
        "currency": "INR",
        "attempt_number": 2,
        "hour_of_day": 14,
        "day_of_week": 2,
        "days_since_last_transaction": 5.0,
        "customer_total_transactions": 20,
        "customer_successful_transactions": 15,
        "customer_failed_transactions": 5,
        "customer_lifetime_value": 50000.0,
        "customer_age_days": 365,
    }


@pytest.fixture
def sample_batch_request() -> Dict[str, Any]:
    """Valid batch ML inference request data."""
    return {
        "transactions": [
            {
                "amount": 5000.0,
                "payment_method": "card",
                "failure_reason": "insufficient_funds",
                "currency": "INR",
                "attempt_number": 1,
                "hour_of_day": 14,
                "day_of_week": 2,
                "days_since_last_transaction": 5.0,
                "customer_total_transactions": 20,
                "customer_successful_transactions": 15,
                "customer_failed_transactions": 5,
                "customer_lifetime_value": 50000.0,
                "customer_age_days": 365,
            },
            {
                "amount": 1000.0,
                "payment_method": "upi",
                "failure_reason": "network_timeout",
                "currency": "INR",
                "attempt_number": 1,
                "hour_of_day": 10,
                "day_of_week": 1,
                "days_since_last_transaction": 2.0,
                "customer_total_transactions": 10,
                "customer_successful_transactions": 8,
                "customer_failed_transactions": 2,
                "customer_lifetime_value": 20000.0,
                "customer_age_days": 90,
            },
        ]
    }


@pytest.fixture
def predictor():
    """Get the ML predictor instance."""
    from ml.predictor import RecoveryPredictor
    return RecoveryPredictor()


# ============================================================================
# 1. MODEL LOADING VERIFICATION
# ============================================================================

class TestModelLoading:
    """Verify saved model can be loaded correctly."""

    def test_model_loads_successfully(self, predictor):
        """Test that model loads without errors."""
        predictor.load()
        assert predictor._loaded is True
        assert predictor.model is not None
        assert predictor.encoders is not None
        assert predictor.scaler is not None
        assert predictor.metadata is not None

    def test_model_has_correct_version(self, predictor):
        """Test that loaded model has correct version."""
        from ml.config import MODEL_VERSION
        predictor.load()
        assert predictor.model_version == MODEL_VERSION

    def test_model_metadata_contains_features(self, predictor):
        """Test that model metadata contains feature names."""
        predictor.load()
        assert "feature_names" in predictor.metadata
        assert len(predictor.metadata["feature_names"]) > 0

    def test_model_encoders_are_fitted(self, predictor):
        """Test that label encoders are properly fitted."""
        predictor.load()
        for encoder_name, encoder in predictor.encoders.items():
            assert hasattr(encoder, "classes_")
            assert len(encoder.classes_) > 0

    def test_model_scaler_is_fitted(self, predictor):
        """Test that scaler is properly fitted."""
        predictor.load()
        assert hasattr(predictor.scaler, "mean_")
        assert hasattr(predictor.scaler, "scale_")

    def test_model_can_predict(self, predictor, sample_valid_request):
        """Test that loaded model can make predictions."""
        predictor.load()
        result = predictor.predict(sample_valid_request)
        assert "recovery_probability" in result
        assert "risk_score" in result


# ============================================================================
# 2. INFERENCE THROUGH FASTAPI ENDPOINT
# ============================================================================

class TestInferenceEndpoint:
    """Verify inference works through FastAPI endpoint."""

    def test_predict_returns_200(self, client, sample_valid_request):
        """Test that predict endpoint returns 200 with valid data."""
        response = client.post("/api/ml/predict", json=sample_valid_request)
        assert response.status_code == 200

    def test_predict_response_structure(self, client, sample_valid_request):
        """Test that predict response has correct structure."""
        response = client.post("/api/ml/predict", json=sample_valid_request)
        data = response.json()
        assert data["success"] is True
        assert "data" in data
        assert "recovery_probability" in data["data"]
        assert "risk_score" in data["data"]
        assert "risk_level" in data["data"]
        assert "priority" in data["data"]
        assert "recovery_category" in data["data"]
        assert "model_version" in data["data"]

    def test_batch_predict_returns_200(self, client, sample_batch_request):
        """Test that batch predict endpoint returns 200 with valid data."""
        response = client.post("/api/ml/predict/batch", json=sample_batch_request)
        assert response.status_code == 200

    def test_batch_predict_response_structure(self, client, sample_batch_request):
        """Test that batch predict response has correct structure."""
        response = client.post("/api/ml/predict/batch", json=sample_batch_request)
        data = response.json()
        assert data["success"] is True
        assert isinstance(data["data"], list)
        assert len(data["data"]) == 2
        for result in data["data"]:
            assert "recovery_probability" in result
            assert "risk_score" in result

    def test_model_info_endpoint(self, client):
        """Test that model info endpoint returns metadata."""
        response = client.get("/api/ml/model")
        assert response.status_code == 200
        data = response.json()
        assert "model_version" in data
        assert "num_features" in data
        assert data["num_features"] > 0

    def test_health_endpoint(self, client):
        """Test that health endpoint returns status."""
        response = client.get("/api/ml/health")
        assert response.status_code == 200
        data = response.json()
        assert data["success"] is True
        assert data["data"]["status"] == "healthy"


# ============================================================================
# 3. PYDANTIC VALIDATION
# ============================================================================

class TestPydanticValidation:
    """Verify Pydantic validation works correctly."""

    def test_missing_amount_returns_422(self, client):
        """Test that missing amount field returns validation error."""
        response = client.post("/api/ml/predict", json={
            "payment_method": "card",
            "failure_reason": "insufficient_funds",
        })
        assert response.status_code == 422

    def test_missing_payment_method_returns_422(self, client):
        """Test that missing payment_method field returns validation error."""
        response = client.post("/api/ml/predict", json={
            "amount": 1000.0,
            "failure_reason": "insufficient_funds",
        })
        assert response.status_code == 422

    def test_missing_failure_reason_returns_422(self, client):
        """Test that missing failure_reason field returns validation error."""
        response = client.post("/api/ml/predict", json={
            "amount": 1000.0,
            "payment_method": "card",
        })
        assert response.status_code == 422

    def test_negative_amount_returns_422(self, client):
        """Test that negative amount returns validation error."""
        response = client.post("/api/ml/predict", json={
            "amount": -100.0,
            "payment_method": "card",
            "failure_reason": "insufficient_funds",
        })
        assert response.status_code == 422

    def test_zero_amount_returns_422(self, client):
        """Test that zero amount returns validation error."""
        response = client.post("/api/ml/predict", json={
            "amount": 0.0,
            "payment_method": "card",
            "failure_reason": "insufficient_funds",
        })
        assert response.status_code == 422

    def test_invalid_attempt_number_returns_422(self, client):
        """Test that invalid attempt_number returns validation error."""
        response = client.post("/api/ml/predict", json={
            "amount": 1000.0,
            "payment_method": "card",
            "failure_reason": "insufficient_funds",
            "attempt_number": 0,  # Invalid: must be >= 1
        })
        assert response.status_code == 422

    def test_invalid_hour_of_day_returns_422(self, client):
        """Test that invalid hour_of_day returns validation error."""
        response = client.post("/api/ml/predict", json={
            "amount": 1000.0,
            "payment_method": "card",
            "failure_reason": "insufficient_funds",
            "hour_of_day": 25,  # Invalid: must be <= 23
        })
        assert response.status_code == 422

    def test_invalid_day_of_week_returns_422(self, client):
        """Test that invalid day_of_week returns validation error."""
        response = client.post("/api/ml/predict", json={
            "amount": 1000.0,
            "payment_method": "card",
            "failure_reason": "insufficient_funds",
            "day_of_week": 7,  # Invalid: must be <= 6
        })
        assert response.status_code == 422

    def test_negative_customer_transactions_returns_422(self, client):
        """Test that negative customer transactions returns validation error."""
        response = client.post("/api/ml/predict", json={
            "amount": 1000.0,
            "payment_method": "card",
            "failure_reason": "insufficient_funds",
            "customer_total_transactions": -1,
        })
        assert response.status_code == 422

    def test_empty_batch_returns_422(self, client):
        """Test that empty batch returns validation error."""
        response = client.post("/api/ml/predict/batch", json={
            "transactions": []
        })
        assert response.status_code == 422

    def test_batch_exceeds_max_length_returns_422(self, client):
        """Test that batch exceeding max length returns validation error."""
        transactions = [
            {
                "amount": 1000.0,
                "payment_method": "card",
                "failure_reason": "insufficient_funds",
            }
            for _ in range(101)  # Max is 100
        ]
        response = client.post("/api/ml/predict/batch", json={
            "transactions": transactions
        })
        assert response.status_code == 422

    def test_extra_fields_ignored(self, client):
        """Test that extra fields are ignored without error."""
        response = client.post("/api/ml/predict", json={
            "amount": 1000.0,
            "payment_method": "card",
            "failure_reason": "insufficient_funds",
            "extra_field": "should_be_ignored",
        })
        # Should still work, extra fields are ignored
        assert response.status_code == 200


# ============================================================================
# 4. PROBABILITY/RISK THRESHOLDS
# ============================================================================

class TestProbabilityRiskThresholds:
    """Verify probability and risk thresholds are applied correctly."""

    def test_recovery_probability_range(self, predictor, sample_valid_request):
        """Test that recovery probability is between 0 and 1."""
        predictor.load()
        result = predictor.predict(sample_valid_request)
        assert 0 <= result["recovery_probability"] <= 1

    def test_risk_score_range(self, predictor, sample_valid_request):
        """Test that risk score is between 0 and 1."""
        predictor.load()
        result = predictor.predict(sample_valid_request)
        assert 0 <= result["risk_score"] <= 1

    def test_risk_score_equals_inverse_of_recovery(self, predictor, sample_valid_request):
        """Test that risk score is inverse of recovery probability."""
        predictor.load()
        result = predictor.predict(sample_valid_request)
        assert abs(result["risk_score"] - (1 - result["recovery_probability"])) < 0.001

    def test_risk_level_low(self, predictor):
        """Test risk level determination for LOW risk."""
        from ml.config import RISK_THRESHOLDS
        # LOW: risk_score < MEDIUM threshold (0.6)
        assert predictor._get_risk_level(0.2) == "LOW"
        assert predictor._get_risk_level(0.59) == "LOW"

    def test_risk_level_medium(self, predictor):
        """Test risk level determination for MEDIUM risk."""
        assert predictor._get_risk_level(0.6) == "MEDIUM"
        assert predictor._get_risk_level(0.7) == "MEDIUM"

    def test_risk_level_high(self, predictor):
        """Test risk level determination for HIGH risk."""
        assert predictor._get_risk_level(0.8) == "HIGH"
        assert predictor._get_risk_level(0.89) == "HIGH"

    def test_risk_level_critical(self, predictor):
        """Test risk level determination for CRITICAL risk."""
        assert predictor._get_risk_level(1.0) == "CRITICAL"

    def test_priority_p0_critical_high_prob(self, predictor):
        """Test P0 priority for CRITICAL risk with high recovery probability."""
        assert predictor._get_priority("CRITICAL", 0.8) == "P0"
        assert predictor._get_priority("CRITICAL", 0.6) == "P0"

    def test_priority_p1_high_medium_prob(self, predictor):
        """Test P1 priority for HIGH risk with medium recovery probability."""
        assert predictor._get_priority("HIGH", 0.6) == "P1"
        assert predictor._get_priority("HIGH", 0.5) == "P1"

    def test_priority_p2_medium_low_prob(self, predictor):
        """Test P2 priority for MEDIUM risk with low recovery probability."""
        assert predictor._get_priority("MEDIUM", 0.4) == "P2"
        assert predictor._get_priority("MEDIUM", 0.3) == "P2"

    def test_priority_p3_low_default(self, predictor):
        """Test P3 priority for LOW risk."""
        assert predictor._get_priority("LOW", 0.2) == "P3"
        assert predictor._get_priority("LOW", 0.0) == "P3"

    def test_recovery_category_high(self, predictor):
        """Test recovery category for high probability."""
        assert predictor._get_recovery_category(0.8) == "HIGH_PROBABILITY"
        assert predictor._get_recovery_category(0.7) == "HIGH_PROBABILITY"

    def test_recovery_category_medium(self, predictor):
        """Test recovery category for medium probability."""
        assert predictor._get_recovery_category(0.5) == "MEDIUM_PROBABILITY"
        assert predictor._get_recovery_category(0.4) == "MEDIUM_PROBABILITY"

    def test_recovery_category_low(self, predictor):
        """Test recovery category for low probability."""
        assert predictor._get_recovery_category(0.3) == "LOW_PROBABILITY"
        assert predictor._get_recovery_category(0.0) == "LOW_PROBABILITY"


# ============================================================================
# 5. INVALID AND MISSING INPUT HANDLING
# ============================================================================

class TestInvalidMissingInputs:
    """Verify invalid and missing inputs are handled properly."""

    def test_empty_request_body(self, client):
        """Test that empty request body returns error."""
        response = client.post("/api/ml/predict", json={})
        assert response.status_code == 422

    def test_none_values(self, client):
        """Test that None values return validation error."""
        response = client.post("/api/ml/predict", json={
            "amount": None,
            "payment_method": "card",
            "failure_reason": "insufficient_funds",
        })
        assert response.status_code == 422

    def test_invalid_data_types(self, client):
        """Test that invalid data types return validation error."""
        response = client.post("/api/ml/predict", json={
            "amount": "not_a_number",
            "payment_method": "card",
            "failure_reason": "insufficient_funds",
        })
        assert response.status_code == 422

    def test_missing_all_optional_fields(self, client):
        """Test that missing all optional fields uses defaults."""
        response = client.post("/api/ml/predict", json={
            "amount": 1000.0,
            "payment_method": "card",
            "failure_reason": "insufficient_funds",
        })
        assert response.status_code == 200

    def test_unknown_payment_method_still_works(self, client):
        """Test that unknown payment method still produces prediction."""
        response = client.post("/api/ml/predict", json={
            "amount": 1000.0,
            "payment_method": "unknown_method",
            "failure_reason": "insufficient_funds",
        })
        # Should work with default handling
        assert response.status_code == 200

    def test_unknown_failure_reason_still_works(self, client):
        """Test that unknown failure reason still produces prediction."""
        response = client.post("/api/ml/predict", json={
            "amount": 1000.0,
            "payment_method": "card",
            "failure_reason": "unknown_reason",
        })
        # Should work with default handling
        assert response.status_code == 200

    def test_extreme_amount_values(self, client):
        """Test that extreme amount values are handled."""
        # Very small amount
        response = client.post("/api/ml/predict", json={
            "amount": 0.01,
            "payment_method": "card",
            "failure_reason": "insufficient_funds",
        })
        assert response.status_code == 200

        # Very large amount
        response = client.post("/api/ml/predict", json={
            "amount": 1000000000.0,
            "payment_method": "card",
            "failure_reason": "insufficient_funds",
        })
        assert response.status_code == 200

    def test_boundary_time_values(self, client):
        """Test that boundary time values are handled."""
        response = client.post("/api/ml/predict", json={
            "amount": 1000.0,
            "payment_method": "card",
            "failure_reason": "insufficient_funds",
            "hour_of_day": 0,
            "day_of_week": 0,
        })
        assert response.status_code == 200

        response = client.post("/api/ml/predict", json={
            "amount": 1000.0,
            "payment_method": "card",
            "failure_reason": "insufficient_funds",
            "hour_of_day": 23,
            "day_of_week": 6,
        })
        assert response.status_code == 200


# ============================================================================
# 6. EXISTING PHASE 1 AND PHASE 2 APIs CONTINUE TO WORK
# ============================================================================

class TestExistingAPIsWork:
    """Verify existing Phase 1 and Phase 2 APIs continue to work."""

    def test_health_endpoint_still_works(self, client):
        """Test that health endpoint still works."""
        response = client.get("/api/health")
        assert response.status_code == 200
        data = response.json()
        assert data["success"] is True
        assert data["data"]["status"] == "healthy"

    def test_cases_list_endpoint_still_works(self, client):
        """Test that cases list endpoint still works."""
        response = client.get("/api/cases")
        assert response.status_code == 200
        data = response.json()
        assert data["success"] is True
        assert "data" in data
        assert "pagination" in data

    def test_cases_list_with_pagination(self, client):
        """Test that cases list endpoint handles pagination."""
        response = client.get("/api/cases?page=1&page_size=10")
        assert response.status_code == 200
        data = response.json()
        assert data["pagination"]["page"] == 1
        assert data["pagination"]["page_size"] == 10

    def test_cases_list_with_status_filter(self, client):
        """Test that cases list endpoint handles status filter."""
        response = client.get("/api/cases?status=OPEN")
        assert response.status_code == 200

    def test_cases_list_with_priority_filter(self, client):
        """Test that cases list endpoint handles priority filter."""
        response = client.get("/api/cases?priority=HIGH")
        assert response.status_code == 200

    def test_cases_list_invalid_page_returns_422(self, client):
        """Test that invalid page returns validation error."""
        response = client.get("/api/cases?page=0")
        assert response.status_code == 422

    def test_cases_list_invalid_page_size_returns_422(self, client):
        """Test that invalid page_size returns validation error."""
        response = client.get("/api/cases?page_size=0")
        assert response.status_code == 422

        response = client.get("/api/cases?page_size=101")
        assert response.status_code == 422

    def test_get_case_nonexistent_returns_404(self, client):
        """Test that getting non-existent case returns 404."""
        response = client.get("/api/cases/NONEXISTENT")
        assert response.status_code == 404

    def test_ml_health_does_not_affect_other_endpoints(self, client):
        """Test that ML health check does not affect other endpoints."""
        # Call ML health
        ml_response = client.get("/api/ml/health")
        assert ml_response.status_code == 200

        # Verify health endpoint still works
        health_response = client.get("/api/health")
        assert health_response.status_code == 200

        # Verify cases endpoint still works
        cases_response = client.get("/api/cases")
        assert cases_response.status_code == 200


# ============================================================================
# 7. RESPONSE SCHEMA VALIDATION
# ============================================================================

class TestResponseSchemaValidation:
    """Verify response schemas are correctly validated."""

    def test_predict_response_schema(self, client, sample_valid_request):
        """Test that predict response matches expected schema."""
        response = client.post("/api/ml/predict", json=sample_valid_request)
        data = response.json()

        # Verify top-level schema
        assert "success" in data
        assert "data" in data
        assert data["success"] is True

        # Verify data schema
        result = data["data"]
        assert isinstance(result["recovery_probability"], float)
        assert isinstance(result["risk_score"], float)
        assert isinstance(result["risk_level"], str)
        assert isinstance(result["priority"], str)
        assert isinstance(result["recovery_category"], str)
        assert isinstance(result["model_version"], str)

        # Verify risk_level is valid
        assert result["risk_level"] in ["LOW", "MEDIUM", "HIGH", "CRITICAL"]

        # Verify priority is valid
        assert result["priority"] in ["P0", "P1", "P2", "P3"]

        # Verify recovery_category is valid
        assert result["recovery_category"] in [
            "HIGH_PROBABILITY", "MEDIUM_PROBABILITY", "LOW_PROBABILITY"
        ]

    def test_batch_predict_response_schema(self, client, sample_batch_request):
        """Test that batch predict response matches expected schema."""
        response = client.post("/api/ml/predict/batch", json=sample_batch_request)
        data = response.json()

        # Verify top-level schema
        assert "success" in data
        assert "data" in data
        assert data["success"] is True
        assert isinstance(data["data"], list)

        # Verify each result matches schema
        for result in data["data"]:
            assert isinstance(result["recovery_probability"], float)
            assert isinstance(result["risk_score"], float)
            assert isinstance(result["risk_level"], str)
            assert isinstance(result["priority"], str)
            assert isinstance(result["recovery_category"], str)
            assert isinstance(result["model_version"], str)

    def test_model_info_response_schema(self, client):
        """Test that model info response matches expected schema."""
        response = client.get("/api/ml/model")
        data = response.json()

        assert "model_version" in data
        assert "model_type" in data
        assert "num_features" in data
        assert "feature_names" in data
        assert isinstance(data["num_features"], int)
        assert isinstance(data["feature_names"], list)

    def test_health_response_schema(self, client):
        """Test that health response matches expected schema."""
        response = client.get("/api/ml/health")
        data = response.json()

        assert "success" in data
        assert "data" in data
        assert "status" in data["data"]
        assert data["data"]["status"] in ["healthy", "unhealthy"]


# ============================================================================
# 8. EDGE CASES AND INTEGRATION
# ============================================================================

class TestEdgeCases:
    """Test edge cases and integration scenarios."""

    def test_predictor_singleton_behavior(self):
        """Test that predictor singleton returns same instance."""
        from ml.predictor import get_predictor
        predictor1 = get_predictor()
        predictor2 = get_predictor()
        assert predictor1 is predictor2

    def test_multiple_predictions_same_input(self, predictor, sample_valid_request):
        """Test that multiple predictions with same input return same result."""
        predictor.load()
        result1 = predictor.predict(sample_valid_request)
        result2 = predictor.predict(sample_valid_request)
        assert result1["recovery_probability"] == result2["recovery_probability"]
        assert result1["risk_score"] == result2["risk_score"]

    def test_different_inputs_different_results(self, predictor):
        """Test that different inputs produce different results."""
        predictor.load()
        result1 = predictor.predict({
            "amount": 100.0,
            "payment_method": "card",
            "failure_reason": "network_timeout",
            "currency": "INR",
            "attempt_number": 1,
            "hour_of_day": 10,
            "day_of_week": 0,
            "days_since_last_transaction": 1.0,
            "customer_total_transactions": 50,
            "customer_successful_transactions": 48,
            "customer_failed_transactions": 2,
            "customer_lifetime_value": 100000.0,
            "customer_age_days": 730,
        })
        result2 = predictor.predict({
            "amount": 10000.0,
            "payment_method": "card",
            "failure_reason": "insufficient_funds",
            "currency": "INR",
            "attempt_number": 3,
            "hour_of_day": 22,
            "day_of_week": 6,
            "days_since_last_transaction": 30.0,
            "customer_total_transactions": 5,
            "customer_successful_transactions": 2,
            "customer_failed_transactions": 3,
            "customer_lifetime_value": 5000.0,
            "customer_age_days": 30,
        })
        # Results should be different for different inputs
        assert result1["recovery_probability"] != result2["recovery_probability"]

    def test_model_version_in_response(self, client, sample_valid_request):
        """Test that model version is included in prediction response."""
        from ml.config import MODEL_VERSION
        response = client.post("/api/ml/predict", json=sample_valid_request)
        data = response.json()
        assert data["data"]["model_version"] == MODEL_VERSION

    def test_batch_single_transaction(self, client):
        """Test batch endpoint with single transaction."""
        response = client.post("/api/ml/predict/batch", json={
            "transactions": [{
                "amount": 1000.0,
                "payment_method": "card",
                "failure_reason": "insufficient_funds",
            }]
        })
        assert response.status_code == 200
        data = response.json()
        assert len(data["data"]) == 1

    def test_batch_max_transactions(self, client):
        """Test batch endpoint with maximum allowed transactions."""
        transactions = [
            {
                "amount": 1000.0,
                "payment_method": "card",
                "failure_reason": "insufficient_funds",
            }
            for _ in range(100)
        ]
        response = client.post("/api/ml/predict/batch", json={
            "transactions": transactions
        })
        assert response.status_code == 200
        data = response.json()
        assert len(data["data"]) == 100
