"""
Simulation Service for Revenue Recovery Agent.

Runs synthetic transactions through the complete pipeline:
  Transaction → ML Prediction → AI Decision → Policy → Recovery → Audit

This service:
- Reuses the existing synthetic dataset (Phase 2)
- Processes each transaction through ML, AI agent, policy engine, and recovery
- Tracks all outcomes and generates audit events
- Computes business metrics
- Clearly labels all output as SIMULATED

CRITICAL SAFETY INVARIANT:
- Simulation NEVER calls real Razorpay APIs
- Simulation NEVER executes uncontrolled financial actions
- All amounts come from the synthetic dataset, not from LLM output
- Policy Engine is always applied
"""
import uuid
import time
import random
from datetime import datetime, timezone
from typing import Dict, Any, List, Optional

from sqlalchemy.orm import Session

from app.models import (
    Customer,
    Transaction,
    RevenueRiskCase,
    RecoveryAction,
    AuditEvent,
    PolicyConfig,
    CaseStatus,
    AuditEventType,
)
from app.services.ml_service import predict_recovery
from app.services.policy_engine import evaluate_action, get_active_policy
from app.utils.logging import logger


# ============================================================================
# SIMULATION CONSTANTS
# ============================================================================

# Label for all simulated data
SIMULATION_LABEL = "SIMULATED"

# Deterministic recovery outcomes for simulation
# These are used instead of actual Razorpay calls
SIMULATED_RECOVERY_OUTCOMES = {
    "CREATE_PAYMENT_LINK": {"success_rate": 0.65, "delay_ms": 50},
    "RETRY": {"success_rate": 0.50, "delay_ms": 30},
    "SEND_PAYMENT_REMINDER": {"success_rate": 0.40, "delay_ms": 20},
    "NO_ACTION": {"success_rate": 0.0, "delay_ms": 5},
    "WAIT_AND_RETRY": {"success_rate": 0.55, "delay_ms": 40},
    "ESCALATE_TO_HUMAN": {"success_rate": 0.0, "delay_ms": 10},
    "MARK_UNRECOVERABLE": {"success_rate": 0.0, "delay_ms": 5},
}

# Maximum recovery attempts before escalation
MAX_SIMULATION_RECOVERY_ATTEMPTS = 3


# ============================================================================
# SYNTHETIC DATA GENERATION
# ============================================================================

PAYMENT_METHODS = ["card", "upi", "netbanking", "wallet", "emi"]
FAILURE_REASONS = [
    "insufficient_funds", "card_expired", "bank_declined",
    "network_timeout", "authentication_failed", "daily_limit_exceeded",
    "card_blocked", "invalid_cvv", "merchant_error", "customer_cancelled",
]


def _generate_simulation_transactions(
    num_transactions: int,
    seed: Optional[int] = None,
) -> List[Dict[str, Any]]:
    """
    Generate synthetic transactions for simulation.
    Uses similar logic to the ML training data generator for consistency.
    """
    if seed is not None:
        random.seed(seed)

    transactions = []
    for i in range(num_transactions):
        # Generate realistic amount distribution
        amount_type = random.random()
        if amount_type < 0.4:
            amount = round(random.lognormvariate(4, 1), 2)  # Small: ~5-100
        elif amount_type < 0.8:
            amount = round(random.lognormvariate(6, 1), 2)  # Medium: ~100-1000
        else:
            amount = round(random.lognormvariate(8, 1), 2)  # Large: ~1000+

        # Customer features
        customer_total_txn = random.randint(2, 50)
        success_rate = random.uniform(0.2, 0.95)
        customer_successful = int(customer_total_txn * success_rate)
        customer_failed = customer_total_txn - customer_successful
        customer_ltv = round(random.lognormvariate(6, 1.5), 2)

        transactions.append({
            "amount": max(amount, 10.0),  # Minimum 10 INR
            "currency": "INR",
            "payment_method": random.choice(PAYMENT_METHODS),
            "failure_reason": random.choice(FAILURE_REASONS),
            "attempt_number": random.choice([1, 1, 1, 2, 2, 3]),
            "hour_of_day": random.choices(
                range(24),
                weights=[1,1,1,1,1,1,2,3,4,5,8,9,10,8,6,5,4,4,5,7,8,6,3,2],
            )[0],
            "day_of_week": random.randint(0, 6),
            "days_since_last_transaction": round(random.expovariate(0.2), 1),
            "customer_total_transactions": customer_total_txn,
            "customer_successful_transactions": customer_successful,
            "customer_failed_transactions": customer_failed,
            "customer_lifetime_value": customer_ltv,
            "customer_age_days": random.randint(30, 1800),
        })

    return transactions


