"""sample checks: tag checks, feedback and analytics events

Revision ID: 7b2e4c91d0a5
Revises: 3c1f0a9d7e21
Create Date: 2026-11-27 10:00:00.000000

- checks.sample_id: which sample (app/samples.py) a check ran, or NULL for a real check.
- feedback.sample, events.sample: true for sample checks, so launch metrics count real ones only.
  Existing rows are all real checks (false).
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '7b2e4c91d0a5'
down_revision: Union[str, Sequence[str], None] = '3c1f0a9d7e21'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.add_column('checks', sa.Column('sample_id', sa.String(length=32), nullable=True))
    op.add_column('feedback', sa.Column('sample', sa.Boolean(), nullable=False, server_default=sa.false()))
    op.add_column('events', sa.Column('sample', sa.Boolean(), nullable=False, server_default=sa.false()))
    op.create_index(op.f('ix_events_sample'), 'events', ['sample'], unique=False)


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_index(op.f('ix_events_sample'), table_name='events')
    op.drop_column('events', 'sample')
    op.drop_column('feedback', 'sample')
    op.drop_column('checks', 'sample_id')
