"""
Value-Aware Recovery Scoring (Phase 2).

This module estimates the *value* of recovering a failed payment:

    expected gross recovery = payment amount x probability of verified recovery
    expected net recovery   = expected gross recovery - intervention cost
    utility score           = expected net recovery - configured penalties

Everything produced here is an ESTIMATE.  It ranks eligible candidate actions
for human/agent consideration and never authorizes or executes anything: the
Policy Engine remains the sole authorization authority and the recovery service
remains the only executor (see ``assess_case_value`` -> ``authorization``).

Important limitations, deliberately represented as data rather than assumed:

1. ``case.recovery_probability`` is a CASE-LEVEL model output, not an
   action-specific one.  The RandomForest v1 is trained to predict whether a
   failed payment ends up recovered (target ``recovered``) from features known
   at failure time; it has no notion of "probability of recovery *given* a
   specific action".  The same probability is therefore applied to every
   candidate action, which is why the model improves the *level* of the
   estimate and cross-case comparison but does NOT discriminate between actions
   for a single case.  Ranking differences within a case come from intervention
   cost and eligibility, never from an invented per-action probability.
   Measured quality of that probability lives in
   ``ml/saved_models/evaluation_summary_v1.json``; it is reported here so the
   weakness is visible (ROC-AUC 0.668, precision 0.328, recall 0.186).

2. No intervention costs are measured anywhere in this system.  A cost is used
   only when a deployment explicitly configures one (``INTERVENTION_COSTS``) or
   a caller passes one.  When no cost is available the expected net recovery is
   reported as ``None`` ("not computed") instead of pretending the cost is zero.

3. Penalties are never folded into the net-recovery formula.  They are itemized
   separately and only affect the ``utility_score``, which is explicitly not a
   monetary figure.
"""
from __future__ import annotations

import math
from typing import Any, Dict, List, Mapping, Optional, Sequence, Tuple

from app.models import CaseStatus, RevenueRiskCase

# The policy engine is addressed through its module (not a copied name) so the
# exact rules used for the eligibility pre-check are always the ones in
# app.services.policy_engine, and so tests can observe the call.
from app.services import policy_engine
from app.utils.logging import logger

# ============================================================================
# CONSTANTS
# ============================================================================

#: Label attached to every monetary figure produced by this module.  Estimates
#: must never be presented as booked revenue.
ESTIMATE_LABEL = "ESTIMATE"

#: Human-readable formula, returned with every assessment so the UI and API
#: consumers display the same arithmetic that was executed.
FORMULA = {
    "expected_gross_recovery": "payment_amount * probability_of_verified_recovery",
    "expected_net_recovery": "expected_gross_recovery - intervention_cost",
    "utility_score": "expected_net_recovery - configured_penalties",
}

#: Actions this system can actually execute (see recovery_service).  Only these
#: are ranked as recovery candidates.  NO_ACTION / ESCALATE_TO_HUMAN /
#: MARK_UNRECOVERABLE generate no recovery revenue, and WAIT_AND_RETRY has no
#: executor (``execute_recovery_action`` rejects it), so none of them are ranked.
EXECUTABLE_ACTIONS: Tuple[str, ...] = (
    "CREATE_PAYMENT_LINK",
    "RETRY",
    "SEND_PAYMENT_REMINDER",
)

#: Case statuses that can no longer receive a recovery recommendation.
TERMINAL_STATUSES: Tuple[str, ...] = (
    CaseStatus.RECOVERED.value,
    CaseStatus.CLOSED.value,
)

PROBABILITY_SOURCE_CASE_LEVEL = "case_level_ml_model"
PROBABILITY_SOURCE_CALLER = "caller_supplied"
PROBABILITY_SOURCE_UNAVAILABLE = "unavailable"

COST_SOURCE_NOT_CONFIGURED = "not_configured"
COST_SOURCE_CONFIGURED = "configured_template"
COST_SOURCE_CALLER = "caller_supplied"

#: Correctness of the ranking when no per-action probability exists.
RANKING_BASIS_NET = "expected_net_recovery"
RANKING_BASIS_GROSS = "expected_gross_recovery (intervention cost not configured)"

#: Measured quality of the case-level probability, taken from the committed
#: evaluation summary.  Reported so callers can judge the estimate.
MODEL_QUALITY = {
    "source": "ml/saved_models/evaluation_summary_v1.json",
    "evaluation": "held-out synthetic evaluation split (1200 records)",
    "roc_auc": 0.6684,
    "precision": 0.3276,
    "recall": 0.1863,
    "base_recovery_rate": 0.1703,
}


# ============================================================================
# VALIDATION
# ============================================================================

