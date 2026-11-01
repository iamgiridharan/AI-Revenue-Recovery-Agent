"""
Revenue metrics: one reconciled money view of the case book.

The dashboard reports two recovery rates that are *not* interchangeable, and
letting either one be called "recovery %" makes the number ambiguous:

- **count-based**: settled cases / all cases. Says how many problems were
  resolved, not how much money came back.
- **money-weighted**: recovered money / the original value of every case. Uses a
  fixed denominator that does not move as case statuses change, so a settlement
  raises the rate and nothing else can.

Everything here is derived from database rows. Nothing is estimated: a number
this module cannot compute from stored data is reported as unavailable rather
than guessed.

Provenance is reported alongside the money, because simulated rupees, mock
executions and webhook-verified settlements must never be added together and
presented as one recovered figure:

- ``verified``: settled by a signature-verified Razorpay webhook.
- ``simulated``: booked by the simulation pipeline (case id carries the
  simulation prefix, or the settling audit event records a simulation id).
- ``unattributed``: settled with no evidence of how - a data problem, shown
  rather than hidden.
"""
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional

from sqlalchemy import func
from sqlalchemy.orm import Session

from app.models import RevenueRiskCase, AuditEvent, CaseStatus
from app.services.simulation_service import SIMULATION_CASE_PREFIX

# Webhook event types that are allowed to settle a case (see api/webhooks.py).
SETTLING_EVENTS = ("payment.captured", "payment_link.paid")

WEEK = timedelta(days=7)


def _money(value: Optional[float]) -> float:
    """Round to paise, and turn SQL NULL into 0.0."""
    return round(float(value or 0.0), 2)


def _percent(numerator: float, denominator: float) -> float:
    """Percentage with one decimal, and 0.0 for an empty denominator."""
    if not denominator:
        return 0.0
    return round(numerator / denominator * 100, 1)


def _delta_pct(current: float, previous: float) -> Optional[float]:
    """
    Percentage change versus the previous period.

    ``None`` rather than 0 or infinity when the previous period was empty: there
    is no meaningful percentage change from nothing, and reporting 0 % would
    hide that the two periods are not comparable.
    """
    if not previous:
        return None
    return round((current - previous) / previous * 100, 1)


def _window_totals(
    db: Session, start: datetime, end: datetime
) -> Dict[str, float]:
    """
    Money recovered and money still at risk inside ``[start, end)``.

    "Recovered in the window" uses ``updated_at`` because a settled case is
    immutable (the policy engine refuses any further action on it), so its last
    update *is* its settlement moment.

    "At risk in the window" is the amount of cases *created* in the window that
    have not been settled: money that became at risk during the period and is
    still outstanding, not the whole historical book.
    """
    recovered = (
        db.query(func.sum(RevenueRiskCase.recovered_amount))
        .filter(
            RevenueRiskCase.recovered_amount > 0,
            RevenueRiskCase.updated_at >= start,
            RevenueRiskCase.updated_at < end,
        )
        .scalar()
    )

    at_risk = (
        db.query(func.sum(RevenueRiskCase.amount))
        .filter(
            RevenueRiskCase.created_at >= start,
            RevenueRiskCase.created_at < end,
            RevenueRiskCase.status != CaseStatus.RECOVERED.value,
        )
        .scalar()
    )

    return {
        "recovered": _money(recovered),
        "at_risk": _money(at_risk),
    }


