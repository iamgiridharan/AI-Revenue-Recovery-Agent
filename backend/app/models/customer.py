from sqlalchemy import Column, Integer, String, Float, DateTime, Index
from sqlalchemy.orm import relationship
from sqlalchemy.sql import func
from app.core.database import Base


class Customer(Base):
    """Customer model storing customer information and transaction stats."""

    __tablename__ = "customers"

    id = Column(Integer, primary_key=True, autoincrement=True)
    customer_id = Column(String(255), unique=True, nullable=False, index=True)
    name = Column(String(255), nullable=False)
    email = Column(String(255), nullable=False, index=True)
    phone = Column(String(50), nullable=True)
    total_transactions = Column(Integer, default=0, nullable=False)
    successful_transactions = Column(Integer, default=0, nullable=False)
    failed_transactions = Column(Integer, default=0, nullable=False)
    lifetime_value = Column(Float, default=0.0, nullable=False)
    last_payment_date = Column(DateTime(timezone=True), nullable=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    updated_at = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False)

    # Relationships
    transactions = relationship("Transaction", back_populates="customer", lazy="dynamic")
    risk_cases = relationship("RevenueRiskCase", back_populates="customer", lazy="dynamic")

    def __repr__(self):
        return f"<Customer(id={self.id}, customer_id='{self.customer_id}', name='{self.name}')>"
