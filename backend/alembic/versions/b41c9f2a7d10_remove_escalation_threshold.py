"""remove escalation_threshold from policy_configs

Revision ID: b41c9f2a7d10
Revises: 0267b7bae8a3
Create Date: 2026-10-19 10:00:00.000000

Drops the inactive ``escalation_threshold`` policy setting.

The setting was stored, validated and returned by the API, but its policy check
hard-coded ``passed=True`` and the decision was never branched on, so it could
not influence any decision. Escalation is driven by the limits that gate an
action (AUTONOMOUS_AMOUNT_LIMIT, MINIMUM_AI_CONFIDENCE,
MINIMUM_RECOVERY_PROBABILITY), which are untouched by this migration.
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'b41c9f2a7d10'
down_revision: Union[str, Sequence[str], None] = '0267b7bae8a3'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Drop the unused escalation_threshold column."""
    op.drop_column('policy_configs', 'escalation_threshold')


def downgrade() -> None:
    """Re-add the column with its original default."""
    op.add_column(
        'policy_configs',
        sa.Column(
            'escalation_threshold',
            sa.Float(),
            nullable=False,
            server_default=sa.text('0.7'),
            comment='Confidence threshold above which escalation is triggered',
        ),
    )
