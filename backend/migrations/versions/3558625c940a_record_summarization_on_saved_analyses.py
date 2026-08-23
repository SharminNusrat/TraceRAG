"""record summarization on saved analyses

Revision ID: 3558625c940a
Revises: 6c07018d8f9e
Create Date: 2026-08-18 20:18:51.299649

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '3558625c940a'
down_revision: Union[str, Sequence[str], None] = '6c07018d8f9e'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    # Analyses saved before summarisation existed genuinely ran without it, so
    # false is the true value for them - not merely a placeholder to satisfy
    # the NOT NULL. Every row written afterwards states its own setting.
    op.add_column(
        'analyses',
        sa.Column(
            'summarize_elements',
            sa.Boolean(),
            nullable=False,
            server_default=sa.false(),
        ),
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_column('analyses', 'summarize_elements')
