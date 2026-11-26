"""page image format, and rate-limit key while a check is processing

Revision ID: 3c1f0a9d7e21
Revises: 8548f2724b1c
Create Date: 2026-11-26 16:30:00.000000

- page_images.png -> page_images.image, plus media_type. New images are WebP; rows from before this
  migration (at most 24 hours old) stay PNG and are served as PNG.
- checks.rate_limit_key: the salted IP hash that paid for the check, kept only while the check is
  processing, so a check interrupted by a restart can be given back. Cleared when it finishes.
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '3c1f0a9d7e21'
down_revision: Union[str, Sequence[str], None] = '8548f2724b1c'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.alter_column('page_images', 'png', new_column_name='image')
    op.add_column('page_images', sa.Column('media_type', sa.String(length=32), nullable=False,
                                           server_default='image/png'))
    op.add_column('checks', sa.Column('rate_limit_key', sa.String(length=64), nullable=True))


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_column('checks', 'rate_limit_key')
    op.drop_column('page_images', 'media_type')
    op.alter_column('page_images', 'image', new_column_name='png')
