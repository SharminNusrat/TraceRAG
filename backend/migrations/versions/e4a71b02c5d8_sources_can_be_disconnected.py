"""sources can be disconnected without losing their history

Revision ID: e4a71b02c5d8
Revises: c92f5d1a4e07
Create Date: 2026-08-27 12:31:07.442918

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'e4a71b02c5d8'
down_revision: Union[str, Sequence[str], None] = 'c92f5d1a4e07'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    # Existing sources are all connected, so they start true. The server
    # default stays on the column: a source is active unless something says
    # otherwise, and that is worth having in the schema rather than only in
    # the application.
    op.add_column(
        'project_sources',
        sa.Column('is_active', sa.Boolean(), nullable=False, server_default=sa.true()),
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_column('project_sources', 'is_active')
