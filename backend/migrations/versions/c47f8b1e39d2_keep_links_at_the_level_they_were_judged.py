"""Keep links at the level they were judged

A graph edge is stored at the configuration's output level, so a file-level
edge cannot say which method earned it - and the classifier takes elements, not
files. Without the underlying pair there is nothing to offer the classifier on
the next run, and a link can vanish because retrieval stopped surfacing it
rather than because anything decided against it.

Revision ID: c47f8b1e39d2
Revises: b8f2c04d7e91
"""

import sqlalchemy as sa
from alembic import op

revision = "c47f8b1e39d2"
down_revision = "b8f2c04d7e91"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "element_links",
        sa.Column("element_link_id", sa.Integer(), primary_key=True),
        sa.Column("config_id", sa.Integer(), nullable=False),
        sa.Column("source_identifier", sa.String(length=500), nullable=False),
        sa.Column("target_identifier", sa.String(length=500), nullable=False),
        sa.ForeignKeyConstraint(
            ["config_id"], ["project_configs.config_id"], ondelete="CASCADE"
        ),
        sa.UniqueConstraint(
            "config_id", "source_identifier", "target_identifier",
            name="uq_element_links_config_pair",
        ),
    )
    op.create_index("ix_element_links_config_id", "element_links", ["config_id"])


def downgrade() -> None:
    op.drop_index("ix_element_links_config_id", table_name="element_links")
    op.drop_table("element_links")
