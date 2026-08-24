"""
Pydantic schemas for ML inference API endpoints.
"""
from pydantic import BaseModel, Field
from typing import Optional


class MLInferenceRequest(BaseModel):
    """Request schema for ML recovery prediction."""
    
    amount: float = Field(..., gt=0, description="Transaction amount in INR")
    payment_method: str = Field(..., description="Payment method (card, upi, netbanking, wallet, emi)")
    failure_reason: str = Field(..., description="Reason for payment failure")
    currency: str = Field(default="INR", description="Currency code")
    attempt_number: int = Field(default=1, ge=1, le=10, description="Attempt number")
    hour_of_day: int = Field(default=12, ge=0, le=23, description="Hour of day (0-23)")
    day_of_week: int = Field(default=0, ge=0, le=6, description="Day of week (0=Monday, 6=Sunday)")
    days_since_last_transaction: float = Field(default=0.0, ge=0, description="Days since last transaction")
    customer_total_transactions: int = Field(default=0, ge=0, description="Total transactions by customer")
    customer_successful_transactions: int = Field(default=0, ge=0, description="Successful transactions by customer")
    customer_failed_transactions: int = Field(default=0, ge=0, description="Failed transactions by customer")
    customer_lifetime_value: float = Field(default=0.0, ge=0, description="Customer lifetime value")
    customer_age_days: int = Field(default=0, ge=0, description="Customer age in days")


class MLInferenceResponse(BaseModel):
    """Response schema for ML recovery prediction."""
    
    success: bool = True
    data: "MLPredictionResult"


class MLPredictionResult(BaseModel):
    """ML prediction result."""
    
    recovery_probability: float = Field(..., description="Probability of recovery (0-1)")
    risk_score: float = Field(..., description="Risk score (0-1, higher = more risky)")
    risk_level: str = Field(..., description="Risk level (LOW, MEDIUM, HIGH, CRITICAL)")
    priority: str = Field(..., description="Priority level (P0, P1, P2, P3)")
    recovery_category: str = Field(..., description="Recovery probability category")
    model_version: str = Field(..., description="Model version used")


class MLPredictRequest(BaseModel):
    """Request schema for batch ML predictions."""
    
    transactions: list[MLInferenceRequest] = Field(..., min_length=1, max_length=100, description="List of transactions to predict")


class MLPredictResponse(BaseModel):
    """Response schema for batch ML predictions."""
    
    success: bool = True
    data: list[MLPredictionResult]


class MLHealthResponse(BaseModel):
    """Response schema for ML health check."""
    
    success: bool = True
    data: dict
