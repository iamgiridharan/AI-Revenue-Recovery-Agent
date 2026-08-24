"""
Tests for ML preprocessing, feature engineering, model training, and inference.
"""
import pytest
import sys
from pathlib import Path
from unittest.mock import patch, MagicMock

import numpy as np
import pandas as pd

# Add project root to path for ml module imports
_project_root = Path(__file__).resolve().parent.parent.parent
if str(_project_root) not in sys.path:
    sys.path.insert(0, str(_project_root))


class TestMLPreprocessing:
    """Tests for ML preprocessing pipeline."""
    
    def test_load_dataset(self):
        """Test loading synthetic dataset."""
        from ml.data_generator import load_dataset
        
        df = load_dataset()
        assert len(df) >= 5000
        assert "recovered" in df.columns
        assert "amount" in df.columns
        
    def test_feature_columns_exist_after_preprocessing(self):
        """Test that all feature columns exist after preprocessing."""
        from ml.config import FEATURE_COLUMNS
        from ml.data_generator import load_dataset
        from ml.preprocessing import preprocess_training_data
        
        df = load_dataset()
        X_train, X_test, y_train, y_test, _, _ = preprocess_training_data(df)
        # Verify we have the right number of features
        assert X_train.shape[1] == len(FEATURE_COLUMNS)
    
    def test_preprocess_training_data(self):
        """Test preprocessing training data."""
        from ml.preprocessing import preprocess_training_data
        from ml.data_generator import load_dataset
        
        df = load_dataset()
        X_train, X_test, y_train, y_test, encoders, scaler = preprocess_training_data(df)
        
        # Check shapes
        assert X_train.shape[1] == 20  # 20 features
        assert X_test.shape[1] == 20
        assert len(y_train) == X_train.shape[0]
        assert len(y_test) == X_test.shape[0]
        
        # Check encoders
        assert "currency" in encoders
        assert "payment_method" in encoders
        assert "failure_reason" in encoders
        
        # Check scaler
        assert scaler is not None
        
    def test_preprocess_inference_data(self):
        """Test preprocessing single data point for inference."""
        from ml.preprocessing import preprocess_inference_data, create_features_from_raw
        from ml.data_generator import load_dataset
        from ml.preprocessing import _encode_categoricals
        from sklearn.preprocessing import StandardScaler
        
        # Load and preprocess training data to get encoders and scaler
        df = load_dataset()
        _, encoders, scaler = _encode_categoricals(df), None, None
        
        # Actually, let's use the full preprocessing pipeline
        from ml.preprocessing import preprocess_training_data
        _, _, _, _, encoders, scaler = preprocess_training_data(df)
        
        # Create features from raw data
        raw_data = {
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
        }
        
        features = create_features_from_raw(raw_data)
        X = preprocess_inference_data(features, encoders, scaler)
        
        assert X.shape == (1, 20)
        assert not np.isnan(X).any()


