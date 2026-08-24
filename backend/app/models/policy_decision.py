"""
Policy Decision model for storing policy engine decisions.

Every policy decision must be auditable. This model stores
the case, proposed action, policy result, rules evaluated,
reason, timestamp, and final decision.
"""
from sqlalchemy import Column, Integer, String, Float, DateTime, ForeignKey, Text, JSON
from sqlalchemy.orm import relationship
from sqlalchemy.sql import func
from app.core.database import Base


class PolicyDecision(Base):
    """Policy decision model for tracking all policy engine decisions."""
    
    __tablename__ = "policy_decisions"
    
    id = Column(Integer, primary_key=True, autoincrement=True)
    
    # Case reference
    case_id = Column(Integer, ForeignKey("revenue_risk_cases.id", ondelete="CASCADE"), 
                     nullable=False, index=True)
    
    # Decision details
    proposed_action = Column(String(100), nullable=False, 
                             comment="The action proposed by the AI agent")
    decision = Column(String(30), nullable=False, 
                      comment="APPROVED, BLOCKED, or ESCALATED")
    reason = Column(Text, nullable=True, 
                    comment="Reason for the decision")
    
    # Policy checks performed
    checks = Column(JSON, nullable=True, 
                    comment="List of policy checks with results")
    
    # Context
    confidence = Column(Float, nullable=True, 
                        comment="AI confidence at time of decision")
    recovery_probability = Column(Float, nullable=True,
                                  comment="Recovery probability at time of decision")
    amount = Column(Float, nullable=True,
                    comment="Transaction amount at time of decision")
    
    # Result
    final_decision = Column(String(30), nullable=False,
                            comment="Final decision after all checks")
    
    # Timestamps
    created_at = Column(DateTime(timezone=True), server_default=func.now(), 
                        nullable=False, index=True)
    
    # Relationships
    risk_case = relationship("RevenueRiskCase", back_populates="policy_decisions")
    
    def __repr__(self):
        return f"<PolicyDecision(id={self.id}, decision='{self.decision}', action='{self.proposed_action}')>"
