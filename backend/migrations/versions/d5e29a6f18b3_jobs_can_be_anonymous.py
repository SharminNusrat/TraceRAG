"""a job can belong to nobody, and is watched by its token

Revision ID: d5e29a6f18b3
Revises: a7d3e91b4c60
Create Date: 2026-09-02 09:41:18.224507

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'd5e29a6f18b3'
down_revision: Union[str, Sequence[str], None] = 'a7d3e91b4c60'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    # Running an analysis needs no account, so the job it becomes cannot
    # require one either.
    op.alter_column('jobs', 'user_id', existing_type=sa.Integer(), nullable=True)
    # Job ids run in sequence. Without something unguessable, watching job 41
    # would mean being able to read job 40 - someone else's result.
    op.add_column('jobs', sa.Column('token', sa.String(length=64), nullable=False,
                                    server_default=''))
    op.create_index('ix_jobs_token', 'jobs', ['token'])


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_index('ix_jobs_token', table_name='jobs')
    op.drop_column('jobs', 'token')
    op.alter_column('jobs', 'user_id', existing_type=sa.Integer(), nullable=False)
