from sqlalchemy import Column, Integer, String, Float, DateTime, ForeignKey, Text, Index
from sqlalchemy.orm import relationship
from sqlalchemy.sql import func
from app.core.database import Base


class RevenueRiskCase(Base):
    """Revenue risk case model tracking failed payments and recovery attempts."""

    __tablename__ = "revenue_risk_cases"

    id = Column(Integer, primary_key=True, autoincrement=True)
    case_id = Column(String(255), unique=True, nullable=False, index=True)
    transaction_id = Column(Integer, ForeignKey("transactions.id", ondelete="CASCADE"), nullable=False, index=True)
    customer_id = Column(Integer, ForeignKey("customers.id", ondelete="CASCADE"), nullable=False, index=True)
    amount = Column(Float, nullable=False)
    risk_score = Column(Float, nullable=True)  # ML prediction score 0-1
    recovery_probability = Column(Float, nullable=True)  # ML prediction 0-1
    priority = Column(String(20), nullable=False, default="MEDIUM", index=True)
    diagnosis = Column(Text, nullable=True)  # AI agent diagnosis
    recommended_action = Column(String(100), nullable=True)  # AI agent recommendation
    status = Column(String(30), nullable=False, default="OPEN", index=True)
    attempt_count = Column(Integer, default=0, nullable=False)
    recovered_amount = Column(Float, default=0.0, nullable=False)
    created_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    updated_at = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False)

    # Relationships
    transaction = relationship("Transaction", back_populates="risk_case")
    customer = relationship("Customer", back_populates="risk_cases")
    recovery_actions = relationship("RecoveryAction", back_populates="risk_case", lazy="dynamic", cascade="all, delete-orphan")
    audit_events = relationship("AuditEvent", back_populates="risk_case", lazy="dynamic", cascade="all, delete-orphan")

    # Composite indexes for common queries
    __table_args__ = (
        Index("ix_risk_cases_status_priority", "status", "priority"),
        Index("ix_risk_cases_created_status", "created_at", "status"),
    )

    def __repr__(self):
        return f"<RevenueRiskCase(id={self.id}, case_id='{self.case_id}', status='{self.status}')>"