def _simulate_recovery_outcome(
    action_type: str,
    case_amount: float,
    confidence: float,
) -> Dict[str, Any]:
    """
    Simulate the outcome of a recovery action deterministically.
    Returns success/failure based on configured probabilities.
    """
    config = SIMULATED_RECOVERY_OUTCOMES.get(
        action_type,
        {"success_rate": 0.3, "delay_ms": 20},
    )

    # Adjust success rate based on confidence
    adjusted_rate = config["success_rate"] * (0.5 + 0.5 * confidence)

    # Higher amounts slightly harder to recover
    if case_amount > 10000:
        adjusted_rate *= 0.8
    elif case_amount > 5000:
        adjusted_rate *= 0.9

    success = random.random() < adjusted_rate
    recovered_amount = case_amount if success else 0.0

    return {
        "success": success,
        "recovered_amount": recovered_amount,
        "payment_id": f"sim_pay_{uuid.uuid4().hex[:8]}" if success else None,
        "processing_time_ms": config["delay_ms"],
        "mock": True,
        "simulated": True,
    }


# ============================================================================
# MAIN SIMULATION RUNNER
# ============================================================================

def run_simulation(
    db: Session,
    num_transactions: int = 1000,
    seed: Optional[int] = None,
) -> Dict[str, Any]:
    """
    Run a batch simulation of synthetic payment failures and recovery attempts.

    Flow per transaction:
      1. Create customer + transaction + risk case in DB
      2. Run ML prediction
      3. Generate AI diagnosis/recommendation (deterministic mock)
      4. Check policy engine
      5. Execute recovery action (simulated)
      6. Record audit events
      7. Update case status

    Args:
        db: Database session
        num_transactions: Number of synthetic transactions
        seed: Random seed for reproducibility

    Returns:
        Complete simulation result dictionary
    """
    simulation_id = f"SIM-{uuid.uuid4().hex[:12].upper()}"
    started_at = datetime.now(timezone.utc)
    start_time = time.time()

    logger.info(f"Starting simulation {simulation_id} with {num_transactions} transactions")

    # Set seed for reproducibility
    if seed is not None:
        random.seed(seed)

    # Ensure active policy exists
    policy = get_active_policy(db)

    # Generate synthetic transactions
    synthetic_txns = _generate_simulation_transactions(num_transactions, seed)

    # Tracking variables
    total_recovered = 0.0
    total_at_risk = 0.0
    successful_recoveries = 0
    failed_recoveries = 0
    escalations = 0
    policy_blocks = 0
    total_processing_ms = 0.0
    recoverable_count = 0
    recovery_attempts = 0

    # Breakdowns
    action_breakdown = {}
    status_counts = {}
    ml_probs = []
    audit_count = 0
    policy_decisions_count = 0
    recovery_actions_count = 0

    for idx, txn_data in enumerate(synthetic_txns):
        try:
            txn_start = time.time()
            amount = txn_data["amount"]
            total_at_risk += amount

            # --- Step 1: Create customer ---
            cust_id = f"SIM-CUST-{idx:06d}"
            customer = Customer(
                customer_id=cust_id,
                name=f"Simulated Customer {idx}",
                email=f"sim{idx}@test.example.com",
                phone=f"+91{random.randint(7000000000, 9999999999)}",
                total_transactions=txn_data["customer_total_transactions"],
                successful_transactions=txn_data["customer_successful_transactions"],
                failed_transactions=txn_data["customer_failed_transactions"],
                lifetime_value=txn_data["customer_lifetime_value"],
            )
            db.add(customer)
            db.flush()

            # --- Step 2: Create transaction ---
            txn_id_str = f"SIM-TXN-{idx:06d}"
            transaction = Transaction(
                transaction_id=txn_id_str,
                customer_id=customer.id,
                amount=amount,
                currency=txn_data["currency"],
                payment_method=txn_data["payment_method"],
                status="FAILED",
                failure_reason=txn_data["failure_reason"],
                attempt_count=txn_data["attempt_number"],
            )
            db.add(transaction)
            db.flush()

            # --- Step 3: ML Prediction ---
            ml_result = predict_recovery(txn_data)
            recovery_prob = ml_result["recovery_probability"]
            risk_score = ml_result["risk_score"]
            priority = ml_result["priority"]
            ml_probs.append(recovery_prob)

            # --- Step 4: Create risk case ---
            case_id_str = f"SIM-CASE-{idx:06d}"
            case = RevenueRiskCase(
                case_id=case_id_str,
                transaction_id=transaction.id,
                customer_id=customer.id,
                amount=amount,
                risk_score=risk_score,
                recovery_probability=recovery_prob,
                priority=priority,
                status=CaseStatus.OPEN.value,
                attempt_count=0,
                recovered_amount=0.0,
            )
            db.add(case)
            db.flush()

            # Record case creation audit
            audit_event = AuditEvent(
                case_id=case.id,
                event_type=AuditEventType.CASE_CREATED.value,
                actor="simulation",
                decision="CREATED",
                reason=f"Simulated failed payment: {txn_data['failure_reason']}",
                metadata_={"simulation_id": simulation_id, "label": SIMULATION_LABEL},
            )
            db.add(audit_event)
            audit_count += 1

            # --- Step 5: ML risk assessment audit ---
            audit_event = AuditEvent(
                case_id=case.id,
                event_type=AuditEventType.RISK_ASSESSED.value,
                actor="ml_model",
                decision=f"Risk: {risk_score:.2f}, Recovery: {recovery_prob:.2f}",
                confidence=recovery_prob,
                metadata_={"simulation_id": simulation_id, "label": SIMULATION_LABEL},
            )
            db.add(audit_event)
            audit_count += 1

            # --- Step 6: AI Diagnosis (deterministic mock) ---
            recommended_action = _determine_action(
                recovery_prob, risk_score, txn_data["failure_reason"]
            )
            confidence = min(max(recovery_prob * 0.9 + random.uniform(-0.05, 0.05), 0.1), 0.99)

            diagnosis = _generate_diagnosis(
                txn_data["failure_reason"], recovery_prob, risk_score
            )

            # Update case with diagnosis
            case.diagnosis = diagnosis
            case.recommended_action = recommended_action
            case.status = CaseStatus.IN_PROGRESS.value

            # Record AI diagnosis audit
            audit_event = AuditEvent(
                case_id=case.id,
                event_type=AuditEventType.DIAGNOSIS_COMPLETED.value,
                actor="ai_agent",
                decision=recommended_action,
                reason=diagnosis,
                confidence=confidence,
                action=recommended_action,
                metadata_={
                    "simulation_id": simulation_id,
                    "label": SIMULATION_LABEL,
                    "recovery_probability": recovery_prob,
                },
            )
            db.add(audit_event)
            audit_count += 1

            # --- Step 7: Policy Engine Check ---
            policy_result = evaluate_action(
                db=db,
                case_id=case_id_str,
                proposed_action=recommended_action,
                confidence=confidence,
                recovery_probability=recovery_prob,
            )
            policy_decisions_count += 1

            if not policy_result.allowed:
                # Policy blocked
                policy_blocks += 1
                case.status = CaseStatus.ESCALATED.value if policy_result.decision == "ESCALATED" else case.status

                recovery_action = RecoveryAction(
                    case_id=case.id,
                    action_type=recommended_action,
                    reason=f"Policy {policy_result.decision}: {policy_result.reason}",
                    confidence=confidence,
                    policy_result=policy_result.decision,
                    execution_status="BLOCKED_BY_POLICY",
                )
                db.add(recovery_action)
                recovery_actions_count += 1

                # Audit
                audit_event = AuditEvent(
                    case_id=case.id,
                    event_type=AuditEventType.ACTION_FAILED.value,
                    actor="recovery_service",
                    decision=policy_result.decision,
                    reason=f"Policy blocked: {policy_result.reason}",
                    confidence=confidence,
                    action=recommended_action,
                    result="BLOCKED_BY_POLICY",
                    metadata_={"simulation_id": simulation_id, "label": SIMULATION_LABEL},
                )
                db.add(audit_event)
                audit_count += 1

                if policy_result.decision == "ESCALATED":
                    escalations += 1
                    status_counts["ESCALATED"] = status_counts.get("ESCALATED", 0) + 1
                else:
                    status_counts[case.status] = status_counts.get(case.status, 0) + 1

            else:
                # --- Step 8: Execute Recovery (Simulated) ---
                recovery_result = _simulate_recovery_outcome(
                    recommended_action, amount, confidence
                )
                recovery_attempts += 1

                if recommended_action not in action_breakdown:
                    action_breakdown[recommended_action] = {"success": 0, "failed": 0}

                if recovery_result["success"]:
                    # Successful recovery
                    successful_recoveries += 1
                    total_recovered += recovery_result["recovered_amount"]

                    case.status = CaseStatus.RECOVERED.value
                    case.recovered_amount = recovery_result["recovered_amount"]
                    case.attempt_count += 1

                    recovery_action = RecoveryAction(
                        case_id=case.id,
                        action_type=recommended_action,
                        reason=f"Simulated recovery: {recommended_action}",
                        confidence=confidence,
                        policy_result="APPROVED",
                        execution_status="SUCCESS",
                        api_reference=recovery_result["payment_id"],
                    )
                    db.add(recovery_action)
                    recovery_actions_count += 1

                    # Audit
                    audit_event = AuditEvent(
                        case_id=case.id,
                        event_type=AuditEventType.ACTION_EXECUTED.value,
                        actor="recovery_service",
                        decision="APPROVED",
                        reason=f"Simulated payment successful: {recovery_result['payment_id']}",
                        confidence=confidence,
                        action=recommended_action,
                        result="SUCCESS",
                        metadata_={
                            "simulation_id": simulation_id,
                            "label": SIMULATION_LABEL,
                            "payment_id": recovery_result["payment_id"],
                            "amount": recovery_result["recovered_amount"],
                            "simulated": True,
                        },
                    )
                    db.add(audit_event)
                    audit_count += 1

                    action_breakdown[recommended_action]["success"] += 1
                    status_counts["RECOVERED"] = status_counts.get("RECOVERED", 0) + 1

                else:
                    # Recovery failed — check stopping rules
                    case.attempt_count += 1

                    if case.attempt_count >= MAX_SIMULATION_RECOVERY_ATTEMPTS:
                        # Max attempts reached — escalate
                        case.status = CaseStatus.ESCALATED.value
                        escalations += 1

                        recovery_action = RecoveryAction(
                            case_id=case.id,
                            action_type=recommended_action,
                            reason=f"Simulated recovery failed after {case.attempt_count} attempts",
                            confidence=confidence,
                            policy_result="APPROVED",
                            execution_status="FAILED",
                        )
                        db.add(recovery_action)
                        recovery_actions_count += 1

                        audit_event = AuditEvent(
                            case_id=case.id,
                            event_type=AuditEventType.CASE_ESCALATED.value,
                            actor="recovery_service",
                            decision="ESCALATED",
                            reason=f"Max recovery attempts ({MAX_SIMULATION_RECOVERY_ATTEMPTS}) reached",
                            confidence=confidence,
                            action=recommended_action,
                            result="ESCALATED",
                            metadata_={"simulation_id": simulation_id, "label": SIMULATION_LABEL},
                        )
                        db.add(audit_event)
                        audit_count += 1

                        action_breakdown[recommended_action]["failed"] += 1
                        status_counts["ESCALATED"] = status_counts.get("ESCALATED", 0) + 1
                    else:
                        # More attempts allowed — reopen case
                        case.status = CaseStatus.OPEN.value
                        failed_recoveries += 1

                        recovery_action = RecoveryAction(
                            case_id=case.id,
                            action_type=recommended_action,
                            reason=f"Simulated recovery failed, attempt {case.attempt_count}",
                            confidence=confidence,
                            policy_result="APPROVED",
                            execution_status="FAILED",
                        )
                        db.add(recovery_action)
                        recovery_actions_count += 1

                        audit_event = AuditEvent(
                            case_id=case.id,
                            event_type=AuditEventType.ACTION_FAILED.value,
                            actor="recovery_service",
                            decision="FAILED",
                            reason=f"Simulated payment failed: {recovery_result.get('error', 'Unknown')}",
                            confidence=confidence,
                            action=recommended_action,
                            result="FAILED",
                            metadata_={"simulation_id": simulation_id, "label": SIMULATION_LABEL},
                        )
                        db.add(audit_event)
                        audit_count += 1

                        action_breakdown[recommended_action]["failed"] += 1
                        status_counts[case.status] = status_counts.get(case.status, 0) + 1

            # Check if case is recoverable (ML probability > 0.3)
            if recovery_prob > 0.3:
                recoverable_count += 1

            # Track processing time
            txn_ms = (time.time() - txn_start) * 1000
            total_processing_ms += txn_ms

            # Commit in batches of 100 for performance
            if (idx + 1) % 100 == 0:
                db.flush()
                logger.info(f"Simulation {simulation_id}: processed {idx + 1}/{num_transactions}")

        except Exception as e:
            logger.error(f"Simulation error at transaction {idx}: {e}")
            # Continue processing other transactions
            continue

    # Final flush
    db.commit()

    # Calculate final metrics
    end_time = time.time()
    completed_at = datetime.now(timezone.utc)
    duration = end_time - start_time
    avg_ms = total_processing_ms / max(num_transactions, 1)

    recovery_rate = (total_recovered / total_at_risk * 100) if total_at_risk > 0 else 0.0
    outstanding = total_at_risk - total_recovered

    # ML prediction statistics
    ml_stats = {
        "avg_recovery_probability": round(sum(ml_probs) / max(len(ml_probs), 1), 4),
        "min_recovery_probability": round(min(ml_probs), 4) if ml_probs else 0,
        "max_recovery_probability": round(max(ml_probs), 4) if ml_probs else 0,
        "high_prob_count": sum(1 for p in ml_probs if p >= 0.7),
        "medium_prob_count": sum(1 for p in ml_probs if 0.4 <= p < 0.7),
        "low_prob_count": sum(1 for p in ml_probs if p < 0.4),
    }

    result = {
        "simulation_id": simulation_id,
        "status": "COMPLETED",
        "label": SIMULATION_LABEL,
        "num_transactions_processed": num_transactions,
        "recoverable_cases": recoverable_count,
        "successful_recoveries": successful_recoveries,
        "failed_recoveries": failed_recoveries,
        "escalations": escalations,
        "policy_blocks": policy_blocks,
        "revenue_at_risk": round(total_at_risk, 2),
        "simulated_revenue_recovered": round(total_recovered, 2),
        "recovery_rate": round(recovery_rate, 1),
        "processing_duration_seconds": round(duration, 2),
        "avg_processing_time_ms": round(avg_ms, 2),
        "total_audit_events": audit_count,
        "total_policy_decisions": policy_decisions_count,
        "total_recovery_actions": recovery_actions_count,
        "metrics": {
            "revenue_at_risk": round(total_at_risk, 2),
            "revenue_recovered": round(total_recovered, 2),
            "recovery_rate": round(recovery_rate, 1),
            "total_recovery_attempts": recovery_attempts,
            "successful_recoveries": successful_recoveries,
            "failed_recoveries": failed_recoveries,
            "escalated_cases": escalations,
            "policy_blocked": policy_blocks,
            "average_recovery_time_seconds": round(duration / max(num_transactions, 1), 4),
            "outstanding_revenue": round(outstanding, 2),
        },
        "recovery_action_breakdown": action_breakdown,
        "status_breakdown": status_counts,
        "ml_prediction_stats": ml_stats,
        "started_at": started_at.isoformat(),
        "completed_at": completed_at.isoformat(),
    }

    logger.info(
        f"Simulation {simulation_id} completed: "
        f"{num_transactions} transactions, "
        f"recovery_rate={recovery_rate:.1f}%, "
        f"duration={duration:.2f}s"
    )

    return result


