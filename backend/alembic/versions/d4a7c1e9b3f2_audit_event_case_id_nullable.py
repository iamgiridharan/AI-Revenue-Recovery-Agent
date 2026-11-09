"""make audit_events.case_id nullable

Revision ID: d4a7c1e9b3f2
Revises: c58e0a4b9f31
Create Date: 2026-11-09 17:05:00.000000

Policy configuration changes must be recorded durably, but they do not belong to
any revenue risk case.  The foreign key stays in place for case events; it is
simply allowed to be NULL for organisation-level events.
"""

from typing import Sequence, Union

from alembic import op


# revision identifiers, used by Alembic.
revision: str = "d4a7c1e9b3f2"
down_revision: Union[str, Sequence[str], None] = "c58e0a4b9f31"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.alter_column("audit_events", "case_id", existing_type=None, nullable=True)


def downgrade() -> None:
    # Only safe when no organisation-level (NULL case) events exist.
    op.alter_column("audit_events", "case_id", existing_type=None, nullable=False)
