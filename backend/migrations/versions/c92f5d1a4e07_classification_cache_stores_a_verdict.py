"""classification cache stores a verdict, not a score

Revision ID: c92f5d1a4e07
Revises: b1c4e7a92f30
Create Date: 2026-08-25 12:18:44.921037

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'c92f5d1a4e07'
down_revision: Union[str, Sequence[str], None] = 'b1c4e7a92f30'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    # The classifier answers yes or no; the score a link carries is the
    # retrieval similarity, which belongs to the run and not to the pair.
    # Caching a score would hand a later run a number its own retrieval did
    # not produce. No default is needed: the table was created empty and
    # nothing has written to it yet.
    op.drop_column('classification_cache', 'confidence')
    op.add_column('classification_cache', sa.Column('linked', sa.Boolean(), nullable=False))


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_column('classification_cache', 'linked')
    op.add_column('classification_cache', sa.Column('confidence', sa.Float(), nullable=False))
