"""
Pydantic schemas for Simulation API.

The simulation runs synthetic transactions through the full pipeline:
  Transaction → ML Prediction → AI Decision → Policy → Recovery → Audit
"""
from pydantic import BaseModel, Field
from typing import Optional, List
from datetime import datetime


class SimulationRequest(BaseModel):
    """Request to run a batch simulation."""
    num_transactions: int = Field(
        default=1000,
        ge=10,
        le=50000,
        description="Number of synthetic transactions to simulate (min 10, max 50000)"
    )
    seed: Optional[int] = Field(
        default=None,
        description="Random seed for reproducibility (None for random)"
    )


class SimulationMetrics(BaseModel):
    """Business metrics computed during simulation."""
    revenue_at_risk: float = Field(description="Total amount of failed transactions (SIMULATED)")
    revenue_recovered: float = Field(description="Amount successfully recovered (SIMULATED)")
    recovery_rate: float = Field(description="Recovery rate percentage")
    total_recovery_attempts: int = Field(description="Total recovery actions attempted")
    successful_recoveries: int = Field(description="Number of successful recoveries")
    failed_recoveries: int = Field(description="Number of failed recovery attempts")
    escalated_cases: int = Field(description="Number of escalated cases")
    policy_blocked: int = Field(description="Number of policy-blocked actions")
    average_recovery_time_seconds: float = Field(description="Average time per case processing")
    outstanding_revenue: float = Field(description="Revenue still at risk after simulation")


class SimulationResult(BaseModel):
    """Complete simulation result."""
    simulation_id: str = Field(description="Unique simulation identifier")
    status: str = Field(description="Simulation status: COMPLETED, FAILED, etc.")
    label: str = Field(default="SIMULATED", description="Always 'SIMULATED' to distinguish from real data")

    # Input
    num_transactions_processed: int = Field(description="Number of transactions processed")

    # Pipeline outcomes
    recoverable_cases: int = Field(description="Cases identified as recoverable by ML")
    successful_recoveries: int = Field(description="Cases with successful recovery")
    failed_recoveries: int = Field(description="Cases where recovery failed")
    escalations: int = Field(description="Cases escalated to human review")
    policy_blocks: int = Field(description="Actions blocked by policy engine")

    # Revenue (SIMULATED)
    revenue_at_risk: float = Field(description="Total failed payment value (SIMULATED)")
    simulated_revenue_recovered: float = Field(description="Revenue recovered in simulation (SIMULATED)")
    recovery_rate: float = Field(description="Recovery rate percentage")

    # Performance
    processing_duration_seconds: float = Field(description="Total simulation processing time")
    avg_processing_time_ms: float = Field(description="Average per-transaction processing time")

    # Audit
    total_audit_events: int = Field(description="Total audit events generated")
    total_policy_decisions: int = Field(description="Total policy decisions made")
    total_recovery_actions: int = Field(description="Total recovery actions created")

    # Detailed metrics
    metrics: SimulationMetrics = Field(description="Detailed business metrics")

    # Breakdown
    recovery_action_breakdown: dict = Field(default_factory=dict, description="Breakdown by action type")
    status_breakdown: dict = Field(default_factory=dict, description="Breakdown by case status")
    ml_prediction_stats: dict = Field(default_factory=dict, description="ML prediction statistics")

    # Timestamps
    started_at: str = Field(description="Simulation start time")
    completed_at: str = Field(description="Simulation completion time")
