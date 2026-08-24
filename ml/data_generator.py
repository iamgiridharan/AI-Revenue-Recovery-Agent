"""
Synthetic Dataset Generator for Revenue Recovery Agent.

Generates realistic transaction data with failure scenarios
and recovery outcomes for ML model training.
"""

import numpy as np
import pandas as pd
from datetime import datetime, timedelta
from pathlib import Path
from ml.config import (
    NUM_TRANSACTIONS,
    NUM_CUSTOMERS,
    RANDOM_SEED,
    DATA_DIR,
)


# ============================================================================
# CONSTANTS
# ============================================================================

PAYMENT_METHODS = ["card", "upi", "netbanking", "wallet", "emi"]
FAILURE_REASONS = [
    "insufficient_funds",
    "card_expired",
    "bank_declined",
    "network_timeout",
    "authentication_failed",
    "daily_limit_exceeded",
    "card_blocked",
    "invalid_cvv",
    "merchant_error",
    "customer_cancelled",
]
CURRENCIES = ["INR"]


def generate_synthetic_dataset(
    num_transactions: int = NUM_TRANSACTIONS,
    num_customers: int = NUM_CUSTOMERS,
    seed: int = RANDOM_SEED,
) -> pd.DataFrame:
    """
    Generate a realistic synthetic dataset of payment transactions.

    The dataset includes:
    - Transaction details (amount, method, failure reason)
    - Customer history and statistics
    - Time-based features
    - Recovery outcome (target variable)

    Args:
        num_transactions: Number of transactions to generate
        num_customers: Number of unique customers
        seed: Random seed for reproducibility

    Returns:
        DataFrame with synthetic transaction data
    """
    np.random.seed(seed)

    # Generate customers first
    customers = _generate_customers(num_customers, seed)

    # Generate transactions
    transactions = _generate_transactions(
        num_transactions, customers, seed
    )

    # Add recovery outcomes
    transactions = _add_recovery_outcomes(transactions, seed)

    return transactions


def _generate_customers(num_customers: int, seed: int) -> pd.DataFrame:
    """Generate customer profiles with varying characteristics."""
    np.random.seed(seed)

    customers = pd.DataFrame({
        "customer_id": range(1, num_customers + 1),
        "customer_age_days": np.random.randint(30, 1800, num_customers),
        "customer_total_transactions": np.random.poisson(15, num_customers) + 1,
        "customer_lifetime_value": np.random.lognormal(6, 1.5, num_customers),
        "customer_segment": np.random.choice(
            ["new", "regular", "premium", "at_risk"],
            num_customers,
            p=[0.2, 0.5, 0.2, 0.1],
        ),
    })

    # Calculate success/failure counts based on segment
    for idx, row in customers.iterrows():
        segment = row["customer_segment"]
        total = row["customer_total_transactions"]

        if segment == "new":
            success_rate = np.random.uniform(0.3, 0.7)
        elif segment == "regular":
            success_rate = np.random.uniform(0.6, 0.9)
        elif segment == "premium":
            success_rate = np.random.uniform(0.8, 0.95)
        else:  # at_risk
            success_rate = np.random.uniform(0.2, 0.5)

        customers.at[idx, "customer_successful_transactions"] = int(total * success_rate)
        customers.at[idx, "customer_failed_transactions"] = total - customers.at[idx, "customer_successful_transactions"]
        customers.at[idx, "customer_success_rate"] = success_rate

    return customers