def _as_number(value: Any, field: str) -> float:
    """Return ``value`` as a finite float, rejecting bools and junk."""
    if value is None:
        raise ValueError(f"{field} is required")
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f"{field} must be a number, got {type(value).__name__}")
    number = float(value)
    if math.isnan(number) or math.isinf(number):
        raise ValueError(f"{field} must be a finite number")
    return number


def validate_amount(amount: Any) -> float:
    """Validate a payment amount: a finite number that is not negative."""
    number = _as_number(amount, "amount")
    if number < 0:
        raise ValueError(f"amount must not be negative, got {number}")
    return number


def validate_probability(probability: Any) -> float:
    """Validate a recovery probability: a finite number within [0, 1]."""
    number = _as_number(probability, "probability")
    if number < 0 or number > 1:
        raise ValueError(f"probability must be within [0, 1], got {number}")
    return number


def validate_cost(cost: Any, action: str = "") -> Optional[float]:
    """
    Validate an intervention cost.

    ``None`` means "not available" and stays ``None`` (it is never coerced to
    zero).  A provided cost must be a finite, non-negative number.
    """
    if cost is None:
        return None
    number = _as_number(cost, f"intervention cost for {action or 'action'}")
    if number < 0:
        raise ValueError(
            f"intervention cost for {action or 'action'} must not be negative, got {number}"
        )
    return number


def _round_money(value: Optional[float]) -> Optional[float]:
    return None if value is None else round(value, 2)


# ============================================================================
# CORE CALCULATION
# ============================================================================

def expected_gross_recovery(amount: Any, probability: Any) -> float:
    """
    Expected gross recovery = payment amount x probability of verified recovery.

    Both inputs are validated; the result is rounded to 2 decimals (INR).
    """
    return round(validate_amount(amount) * validate_probability(probability), 2)


def expected_net_recovery(gross: Any, cost: Any) -> Optional[float]:
    """
    Expected net recovery = expected gross recovery - intervention cost.

    Returns ``None`` when the cost is unavailable, because a missing cost is
    unknown, not zero.
    """
    gross_value = _as_number(gross, "expected gross recovery")
    cost_value = validate_cost(cost)
    if cost_value is None:
        return None
    return round(gross_value - cost_value, 2)


def resolve_intervention_cost(
    action: str,
    costs: Optional[Mapping[str, float]] = None,
) -> Tuple[Optional[float], str]:
    """
    Look up the intervention cost for an action.

    Returns ``(cost, source)`` where the cost is ``None`` when nothing is
    configured for that action, and source is one of the COST_SOURCE_* values.
    """
    if costs and action in costs and costs[action] is not None:
        return validate_cost(costs[action], action), COST_SOURCE_CONFIGURED
    return None, COST_SOURCE_NOT_CONFIGURED


def resolve_penalty(action: str, penalties: Optional[Mapping[str, float]]) -> float:
    """Return the configured operational penalty for an action (default 0.0)."""
    if not penalties or penalties.get(action) is None:
        return 0.0
    value = validate_cost(penalties[action], action)
    return 0.0 if value is None else value


def calculate_utility_score(
    net_recovery: Optional[float],
    penalties: float,
) -> Optional[float]:
    """
    Utility score = expected net recovery - configured penalties.

    Penalties are subtracted *here only*; they never enter the net-recovery
    formula.  ``None`` when net recovery could not be computed.
    """
    penalty_value = validate_cost(penalties, "penalties") or 0.0
    if net_recovery is None:
        return None
    return round(_as_number(net_recovery, "expected net recovery") - penalty_value, 2)


def _probability_for_action(
    action: str,
    case_probability: Optional[float],
    action_probabilities: Optional[Mapping[str, float]],
) -> Tuple[Optional[float], str]:
    """Return ``(probability, source)`` for a candidate action."""
    if action_probabilities and action_probabilities.get(action) is not None:
        return validate_probability(action_probabilities[action]), PROBABILITY_SOURCE_CALLER
    if case_probability is None:
        return None, PROBABILITY_SOURCE_UNAVAILABLE
    return validate_probability(case_probability), PROBABILITY_SOURCE_CASE_LEVEL


# ============================================================================
# CANDIDATES, ELIGIBILITY, RANKING
# ============================================================================

