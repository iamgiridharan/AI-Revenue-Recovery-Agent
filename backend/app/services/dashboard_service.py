"""
Dashboard Service for Revenue Recovery Agent.

Provides aggregate statistics for the merchant dashboard.
All data comes from the database — no hardcoded business data.
"""
from sqlalchemy.orm import Session
from sqlalchemy import func, case, extract
from datetime import datetime, timezone, timedelta
from typing import Dict, Any, List

from app.models import (
    RevenueRiskCase,
    Transaction,
    Customer,
    RecoveryAction,
    AuditEvent,
    CaseStatus,
)
from app.utils.logging import logger


def get_dashboard_stats(db: Session) -> Dict[str, Any]:
    """
    Get aggregate dashboard statistics.

    Returns comprehensive metrics for the overview dashboard.
    """
    # Total cases
    total_cases = db.query(func.count(RevenueRiskCase.id)).scalar() or 0

    # Revenue metrics
    total_at_risk = db.query(
        func.sum(RevenueRiskCase.amount)
    ).filter(
        RevenueRiskCase.status != CaseStatus.RECOVERED.value
    ).scalar() or 0.0

    total_recovered = db.query(
        func.sum(RevenueRiskCase.recovered_amount)
    ).filter(
        RevenueRiskCase.recovered_amount > 0
    ).scalar() or 0.0

    # Status breakdown
    status_counts = dict(
        db.query(RevenueRiskCase.status, func.count(RevenueRiskCase.id))
        .group_by(RevenueRiskCase.status)
        .all()
    )

    # Priority breakdown
    priority_counts = dict(
        db.query(RevenueRiskCase.priority, func.count(RevenueRiskCase.id))
        .group_by(RevenueRiskCase.priority)
        .all()
    )

    # Recovery action stats
    total_actions = db.query(func.count(RecoveryAction.id)).scalar() or 0
    successful_actions = db.query(func.count(RecoveryAction.id)).filter(
        RecoveryAction.execution_status == "SUCCESS"
    ).scalar() or 0
    failed_actions = db.query(func.count(RecoveryAction.id)).filter(
        RecoveryAction.execution_status == "FAILED"
    ).scalar() or 0
    blocked_actions = db.query(func.count(RecoveryAction.id)).filter(
        RecoveryAction.execution_status == "BLOCKED_BY_POLICY"
    ).scalar() or 0

    # Recovery rate
    recovered_count = status_counts.get(CaseStatus.RECOVERED.value, 0)
    recovery_rate = (recovered_count / total_cases * 100) if total_cases > 0 else 0.0

    # Escalated cases
    escalated_count = status_counts.get(CaseStatus.ESCALATED.value, 0)

    # Cases awaiting action (OPEN or IN_PROGRESS)
    awaiting_action = (
        status_counts.get(CaseStatus.OPEN.value, 0) +
        status_counts.get(CaseStatus.IN_PROGRESS.value, 0)
    )

    # Average recovery probability across all cases
    avg_recovery_prob = db.query(
        func.avg(RevenueRiskCase.recovery_probability)
    ).filter(
        RevenueRiskCase.recovery_probability.isnot(None)
    ).scalar() or 0.0

    # Average risk score
    avg_risk_score = db.query(
        func.avg(RevenueRiskCase.risk_score)
    ).filter(
        RevenueRiskCase.risk_score.isnot(None)
    ).scalar() or 0.0

    # Total customers affected
    total_customers = db.query(
        func.count(func.distinct(RevenueRiskCase.customer_id))
    ).scalar() or 0

    # Recent activity (last 7 days)
    seven_days_ago = datetime.now(timezone.utc) - timedelta(days=7)
    recent_cases = db.query(func.count(RevenueRiskCase.id)).filter(
        RevenueRiskCase.created_at >= seven_days_ago
    ).scalar() or 0

    return {
        "total_cases": total_cases,
        "total_at_risk": round(total_at_risk, 2),
        "total_recovered": round(total_recovered, 2),
        "recovery_rate": round(recovery_rate, 1),
        "total_actions": total_actions,
        "successful_actions": successful_actions,
        "failed_actions": failed_actions,
        "blocked_actions": blocked_actions,
        "escalated_count": escalated_count,
        "awaiting_action": awaiting_action,
        "recovered_count": recovered_count,
        "avg_recovery_probability": round(avg_recovery_prob, 3),
        "avg_risk_score": round(avg_risk_score, 3),
        "total_customers": total_customers,
        "recent_cases_7d": recent_cases,
        "status_breakdown": status_counts,
        "priority_breakdown": priority_counts,
    }


