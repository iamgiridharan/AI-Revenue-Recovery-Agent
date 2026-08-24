"""
ML Service Layer for Revenue Recovery Agent.

Provides inference capabilities through the backend API.
"""
import sys
from pathlib import Path

# Add project root to path for ml module imports
_project_root = Path(__file__).resolve().parent.parent.parent.parent
if str(_project_root) not in sys.path:
    sys.path.insert(0, str(_project_root))

from ml.predictor import RecoveryPredictor, get_predictor
from ml.config import MODEL_VERSION


_predictor_instance = None


def get_ml_predictor() -> RecoveryPredictor:
    """Get or initialize the ML predictor singleton."""
    global _predictor_instance
    if _predictor_instance is None:
        _predictor_instance = RecoveryPredictor()
        _predictor_instance.load()
    return _predictor_instance


def predict_recovery(raw_data: dict) -> dict:
    """
    Make a recovery prediction for a failed payment.
    
    Args:
        raw_data: Dictionary with transaction and customer data
        
    Returns:
        Dictionary with prediction results
    """
    predictor = get_ml_predictor()
    return predictor.predict(raw_data)


def predict_batch(raw_data_list: list[dict]) -> list[dict]:
    """
    Make predictions for multiple transactions.
    
    Args:
        raw_data_list: List of dictionaries with transaction data
        
    Returns:
        List of prediction results
    """
    predictor = get_ml_predictor()
    return [predictor.predict(data) for data in raw_data_list]


def get_model_info() -> dict:
    """
    Get information about the loaded ML model.
    
    Returns:
        Dictionary with model metadata
    """
    predictor = get_ml_predictor()
    return {
        "model_version": predictor.model_version,
        "model_type": "RandomForestClassifier",
        "num_features": len(predictor.metadata.get("feature_names", [])),
        "feature_names": predictor.metadata.get("feature_names", []),
        "training_timestamp": predictor.metadata.get("training_timestamp"),
    }


def get_model_health() -> dict:
    """
    Check ML model health status.
    
    Returns:
        Dictionary with health status
    """
    try:
        predictor = get_ml_predictor()
        # Try a simple prediction to verify model is working
        test_data = {
            "amount": 1000.0,
            "payment_method": "card",
            "failure_reason": "insufficient_funds",
            "currency": "INR",
            "attempt_number": 1,
            "hour_of_day": 12,
            "day_of_week": 0,
            "days_since_last_transaction": 5.0,
            "customer_total_transactions": 10,
            "customer_successful_transactions": 8,
            "customer_failed_transactions": 2,
            "customer_lifetime_value": 10000.0,
            "customer_age_days": 180,
        }
        result = predictor.predict(test_data)
        
        return {
            "status": "healthy",
            "model_version": predictor.model_version,
            "model_loaded": predictor._loaded,
            "test_prediction_successful": True,
            "recovery_probability": result["recovery_probability"],
        }
    except Exception as e:
        return {
            "status": "unhealthy",
            "error": str(e),
            "model_loaded": False,
            "test_prediction_successful": False,
        }
