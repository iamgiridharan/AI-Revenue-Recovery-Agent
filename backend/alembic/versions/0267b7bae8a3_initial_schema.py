"""initial_schema — create all 7 tables for the Revenue Recovery Agent

Revision ID: 0267b7bae8a3
Revises:
Create Date: 2026-08-27 17:25:35.363446

"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '0267b7bae8a3'
down_revision: Union[str, Sequence[str], None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Create all tables."""

    # ── customers ─────────────────────────────────────────────────────
    op.create_table(
        'customers',
        sa.Column('id', sa.Integer(), autoincrement=True, nullable=False),
        sa.Column('customer_id', sa.String(255), nullable=False),
        sa.Column('name', sa.String(255), nullable=False),
        sa.Column('email', sa.String(255), nullable=False),
        sa.Column('phone', sa.String(50), nullable=True),
        sa.Column('total_transactions', sa.Integer(), nullable=False, server_default=sa.text('0')),
        sa.Column('successful_transactions', sa.Integer(), nullable=False, server_default=sa.text('0')),
        sa.Column('failed_transactions', sa.Integer(), nullable=False, server_default=sa.text('0')),
        sa.Column('lifetime_value', sa.Float(), nullable=False, server_default=sa.text('0.0')),
        sa.Column('last_payment_date', sa.DateTime(timezone=True), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_index(op.f('ix_customers_customer_id'), 'customers', ['customer_id'], unique=True)
    op.create_index(op.f('ix_customers_email'), 'customers', ['email'], unique=False)

    # ── policy_configs ────────────────────────────────────────────────
    op.create_table(
        'policy_configs',
        sa.Column('id', sa.Integer(), autoincrement=True, nullable=False),
        sa.Column('max_retries', sa.Integer(), nullable=False, server_default=sa.text('2'),
                   comment='Maximum retry attempts per case'),
        sa.Column('max_reminders', sa.Integer(), nullable=False, server_default=sa.text('2'),
                   comment='Maximum payment reminders per case'),
        sa.Column('max_recovery_attempts', sa.Integer(), nullable=False, server_default=sa.text('3'),
                   comment='Maximum total recovery attempts per case'),
        sa.Column('autonomous_amount_limit', sa.Float(), nullable=False, server_default=sa.text('10000.0'),
                   comment='Maximum amount for autonomous actions (INR)'),
        sa.Column('minimum_ai_confidence', sa.Float(), nullable=False, server_default=sa.text('0.3'),
                   comment='Minimum AI confidence required for action'),
        sa.Column('minimum_recovery_probability', sa.Float(), nullable=False, server_default=sa.text('0.2'),
                   comment='Minimum recovery probability required'),
        sa.Column('case_lifetime_days', sa.Integer(), nullable=False, server_default=sa.text('7'),
                   comment='Maximum case lifetime in days'),
        sa.Column('escalation_threshold', sa.Float(), nullable=False, server_default=sa.text('0.7'),
                   comment='Confidence threshold above which escalation is triggered'),
        sa.Column('is_active', sa.Boolean(), nullable=False, server_default=sa.text('true'),
                   comment='Whether this policy configuration is active'),
        sa.Column('description', sa.Text(), nullable=True,
                   comment='Description of this policy configuration'),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.PrimaryKeyConstraint('id'),
    )

    # ── transactions ──────────────────────────────────────────────────
    op.create_table(
        'transactions',
        sa.Column('id', sa.Integer(), autoincrement=True, nullable=False),
        sa.Column('transaction_id', sa.String(255), nullable=False),
        sa.Column('customer_id', sa.Integer(), nullable=False),
        sa.Column('amount', sa.Float(), nullable=False),
        sa.Column('currency', sa.String(10), nullable=False, server_default=sa.text("'INR'")),
        sa.Column('payment_method', sa.String(50), nullable=True),
        sa.Column('status', sa.String(20), nullable=False),
        sa.Column('failure_reason', sa.String(500), nullable=True),
        sa.Column('attempt_count', sa.Integer(), nullable=False, server_default=sa.text('1')),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.ForeignKeyConstraint(['customer_id'], ['customers.id'], ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_index(op.f('ix_transactions_customer_id'), 'transactions', ['customer_id'], unique=False)
    op.create_index(op.f('ix_transactions_status'), 'transactions', ['status'], unique=False)
    op.create_index(op.f('ix_transactions_transaction_id'), 'transactions', ['transaction_id'], unique=True)

    # ── revenue_risk_cases ────────────────────────────────────────────
    op.create_table(
        'revenue_risk_cases',
        sa.Column('id', sa.Integer(), autoincrement=True, nullable=False),
        sa.Column('case_id', sa.String(255), nullable=False),
        sa.Column('transaction_id', sa.Integer(), nullable=False),
        sa.Column('customer_id', sa.Integer(), nullable=False),
        sa.Column('amount', sa.Float(), nullable=False),
        sa.Column('risk_score', sa.Float(), nullable=True),
        sa.Column('recovery_probability', sa.Float(), nullable=True),
        sa.Column('priority', sa.String(20), nullable=False, server_default=sa.text("'MEDIUM'")),
        sa.Column('diagnosis', sa.Text(), nullable=True),
        sa.Column('recommended_action', sa.String(100), nullable=True),
        sa.Column('status', sa.String(30), nullable=False, server_default=sa.text("'OPEN'")),
        sa.Column('attempt_count', sa.Integer(), nullable=False, server_default=sa.text('0')),
        sa.Column('recovered_amount', sa.Float(), nullable=False, server_default=sa.text('0.0')),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.ForeignKeyConstraint(['customer_id'], ['customers.id'], ondelete='CASCADE'),
        sa.ForeignKeyConstraint(['transaction_id'], ['transactions.id'], ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_index(op.f('ix_revenue_risk_cases_case_id'), 'revenue_risk_cases', ['case_id'], unique=True)
    op.create_index(op.f('ix_revenue_risk_cases_customer_id'), 'revenue_risk_cases', ['customer_id'], unique=False)
    op.create_index(op.f('ix_revenue_risk_cases_priority'), 'revenue_risk_cases', ['priority'], unique=False)
    op.create_index(op.f('ix_revenue_risk_cases_status'), 'revenue_risk_cases', ['status'], unique=False)
    op.create_index(op.f('ix_revenue_risk_cases_transaction_id'), 'revenue_risk_cases', ['transaction_id'], unique=False)
    op.create_index('ix_risk_cases_created_status', 'revenue_risk_cases', ['created_at', 'status'], unique=False)
    op.create_index('ix_risk_cases_status_priority', 'revenue_risk_cases', ['status', 'priority'], unique=False)

    # ── audit_events ──────────────────────────────────────────────────
    op.create_table(
        'audit_events',
        sa.Column('id', sa.Integer(), autoincrement=True, nullable=False),
        sa.Column('case_id', sa.Integer(), nullable=False),
        sa.Column('timestamp', sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column('event_type', sa.String(50), nullable=False),
        sa.Column('actor', sa.String(100), nullable=False),
        sa.Column('decision', sa.String(100), nullable=True),
        sa.Column('reason', sa.Text(), nullable=True),
        sa.Column('confidence', sa.Float(), nullable=True),
        sa.Column('policy_checks', sa.JSON(), nullable=True),
        sa.Column('action', sa.String(100), nullable=True),
        sa.Column('result', sa.String(100), nullable=True),
        sa.Column('metadata', sa.JSON(), nullable=True),
        sa.ForeignKeyConstraint(['case_id'], ['revenue_risk_cases.id'], ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_index(op.f('ix_audit_events_case_id'), 'audit_events', ['case_id'], unique=False)
    op.create_index(op.f('ix_audit_events_event_type'), 'audit_events', ['event_type'], unique=False)
    op.create_index(op.f('ix_audit_events_timestamp'), 'audit_events', ['timestamp'], unique=False)

    # ── policy_decisions ──────────────────────────────────────────────
    op.create_table(
        'policy_decisions',
        sa.Column('id', sa.Integer(), autoincrement=True, nullable=False),
        sa.Column('case_id', sa.Integer(), nullable=False),
        sa.Column('proposed_action', sa.String(100), nullable=False,
                   comment='The action proposed by the AI agent'),
        sa.Column('decision', sa.String(30), nullable=False,
                   comment='APPROVED, BLOCKED, or ESCALATED'),
        sa.Column('reason', sa.Text(), nullable=True,
                   comment='Reason for the decision'),
        sa.Column('checks', sa.JSON(), nullable=True,
                   comment='List of policy checks with results'),
        sa.Column('confidence', sa.Float(), nullable=True,
                   comment='AI confidence at time of decision'),
        sa.Column('recovery_probability', sa.Float(), nullable=True,
                   comment='Recovery probability at time of decision'),
        sa.Column('amount', sa.Float(), nullable=True,
                   comment='Transaction amount at time of decision'),
        sa.Column('final_decision', sa.String(30), nullable=False,
                   comment='Final decision after all checks'),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.func.now(),
                   nullable=False),
        sa.ForeignKeyConstraint(['case_id'], ['revenue_risk_cases.id'], ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_index(op.f('ix_policy_decisions_case_id'), 'policy_decisions', ['case_id'], unique=False)
    op.create_index(op.f('ix_policy_decisions_created_at'), 'policy_decisions', ['created_at'], unique=False)

    # ── recovery_actions ──────────────────────────────────────────────
    op.create_table(
        'recovery_actions',
        sa.Column('id', sa.Integer(), autoincrement=True, nullable=False),
        sa.Column('case_id', sa.Integer(), nullable=False),
        sa.Column('action_type', sa.String(50), nullable=False),
        sa.Column('reason', sa.Text(), nullable=True),
        sa.Column('confidence', sa.Float(), nullable=True),
        sa.Column('policy_result', sa.String(30), nullable=True),
        sa.Column('execution_status', sa.String(30), nullable=True, server_default=sa.text("'PENDING'")),
        sa.Column('api_reference', sa.String(255), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.ForeignKeyConstraint(['case_id'], ['revenue_risk_cases.id'], ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_index(op.f('ix_recovery_actions_case_id'), 'recovery_actions', ['case_id'], unique=False)


def downgrade() -> None:
    """Drop all tables in reverse dependency order."""
    op.drop_index(op.f('ix_recovery_actions_case_id'), table_name='recovery_actions')
    op.drop_table('recovery_actions')

    op.drop_index(op.f('ix_policy_decisions_created_at'), table_name='policy_decisions')
    op.drop_index(op.f('ix_policy_decisions_case_id'), table_name='policy_decisions')
    op.drop_table('policy_decisions')

    op.drop_index(op.f('ix_audit_events_timestamp'), table_name='audit_events')
    op.drop_index(op.f('ix_audit_events_event_type'), table_name='audit_events')
    op.drop_index(op.f('ix_audit_events_case_id'), table_name='audit_events')
    op.drop_table('audit_events')

    op.drop_index('ix_risk_cases_status_priority', table_name='revenue_risk_cases')
    op.drop_index('ix_risk_cases_created_status', table_name='revenue_risk_cases')
    op.drop_index(op.f('ix_revenue_risk_cases_transaction_id'), table_name='revenue_risk_cases')
    op.drop_index(op.f('ix_revenue_risk_cases_status'), table_name='revenue_risk_cases')
    op.drop_index(op.f('ix_revenue_risk_cases_priority'), table_name='revenue_risk_cases')
    op.drop_index(op.f('ix_revenue_risk_cases_customer_id'), table_name='revenue_risk_cases')
    op.drop_index(op.f('ix_revenue_risk_cases_case_id'), table_name='revenue_risk_cases')
    op.drop_table('revenue_risk_cases')

    op.drop_index(op.f('ix_transactions_transaction_id'), table_name='transactions')
    op.drop_index(op.f('ix_transactions_status'), table_name='transactions')
    op.drop_index(op.f('ix_transactions_customer_id'), table_name='transactions')
    op.drop_table('transactions')

    op.drop_table('policy_configs')

    op.drop_index(op.f('ix_customers_email'), table_name='customers')
    op.drop_index(op.f('ix_customers_customer_id'), table_name='customers')
    op.drop_table('customers')
