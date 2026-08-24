"""
ML Predictor for Revenue Recovery Agent.

Provides inference capabilities for predicting recovery probability
and risk assessment for failed payments.
"""

import numpy as np
from ml.config import (
    RISK_THRESHOLDS,
    RECOVERY_THRESHOLDS,
    PRIORITY_THRESHOLDS,
    MODEL_VERSION,
    FEATURE_COLUMNS,
)
from ml.preprocessing import preprocess_inference_data, create_features_from_raw
from ml.model import load_model


class RecoveryPredictor:
    """
    Predictor class for revenue recovery predictions.

    This class loads a trained model and provides inference
    capabilities for predicting recovery probability and risk levels.
    """

    def __init__(self, model_version: str = None):
        """
        Initialize the predictor.

        Args:
            model_version: Version of the model to load
        """
        self.model_version = model_version or MODEL_VERSION
        self.model = None
        self.encoders = None
        self.scaler = None
        self.metadata = None
        self._loaded = False

    def load(self):
        """Load the model and artifacts."""
        if self._loaded:
            return

        self.model, self.encoders, self.scaler, self.metadata = load_model(
            self.model_version
        )
        self._loaded = True

    def predict(self, raw_data: dict) -> dict:
        """
        Make a recovery prediction for a failed payment.

        Args:
            raw_data: Dictionary with transaction and customer data

        Returns:
            Dictionary with prediction results
        """
        if not self._loaded:
            self.load()

        # Create features from raw data
        features = create_features_from_raw(raw_data)

        # Add categorical values for encoding
        features["payment_method"] = raw_data.get("payment_method", "unknown")
        features["failure_reason"] = raw_data.get("failure_reason", "unknown")
        features["currency"] = raw_data.get("currency", "INR")

        # Preprocess
        X = preprocess_inference_data(features, self.encoders, self.scaler)

        # Predict
        recovery_prob = self.model.predict_proba(X)[0][1]

        # Calculate risk score (inverse of recovery probability)
        risk_score = 1 - recovery_prob

        # Determine risk level
        risk_level = self._get_risk_level(risk_score)

        # Determine priority
        priority = self._get_priority(risk_level, recovery_prob)

        # Determine recovery probability category
        recovery_category = self._get_recovery_category(recovery_prob)

        return {
            "recovery_probability": round(float(recovery_prob), 4),
            "risk_score": round(float(risk_score), 4),
            "risk_level": risk_level,
            "priority": priority,
            "recovery_category": recovery_category,
            "model_version": self.model_version,
            "features_used": FEATURE_COLUMNS,
        }

    def _get_risk_level(self, risk_score: float) -> str:
        """Determine risk level from risk score."""
        if risk_score >= RISK_THRESHOLDS["CRITICAL"]:
            return "CRITICAL"
        elif risk_score >= RISK_THRESHOLDS["HIGH"]:
            return "HIGH"
        elif risk_score >= RISK_THRESHOLDS["MEDIUM"]:
            return "MEDIUM"
        else:
            return "LOW"

    def _get_priority(self, risk_level: str, recovery_prob: float) -> str:
        """Determine priority based on risk level and recovery probability."""
        for priority, config in PRIORITY_THRESHOLDS.items():
            if (
                risk_level == config["risk_level"]
                and recovery_prob >= config["recovery_prob_min"]
            ):
                return priority
        return "P3"  # Default lowest priority

    def _get_recovery_category(self, recovery_prob: float) -> str:
        """Determine recovery probability category."""
        if recovery_prob >= RECOVERY_THRESHOLDS["HIGH_PROBABILITY"]:
            return "HIGH_PROBABILITY"
        elif recovery_prob >= RECOVERY_THRESHOLDS["MEDIUM_PROBABILITY"]:
            return "MEDIUM_PROBABILITY"
        else:
            return "LOW_PROBABILITY"


# Singleton predictor instance
_predictor = None


def get_predictor() -> RecoveryPredictor:
    """Get or create the singleton predictor instance."""
    global _predictor
    if _predictor is None:
        _predictor = RecoveryPredictor()
    return _predictor


def predict_recovery(raw_data: dict) -> dict:
    """
    Convenience function to predict recovery probability.

    Args:
        raw_data: Dictionary with transaction and customer data

    Returns:
        Dictionary with prediction results
    """
    predictor = get_predictor()
    return predictor.predict(raw_data)
