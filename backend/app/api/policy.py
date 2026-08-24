"""
Policy Engine API Endpoints.

Provides endpoints for:
- Getting policy configuration
- Updating policy configuration
- Evaluating actions against policy rules
"""
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app.db.session import get_db
from app.schemas.policy import (
    PolicyConfigResponse,
    PolicyConfigUpdate,
    PolicyDecisionResponse,
)
from app.services.policy_engine import get_active_policy, evaluate_action
from app.utils.logging import logger

router = APIRouter(tags=["policy"])


@router.get("/policies", response_model=PolicyConfigResponse)
def get_policy_config(db: Session = Depends(get_db)):
    """
    Get the current active policy configuration.
    
    Returns the policy settings that control the Policy Engine.
    """
    policy = get_active_policy(db)
    return policy


@router.put("/policies", response_model=PolicyConfigResponse)
def update_policy_config(
    update: PolicyConfigUpdate,
    db: Session = Depends(get_db),
):
    """
    Update the policy configuration.
    
    Validates all configuration values to prevent unsafe settings.
    Only updates fields that are provided in the request.
    """
    policy = get_active_policy(db)
    
    # Update only provided fields
    update_data = update.model_dump(exclude_unset=True)
    
    if not update_data:
        raise HTTPException(status_code=400, detail="No fields to update")
    
    # Apply updates
    for field, value in update_data.items():
        setattr(policy, field, value)
    
    db.commit()
    db.refresh(policy)
    
    logger.info(f"Policy configuration updated: {update_data}")
    
    return policy


@router.post("/policies/evaluate", response_model=PolicyDecisionResponse)
def evaluate_action_endpoint(
    case_id: str,
    proposed_action: str,
    confidence: float,
    recovery_probability: float,
    db: Session = Depends(get_db),
):
    """
    Evaluate a proposed action against policy rules.
    
    This endpoint is used by the agent service to check
    whether an action is allowed before execution.
    """
    # Validate inputs
    if confidence < 0 or confidence > 1:
        raise HTTPException(status_code=400, detail="Confidence must be between 0 and 1")
    
    if recovery_probability < 0 or recovery_probability > 1:
        raise HTTPException(status_code=400, detail="Recovery probability must be between 0 and 1")
    
    result = evaluate_action(
        db=db,
        case_id=case_id,
        proposed_action=proposed_action,
        confidence=confidence,
        recovery_probability=recovery_probability,
    )
    
    return result
