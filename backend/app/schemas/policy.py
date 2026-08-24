"""
Pydantic schemas for Policy Engine.

The Policy Engine validates AI recommendations before any financial actions.
All output must be validated against these schemas.
"""
from pydantic import BaseModel, Field, field_validator
from typing import Optional, List
from datetime import datetime


class PolicyCheck(BaseModel):
    """Individual policy check result."""
    rule: str = Field(..., description="Name of the rule checked")
    passed: bool = Field(..., description="Whether the rule passed")
    reason: Optional[str] = Field(None, description="Reason if rule failed")
    value: Optional[float] = Field(None, description="Current value being checked")
    limit: Optional[float] = Field(None, description="Configured limit for this rule")


class PolicyDecisionResponse(BaseModel):
    """Response from the Policy Engine."""
    allowed: bool = Field(..., description="Whether the action is allowed")
    decision: str = Field(..., description="APPROVED, BLOCKED, or ESCALATED")
    reason: str = Field(..., description="Reason for the decision")
    checks: List[PolicyCheck] = Field(..., description="List of policy checks performed")
    case_id: str = Field(..., description="Case ID being checked")
    proposed_action: str = Field(..., description="Action that was proposed")


class PolicyConfigResponse(BaseModel):
    """Response for policy configuration."""
    id: int
    max_retries: int
    max_reminders: int
    max_recovery_attempts: int
    autonomous_amount_limit: float
    minimum_ai_confidence: float
    minimum_recovery_probability: float
    case_lifetime_days: int
    escalation_threshold: float
    is_active: bool
    description: Optional[str] = None
    created_at: datetime
    updated_at: datetime
    
    model_config = {"from_attributes": True}


class PolicyConfigUpdate(BaseModel):
    """Request to update policy configuration."""
    max_retries: Optional[int] = Field(None, ge=0, le=10, 
                                       description="Maximum retry attempts per case")
    max_reminders: Optional[int] = Field(None, ge=0, le=10,
                                         description="Maximum payment reminders per case")
    max_recovery_attempts: Optional[int] = Field(None, ge=0, le=20,
                                                 description="Maximum total recovery attempts per case")
    autonomous_amount_limit: Optional[float] = Field(None, ge=0, 
                                                     description="Maximum amount for autonomous actions (INR)")
    minimum_ai_confidence: Optional[float] = Field(None, ge=0.0, le=1.0,
                                                   description="Minimum AI confidence required for action")
    minimum_recovery_probability: Optional[float] = Field(None, ge=0.0, le=1.0,
                                                          description="Minimum recovery probability required")
    case_lifetime_days: Optional[int] = Field(None, ge=1, le=90,
                                              description="Maximum case lifetime in days")
    escalation_threshold: Optional[float] = Field(None, ge=0.0, le=1.0,
                                                  description="Confidence threshold above which escalation is triggered")
    is_active: Optional[bool] = Field(None, description="Whether this policy configuration is active")
    description: Optional[str] = Field(None, description="Description of this policy configuration")
    
    @field_validator("autonomous_amount_limit")
    @classmethod
    def validate_amount_limit(cls, v):
        """Prevent obviously unsafe configurations."""
        if v is not None and v > 100000:
            raise ValueError("Autonomous amount limit cannot exceed 100,000 INR")
        return v
    
    @field_validator("minimum_ai_confidence")
    @classmethod
    def validate_confidence(cls, v):
        """Prevent obviously unsafe configurations."""
        if v is not None and v < 0.1:
            raise ValueError("Minimum AI confidence cannot be below 0.1")
        return v
    
    @field_validator("minimum_recovery_probability")
    @classmethod
    def validate_recovery_probability(cls, v):
        """Prevent obviously unsafe configurations."""
        if v is not None and v < 0.05:
            raise ValueError("Minimum recovery probability cannot be below 0.05")
        return v


class PolicyDecisionCreate(BaseModel):
    """Schema for creating a policy decision record."""
    case_id: int
    proposed_action: str
    decision: str
    reason: Optional[str] = None
    checks: Optional[List[dict]] = None
    confidence: Optional[float] = None
    recovery_probability: Optional[float] = None
    amount: Optional[float] = None
    final_decision: str
