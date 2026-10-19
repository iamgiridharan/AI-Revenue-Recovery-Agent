"""
Webhook Event ledger model.

Razorpay retries webhook deliveries, and the same event can be delivered more
than once (or concurrently).  Decisions about money - marking a case recovered
and booking its recovered revenue - must therefore be made at most once per
provider event, and that record has to survive a process restart or a second
worker.  An in-process set cannot do that, so every handled delivery is recorded
here: this table is the durable idempotency ledger and the auditable record of
which verified deliveries were processed.

A row is written only AFTER the delivery has been handled successfully, so a
delivery that fails is still retryable by the provider.
"""
from sqlalchemy import Column, Integer, String, Float, Boolean, DateTime
from sqlalchemy.sql import func
from app.core.database import Base


class WebhookEvent(Base):
    """Durable record of a handled (or deliberately ignored) webhook delivery."""

    __tablename__ = "webhook_events"

    id = Column(Integer, primary_key=True, autoincrement=True)

    # Provider event id (X-Razorpay-Event-Id). Unique: this is what makes the
    # ledger an idempotency guard rather than just a log.
    event_id = Column(String(255), unique=True, nullable=False, index=True)
    event_type = Column(String(100), nullable=False, index=True)

    payment_id = Column(String(255), nullable=True, index=True)
    # The revenue risk case the delivery was associated with, if any.
    case_id = Column(String(255), nullable=True, index=True)
    amount = Column(Float, nullable=True)

    # Whether the delivery carried a valid signature. Only verified deliveries
    # are ever recorded (an unverified one is rejected before handling).
    signature_verified = Column(Boolean, nullable=False, default=True)

    # outcome: PROCESSED, NO_CASE_REFERENCE, NOT_CAPTURED, UNHANDLED, DUPLICATE
    result = Column(String(50), nullable=True)
    received_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)

    def __repr__(self):
        return (f"<WebhookEvent(event_id='{self.event_id}', "
                f"event_type='{self.event_type}', result='{self.result}')>")
