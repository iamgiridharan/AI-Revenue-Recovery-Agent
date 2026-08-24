"""
Data Preprocessing Pipeline for Revenue Recovery Agent.

Handles data cleaning, encoding, and feature engineering
for ML model training and inference.
"""

import numpy as np
import pandas as pd
from sklearn.preprocessing import LabelEncoder, StandardScaler
from sklearn.model_selection import train_test_split
from ml.config import (
    FEATURE_COLUMNS,
    TARGET_COLUMN,
    RANDOM_SEED,
    TRAIN_TEST_SEED,
    TEST_SIZE,
)


# ============================================================================
# LABEL ENCODERS (fitted on training data)
# ============================================================================

_label_encoders = {}
_scaler = None


def get_feature_columns() -> list:
    """Return the list of feature columns used by the model."""
    return FEATURE_COLUMNS.copy()


def preprocess_training_data(df: pd.DataFrame) -> tuple:
    """
    Preprocess data for training.

    Args:
        df: Raw DataFrame with transaction data

    Returns:
        Tuple of (X_train, X_test, y_train, y_test, encoders, scaler)
    """
    global _label_encoders, _scaler

    # Work on a copy
    data = df.copy()

    # Separate features and target
    y = data[TARGET_COLUMN].values

    # Encode categorical variables
    data, encoders = _encode_categoricals(data)
    _label_encoders = encoders

    # Select feature columns
    X = data[FEATURE_COLUMNS].values

    # Scale features
    scaler = StandardScaler()
    X_scaled = scaler.fit_transform(X)
    _scaler = scaler

    # Train/test split (time-aware if possible, otherwise random)
    X_train, X_test, y_train, y_test = train_test_split(
        X_scaled, y,
        test_size=TEST_SIZE,
        random_state=TRAIN_TEST_SEED,
        stratify=y,
    )

    return X_train, X_test, y_train, y_test, encoders, scaler


def preprocess_inference_data(
    data: dict,
    encoders: dict,
    scaler: StandardScaler,
) -> np.ndarray:
    """
    Preprocess a single data point for inference.

    Args:
        data: Dictionary of feature values
        encoders: Fitted label encoders
        scaler: Fitted standard scaler

    Returns:
        Preprocessed feature array
    """
    # Create DataFrame from dict
    df = pd.DataFrame([data])

    # Encode categorical variables using fitted encoders
    for col, encoder in encoders.items():
        encoded_col = f"{col}_encoded"
        if col in df.columns:
            # Create encoded column (do not overwrite original)
            df[encoded_col] = df[col].apply(
                lambda x, _enc=encoder: _enc.transform([x])[0]
                if x in _enc.classes_
                else -1
            )

    # Select feature columns
    X = df[FEATURE_COLUMNS].values

    # Scale features
    X_scaled = scaler.transform(X)

    return X_scaled


def _encode_categoricals(df: pd.DataFrame) -> tuple:
    """Encode categorical variables using LabelEncoder."""
    categorical_columns = ["currency", "payment_method", "failure_reason"]
    encoders = {}

    data = df.copy()
    for col in categorical_columns:
        if col in data.columns:
            le = LabelEncoder()
            # Fit on all unique values including -1 for unknown
            all_values = list(data[col].unique()) + ["unknown"]
            le.fit(all_values)
            data[f"{col}_encoded"] = le.transform(data[col])
            encoders[col] = le

    return data, encoders


def create_features_from_raw(raw_data: dict) -> dict:
    """
    Create model features from raw transaction data.

    This function transforms raw input data into the features
    expected by the ML model. All features are derived from
    information available at the time of payment failure.

    Args:
        raw_data: Dictionary with raw transaction data

    Returns:
        Dictionary with engineered features
    """
    features = {}

    # Transaction features (direct)
    features["amount"] = raw_data.get("amount", 0)
    features["payment_method"] = raw_data.get("payment_method", "unknown")
    features["failure_reason"] = raw_data.get("failure_reason", "unknown")
    features["currency"] = raw_data.get("currency", "INR")
    features["attempt_number"] = raw_data.get("attempt_number", 1)
    features["is_retry"] = 1 if features["attempt_number"] > 1 else 0

    # Time features
    features["hour_of_day"] = raw_data.get("hour_of_day", 12)
    features["day_of_week"] = raw_data.get("day_of_week", 0)
    features["days_since_last_transaction"] = raw_data.get("days_since_last_transaction", 0)

    # Customer features (known at failure time)
    features["customer_total_transactions"] = raw_data.get("customer_total_transactions", 0)
    features["customer_successful_transactions"] = raw_data.get("customer_successful_transactions", 0)
    features["customer_failed_transactions"] = raw_data.get("customer_failed_transactions", 0)

    # Derived customer features
    total = max(features["customer_total_transactions"], 1)
    features["customer_success_rate"] = features["customer_successful_transactions"] / total
    features["customer_failure_rate"] = 1 - features["customer_success_rate"]
    features["customer_lifetime_value"] = raw_data.get("customer_lifetime_value", 0)
    features["customer_avg_transaction_amount"] = raw_data.get("customer_lifetime_value", 0) / total

    # Recent history
    features["customer_recent_failure_count"] = min(features["customer_failed_transactions"], 5)
    features["customer_recent_success_count"] = min(features["customer_successful_transactions"], 10)

    # Amount ratio
    avg_amount = features["customer_avg_transaction_amount"]
    features["amount_vs_avg_ratio"] = features["amount"] / max(avg_amount, 1)

    # Customer age
    features["customer_days_active"] = raw_data.get("customer_age_days", 0)

    return features
