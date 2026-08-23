"""replace analysis version name with a note

Revision ID: 86cf3c94ca31
Revises: 3558625c940a
Create Date: 2026-08-19 06:41:39.037638

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '86cf3c94ca31'
down_revision: Union[str, Sequence[str], None] = '3558625c940a'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    # A run is identified by when it ran, so it never needed a name. What the
    # field was actually being used for - a remark on what changed since the
    # last run - becomes a field that says so.
    op.add_column('analyses', sa.Column('note', sa.String(length=200), nullable=True))
    op.drop_column('analyses', 'version_name')


def downgrade() -> None:
    """Downgrade schema."""
    op.add_column(
        'analyses',
        sa.Column('version_name', sa.VARCHAR(length=100), autoincrement=False, nullable=True),
    )
    op.drop_column('analyses', 'note')
