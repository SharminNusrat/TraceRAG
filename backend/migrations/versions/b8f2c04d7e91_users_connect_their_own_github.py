"""a user connects their own GitHub account

Revision ID: b8f2c04d7e91
Revises: d5e29a6f18b3
Create Date: 2026-09-02 15:22:41.118903

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'b8f2c04d7e91'
down_revision: Union[str, Sequence[str], None] = 'd5e29a6f18b3'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    # A token belongs to a person, not to a repository. Held here, connecting a
    # second repository asks for nothing, and disconnecting covers them all.
    # Encrypted before it is written, like the per-source tokens beside it.
    op.add_column('users', sa.Column('github_token', sa.Text(), nullable=True))
    op.add_column('users', sa.Column('github_login', sa.String(length=255), nullable=True))


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_column('users', 'github_login')
    op.drop_column('users', 'github_token')