def get_recovery_by_status(db: Session) -> List[Dict[str, Any]]:
    """Get recovery counts grouped by case status."""
    results = (
        db.query(RevenueRiskCase.status, func.count(RevenueRiskCase.id))
        .group_by(RevenueRiskCase.status)
        .all()
    )
    return [{"status": status, "count": count} for status, count in results]


def get_recovery_by_priority(db: Session) -> List[Dict[str, Any]]:
    """Get recovery counts grouped by priority."""
    results = (
        db.query(RevenueRiskCase.priority, func.count(RevenueRiskCase.id))
        .group_by(RevenueRiskCase.priority)
        .all()
    )
    return [{"priority": priority, "count": count} for priority, count in results]


def get_recovery_by_action(db: Session) -> List[Dict[str, Any]]:
    """Get recovery outcomes grouped by action type."""
    results = (
        db.query(
            RecoveryAction.action_type,
            RecoveryAction.execution_status,
            func.count(RecoveryAction.id),
        )
        .group_by(RecoveryAction.action_type, RecoveryAction.execution_status)
        .all()
    )
    return [
        {"action_type": action, "status": status, "count": count}
        for action, status, count in results
    ]


def get_daily_cases(db: Session, days: int = 30) -> List[Dict[str, Any]]:
    """Get daily case creation counts for the last N days."""
    start_date = datetime.now(timezone.utc) - timedelta(days=days)
    results = (
        db.query(
            func.date(RevenueRiskCase.created_at).label("date"),
            func.count(RevenueRiskCase.id).label("count"),
        )
        .filter(RevenueRiskCase.created_at >= start_date)
        .group_by(func.date(RevenueRiskCase.created_at))
        .order_by(func.date(RevenueRiskCase.created_at))
        .all()
    )
    return [{"date": str(row.date), "count": row.count} for row in results]


def get_daily_recovered(db: Session, days: int = 30) -> List[Dict[str, Any]]:
    """Get daily recovered revenue for the last N days."""
    start_date = datetime.now(timezone.utc) - timedelta(days=days)
    results = (
        db.query(
            func.date(RevenueRiskCase.updated_at).label("date"),
            func.sum(RevenueRiskCase.recovered_amount).label("amount"),
        )
        .filter(
            RevenueRiskCase.recovered_amount > 0,
            RevenueRiskCase.updated_at >= start_date,
        )
        .group_by(func.date(RevenueRiskCase.updated_at))
        .order_by(func.date(RevenueRiskCase.updated_at))
        .all()
    )
    return [{"date": str(row.date), "amount": round(row.amount or 0, 2)} for row in results]


