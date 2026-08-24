from sqlalchemy import Column, Integer, String, Float, DateTime, ForeignKey, Text
from sqlalchemy import JSON
from sqlalchemy.orm import relationship
from sqlalchemy.sql import func
from app.core.database import Base


class AuditEvent(Base):
    """Audit event model for tracking all actions on revenue risk cases."""

    __tablename__ = "audit_events"

    id = Column(Integer, primary_key=True, autoincrement=True)
    case_id = Column(Integer, ForeignKey("revenue_risk_cases.id", ondelete="CASCADE"), nullable=False, index=True)
    timestamp = Column(DateTime(timezone=True), server_default=func.now(), nullable=False, index=True)
    event_type = Column(String(50), nullable=False, index=True)  # From AuditEventType enum
    actor = Column(String(100), nullable=False)  # Who/what performed the action (e.g., "ai_agent", "policy_engine", "user")
    decision = Column(String(100), nullable=True)  # The decision made
    reason = Column(Text, nullable=True)  # Reason for the decision
    confidence = Column(Float, nullable=True)  # Confidence in the decision 0-1
    policy_checks = Column(JSON, nullable=True)  # Policy engine check results
    action = Column(String(100), nullable=True)  # Action taken
    result = Column(String(100), nullable=True)  # Result of the action
    metadata_ = Column("metadata", JSON, nullable=True)  # Additional metadata

    # Relationships
    risk_case = relationship("RevenueRiskCase", back_populates="audit_events")

    def __repr__(self):
        return f"<AuditEvent(id={self.id}, event_type='{self.event_type}', actor='{self.actor}')>"
