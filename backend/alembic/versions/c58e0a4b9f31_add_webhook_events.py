"""add webhook_events idempotency ledger

Revision ID: c58e0a4b9f31
Revises: b41c9f2a7d10
Create Date: 2026-10-19 10:15:00.000000

Creates the durable ledger that makes webhook handling idempotent.

Duplicate protection used to live in a process-local ``set``, so a provider
retry after a restart (or a second worker) was handled as a fresh event.  Each
handled delivery is now recorded by its provider event id, and a duplicate
delivery is answered from this table instead of being applied twice.
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'c58e0a4b9f31'
down_revision: Union[str, Sequence[str], None] = 'b41c9f2a7d10'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Create the webhook_events ledger."""
    op.create_table(
        'webhook_events',
        sa.Column('id', sa.Integer(), autoincrement=True, nullable=False),
        sa.Column('event_id', sa.String(255), nullable=False,
                  comment='Razorpay event id (X-Razorpay-Event-Id)'),
        sa.Column('event_type', sa.String(100), nullable=False),
        sa.Column('payment_id', sa.String(255), nullable=True),
        sa.Column('case_id', sa.String(255), nullable=True,
                  comment='Revenue risk case the event was associated with'),
        sa.Column('amount', sa.Float(), nullable=True,
                  comment='Captured amount in INR, when the event carried one'),
        sa.Column('signature_verified', sa.Boolean(), nullable=False,
                  server_default=sa.text('true')),
        sa.Column('result', sa.String(50), nullable=True),
        sa.Column('received_at', sa.DateTime(timezone=True),
                  server_default=sa.func.now(), nullable=False),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_index(op.f('ix_webhook_events_event_id'), 'webhook_events', ['event_id'], unique=True)
    op.create_index(op.f('ix_webhook_events_event_type'), 'webhook_events', ['event_type'], unique=False)
    op.create_index(op.f('ix_webhook_events_payment_id'), 'webhook_events', ['payment_id'], unique=False)
    op.create_index(op.f('ix_webhook_events_case_id'), 'webhook_events', ['case_id'], unique=False)


def downgrade() -> None:
    """Drop the webhook_events ledger."""
    op.drop_index(op.f('ix_webhook_events_case_id'), table_name='webhook_events')
    op.drop_index(op.f('ix_webhook_events_payment_id'), table_name='webhook_events')
    op.drop_index(op.f('ix_webhook_events_event_type'), table_name='webhook_events')
    op.drop_index(op.f('ix_webhook_events_event_id'), table_name='webhook_events')
    op.drop_table('webhook_events')