def _eligibility(
    db,
    case: RevenueRiskCase,
    action: str,
) -> Tuple[bool, str, Optional[str]]:
    """
    Decide whether a candidate action may appear in the ranking.

    Returns ``(eligible, reason, policy_decision)``.  The policy rules are
    consulted through the real Policy Engine with ``persist=False``: no policy
    decision or audit row is written by a value assessment, and an eligible
    result here still has to be authorized by the Policy Engine at execution
    time.
    """
    if case.status in TERMINAL_STATUSES:
        return (
            False,
            f"case is {case.status} (terminal): no further recovery action is recommended",
            None,
        )

    if action not in EXECUTABLE_ACTIONS:
        return False, f"{action} is not an executable recovery action", None

    # Non-authorizing policy pre-filter.  Confidence/probability are passed as
    # the case-level values already stored on the case; this mirrors what the
    # recovery service would evaluate, without recording a decision.
    decision = policy_engine.evaluate_action(
        db=db,
        case_id=case.case_id,
        proposed_action=action,
        confidence=case.recovery_probability if case.recovery_probability is not None else 0.0,
        recovery_probability=case.recovery_probability if case.recovery_probability is not None else 0.0,
        persist=False,
    )

    if decision.decision == "APPROVED":
        return True, "eligible (policy pre-check passed)", decision.decision
    if decision.decision == "ESCALATED":
        return (
            False,
            f"requires human review (policy ESCALATED for this candidate: {decision.reason})",
            decision.decision,
        )
    return False, f"blocked by policy: {decision.reason}", decision.decision


def _rank_key(candidate: Dict[str, Any], index: int) -> Tuple[float, int]:
    """
    Deterministic ranking key: higher value first, then declared action order.

    The value used is expected net recovery when it is available, otherwise
    expected gross recovery (reported via ``ranking_basis``).  Ties fall back to
    the canonical order in ``EXECUTABLE_ACTIONS`` so ranking is reproducible.
    """
    value = candidate.get("expected_net_recovery")
    if value is None:
        value = candidate.get("expected_gross_recovery")
    if value is None:
        value = 0.0
    return (-value, index)


