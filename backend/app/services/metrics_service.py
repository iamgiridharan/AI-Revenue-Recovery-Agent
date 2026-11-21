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

- ``verified``: settled by a signature-verified Razorpay webhook.  THIS is the
  only bucket the headline figures use.
- ``simulated``: booked by the simulation pipeline (case id carries the
  simulation prefix, or the settling audit event records a simulation id).
- ``unattributed``: settled with no evidence of how - a data problem, shown
  rather than hidden.  Counted in ``*_all_sources`` totals only, never as
  verified revenue.

Three money categories are therefore kept apart:

- ``verified_recovered_amount``: money a provider confirmed (headline).
- ``simulated_recovered_amount``: money the simulation booked.  Never added to
  the headline, and the historical simulation rows are left untouched.
- ``predicted_recovery_value``: an EXPECTED value for open cases
  (``case.amount x case.recovery_probability``).  It is labelled an estimate and
  is not revenue of any kind.

The older ``total_recovered`` key now means verified money only; the all-sources
figure is ``total_recovered_all_sources`` so no consumer can silently keep
reporting simulated rupees as recovered revenue.
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

#: Definitions of every money figure, returned with the metrics so the API (and
#: therefore the UI) can state what each number means and where it came from.
METRIC_DEFINITIONS = {
    "verified_recovered_amount": (
        "money confirmed by a signature-verified Razorpay webhook; the only bucket "
        "used by headline recovery figures"
    ),
    "simulated_recovered_amount": (
        "money booked by the simulation pipeline (SIM-CASE-* cases or a settling "
        "event carrying a simulation id); never added to verified revenue"
    ),
    "unattributed_recovered_amount": (
        "money booked as recovered with no audit evidence of how; shown, never "
        "counted as verified"
    ),
    "total_recovered_all_sources": (
        "verified + simulated + unattributed; reconciles with outstanding_amount"
    ),
    "money_recovery_rate": (
        "verified money / value of every case (fixed denominator), in percent"
    ),
    "money_recovery_rate_all_sources": (
        "all booked money / value of every case, in percent; not the headline"
    ),
    "predicted_recovery_value": (
        "ESTIMATE, not revenue: sum(case.amount * case.recovery_probability) over "
        "unsettled cases with a stored ML probability"
    ),
    "count_recovery_rate": "settled cases / all cases, in percent",
}


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
    db: Session, start: datetime, end: datetime, bucket_by_case: Dict[int, str]
) -> Dict[str, float]:
    """
    Money recovered - split by provenance - and money still at risk inside
    ``[start, end)``.

    "Recovered in the window" uses ``updated_at`` because a settled case is
    immutable (the policy engine refuses any further action on it), so its last
    update *is* its settlement moment.  Each settlement is attributed with the
    same classification as the all-time figures, so a simulated settlement can
    never appear in the verified window figure.

    "At risk in the window" is the amount of cases *created* in the window that
    have not been settled: money that became at risk during the period and is
    still outstanding, not the whole historical book.
    """
    settled_in_window: List[RevenueRiskCase] = (
        db.query(RevenueRiskCase)
        .filter(
            RevenueRiskCase.recovered_amount > 0,
            RevenueRiskCase.updated_at >= start,
            RevenueRiskCase.updated_at < end,
        )
        .all()
    )

    totals = {"verified": 0.0, "simulated": 0.0, "unattributed": 0.0}
    for case in settled_in_window:
        bucket = bucket_by_case.get(case.id, "unattributed")
        totals[bucket] = _money(totals[bucket] + _money(case.recovered_amount))

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
        "verified": totals["verified"],
        "simulated": totals["simulated"],
        "unattributed": totals["unattributed"],
        "at_risk": _money(at_risk),
    }


def recovered_case_provenance(db: Session) -> Dict[int, str]:
    """
    Map every settled case (``recovered_amount > 0``) to its provenance bucket.

    Classification reads the audit trail rather than trusting the case id: a
    settlement is ``verified`` only when a webhook event that is allowed to
    settle recorded it, and ``simulated`` when the settling audit event carries a
    simulation id (or, for older rows, the case id has the simulation prefix).
    Anything else is ``unattributed`` instead of being counted as real.

    A webhook settlement is the strongest evidence, so it wins if a case somehow
    has both (it should be impossible: a simulated case cannot be paid through a
    real link).

    Returns:
        ``{case.id: "verified" | "simulated" | "unattributed"}``
    """
    settled: List[RevenueRiskCase] = (
        db.query(RevenueRiskCase)
        .filter(RevenueRiskCase.recovered_amount > 0)
        .all()
    )
    if not settled:
        return {}

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

    buckets: Dict[int, str] = {}
    for case in settled:
        if case.id in verified_case_ids:
            buckets[case.id] = "verified"
        elif case.id in simulated_case_ids or str(case.case_id).startswith(
            SIMULATION_CASE_PREFIX
        ):
            buckets[case.id] = "simulated"
        else:
            buckets[case.id] = "unattributed"
    return buckets


def _provenance(db: Session, bucket_by_case: Dict[int, str]) -> Dict[str, Any]:
    """Split recovered money by the evidence behind it (see the module docstring)."""
    buckets = {
        "verified": {"amount": 0.0, "cases": 0},
        "simulated": {"amount": 0.0, "cases": 0},
        "unattributed": {"amount": 0.0, "cases": 0},
    }
    if not bucket_by_case:
        return buckets

    settled: List[RevenueRiskCase] = (
        db.query(RevenueRiskCase)
        .filter(RevenueRiskCase.recovered_amount > 0)
        .all()
    )
    for case in settled:
        bucket = bucket_by_case.get(case.id, "unattributed")
        amount = _money(case.recovered_amount)
        buckets[bucket]["amount"] = _money(buckets[bucket]["amount"] + amount)
        buckets[bucket]["cases"] += 1

    return buckets


