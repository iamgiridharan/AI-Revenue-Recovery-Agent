"""Policy Engine API Endpoints.

Provides endpoints for:
- Getting policy configuration
- Updating policy configuration (atomic; every successful change is durably
  audited in one transaction with actor = demo_api_key)
- Evaluating actions against policy rules
"""

from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app.core.auth import AUTH_ACTOR, require_auth
from app.db.session import get_db
from app.models import AuditEvent, AuditEventType
from app.schemas.policy import PolicyConfigUpdate
from app.services.policy_engine import get_active_policy, evaluate_action
from app.utils.logging import logger

router = APIRouter(tags=["policy"])


@router.get("/policies")
def get_policy_config(db: Session = Depends(get_db)):
    """Get the current active policy configuration."""
    policy = get_active_policy(db)
    return policy


@router.put("/policies")
def update_policy_config(
    update: PolicyConfigUpdate,
    db: Session = Depends(get_db),
    _auth: str = Depends(require_auth),
):
    """Update the policy configuration.

    The update and its audit event commit in a single database transaction: if
    either write fails, both roll back, so a failed change can never leave a
    misleading success record behind.  The audit event records the authenticated
    actor (``demo_api_key``), the previous values, the new values and the
    timestamp -- never the API key itself.

    Returns:
        The merged policy configuration.
    """
    policy = get_active_policy(db)

    # Read the previous values before touching anything.
    previous = {
        "max_retries": policy.max_retries,
        "max_reminders": policy.max_reminders,
        "max_recovery_attempts": policy.max_recovery_attempts,
        "autonomous_amount_limit": policy.autonomous_amount_limit,
        "minimum_ai_confidence": policy.minimum_ai_confidence,
        "minimum_recovery_probability": policy.minimum_recovery_probability,
        "case_lifetime_days": policy.case_lifetime_days,
        "is_active": policy.is_active,
        "description": policy.description,
    }

    update_data = update.model_dump(exclude_unset=True)
    if not update_data:
        raise HTTPException(status_code=400, detail="No fields to update")

    new_values = {**previous, **update_data}
    changed_at = datetime.now(timezone.utc)

    try:
        for field, value in update_data.items():
            setattr(policy, field, value)

        db.add(
            AuditEvent(
                case_id=None,  # policy configuration is not attached to a case
                timestamp=changed_at,
                event_type=AuditEventType.POLICY_CONFIG_CHANGED.value,
                actor=AUTH_ACTOR,
                decision="UPDATED",
                reason="Policy configuration updated by authenticated operator",
                action="POLICY_CONFIG",
                result="SUCCESS",
                policy_checks=[
                    {"rule": field, "previous": previous[field], "new": update_data[field]}
                    for field in update_data
                ],
                metadata_={
                    "previous_values": previous,
                    "new_values": new_values,
                    "changed_fields": sorted(update_data),
                    "changed_by": AUTH_ACTOR,
                    "changed_at": changed_at.isoformat(),
                },
            )
        )

        # One commit for both writes: the configuration and its audit event are
        # durable together or not at all.
        db.commit()
    except Exception:
        db.rollback()
        # Never leak the database error text (it can contain row values).
        logger.error("Policy configuration update failed; rolled back", exc_info=True)
        raise HTTPException(
            status_code=500, detail="Policy configuration update failed"
        ) from None

    db.refresh(policy)

    # Log the *fields* that changed, never the key value.
    logger.info(
        f"Policy configuration updated by {AUTH_ACTOR}: {sorted(update_data)}"
    )

    return policy


@router.post("/policies/evaluate")
def evaluate_action_endpoint(
    case_id: str,
    proposed_action: str,
    confidence: float,
    recovery_probability: float,
    db: Session = Depends(get_db),
    _auth: str = Depends(require_auth),
):
    """Evaluate a proposed action against policy rules.

    This endpoint is used by the agent service to check whether an action is
    allowed before execution.
    """
    if confidence < 0 or confidence > 1:
        raise HTTPException(status_code=400, detail="Confidence must be between 0 and 1")

    if recovery_probability < 0 or recovery_probability > 1:
        raise HTTPException(
            status_code=400, detail="Recovery probability must be between 0 and 1"
        )

    return evaluate_action(
        db=db,
        case_id=case_id,
        proposed_action=proposed_action,
        confidence=confidence,
        recovery_probability=recovery_probability,
    )