def get_case_detail_with_relations(db: Session, case_id: str) -> Dict[str, Any]:
    """
    Get a case with all related entities for the detail view.

    Returns case info, customer, transaction, recovery actions, audit events.
    """
    case = db.query(RevenueRiskCase).filter(RevenueRiskCase.case_id == case_id).first()
    if not case:
        return None

    # Get customer
    customer = db.query(Customer).filter(Customer.id == case.customer_id).first()
    customer_data = None
    if customer:
        customer_data = {
            "id": customer.id,
            "customer_id": customer.customer_id,
            "name": customer.name,
            "email": customer.email,
            "phone": customer.phone,
            "total_transactions": customer.total_transactions,
            "successful_transactions": customer.successful_transactions,
            "failed_transactions": customer.failed_transactions,
            "lifetime_value": customer.lifetime_value,
        }

    # Get transaction
    transaction = db.query(Transaction).filter(Transaction.id == case.transaction_id).first()
    transaction_data = None
    if transaction:
        transaction_data = {
            "id": transaction.id,
            "transaction_id": transaction.transaction_id,
            "amount": transaction.amount,
            "currency": transaction.currency,
            "payment_method": transaction.payment_method,
            "status": transaction.status,
            "failure_reason": transaction.failure_reason,
            "attempt_count": transaction.attempt_count,
            "created_at": transaction.created_at.isoformat() if transaction.created_at else None,
        }

    # Get recovery actions
    recovery_actions = (
        db.query(RecoveryAction)
        .filter(RecoveryAction.case_id == case.id)
        .order_by(RecoveryAction.created_at.desc())
        .all()
    )
    actions_data = [
        {
            "id": a.id,
            "action_type": a.action_type,
            "reason": a.reason,
            "confidence": a.confidence,
            "policy_result": a.policy_result,
            "execution_status": a.execution_status,
            "api_reference": a.api_reference,
            "created_at": a.created_at.isoformat() if a.created_at else None,
        }
        for a in recovery_actions
    ]

    # Get audit events
    audit_events = (
        db.query(AuditEvent)
        .filter(AuditEvent.case_id == case.id)
        .order_by(AuditEvent.timestamp.desc())
        .all()
    )
    audit_data = [
        {
            "id": a.id,
            "timestamp": a.timestamp.isoformat() if a.timestamp else None,
            "event_type": a.event_type,
            "actor": a.actor,
            "decision": a.decision,
            "reason": a.reason,
            "confidence": a.confidence,
            "action": a.action,
            "result": a.result,
            "metadata": a.metadata_,
        }
        for a in audit_events
    ]

    return {
        "case": {
            "id": case.id,
            "case_id": case.case_id,
            "amount": case.amount,
            "risk_score": case.risk_score,
            "recovery_probability": case.recovery_probability,
            "priority": case.priority,
            "diagnosis": case.diagnosis,
            "recommended_action": case.recommended_action,
            "status": case.status,
            "attempt_count": case.attempt_count,
            "recovered_amount": case.recovered_amount,
            "created_at": case.created_at.isoformat() if case.created_at else None,
            "updated_at": case.updated_at.isoformat() if case.updated_at else None,
        },
        "customer": customer_data,
        "transaction": transaction_data,
        "recovery_actions": actions_data,
        "audit_events": audit_data,
    }


def get_audit_events(
    db: Session,
    page: int = 1,
    page_size: int = 50,
    case_id: str = None,
    event_type: str = None,
    actor: str = None,
) -> tuple[List[Dict[str, Any]], int]:
    """
    Get paginated audit events with optional filters.

    Returns:
        Tuple of (list of audit event dicts, total count)
    """
    query = db.query(AuditEvent)

    if case_id:
        # Join with RevenueRiskCase to filter by case_id string
        query = query.join(RevenueRiskCase).filter(
            RevenueRiskCase.case_id == case_id
        )

    if event_type:
        query = query.filter(AuditEvent.event_type == event_type)

    if actor:
        query = query.filter(AuditEvent.actor == actor)

    total = query.count()

    offset = (page - 1) * page_size
    events = (
        query
        .order_by(AuditEvent.timestamp.desc())
        .offset(offset)
        .limit(page_size)
        .all()
    )

    events_data = []
    for e in events:
        # Get case_id string from the related case
        case_obj = db.query(RevenueRiskCase).filter(RevenueRiskCase.id == e.case_id).first()
        events_data.append({
            "id": e.id,
            "case_id": case_obj.case_id if case_obj else str(e.case_id),
            "timestamp": e.timestamp.isoformat() if e.timestamp else None,
            "event_type": e.event_type,
            "actor": e.actor,
            "decision": e.decision,
            "reason": e.reason,
            "confidence": e.confidence,
            "action": e.action,
            "result": e.result,
            "metadata": e.metadata_,
        })

    return events_data, total