# ============================================================================
# HELPER FUNCTIONS
# ============================================================================

def _determine_action(
    recovery_prob: float,
    risk_score: float,
    failure_reason: str,
) -> str:
    """
    Deterministically determine the recommended action based on ML outputs.
    Mirrors the AI agent's decision logic without requiring an LLM call.
    """
    # Very low recovery probability — no action or escalate
    if recovery_prob < 0.2:
        if risk_score > 0.8:
            return "ESCALATE_TO_HUMAN"
        return "NO_ACTION"

    # Low recovery probability — wait and retry
    if recovery_prob < 0.4:
        return "WAIT_AND_RETRY"

    # Network or temporary failures — retry
    if failure_reason in ("network_timeout", "authentication_failed"):
        return "RETRY"

    # Merchant errors — create payment link
    if failure_reason == "merchant_error":
        return "CREATE_PAYMENT_LINK"

    # High recovery probability
    if recovery_prob >= 0.7:
        return "CREATE_PAYMENT_LINK"

    # Medium recovery probability — send reminder
    if recovery_prob >= 0.5:
        return "SEND_PAYMENT_REMINDER"

    # Default
    return "RETRY"


def _generate_diagnosis(
    failure_reason: str,
    recovery_prob: float,
    risk_score: float,
) -> str:
    """
    Generate a deterministic diagnosis based on failure characteristics.
    """
    reason_map = {
        "insufficient_funds": "Temporary insufficient funds. Customer has adequate transaction history suggesting this is a recoverable failure.",
        "card_expired": "Card expiration detected. Customer needs to update payment method before retry.",
        "bank_declined": "Bank declined the transaction. May be due to daily limits or temporary bank restriction.",
        "network_timeout": "Network timeout during payment processing. Likely transient issue, high recovery probability.",
        "authentication_failed": "3D Secure authentication failed. Customer may need to complete verification.",
        "daily_limit_exceeded": "Daily transaction limit exceeded. Amount will be recoverable after limit resets.",
        "card_blocked": "Card blocked by issuing bank. Requires customer to contact bank for unblocking.",
        "invalid_cvv": "Invalid CVV entered. Customer may have mistyped security code.",
        "merchant_error": "Merchant-side configuration error. Payment infrastructure issue, not customer-related.",
        "customer_cancelled": "Customer intentionally cancelled the transaction. Low recovery probability without re-engagement.",
    }

    base = reason_map.get(failure_reason, "Payment failure analyzed. Recovery probability assessed based on customer history and failure characteristics.")

    if recovery_prob >= 0.7:
        return f"{base} ML confidence: HIGH ({recovery_prob:.0%}). Recommended immediate recovery action."
    elif recovery_prob >= 0.4:
        return f"{base} ML confidence: MEDIUM ({recovery_prob:.0%}). Graduated recovery approach recommended."
    else:
        return f"{base} ML confidence: LOW ({recovery_prob:.0%}). Caution advised, consider escalation."
