"""
Pydantic schemas for AI Agent structured output validation.

The agent produces structured recommendations for revenue recovery.
All output must be validated before any action is taken.
"""
from pydantic import BaseModel, Field, field_validator
from typing import Optional
from enum import Enum


class RecoveryAction(str, Enum):
    """Controlled recovery actions the agent may select."""
    NO_ACTION = "NO_ACTION"
    RETRY = "RETRY"
    CREATE_PAYMENT_LINK = "CREATE_PAYMENT_LINK"
    SEND_PAYMENT_REMINDER = "SEND_PAYMENT_REMINDER"
    WAIT_AND_RETRY = "WAIT_AND_RETRY"
    ESCALATE_TO_HUMAN = "ESCALATE_TO_HUMAN"
    MARK_UNRECOVERABLE = "MARK_UNRECOVERABLE"


class AgentDiagnosisRequest(BaseModel):
    """Request to diagnose a revenue risk case."""
    case_id: str = Field(..., description="Revenue risk case ID")


class AgentRecommendation(BaseModel):
    """
    Structured output from the AI agent.
    
    This is the validated output schema. The LLM must produce
    output matching this structure exactly.
    """
    diagnosis: str = Field(
        ...,
        min_length=10,
        max_length=500,
        description="Diagnosis of the payment failure"
    )
    reasoning_summary: str = Field(
        ...,
        min_length=20,
        max_length=1000,
        description="Summary of reasoning behind the recommendation"
    )
    recovery_probability: float = Field(
        ...,
        ge=0.0,
        le=1.0,
        description="Estimated probability of successful recovery (0-1)"
    )
    recommended_action: RecoveryAction = Field(
        ...,
        description="Recommended recovery action from controlled list"
    )
    confidence: float = Field(
        ...,
        ge=0.0,
        le=1.0,
        description="Agent confidence in its recommendation (0-1)"
    )
    customer_message: str = Field(
        ...,
        min_length=10,
        max_length=500,
        description="Message to be shown to the customer"
    )
    additional_information_required: bool = Field(
        default=False,
        description="Whether additional information is needed before proceeding"
    )

    @field_validator("diagnosis")
    @classmethod
    def diagnosis_not_empty(cls, v: str) -> str:
        """Ensure diagnosis is not just whitespace."""
        if not v.strip():
            raise ValueError("Diagnosis cannot be empty")
        return v.strip()

    @field_validator("reasoning_summary")
    @classmethod
    def reasoning_not_empty(cls, v: str) -> str:
        """Ensure reasoning is not just whitespace."""
        if not v.strip():
            raise ValueError("Reasoning summary cannot be empty")
        return v.strip()

    @field_validator("customer_message")
    @classmethod
    def customer_message_not_empty(cls, v: str) -> str:
        """Ensure customer message is not just whitespace."""
        if not v.strip():
            raise ValueError("Customer message cannot be empty")
        return v.strip()


class AgentDiagnosisResponse(BaseModel):
    """Response from the agent diagnosis endpoint."""
    success: bool = True
    data: AgentRecommendation
    case_id: str
    model_used: str
    processing_time_ms: Optional[float] = None


class AgentErrorResponse(BaseModel):
    """Error response from the agent."""
    success: bool = False
    error: dict
    case_id: str
    fallback_action: Optional[str] = None
