"""
ML Configuration for Revenue Recovery Agent.

This module defines all constants, thresholds, and configuration
for the ML prediction pipeline.
"""

import os
from pathlib import Path

# ============================================================================
# PATHS
# ============================================================================

BASE_DIR = Path(__file__).parent
DATA_DIR = BASE_DIR.parent / "data"
MODELS_DIR = BASE_DIR / "saved_models"

# Ensure directories exist
DATA_DIR.mkdir(exist_ok=True)
MODELS_DIR.mkdir(exist_ok=True)

# ============================================================================
# REPRODUCIBILITY
# ============================================================================

RANDOM_SEED = 42
DATASET_SEED = 42
TRAIN_TEST_SEED = 42
MODEL_SEED = 42

# ============================================================================
# DATASET CONFIGURATION
# ============================================================================

NUM_TRANSACTIONS = 6000  # Generate more than 5000 for safety
NUM_CUSTOMERS = 1500

# ============================================================================
# FEATURE ENGINEERING
# ============================================================================

# Features used by the model (known at prediction time)
FEATURE_COLUMNS = [
    # Transaction features
    "amount",
    "currency_encoded",
    "payment_method_encoded",
    "failure_reason_encoded",
    "attempt_number",
    "is_retry",

    # Time features
    "hour_of_day",
    "day_of_week",
    "days_since_last_transaction",

    # Customer features (known at failure time)
    "customer_total_transactions",
    "customer_successful_transactions",
    "customer_failed_transactions",
    "customer_success_rate",
    "customer_failure_rate",
    "customer_lifetime_value",
    "customer_avg_transaction_amount",
    "customer_days_active",

    # Transaction history features
    "customer_recent_failure_count",
    "customer_recent_success_count",
    "amount_vs_avg_ratio",
]

# Target column
TARGET_COLUMN = "recovered"

# ============================================================================
# MODEL CONFIGURATION
# ============================================================================

MODEL_VERSION = "v1"
MODEL_TYPE = "RandomForestClassifier"
MODEL_filename = f"recovery_model_{MODEL_VERSION}.joblib"
METADATA_filename = f"model_metadata_{MODEL_VERSION}.json"

# ============================================================================
# PREDICTION THRESHOLDS
# ============================================================================

# Risk score thresholds (0-1 scale, higher = more risky)
RISK_THRESHOLDS = {
    "LOW": 0.3,       # risk_score < 0.3
    "MEDIUM": 0.6,    # 0.3 <= risk_score < 0.6
    "HIGH": 0.8,      # 0.6 <= risk_score < 0.8
    "CRITICAL": 1.0,  # risk_score >= 0.8
}

# Recovery probability thresholds
RECOVERY_THRESHOLDS = {
    "HIGH_PROBABILITY": 0.7,   # >= 0.7
    "MEDIUM_PROBABILITY": 0.4, # 0.4 - 0.7
    "LOW_PROBABILITY": 0.0,    # < 0.4
}

# Priority mapping based on risk level and recovery probability
PRIORITY_THRESHOLDS = {
    "P0": {"risk_level": "CRITICAL", "recovery_prob_min": 0.6},
    "P1": {"risk_level": "HIGH", "recovery_prob_min": 0.5},
    "P2": {"risk_level": "MEDIUM", "recovery_prob_min": 0.3},
    "P3": {"risk_level": "LOW", "recovery_prob_min": 0.0},
}

# ============================================================================
# EVALUATION METRICS
# ============================================================================

METRICS_TO_CALCULATE = [
    "precision",
    "recall",
    "f1_score",
    "roc_auc",
    "confusion_matrix",
    "classification_report",
]

# ============================================================================
# TRAINING CONFIGURATION
# ============================================================================

TEST_SIZE = 0.2
VALIDATION_SIZE = 0.1
CV_FOLDS = 5

# Model hyperparameters
RANDOM_FOREST_PARAMS = {
    "n_estimators": 200,
    "max_depth": 12,
    "min_samples_split": 5,
    "min_samples_leaf": 2,
    "max_features": "sqrt",
    "class_weight": "balanced",
    "random_state": MODEL_SEED,
    "n_jobs": -1,
}
