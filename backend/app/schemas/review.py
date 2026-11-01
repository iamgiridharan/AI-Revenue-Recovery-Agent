"""
Pydantic schemas for the human review flow.

An ESCALATED case is one the Policy Engine refused to authorize on its own. Only a
named human may resolve it, and the review request can only say *who* decided,
*what* they decided, and *which* executable action they authorized.  It can never
carry the amount (always the failed transaction record) or the confidence /
recovery probability (always the recorded diagnosis): a reviewer who could supply
those would be rewriting the evidence the policy checks are based on.
"""
import enum
from typing import Optional

from pydantic import BaseModel, Field, field_validator, model_validator

from app.schemas.recovery import EXECUTABLE_ACTIONS


class ReviewDecision(str, enum.Enum):
    """What a human reviewer decided about a case."""

    # Execute the recorded AI recommendation on the reviewer's authority.
    APPROVE = "APPROVE"
    # Execute a different executable action than the recorded recommendation.
    OVERRIDE = "OVERRIDE"
    # Settle the case outside the recovery flow.
    CLOSE = "CLOSE"
    # Close the case as uncollectable.
    MARK_UNRECOVERABLE = "MARK_UNRECOVERABLE"


class CaseReviewRequest(BaseModel):
    """A reviewer's decision on an escalated case."""

    reviewer: str = Field(
        ...,
        max_length=100,
        description=("Name of the human reviewer. Recorded as the audit actor for "
                     "the decision, so the review trail names a person."),
    )
    decision: ReviewDecision = Field(
        ...,
        description="APPROVE, OVERRIDE, CLOSE or MARK_UNRECOVERABLE",
    )
    action_type: Optional[str] = Field(
        None,
        description=("Executable action to authorize. Defaults to the recorded AI "
                     "recommendation for APPROVE; required for OVERRIDE. One of: "
                     "RETRY, CREATE_PAYMENT_LINK, SEND_PAYMENT_REMINDER"),
    )
    note: str = Field(
        "",
        max_length=1000,
        description=("Reviewer's note, recorded with the decision. Required for "
                     "OVERRIDE and MARK_UNRECOVERABLE."),
    )

    @field_validator("reviewer")
    @classmethod
    def validate_reviewer(cls, v):
        cleaned = (v or "").strip()
        if len(cleaned) < 2:
            raise ValueError(
                "A reviewer name of at least 2 characters is required: every review "
                "decision is attributed to a named person"
            )
        return cleaned

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

    @field_validator("note")
    @classmethod
    def strip_note(cls, v):
        return (v or "").strip()

    @model_validator(mode="after")
    def validate_decision_requirements(self):
        """Reject decisions that would be unauditable without a reason."""
        if self.decision == ReviewDecision.OVERRIDE:
            if not self.action_type:
                raise ValueError(
                    "OVERRIDE requires action_type: name the action you are "
                    "authorizing instead of the recorded recommendation"
                )
            if len(self.note) < 5:
                raise ValueError(
                    "OVERRIDE requires a note of at least 5 characters explaining why "
                    "the recommendation is being overridden"
                )
        if self.decision == ReviewDecision.MARK_UNRECOVERABLE and len(self.note) < 5:
            raise ValueError(
                "MARK_UNRECOVERABLE requires a note of at least 5 characters giving "
                "the reason"
            )
        return self