def assess_case_value(
    db,
    case: RevenueRiskCase,
    *,
    costs: Optional[Mapping[str, float]] = None,
    penalties: Optional[Mapping[str, float]] = None,
    action_probabilities: Optional[Mapping[str, float]] = None,
) -> Dict[str, Any]:
    """
    Build a deterministic, fully inspectable value assessment for one case.

    Args:
        db: Database session (used only for read-only policy pre-checks).
        case: The revenue risk case to assess.
        costs: Optional ``{action: cost_inr}``.  When omitted, the configured
            template (``settings.INTERVENTION_COSTS``) applies; if that is empty
            too, costs are "not_configured" and net recovery is not computed.
        penalties: Optional ``{action: penalty_inr}`` applied only to the
            utility score.  Falls back to ``settings.UTILITY_PENALTIES``.
        action_probabilities: Optional ``{action: probability}``.  Only pass
            these when they are genuinely action-specific; otherwise the
            case-level model probability is used and flagged as such.

    Returns:
        A dict containing the formula, inputs and their provenance, the gross /
        cost / net / utility figures (separately inspectable), the ranked
        eligible candidates, the excluded candidates with reasons, and the
        explicit statement that the Policy Engine remains the authorizer.
    """
    from app.core.config import get_settings

    settings = get_settings()
    if costs is None:
        costs = settings.INTERVENTION_COSTS or None
    if penalties is None:
        penalties = settings.UTILITY_PENALTIES or None

    amount = validate_amount(case.amount)
    case_probability = (
        validate_probability(case.recovery_probability)
        if case.recovery_probability is not None
        else None
    )

    probability_note = (
        "case-level model output applied to every candidate action; the model is "
        "not action-specific, so it does not discriminate between actions for "
        "this case"
        if action_probabilities is None
        else "per-action probabilities supplied by the caller"
    )

    candidates: List[Dict[str, Any]] = []
    excluded: List[Dict[str, Any]] = []

    for index, action in enumerate(EXECUTABLE_ACTIONS):
        eligible, reason, policy_decision = _eligibility(db, case, action)
        probability, probability_source = _probability_for_action(
            action, case_probability, action_probabilities
        )

        gross = expected_gross_recovery(amount, probability) if probability is not None else None
        cost, cost_source = (
            resolve_intervention_cost(action, costs) if eligible else (None, COST_SOURCE_NOT_CONFIGURED)
        )
        net = expected_net_recovery(gross, cost) if gross is not None else None
        penalty = resolve_penalty(action, penalties)
        utility = calculate_utility_score(net, penalty) if eligible else None

        entry: Dict[str, Any] = {
            "action": action,
            "eligible": eligible,
            "eligibility_reason": reason,
            "policy_precheck_decision": policy_decision,
            "probability": probability,
            "probability_source": probability_source,
            "expected_gross_recovery": gross,
            "intervention_cost": cost,
            "intervention_cost_source": cost_source,
            "expected_net_recovery": net,
            "penalty": penalty,
            "utility_score": utility,
            "rank": None,
        }

        if eligible:
            candidates.append(entry)
        else:
            if gross is not None:
                # Keep the arithmetic visible for transparency, but the action is
                # not rankable and never presented as a recommendation.
                entry["expected_net_recovery"] = None
                entry["utility_score"] = None
            excluded.append(entry)

    ranked = sorted(
        enumerate(candidates),
        key=lambda pair: _rank_key(pair[1], pair[0]),
    )
    ranked_candidates: List[Dict[str, Any]] = []
    for position, (_, candidate) in enumerate(ranked, start=1):
        candidate["rank"] = position
        ranked_candidates.append(candidate)

    top = ranked_candidates[0] if ranked_candidates else None

    # Top-level figures are the case-level estimate; cost, net and utility refer
    # to the recommended candidate action so the displayed arithmetic is the
    # same arithmetic that produced the recommendation.
    total_penalties = round(resolve_penalty(top["action"], penalties), 2) if top else 0.0
    case_cost, case_cost_source = (
        resolve_intervention_cost(top["action"], costs) if top else (None, COST_SOURCE_NOT_CONFIGURED)
    )
    gross = (
        expected_gross_recovery(amount, case_probability)
        if case_probability is not None
        else None
    )
    net = expected_net_recovery(gross, case_cost) if gross is not None else None
    utility = calculate_utility_score(net, total_penalties)

    assessment: Dict[str, Any] = {
        "case_id": case.case_id,
        "estimate_label": ESTIMATE_LABEL,
        "formula": FORMULA,
        "inputs": {
            "payment_amount": amount,
            "payment_amount_source": "case.amount (trusted database value)",
            "recovery_probability": case_probability,
            "recovery_probability_source": (
                PROBABILITY_SOURCE_CASE_LEVEL if case_probability is not None
                else PROBABILITY_SOURCE_UNAVAILABLE
            ),
            "recovery_probability_is_action_specific": action_probabilities is not None,
            "recovery_probability_note": probability_note,
            "model": {
                "version": getattr(case, "model_version", None) or "v1",
                "target": "recovered (case-level)",
                "quality": MODEL_QUALITY,
            },
            "costs_configured_for": sorted(costs.keys()) if costs else [],
            "penalties_configured_for": sorted(penalties.keys()) if penalties else [],
        },
        "expected_gross_recovery": gross,
        "intervention_cost": case_cost,
        "intervention_cost_source": case_cost_source,
        "expected_net_recovery": net,
        "figures_scope": (
            f"case-level estimate; cost, net and utility refer to the recommended "
            f"candidate action ({top['action']})"
            if top else
            "case-level estimate; no candidate action is eligible, so cost/net/utility "
            "are not computed"
        ),
        "penalties": {
            "total_penalties": total_penalties,
            "applied_to": "utility_score only (never to expected_net_recovery)",
        },
        "utility_score": utility,
        "candidates": ranked_candidates,
        "excluded_candidates": excluded,
        "recommended_action": top["action"] if top else None,
        "ranking_basis": (
            RANKING_BASIS_NET if top and top["expected_net_recovery"] is not None
            else RANKING_BASIS_GROSS
        ),
        "ranking_note": (
            "ranking is advisory and deterministic: candidates are ordered by "
            "expected net recovery (or expected gross recovery when no cost is "
            "configured), ties broken by the declared action order"
        ),
        "assumptions": [
            "the payment amount comes from the trusted database record (case.amount)",
            "recovery_probability is the case-level ML estimate; it is not "
            "action-specific and is applied unchanged to every eligible candidate",
            "intervention costs are only used when explicitly configured; no "
            "cost is assumed",
            "penalties affect only the utility score and are itemized separately",
            "a value assessment is not an AI recommendation, so the case's ML "
            "recovery probability is passed to the policy pre-check as both the "
            "confidence and recovery-probability input; this can only exclude "
            "candidates (never approve one) and the failing rule is reported",
        ],
        "limitations": [
            "no action-specific recovery probabilities exist in this system, so "
            "the model cannot rank actions by likelihood - only by value given "
            "the case-level estimate and configured costs",
            "no measured intervention costs exist in this system; when none are "
            "configured the expected net recovery is reported as not computed",
            "the model's measured discrimination is weak (ROC-AUC 0.668, "
            "precision 0.328, recall 0.186 on the synthetic evaluation split)",
            "all figures are estimates, not booked revenue; actual recovered "
            "revenue is case.recovered_amount and is only set by a verified "
            "payment webhook",
        ],
        "authorization": {
            "required": True,
            "authority": "policy_engine",
            "note": (
                "value scoring ranks eligible candidates only; it never "
                "authorizes or executes an action. The Policy Engine is "
                "consulted again (and recorded) when an action is executed."
            ),
        },
    }

    if case_probability is None:
        logger.warning(
            f"Value assessment for case {case.case_id} has no recovery probability; "
            f"gross/net values are not computed"
        )

    return assessment


def assess_case_value_by_id(db, case_id: str) -> Optional[Dict[str, Any]]:
    """Assess a case by id; returns None when the case does not exist."""
    case = db.query(RevenueRiskCase).filter(RevenueRiskCase.case_id == case_id).first()
    if not case:
        return None
    return assess_case_value(db, case)
