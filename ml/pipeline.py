"""
ML Training Pipeline for Revenue Recovery Agent.

Complete pipeline:
  dataset generation -> preprocessing -> feature engineering ->
  train/test split -> model training -> evaluation -> model persistence

Run: python -m ml.pipeline
"""

import sys
import json
from pathlib import Path

# Ensure the project root is on the path so `ml` and `data` resolve
_root = Path(__file__).resolve().parent.parent
if str(_root) not in sys.path:
    sys.path.insert(0, str(_root))

import numpy as np
import pandas as pd

from ml.config import (
    FEATURE_COLUMNS,
    TARGET_COLUMN,
    DATA_DIR,
    MODELS_DIR,
    RANDOM_SEED,
    RANDOM_FOREST_PARAMS,
    MODEL_VERSION,
)
from ml.data_generator import generate_synthetic_dataset, save_dataset
from ml.preprocessing import preprocess_training_data
from ml.model import (
    train_model,
    evaluate_model,
    revenue_weighted_evaluation,
    save_model,
)


def run_pipeline() -> dict:
    """Execute the full ML training pipeline. Returns a results dict."""

    print("=" * 70)
    print("  REVENUE RECOVERY AGENT - ML TRAINING PIPELINE")
    print("=" * 70)

    # ------------------------------------------------------------------
    # 1. Generate synthetic dataset
    # ------------------------------------------------------------------
    print("\n[1/6] Generating synthetic dataset...")
    df = generate_synthetic_dataset()
    save_dataset(df)

    n_records = len(df)
    n_customers = df["customer_id"].nunique()
    recovery_rate = df[TARGET_COLUMN].mean()
    print(f"  Records       : {n_records}")
    print(f"  Unique cust.  : {n_customers}")
    print(f"  Recovery rate : {recovery_rate:.2%}")
    assert n_records >= 5000, f"Expected >= 5000 records, got {n_records}"

    # ------------------------------------------------------------------
    # 2. Preprocessing & train/test split
    # ------------------------------------------------------------------
    print("\n[2/6] Preprocessing & train/test split...")
    X_train, X_test, y_train, y_test, encoders, scaler = (
        preprocess_training_data(df)
    )
    print(f"  X_train shape : {X_train.shape}")
    print(f"  X_test  shape : {X_test.shape}")
    print(f"  y_train dist  : {dict(zip(*np.unique(y_train, return_counts=True)))}")
    print(f"  y_test  dist  : {dict(zip(*np.unique(y_test, return_counts=True)))}")

    # Keep raw amounts aligned with the test split so we can compute
    # revenue-weighted metrics.  preprocess_training_data uses the same
    # random_state / stratify as this pipeline, so the row ordering of
    # df corresponds to the concatenated train+test split.
    from sklearn.model_selection import train_test_split as _split

    _, amounts_test_raw = _split(
        df["amount"].values,
        test_size=0.2,
        random_state=42,
        stratify=df[TARGET_COLUMN].values,
    )

    # ------------------------------------------------------------------
    # 3. Train model
    # ------------------------------------------------------------------
    print("\n[3/6] Training RandomForest model...")
    model = train_model(X_train, y_train)
    print(f"  Model type    : {type(model).__name__}")
    print(f"  Estimators    : {model.n_estimators}")

    # ------------------------------------------------------------------
    # 4. Standard evaluation metrics
    # ------------------------------------------------------------------
    print("\n[4/6] Computing standard evaluation metrics...")
    metrics = evaluate_model(model, X_test, y_test, feature_names=FEATURE_COLUMNS)

    print(f"  Precision     : {metrics['precision']:.4f}")
    print(f"  Recall        : {metrics['recall']:.4f}")
    print(f"  F1 Score      : {metrics['f1_score']:.4f}")
    print(f"  ROC-AUC       : {metrics['roc_auc']:.4f}")
    print(f"  Confusion mat.: {metrics['confusion_matrix']}")

    # Top 5 features by importance
    fi = metrics.get("feature_importance", {})
    top5 = list(fi.items())[:5]
    print("  Top features  :")
    for fname, imp in top5:
        print(f"    {fname:40s} {imp:.4f}")

    # ------------------------------------------------------------------
    # 5. Revenue-weighted evaluation
    # ------------------------------------------------------------------
    print("\n[5/6] Computing revenue-weighted recovery metrics...")
    rev_metrics = revenue_weighted_evaluation(
        model, X_test, y_test, amounts_test_raw
    )

    print(f"  Total failed value       : INR {rev_metrics['total_failed_payment_value']:,.2f}")
    print(f"  Total recoverable value  : INR {rev_metrics['total_recoverable_payment_value']:,.2f}")
    print(f"  Predicted recoverable    : INR {rev_metrics['predicted_recoverable_payment_value']:,.2f}")
    print(f"  Correctly identified     : INR {rev_metrics['correctly_identified_recoverable_revenue']:,.2f}")
    print(f"  Missed recoverable       : INR {rev_metrics['missed_recoverable_revenue']:,.2f}")
    print(f"  Revenue-weighted recall  : {rev_metrics['revenue_weighted_recall']:.4f}")
    print(f"  Revenue-weighted prec.   : {rev_metrics['revenue_weighted_precision']:.4f}")
    print(f"  Recovery rate (actual)   : {rev_metrics['recovery_rate']:.2%}")

    # ------------------------------------------------------------------
    # 6. Persist everything
    # ------------------------------------------------------------------
    print("\n[6/6] Saving model & artifacts...")
    paths = save_model(model, encoders, scaler, metrics, rev_metrics, FEATURE_COLUMNS)

    # Also save a human-readable evaluation summary
    eval_summary = {
        "model_version": MODEL_VERSION,
        "dataset_records": n_records,
        "dataset_customers": n_customers,
        "recovery_rate": float(recovery_rate),
        "standard_metrics": {
            "precision": metrics["precision"],
            "recall": metrics["recall"],
            "f1_score": metrics["f1_score"],
            "roc_auc": metrics["roc_auc"],
            "confusion_matrix": metrics["confusion_matrix"],
        },
        "revenue_metrics": rev_metrics,
        "feature_importance_top10": dict(list(fi.items())[:10]),
    }
    summary_path = MODELS_DIR / f"evaluation_summary_{MODEL_VERSION}.json"
    with open(summary_path, "w") as f:
        json.dump(eval_summary, f, indent=2)
    paths["evaluation_summary_path"] = str(summary_path)

    print("\n  Saved artifacts:")
    for key, val in paths.items():
        print(f"    {key}: {val}")

    # ------------------------------------------------------------------
    # Done
    # ------------------------------------------------------------------
    print("\n" + "=" * 70)
    print("  PIPELINE COMPLETE")
    print("=" * 70)

    return {
        "dataset_records": n_records,
        "metrics": metrics,
        "revenue_metrics": rev_metrics,
        "paths": paths,
    }


if __name__ == "__main__":
    run_pipeline()
