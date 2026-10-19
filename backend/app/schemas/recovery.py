"""
Pydantic schemas for controlled recovery execution.

The caller chooses *which* recommended action to execute; it can never choose the
amount (always the database transaction amount) or the confidence / recovery
probability (always the values recorded when the case was diagnosed).  Trusting a
caller-supplied confidence would let anyone step over MINIMUM_AI_CONFIDENCE.
"""
from pydantic import BaseModel, Field, field_validator
from typing import Optional

from app.schemas.agent import RecoveryAction


# Actions the recovery service can actually execute.  The remaining enum values
# (NO_ACTION, WAIT_AND_RETRY, ESCALATE_TO_HUMAN, MARK_UNRECOVERABLE) are decisions
# rather than executable recovery attempts.
EXECUTABLE_ACTIONS = {
    RecoveryAction.RETRY.value,
    RecoveryAction.CREATE_PAYMENT_LINK.value,
    RecoveryAction.SEND_PAYMENT_REMINDER.value,
}


class RecoveryExecutionRequest(BaseModel):
    """Request to execute a recovery action for a case."""

    action_type: Optional[str] = Field(
        None,
        description=("Action to execute. Defaults to the case's recorded AI "
                     "recommendation. One of: RETRY, CREATE_PAYMENT_LINK, "
                     "SEND_PAYMENT_REMINDER"),
    )
    reason: str = Field("", max_length=1000,
                        description="Operator-supplied note recorded with the action")

    @field_validator("action_type")
    @classmethod
    def validate_action_type(cls, v):
        if v is None:
            return v
        if v not in EXECUTABLE_ACTIONS:
            raise ValueError(
                f"Action must be one of: {', '.join(sorted(EXECUTABLE_ACTIONS))}"
            )
        return v
