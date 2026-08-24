from pydantic import BaseModel, Field
from datetime import datetime
from typing import Optional


class CaseResponse(BaseModel):
    """Schema for a single revenue risk case response."""
    id: int
    case_id: str
    transaction_id: int
    customer_id: int
    amount: float
    risk_score: Optional[float] = None
    recovery_probability: Optional[float] = None
    priority: str
    diagnosis: Optional[str] = None
    recommended_action: Optional[str] = None
    status: str
    attempt_count: int
    recovered_amount: float
    created_at: datetime
    updated_at: datetime

    model_config = {"from_attributes": True}


class CaseListResponse(BaseModel):
    """Schema for paginated list of revenue risk cases."""
    success: bool = True
    data: list[CaseResponse]
    pagination: dict


class CaseDetailResponse(BaseModel):
    """Schema for detailed case response including related entities."""
    success: bool = True
    data: CaseResponse


class CaseListQuery(BaseModel):
    """Query parameters for listing cases."""
    page: int = Field(1, ge=1, description="Page number")
    page_size: int = Field(20, ge=1, le=100, description="Items per page")
    status: Optional[str] = Field(None, description="Filter by status")
    priority: Optional[str] = Field(None, description="Filter by priority")
    min_risk_score: Optional[float] = Field(None, ge=0, le=1, description="Minimum risk score")
    max_risk_score: Optional[float] = Field(None, ge=0, le=1, description="Maximum risk score")