class TestMLModel:
    """Tests for ML model training and evaluation."""
    
    def test_train_model(self):
        """Test model training."""
        from ml.model import train_model
        from ml.preprocessing import preprocess_training_data
        from ml.data_generator import load_dataset
        
        df = load_dataset()
        X_train, X_test, y_train, y_test, _, _ = preprocess_training_data(df)
        
        model = train_model(X_train, y_train)
        
        assert model is not None
        assert hasattr(model, "predict")
        assert hasattr(model, "predict_proba")
        
    def test_evaluate_model(self):
        """Test model evaluation."""
        from ml.model import train_model, evaluate_model
        from ml.preprocessing import preprocess_training_data
        from ml.data_generator import load_dataset
        from ml.config import FEATURE_COLUMNS
        
        df = load_dataset()
        X_train, X_test, y_train, y_test, _, _ = preprocess_training_data(df)
        
        model = train_model(X_train, y_train)
        metrics = evaluate_model(model, X_test, y_test, feature_names=FEATURE_COLUMNS)
        
        # Check metrics exist
        assert "precision" in metrics
        assert "recall" in metrics
        assert "f1_score" in metrics
        assert "roc_auc" in metrics
        assert "confusion_matrix" in metrics
        
        # Check metric ranges
        assert 0 <= metrics["precision"] <= 1
        assert 0 <= metrics["recall"] <= 1
        assert 0 <= metrics["f1_score"] <= 1
        assert 0 <= metrics["roc_auc"] <= 1
        
    def test_revenue_weighted_evaluation(self):
        """Test revenue-weighted evaluation."""
        from ml.model import train_model, revenue_weighted_evaluation
        from ml.preprocessing import preprocess_training_data
        from ml.data_generator import load_dataset
        from sklearn.model_selection import train_test_split
        
        df = load_dataset()
        X_train, X_test, y_train, y_test, _, _ = preprocess_training_data(df)
        
        # Get amounts for test set
        _, amounts_test = train_test_split(
            df["amount"].values,
            test_size=0.2,
            random_state=42,
            stratify=df["recovered"].values,
        )
        
        model = train_model(X_train, y_train)
        rev_metrics = revenue_weighted_evaluation(model, X_test, y_test, amounts_test)
        
        # Check revenue metrics exist
        assert "total_failed_payment_value" in rev_metrics
        assert "total_recoverable_payment_value" in rev_metrics
        assert "revenue_weighted_recall" in rev_metrics
        assert "revenue_weighted_precision" in rev_metrics
        
    def test_model_persistence(self):
        """Test saving and loading model."""
        from ml.model import train_model, save_model, load_model
        from ml.preprocessing import preprocess_training_data
        from ml.data_generator import load_dataset
        from ml.config import FEATURE_COLUMNS, MODEL_VERSION
        import tempfile
        import shutil
        
        df = load_dataset()
        X_train, X_test, y_train, y_test, encoders, scaler = preprocess_training_data(df)
        
        model = train_model(X_train, y_train)
        
        # Create temporary directory for test artifacts
        with tempfile.TemporaryDirectory() as tmpdir:
            from ml.config import MODELS_DIR
            original_dir = MODELS_DIR
            
            try:
                # Override MODELS_DIR for testing
                import ml.config
                ml.config.MODELS_DIR = Path(tmpdir)
                
                # Save model
                paths = save_model(model, encoders, scaler, {}, {}, FEATURE_COLUMNS)
                assert "model_path" in paths
                
                # Load model
                loaded_model, loaded_encoders, loaded_scaler, metadata = load_model()
                assert loaded_model is not None
                assert loaded_encoders is not None
                assert loaded_scaler is not None
                
            finally:
                ml.config.MODELS_DIR = original_dir


