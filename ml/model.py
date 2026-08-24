"""
ML Model Training and Evaluation for Revenue Recovery Agent.

Implements the complete ML pipeline including training,
evaluation, and revenue-weighted analysis.
"""

import numpy as np
import pandas as pd
import joblib
import json
from datetime import datetime
from sklearn.ensemble import RandomForestClassifier
from sklearn.metrics import (
    precision_score,
    recall_score,
    f1_score,
    roc_auc_score,
    confusion_matrix,
    classification_report,
)
from ml.config import (
    MODEL_VERSION,
    MODEL_TYPE,
    MODELS_DIR,
    RANDOM_SEED,
    RANDOM_FOREST_PARAMS,
    FEATURE_COLUMNS,
    TARGET_COLUMN,
)


def train_model(
    X_train: np.ndarray,
    y_train: np.ndarray,
    params: dict = None,
) -> RandomForestClassifier:
    """
    Train a RandomForest classifier for recovery prediction.

    Args:
        X_train: Training features
        y_train: Training labels
        params: Model hyperparameters (uses defaults if None)

    Returns:
        Trained model
    """
    if params is None:
        params = RANDOM_FOREST_PARAMS.copy()

    model = RandomForestClassifier(**params)
    model.fit(X_train, y_train)

    return model


def evaluate_model(
    model: RandomForestClassifier,
    X_test: np.ndarray,
    y_test: np.ndarray,
    feature_names: list = None,
) -> dict:
    """
    Evaluate model performance with standard and revenue-weighted metrics.

    Args:
        model: Trained model
        X_test: Test features
        y_test: Test labels
        feature_names: List of feature names for importance

    Returns:
        Dictionary of evaluation metrics
    """
    # Predictions
    y_pred = model.predict(X_test)
    y_prob = model.predict_proba(X_test)[:, 1]

    # Standard metrics
    metrics = {
        "precision": precision_score(y_test, y_pred),
        "recall": recall_score(y_test, y_pred),
        "f1_score": f1_score(y_test, y_pred),
        "roc_auc": roc_auc_score(y_test, y_prob),
        "confusion_matrix": confusion_matrix(y_test, y_pred).tolist(),
        "classification_report": classification_report(y_test, y_pred, output_dict=True),
    }

    # Feature importance
    if feature_names:
        importance = dict(zip(feature_names, model.feature_importances_))
        metrics["feature_importance"] = dict(sorted(importance.items(), key=lambda x: x[1], reverse=True))

    return metrics


def revenue_weighted_evaluation(
    model: RandomForestClassifier,
    X_test: np.ndarray,
    y_test: np.ndarray,
    amounts: np.ndarray,
    y_prob: np.ndarray = None,
) -> dict:
    """
    Calculate revenue-weighted evaluation metrics.

    This evaluates not just classification accuracy, but how well
    the model identifies recoverable revenue.

    Args:
        model: Trained model
        X_test: Test features
        y_test: True labels
        amounts: Transaction amounts for test set
        y_prob: Predicted probabilities (computed if None)

    Returns:
        Dictionary of revenue-weighted metrics
    """
    if y_prob is None:
        y_prob = model.predict_proba(X_test)[:, 1]

    y_pred = model.predict(X_test)

    # Basic revenue metrics
    total_failed_value = amounts.sum()
    total_recoverable_value = amounts[y_test == 1].sum()
    predicted_recoverable_value = amounts[y_pred == 1].sum()

    # Correctly identified recoverable revenue (True Positives * amount)
    correctly_identified = amounts[(y_pred == 1) & (y_test == 1)].sum()
    # Missed recoverable revenue (False Negatives * amount)
    missed_recoverable = amounts[(y_pred == 0) & (y_test == 1)].sum()
    # False positives (incorrectly predicted as recoverable)
    false_positive_value = amounts[(y_pred == 1) & (y_test == 0)].sum()

    # Revenue-weighted recall
    revenue_weighted_recall = (
        correctly_identified / total_recoverable_value
        if total_recoverable_value > 0
        else 0
    )

    # Revenue-weighted precision
    revenue_weighted_precision = (
        correctly_identified / predicted_recoverable_value
        if predicted_recoverable_value > 0
        else 0
    )

    # Revenue at risk (what we'd lose if we didn't attempt recovery)
    revenue_at_risk = total_recoverable_value

    # Recovery opportunity (what we could potentially recover)
    recovery_opportunity = predicted_recoverable_value

    # Actual recovery (what we'd actually recover based on predictions)
    actual_recovery_potential = correctly_identified

    metrics = {
        "total_failed_payment_value": float(total_failed_value),
        "total_recoverable_payment_value": float(total_recoverable_value),
        "predicted_recoverable_payment_value": float(predicted_recoverable_value),
        "correctly_identified_recoverable_revenue": float(correctly_identified),
        "missed_recoverable_revenue": float(missed_recoverable),
        "false_positive_value": float(false_positive_value),
        "revenue_weighted_recall": float(revenue_weighted_recall),
        "revenue_weighted_precision": float(revenue_weighted_precision),
        "recovery_rate": float(total_recoverable_value / total_failed_value) if total_failed_value > 0 else 0,
        "potential_recovery_rate": float(predicted_recoverable_value / total_failed_value) if total_failed_value > 0 else 0,
        "actual_recovery_rate": float(correctly_identified / total_failed_value) if total_failed_value > 0 else 0,
    }

    return metrics


