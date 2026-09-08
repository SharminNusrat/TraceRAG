"""a source can carry its own access token

Revision ID: a7d3e91b4c60
Revises: f0b83c15d7a2
Create Date: 2026-09-01 10:14:22.706311

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'a7d3e91b4c60'
down_revision: Union[str, Sequence[str], None] = 'f0b83c15d7a2'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    # Encrypted before it is written, so this column never holds a usable
    # credential on its own. Null for public repositories and for anything the
    # server's own token can already reach.
    op.add_column('project_sources', sa.Column('access_token', sa.Text(), nullable=True))


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_column('project_sources', 'access_token')
