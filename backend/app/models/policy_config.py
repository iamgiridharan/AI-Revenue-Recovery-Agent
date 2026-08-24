"""
Policy Configuration model for storing configurable policy rules.

These settings control the deterministic Policy Engine that validates
AI recommendations before any financial actions are taken.
"""
from sqlalchemy import Column, Integer, String, Float, DateTime, Boolean, Text
from sqlalchemy.sql import func
from app.core.database import Base


class PolicyConfig(Base):
    """Policy configuration model storing configurable rules."""
    
    __tablename__ = "policy_configs"
    
    id = Column(Integer, primary_key=True, autoincrement=True)
    
    # Retry limits
    max_retries = Column(Integer, nullable=False, default=2, 
                         comment="Maximum retry attempts per case")
    max_reminders = Column(Integer, nullable=False, default=2,
                           comment="Maximum payment reminders per case")
    max_recovery_attempts = Column(Integer, nullable=False, default=3,
                                   comment="Maximum total recovery attempts per case")
    
    # Amount limits
    autonomous_amount_limit = Column(Float, nullable=False, default=10000.0,
                                     comment="Maximum amount for autonomous actions (INR)")
    
    # Confidence thresholds
    minimum_ai_confidence = Column(Float, nullable=False, default=0.3,
                                   comment="Minimum AI confidence required for action")
    minimum_recovery_probability = Column(Float, nullable=False, default=0.2,
                                          comment="Minimum recovery probability required")
    
    # Time limits
    case_lifetime_days = Column(Integer, nullable=False, default=7,
                                comment="Maximum case lifetime in days")
    
    # Escalation
    escalation_threshold = Column(Float, nullable=False, default=0.7,
                                  comment="Confidence threshold above which escalation is triggered")
    
    # Status
    is_active = Column(Boolean, nullable=False, default=True,
                       comment="Whether this policy configuration is active")
    
    # Description
    description = Column(Text, nullable=True,
                         comment="Description of this policy configuration")
    
    # Timestamps
    created_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    updated_at = Column(DateTime(timezone=True), server_default=func.now(), 
                        onupdate=func.now(), nullable=False)
    
    def __repr__(self):
        return f"<PolicyConfig(id={self.id}, max_retries={self.max_retries}, is_active={self.is_active})>"