def save_model(
    model: RandomForestClassifier,
    encoders: dict,
    scaler,
    metrics: dict,
    revenue_metrics: dict,
    feature_names: list,
) -> dict:
    """
    Save model and associated artifacts.

    Args:
        model: Trained model
        encoders: Fitted label encoders
        scaler: Fitted standard scaler
        metrics: Evaluation metrics
        revenue_metrics: Revenue-weighted metrics
        feature_names: List of feature names

    Returns:
        Dictionary with paths to saved artifacts
    """
    timestamp = datetime.now().isoformat()

    # Save model
    model_path = MODELS_DIR / f"recovery_model_{MODEL_VERSION}.joblib"
    joblib.dump(model, model_path)

    # Save encoders
    encoders_path = MODELS_DIR / f"encoders_{MODEL_VERSION}.joblib"
    joblib.dump(encoders, encoders_path)

    # Save scaler
    scaler_path = MODELS_DIR / f"scaler_{MODEL_VERSION}.joblib"
    joblib.dump(scaler, scaler_path)

    # Save metadata
    metadata = {
        "model_version": MODEL_VERSION,
        "model_type": MODEL_TYPE,
        "training_timestamp": timestamp,
        "feature_names": feature_names,
        "num_features": len(feature_names),
        "evaluation_metrics": metrics,
        "revenue_metrics": revenue_metrics,
    }

    metadata_path = MODELS_DIR / f"model_metadata_{MODEL_VERSION}.json"
    with open(metadata_path, "w") as f:
        json.dump(metadata, f, indent=2, default=str)

    return {
        "model_path": str(model_path),
        "encoders_path": str(encoders_path),
        "scaler_path": str(scaler_path),
        "metadata_path": str(metadata_path),
    }


def load_model(version: str = None):
    """
    Load a saved model and its artifacts.

    Args:
        version: Model version to load (uses config default if None)

    Returns:
        Tuple of (model, encoders, scaler, metadata)
    """
    if version is None:
        from ml.config import MODEL_VERSION
        version = MODEL_VERSION

    model_path = MODELS_DIR / f"recovery_model_{version}.joblib"
    encoders_path = MODELS_DIR / f"encoders_{version}.joblib"
    scaler_path = MODELS_DIR / f"scaler_{version}.joblib"
    metadata_path = MODELS_DIR / f"model_metadata_{version}.json"

    # Check if files exist
    for path in [model_path, encoders_path, scaler_path]:
        if not path.exists():
            raise FileNotFoundError(f"Model artifact not found: {path}")

    model = joblib.load(model_path)
    encoders = joblib.load(encoders_path)
    scaler = joblib.load(scaler_path)

    metadata = {}
    if metadata_path.exists():
        with open(metadata_path, "r") as f:
            metadata = json.load(f)

    return model, encoders, scaler, metadata
