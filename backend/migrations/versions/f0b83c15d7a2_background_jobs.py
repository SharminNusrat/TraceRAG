"""background jobs

Revision ID: f0b83c15d7a2
Revises: e4a71b02c5d8
Create Date: 2026-08-27 14:02:55.318604

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'f0b83c15d7a2'
down_revision: Union[str, Sequence[str], None] = 'e4a71b02c5d8'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.create_table(
        'jobs',
        sa.Column('job_id', sa.Integer(), nullable=False),
        sa.Column('user_id', sa.Integer(), nullable=False),
        sa.Column('project_id', sa.Integer(), nullable=True),
        sa.Column('kind', sa.String(length=50), nullable=False),
        sa.Column('state', sa.String(length=20), nullable=False),
        sa.Column('stage', sa.String(length=200), nullable=True),
        sa.Column('progress_current', sa.Integer(), nullable=False),
        sa.Column('progress_total', sa.Integer(), nullable=False),
        sa.Column('result_json', sa.Text(), nullable=True),
        sa.Column('error', sa.Text(), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('started_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('finished_at', sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(['user_id'], ['users.user_id'], ondelete='CASCADE'),
        sa.ForeignKeyConstraint(['project_id'], ['projects.project_id'], ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('job_id'),
    )
    op.create_index('ix_jobs_user_id', 'jobs', ['user_id'])
    op.create_index('ix_jobs_project_id', 'jobs', ['project_id'])
    # Asked on every enqueue: is this project already being synced?
    op.create_index('ix_jobs_state', 'jobs', ['state'])


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_table('jobs')
