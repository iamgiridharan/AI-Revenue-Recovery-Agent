"""
Which recovery actions this deployment can actually carry out.

The system has seven agent action *types*, but only some of them are usable in a
given deployment.  This module is the single source of truth for that question,
so the value ranking (which recommends), the Policy Engine (which authorizes)
and the recovery executor (which performs) cannot drift apart: they all ask the
same function.

Three states are distinguished, because "not recommended" hides three very
different situations:

``EXECUTABLE``
    The action has working integration in this deployment, so it may be
    recommended, authorized and executed.

``SIMULATED_ONLY``
    The action exists and is supported, but the integration it needs is missing,
    so only the simulation pipeline can carry it out.  Recommending it as an
    executable recovery action would be a promise the system cannot keep.

``UNAVAILABLE``
    The action type has no executor at all (it is a decision, not a recovery
    action), or it is not a known action type.

Reminders are the concrete case behind this module: ``SEND_PAYMENT_REMINDER`` is
implemented end to end, but no email/SMS provider is integrated, so nothing can
be delivered.  The reminder functionality is therefore kept (a future channel
flips the state to ``EXECUTABLE`` without touching any caller) while it is kept
out of executable recommendations.

This module deliberately imports nothing from the application: it is a pure
description of capability, so both the database-facing engine and the API layer
can import it without a cycle.
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional, Tuple

# ============================================================================
# STATES
# ============================================================================

EXECUTABLE = "EXECUTABLE"
SIMULATED_ONLY = "SIMULATED_ONLY"
UNAVAILABLE = "UNAVAILABLE"

# ============================================================================
# ACTION TYPES
# ============================================================================

CREATE_PAYMENT_LINK = "CREATE_PAYMENT_LINK"
RETRY = "RETRY"
SEND_PAYMENT_REMINDER = "SEND_PAYMENT_REMINDER"
WAIT_AND_RETRY = "WAIT_AND_RETRY"
NO_ACTION = "NO_ACTION"
ESCALATE_TO_HUMAN = "ESCALATE_TO_HUMAN"
MARK_UNRECOVERABLE = "MARK_UNRECOVERABLE"

#: Canonical order of the action types the recovery service has (or will have) an
#: executor for.  Ranking ties fall back to this order.
EXECUTOR_ACTIONS: Tuple[str, ...] = (
    CREATE_PAYMENT_LINK,
    RETRY,
    SEND_PAYMENT_REMINDER,
)

#: Actions that are decisions rather than attempts to recover money.  They have
#: no executor, so they are never executable or rankable.
DECISION_ONLY_ACTIONS: Tuple[str, ...] = (
    NO_ACTION,
    WAIT_AND_RETRY,
    ESCALATE_TO_HUMAN,
    MARK_UNRECOVERABLE,
)

KNOWN_ACTIONS: Tuple[str, ...] = EXECUTOR_ACTIONS + DECISION_ONLY_ACTIONS

#: Prefix of every case id the simulation pipeline creates.  Kept here (rather
#: than in simulation_service) because several modules -- including the Policy
#: Engine -- have to recognise simulation data without importing the simulator.
SIMULATED_CASE_PREFIX = "SIM-CASE-"

#: The integration a reminder needs before it can be delivered.
REMINDER_DELIVERY_INTEGRATION = "reminder_delivery_provider (email/SMS)"
#: The integration payment links and retries use.
PAYMENT_PROVIDER_INTEGRATION = "razorpay_payments"


# ============================================================================
# CAPABILITY DETECTION
# ============================================================================

def reminder_delivery_channel() -> Optional[str]:
    """
    Return the configured outbound reminder channel, or ``None``.

    No email/SMS provider is integrated in this build, so no channel is
    configured and nothing can be delivered.  This function is the single place
    a channel gets registered: implement delivery in
    ``recovery_service._execute_send_reminder``, return the channel's name here,
    and every caller (value ranking, Policy Engine, recovery executor, agent
    tools, API, UI) flips the reminder to ``EXECUTABLE`` at once.
    """
    return None


def reminder_delivery_available() -> bool:
    """Whether reminders can actually be delivered in this deployment."""
    return reminder_delivery_channel() is not None


def is_simulated_case(case_id: Any) -> bool:
    """Whether a case id belongs to simulation data."""
    return str(case_id or "").startswith(SIMULATED_CASE_PREFIX)


# ============================================================================
# THE CATALOG
# ============================================================================

def action_availability(action_type: str, *, simulated_case: bool = False) -> Dict[str, Any]:
    """
    Describe whether ``action_type`` can be carried out here, and why not.

    Args:
        action_type: One of the agent's action types.
        simulated_case: Whether the case is simulation data.  A simulated case is
            the one context where a ``SIMULATED_ONLY`` action can still be
            carried out (the simulation pipeline records it as simulated).

    Returns:
        A dict with ``action``, ``availability`` (EXECUTABLE / SIMULATED_ONLY /
        UNAVAILABLE), ``executable`` (the single boolean recommendations and
        authorizations should branch on), ``simulated_only``,
        ``available_in_simulation``, ``reason`` and ``required_integration``.
    """
    action = (action_type or "").strip()
    simulated = bool(simulated_case)

    def entry(
        availability: str,
        reason: str,
        required_integration: Optional[str],
        *,
        executable: bool,
        simulated_only: bool = False,
        available_in_simulation: bool = False,
    ) -> Dict[str, Any]:
        return {
            "action": action,
            "action_type": action,
            "availability": availability,
            "executable": executable,
            "simulated_only": simulated_only,
            "available_in_simulation": available_in_simulation,
            "reason": reason,
            "required_integration": required_integration,
            "simulated_case": simulated,
        }

    if action in (CREATE_PAYMENT_LINK, RETRY):
        return entry(
            EXECUTABLE,
            "executable: the recovery service has an executor for this action and "
            "the payment provider integration is present",
            PAYMENT_PROVIDER_INTEGRATION,
            executable=True,
        )

    if action == SEND_PAYMENT_REMINDER:
        if reminder_delivery_available():
            channel = reminder_delivery_channel()
            return entry(
                EXECUTABLE,
                f"executable: reminder delivery channel '{channel}' is configured",
                REMINDER_DELIVERY_INTEGRATION,
                executable=True,
            )
        detail = (
            "it can only be recorded as simulation data (SIMULATED)"
            if simulated
            else "a real case would be recorded as NOT_SENT, because nothing leaves the system"
        )
        return entry(
            SIMULATED_ONLY,
            "no reminder delivery channel is configured (no email/SMS provider "
            f"integration), so a reminder cannot be delivered; {detail}",
            REMINDER_DELIVERY_INTEGRATION,
            executable=False,
            simulated_only=True,
            available_in_simulation=True,
        )

    if action in DECISION_ONLY_ACTIONS:
        return entry(
            UNAVAILABLE,
            f"{action} is a decision, not a recovery action, so it has no executor",
            None,
            executable=False,
        )

    return entry(
        UNAVAILABLE,
        f"'{action or '(empty)'}' is not a known recovery action type",
        None,
        executable=False,
    )


def availability_catalog(*, simulated_case: bool = False) -> List[Dict[str, Any]]:
    """Describe every known action type, in canonical order."""
    return [action_availability(action, simulated_case=simulated_case) for action in KNOWN_ACTIONS]


def is_executable(action_type: str, *, simulated_case: bool = False) -> bool:
    """Whether ``action_type`` may be recommended, authorized and executed here."""
    return action_availability(action_type, simulated_case=simulated_case)["executable"]


def executable_actions(*, simulated_case: bool = False) -> Tuple[str, ...]:
    """
    The executable recovery actions, in canonical ranking order.

    ``CREATE_PAYMENT_LINK`` and ``RETRY`` today; the reminder joins them
    automatically once a delivery channel is configured.
    """
    return tuple(
        action for action in EXECUTOR_ACTIONS
        if is_executable(action, simulated_case=simulated_case)
    )


def unavailable_actions() -> Tuple[str, ...]:
    """Action types that cannot be executed in this deployment at all."""
    return tuple(
        action for action in EXECUTOR_ACTIONS
        if action_availability(action)["availability"] == UNAVAILABLE
    )


def simulated_only_actions() -> Tuple[str, ...]:
    """Supported action types whose only available path here is simulation."""
    return tuple(
        action for action in EXECUTOR_ACTIONS
        if action_availability(action)["availability"] == SIMULATED_ONLY
    )
