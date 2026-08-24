from sqlalchemy import Column, Integer, String, Float, DateTime, ForeignKey, Text
from sqlalchemy.orm import relationship
from sqlalchemy.sql import func
from app.core.database import Base


class RecoveryAction(Base):
    """Recovery action model tracking actions taken on revenue risk cases."""

    __tablename__ = "recovery_actions"

    id = Column(Integer, primary_key=True, autoincrement=True)
    case_id = Column(Integer, ForeignKey("revenue_risk_cases.id", ondelete="CASCADE"), nullable=False, index=True)
    action_type = Column(String(50), nullable=False)  # From RecoveryActionType enum
    reason = Column(Text, nullable=True)  # Why this action was recommended
    confidence = Column(Float, nullable=True)  # Agent confidence 0-1
    policy_result = Column(String(30), nullable=True)  # APPROVED or BLOCKED
    execution_status = Column(String(30), nullable=True, default="PENDING")  # From RecoveryOutcome enum
    api_reference = Column(String(255), nullable=True)  # Razorpay API reference
    created_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)

    # Relationships
    risk_case = relationship("RevenueRiskCase", back_populates="recovery_actions")

    def __repr__(self):
        return f"<RecoveryAction(id={self.id}, action_type='{self.action_type}', status='{self.execution_status}')>"
