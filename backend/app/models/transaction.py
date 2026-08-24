from sqlalchemy import Column, Integer, String, Float, DateTime, ForeignKey, Index
from sqlalchemy.orm import relationship
from sqlalchemy.sql import func
from app.core.database import Base


class Transaction(Base):
    """Transaction model storing payment transaction information."""

    __tablename__ = "transactions"

    id = Column(Integer, primary_key=True, autoincrement=True)
    transaction_id = Column(String(255), unique=True, nullable=False, index=True)
    customer_id = Column(Integer, ForeignKey("customers.id", ondelete="CASCADE"), nullable=False, index=True)
    amount = Column(Float, nullable=False)
    currency = Column(String(10), nullable=False, default="INR")
    payment_method = Column(String(50), nullable=True)
    status = Column(String(20), nullable=False, index=True)
    failure_reason = Column(String(500), nullable=True)
    attempt_count = Column(Integer, default=1, nullable=False)
    created_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    updated_at = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False)

    # Relationships
    customer = relationship("Customer", back_populates="transactions")
    risk_case = relationship("RevenueRiskCase", back_populates="transaction", uselist=False)

    def __repr__(self):
        return f"<Transaction(id={self.id}, transaction_id='{self.transaction_id}', amount={self.amount})>"