def _predicted_recovery_value(db: Session) -> Dict[str, Any]:
    """
    Expected (not booked) value of the cases that are still open.

    ``case.amount x case.recovery_probability``, summed over unsettled cases that
    have a stored ML probability.  Cases without a usable probability are
    excluded and counted separately rather than assumed to be 50/50; a
    probability outside [0, 1] is a data problem and is excluded too.
    """
    rows: List[RevenueRiskCase] = (
        db.query(RevenueRiskCase)
        .filter(
            RevenueRiskCase.status.notin_(
                (CaseStatus.RECOVERED.value, CaseStatus.CLOSED.value)
            )
        )
        .all()
    )

    total = 0.0
    included = 0
    excluded_without_probability = 0
    for case in rows:
        probability = case.recovery_probability
        if (
            isinstance(probability, bool)
            or not isinstance(probability, (int, float))
            or not 0.0 <= float(probability) <= 1.0
        ):
            excluded_without_probability += 1
            continue
        total += float(case.amount or 0.0) * float(probability)
        included += 1

    return {
        "predicted_recovery_value": _money(total),
        "predicted_recovery_value_cases": included,
        "predicted_recovery_value_excluded_cases": excluded_without_probability,
        "predicted_recovery_value_source": (
            "estimate: sum(case.amount * case.recovery_probability) over unsettled "
            "cases with a stored ML probability"
        ),
    }


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
        period comparison, the provenance split, and the separated
        verified / simulated / predicted figures.
    """
    now = now or datetime.now(timezone.utc)
    current_start = now - WEEK
    previous_start = now - (2 * WEEK)

    total_cases = db.query(func.count(RevenueRiskCase.id)).scalar() or 0
    total_case_value = _money(db.query(func.sum(RevenueRiskCase.amount)).scalar())
    recovered_count = (
        db.query(func.count(RevenueRiskCase.id))
        .filter(RevenueRiskCase.status == CaseStatus.RECOVERED.value)
        .scalar()
        or 0
    )

    bucket_by_case = recovered_case_provenance(db)
    provenance = _provenance(db, bucket_by_case)

    verified_recovered = provenance["verified"]["amount"]
    simulated_recovered = provenance["simulated"]["amount"]
    unattributed_recovered = provenance["unattributed"]["amount"]
    all_sources = _money(
        verified_recovered + simulated_recovered + unattributed_recovered
    )

    # The denominator is the original value of every case, recovered or not, so
    # it is fixed: only a settlement changes the rate.
    outstanding = _money(max(total_case_value - all_sources, 0.0))

    current = _window_totals(db, current_start, now, bucket_by_case)
    previous = _window_totals(db, previous_start, current_start, bucket_by_case)

    predicted = _predicted_recovery_value(db)

    return {
        "total_case_value": total_case_value,
        # HEADLINE: provider-verified money only.  Simulated rupees are reported
        # separately below and never enter this figure or its rate.
        "total_recovered": verified_recovered,
        "verified_recovered_amount": verified_recovered,
        "verified_recovered_cases": provenance["verified"]["cases"],
        "simulated_recovered_amount": simulated_recovered,
        "simulated_recovered_cases": provenance["simulated"]["cases"],
        "unattributed_recovered_amount": unattributed_recovered,
        "unattributed_recovered_cases": provenance["unattributed"]["cases"],
        # Kept for reconciliation and for anyone who explicitly wants every
        # booked rupee regardless of provenance.
        "total_recovered_all_sources": all_sources,
        "outstanding_amount": outstanding,
        # Money-weighted: fixed denominator, verified money only (the headline).
        "money_recovery_rate": _percent(verified_recovered, total_case_value),
        # The same rate over every booked rupee, explicitly labelled so it cannot
        # be mistaken for the verified figure.
        "money_recovery_rate_all_sources": _percent(all_sources, total_case_value),
        # Count-based: kept as its own labelled figure, never mixed with the above.
        "count_recovery_rate": _percent(recovered_count, total_cases),
        "recovered_cases": recovered_count,
        "case_count": total_cases,
        "recovered_7d": current["verified"],
        "simulated_recovered_7d": current["simulated"],
        "unattributed_recovered_7d": current["unattributed"],
        "recovered_7d_all_sources": _money(
            current["verified"] + current["simulated"] + current["unattributed"]
        ),
        "at_risk_7d": current["at_risk"],
        "recovered_previous_7d": previous["verified"],
        "simulated_recovered_previous_7d": previous["simulated"],
        "at_risk_previous_7d": previous["at_risk"],
        "recovered_delta_7d": _money(
            current["verified"] - previous["verified"]
        ),
        "at_risk_delta_7d": _money(current["at_risk"] - previous["at_risk"]),
        "recovered_delta_pct_7d": _delta_pct(
            current["verified"], previous["verified"]
        ),
        "at_risk_delta_pct_7d": _delta_pct(current["at_risk"], previous["at_risk"]),
        **predicted,
        "provenance": provenance,
        "metric_definitions": METRIC_DEFINITIONS,
        # True when the three money figures agree: value − all booked recovered =
        # outstanding.  (Verified + simulated + unattributed = all sources.)
        "reconciles": abs(
            (total_case_value - all_sources) - outstanding
        ) <= 0.01,
    }