def _generate_transactions(
    num_transactions: int,
    customers: pd.DataFrame,
    seed: int,
) -> pd.DataFrame:
    """Generate transactions linked to customers."""
    np.random.seed(seed + 1)

    # Sample customers for transactions
    customer_indices = np.random.choice(len(customers), num_transactions, replace=True)
    customer_data = customers.iloc[customer_indices].reset_index(drop=True)

    # Generate base transaction data
    transactions = pd.DataFrame({
        "transaction_id": range(1, num_transactions + 1),
        "customer_id": customer_data["customer_id"].values,
        "amount": _generate_amounts(num_transactions),
        "currency": np.random.choice(CURRENCIES, num_transactions),
        "payment_method": np.random.choice(PAYMENT_METHODS, num_transactions),
        "failure_reason": np.random.choice(FAILURE_REASONS, num_transactions),
        "attempt_number": np.random.choice([1, 1, 1, 2, 2, 3], num_transactions),
    })

    # Add customer features to transactions
    transactions["customer_total_transactions"] = customer_data["customer_total_transactions"].values
    transactions["customer_successful_transactions"] = customer_data["customer_successful_transactions"].values
    transactions["customer_failed_transactions"] = customer_data["customer_failed_transactions"].values
    transactions["customer_success_rate"] = customer_data["customer_success_rate"].values
    transactions["customer_lifetime_value"] = customer_data["customer_lifetime_value"].values
    transactions["customer_age_days"] = customer_data["customer_age_days"].values

    # Add time-based features
    transactions = _add_time_features(transactions, seed + 2)

    # Add derived features
    transactions = _add_derived_features(transactions)

    return transactions


def _generate_amounts(num_transactions: int) -> np.ndarray:
    """Generate realistic transaction amounts."""
    # Mix of small, medium, and large transactions
    amounts = np.concatenate([
        np.random.lognormal(4, 1, int(num_transactions * 0.4)),   # Small (5-100)
        np.random.lognormal(6, 1, int(num_transactions * 0.4)),   # Medium (100-1000)
        np.random.lognormal(8, 1, num_transactions - int(num_transactions * 0.8)),  # Large (1000+)
    ])
    np.random.shuffle(amounts)
    return np.round(amounts, 2)


def _add_time_features(transactions: pd.DataFrame, seed: int) -> pd.DataFrame:
    """Add time-based features to transactions."""
    np.random.seed(seed)

    num = len(transactions)

    # Generate transaction timestamps (last 90 days)
    base_date = datetime(2025, 1, 1)
    days_offset = np.random.randint(0, 90, num)
    hours = np.random.choice(
        range(24),
        num,
        p=_hour_distribution(),
    )

    timestamps = [
        base_date + timedelta(days=int(d), hours=int(h))
        for d, h in zip(days_offset, hours)
    ]

    transactions["transaction_date"] = timestamps
    transactions["hour_of_day"] = [t.hour for t in timestamps]
    transactions["day_of_week"] = [t.weekday() for t in timestamps]

    # Days since last transaction (simulated)
    transactions["days_since_last_transaction"] = np.random.exponential(5, num).clip(0, 90)

    return transactions


def _hour_distribution() -> list:
    """Generate realistic hour-of-day distribution for transactions."""
    # Peak hours: 10am-2pm and 7pm-10pm
    weights = [
        0.01, 0.01, 0.01, 0.01, 0.01, 0.01,  # 0-5
        0.02, 0.03, 0.04, 0.05, 0.08, 0.09,  # 6-11
        0.10, 0.08, 0.06, 0.05, 0.04, 0.04,  # 12-17
        0.05, 0.07, 0.08, 0.06, 0.03, 0.02,  # 18-23
    ]
    return [w / sum(weights) for w in weights]


def _add_derived_features(transactions: pd.DataFrame) -> pd.DataFrame:
    """Add derived features that would be known at prediction time."""
    # Attempt features
    transactions["is_retry"] = (transactions["attempt_number"] > 1).astype(int)

    # Customer failure count (excluding current)
    transactions["customer_recent_failure_count"] = transactions["customer_failed_transactions"].clip(upper=5)

    # Customer success count (recent)
    transactions["customer_recent_success_count"] = transactions["customer_successful_transactions"].clip(upper=10)

    # Customer average transaction amount
    transactions["customer_avg_transaction_amount"] = (
        transactions["customer_lifetime_value"]
        / transactions["customer_total_transactions"].clip(lower=1)
    )

    # Amount vs customer average
    transactions["amount_vs_avg_ratio"] = (
        transactions["amount"]
        / transactions["customer_avg_transaction_amount"].clip(lower=1)
    )

    # Customer failure rate
    transactions["customer_failure_rate"] = 1 - transactions["customer_success_rate"]

    # Days active
    transactions["customer_days_active"] = transactions["customer_age_days"]

    return transactions