class TestMLPredictor:
    """Tests for ML predictor inference."""
    
    def test_predictor_load(self):
        """Test loading predictor."""
        from ml.predictor import RecoveryPredictor
        
        predictor = RecoveryPredictor()
        predictor.load()
        
        assert predictor._loaded
        assert predictor.model is not None
        assert predictor.encoders is not None
        assert predictor.scaler is not None
    
    def test_predictor_prediction(self):
        """Test making a prediction."""
        from ml.predictor import RecoveryPredictor
        
        predictor = RecoveryPredictor()
        predictor.load()
        
        test_data = {
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
        
        result = predictor.predict(test_data)
        
        # Check result structure
        assert "recovery_probability" in result
        assert "risk_score" in result
        assert "risk_level" in result
        assert "priority" in result
        assert "recovery_category" in result
        assert "model_version" in result
        
        # Check probability range
        assert 0 <= result["recovery_probability"] <= 1
        assert 0 <= result["risk_score"] <= 1
        
        # Check risk level
        assert result["risk_level"] in ["LOW", "MEDIUM", "HIGH", "CRITICAL"]
        
        # Check priority
        assert result["priority"] in ["P0", "P1", "P2", "P3"]
        
    def test_risk_level_thresholds(self):
        """Test risk level determination from thresholds."""
        from ml.predictor import RecoveryPredictor
        from ml.config import RISK_THRESHOLDS
        
        predictor = RecoveryPredictor()
        
        # Test based on actual config thresholds
        # LOW: score < MEDIUM threshold (0.6)
        assert predictor._get_risk_level(0.2) == "LOW"
        assert predictor._get_risk_level(0.5) == "LOW"
        
        # MEDIUM: score >= 0.6
        assert predictor._get_risk_level(0.6) == "MEDIUM"
        assert predictor._get_risk_level(0.7) == "MEDIUM"
        
        # HIGH: score >= 0.8
        assert predictor._get_risk_level(0.8) == "HIGH"
        assert predictor._get_risk_level(0.85) == "HIGH"
        
        # CRITICAL: score >= 1.0
        assert predictor._get_risk_level(1.0) == "CRITICAL"
    
    def test_priority_determination(self):
        """Test priority determination."""
        from ml.predictor import RecoveryPredictor
        
        predictor = RecoveryPredictor()
        
        # Test P0: CRITICAL risk with high recovery probability
        assert predictor._get_priority("CRITICAL", 0.8) == "P0"
        
        # Test P1: HIGH risk with medium recovery probability
        assert predictor._get_priority("HIGH", 0.6) == "P1"
        
        # Test P2: MEDIUM risk with low recovery probability
        assert predictor._get_priority("MEDIUM", 0.4) == "P2"
        
        # Test P3: LOW risk (default)
        assert predictor._get_priority("LOW", 0.2) == "P3"


class TestMLService:
    """Tests for ML service layer."""
    
    def test_predict_recovery(self):
        """Test predict_recovery service function."""
        from app.services.ml_service import predict_recovery
        
        test_data = {
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
        }
        
        result = predict_recovery(test_data)
        assert "recovery_probability" in result
        assert "risk_score" in result
        
    def test_get_model_info(self):
        """Test get_model_info service function."""
        from app.services.ml_service import get_model_info
        
        info = get_model_info()
        assert "model_version" in info
        assert "num_features" in info
        
    def test_get_model_health(self):
        """Test get_model_health service function."""
        from app.services.ml_service import get_model_health
        
        health = get_model_health()
        assert "status" in health
        assert health["status"] in ["healthy", "unhealthy"]


class TestMLAPI:
    """Tests for ML API endpoints."""
    
    def test_predict_endpoint(self, client):
        """Test POST /api/ml/predict endpoint."""
        response = client.post(
            "/api/ml/predict",
            json={
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
            },
        )
        
        assert response.status_code == 200
        data = response.json()
        assert data["success"] is True
        assert "recovery_probability" in data["data"]
        assert "risk_score" in data["data"]
        assert "risk_level" in data["data"]
        assert "priority" in data["data"]
        
    def test_batch_predict_endpoint(self, client):
        """Test POST /api/ml/predict/batch endpoint."""
        response = client.post(
            "/api/ml/predict/batch",
            json={
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
                ],
            },
        )
        
        assert response.status_code == 200
        data = response.json()
        assert data["success"] is True
        assert len(data["data"]) == 2
        
    def test_model_info_endpoint(self, client):
        """Test GET /api/ml/model endpoint."""
        response = client.get("/api/ml/model")
        
        assert response.status_code == 200
        data = response.json()
        assert "model_version" in data
        assert "num_features" in data
        
    def test_ml_health_endpoint(self, client):
        """Test GET /api/ml/health endpoint."""
        response = client.get("/api/ml/health")
        
        assert response.status_code == 200
        data = response.json()
        assert data["success"] is True
        assert "data" in data
        assert "status" in data["data"]
        
    def test_predict_invalid_request(self, client):
        """Test prediction with invalid request data."""
        response = client.post(
            "/api/ml/predict",
            json={
                "amount": -100,  # Invalid: negative amount
                "payment_method": "card",
                "failure_reason": "insufficient_funds",
            },
        )
        
        # Should return validation error
        assert response.status_code == 422