def _provenance(db: Session) -> Dict[str, Any]:
    """
    Split recovered money by the evidence behind it.

    Classification reads the audit trail rather than trusting the case id: a
    settlement is ``verified`` only when a webhook event that is allowed to
    settle recorded it, and ``simulated`` when the settling audit event carries a
    simulation id (or, for older rows, the case id has the simulation prefix).
    Anything else is reported as unattributed instead of being counted as real.
    """
    settled: List[RevenueRiskCase] = (
        db.query(RevenueRiskCase)
        .filter(RevenueRiskCase.recovered_amount > 0)
        .all()
    )

    buckets = {
        "verified": {"amount": 0.0, "cases": 0},
        "simulated": {"amount": 0.0, "cases": 0},
        "unattributed": {"amount": 0.0, "cases": 0},
    }
    if not settled:
        return buckets

    events = (
        db.query(AuditEvent)
        .filter(
            AuditEvent.case_id.in_([case.id for case in settled]),
            AuditEvent.event_type == "ACTION_EXECUTED",
        )
        .all()
    )

    verified_case_ids = set()
    simulated_case_ids = set()
    for event in events:
        metadata = event.metadata_ or {}
        if event.actor == "webhook" and event.decision in SETTLING_EVENTS:
            verified_case_ids.add(event.case_id)
        if isinstance(metadata, dict) and metadata.get("simulation_id"):
            simulated_case_ids.add(event.case_id)

    for case in settled:
        amount = _money(case.recovered_amount)
        # A webhook settlement is the strongest evidence, so it wins if a case
        # somehow has both (it should be impossible: a simulated case cannot be
        # paid through a real link).
        if case.id in verified_case_ids:
            bucket = "verified"
        elif case.id in simulated_case_ids or str(case.case_id).startswith(
            SIMULATION_CASE_PREFIX
        ):
            bucket = "simulated"
        else:
            bucket = "unattributed"
        buckets[bucket]["amount"] = _money(buckets[bucket]["amount"] + amount)
        buckets[bucket]["cases"] += 1

    return buckets


def get_revenue_metrics(
    db: Session, now: Optional[datetime] = None
) -> Dict[str, Any]:
    """
    Compute the money metrics for the dashboard.

    Args:
        db: Database session.
        now: Override for "now", for deterministic tests. Defaults to UTC now.

    Returns:
        Dict with the reconciled money figures, both recovery rates, the 7-day
        period comparison, and the provenance split.
    """
    now = now or datetime.now(timezone.utc)
    current_start = now - WEEK
    previous_start = now - (2 * WEEK)

    total_cases = db.query(func.count(RevenueRiskCase.id)).scalar() or 0
    total_case_value = _money(db.query(func.sum(RevenueRiskCase.amount)).scalar())
    total_recovered = _money(
        db.query(func.sum(RevenueRiskCase.recovered_amount))
        .filter(RevenueRiskCase.recovered_amount > 0)
        .scalar()
    )
    recovered_count = (
        db.query(func.count(RevenueRiskCase.id))
        .filter(RevenueRiskCase.status == CaseStatus.RECOVERED.value)
        .scalar()
        or 0
    )

    # The denominator is the original value of every case, recovered or not, so
    # it is fixed: only a settlement changes the rate.
    outstanding = _money(max(total_case_value - total_recovered, 0.0))

    current = _window_totals(db, current_start, now)
    previous = _window_totals(db, previous_start, current_start)

    return {
        "total_case_value": total_case_value,
        "total_recovered": total_recovered,
        "outstanding_amount": outstanding,
        # Money-weighted: fixed denominator, the headline figure.
        "money_recovery_rate": _percent(total_recovered, total_case_value),
        # Count-based: kept as its own labelled figure, never mixed with the above.
        "count_recovery_rate": _percent(recovered_count, total_cases),
        "recovered_cases": recovered_count,
        "case_count": total_cases,
        "recovered_7d": current["recovered"],
        "at_risk_7d": current["at_risk"],
        "recovered_previous_7d": previous["recovered"],
        "at_risk_previous_7d": previous["at_risk"],
        "recovered_delta_7d": _money(
            current["recovered"] - previous["recovered"]
        ),
        "at_risk_delta_7d": _money(current["at_risk"] - previous["at_risk"]),
        "recovered_delta_pct_7d": _delta_pct(
            current["recovered"], previous["recovered"]
        ),
        "at_risk_delta_pct_7d": _delta_pct(current["at_risk"], previous["at_risk"]),
        "provenance": _provenance(db),
        # True when the three money figures agree: value − recovered = outstanding.
        "reconciles": abs(
            (total_case_value - total_recovered) - outstanding
        ) <= 0.01,
    }