def _add_recovery_outcomes(transactions: pd.DataFrame, seed: int) -> pd.DataFrame:
    """
    Add recovery outcomes based on realistic rules.

    Recovery probability depends on:
    - Customer history (success rate, lifetime value)
    - Failure reason (some are temporary, some permanent)
    - Attempt number (more attempts = less likely to succeed)
    - Amount (very high amounts less likely to be recovered)
    - Time features (some patterns)
    """
    np.random.seed(seed + 3)

    num = len(transactions)
    recovery_probs = np.zeros(num)

    # Factor 1: Customer success rate (strong predictor)
    recovery_probs += transactions["customer_success_rate"].values * 0.3

    # Factor 2: Failure reason impact
    failure_impact = {
        "insufficient_funds": -0.1,
        "card_expired": -0.3,
        "bank_declined": -0.15,
        "network_timeout": 0.15,
        "authentication_failed": 0.05,
        "daily_limit_exceeded": 0.1,
        "card_blocked": -0.25,
        "invalid_cvv": 0.1,
        "merchant_error": 0.2,
        "customer_cancelled": -0.35,
    }
    for reason, impact in failure_impact.items():
        mask = transactions["failure_reason"] == reason
        recovery_probs[mask] += impact

    # Factor 3: Attempt number (diminishing returns)
    attempt_penalty = (transactions["attempt_number"].values - 1) * 0.08
    recovery_probs -= attempt_penalty

    # Factor 4: Amount (high amounts harder to recover)
    amount_normalized = transactions["amount"].values / transactions["amount"].max()
    recovery_probs -= amount_normalized * 0.15

    # Factor 5: Customer lifetime value (high value customers get more help)
    clv_normalized = transactions["customer_lifetime_value"].values / transactions["customer_lifetime_value"].max()
    recovery_probs += clv_normalized * 0.1

    # Factor 6: Time-based patterns
    # Weekend transactions slightly harder to recover
    is_weekend = transactions["day_of_week"].isin([5, 6]).astype(int)
    recovery_probs -= is_weekend * 0.05

    # Clip probabilities
    recovery_probs = np.clip(recovery_probs, 0.05, 0.95)

    # Add noise
    noise = np.random.normal(0, 0.1, num)
    recovery_probs = np.clip(recovery_probs + noise, 0.01, 0.99)

    # Generate binary outcome based on probability
    recovered = np.random.binomial(1, recovery_probs)

    transactions["recovery_probability_actual"] = recovery_probs
    transactions["recovered"] = recovered

    return transactions


def save_dataset(df: pd.DataFrame, filename: str = "synthetic_transactions.csv") -> Path:
    """Save dataset to CSV."""
    filepath = DATA_DIR / filename
    df.to_csv(filepath, index=False)
    print(f"Dataset saved to {filepath}")
    print(f"Shape: {df.shape}")
    return filepath


def load_dataset(filename: str = "synthetic_transactions.csv") -> pd.DataFrame:
    """Load dataset from CSV."""
    filepath = DATA_DIR / filename
    if not filepath.exists():
        raise FileNotFoundError(f"Dataset not found at {filepath}")
    return pd.read_csv(filepath)


if __name__ == "__main__":
    # Generate and save dataset
    print("Generating synthetic dataset...")
    df = generate_synthetic_dataset()
    save_dataset(df)

    # Print summary
    print("\nDataset Summary:")
    print(f"Total transactions: {len(df)}")
    print(f"Unique customers: {df['customer_id'].nunique()}")
    print(f"Recovery rate: {df['recovered'].mean():.2%}")
    print(f"\nFailure reasons distribution:")
    print(df["failure_reason"].value_counts())
    print(f"\nPayment methods distribution:")
    print(df["payment_method"].value_counts())
    print(f"\nRecovery by failure reason:")
    print(df.groupby("failure_reason")["recovered"].mean().sort_values(ascending=False))
