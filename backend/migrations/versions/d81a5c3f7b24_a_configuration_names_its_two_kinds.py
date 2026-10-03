"""A configuration names its two kinds

A configuration was identified by its settings alone. Two kinds that share a
preprocessor - requirements and architecture documents are both split into
sections - would then be filed under one configuration, and two different
relations would share one graph.

Existing rows take their kinds from the artifacts of their most recent run, and
their key is recomputed to include them.

Revision ID: d81a5c3f7b24
Revises: c47f8b1e39d2
"""

from hashlib import sha256

import sqlalchemy as sa
from alembic import op

revision = "d81a5c3f7b24"
down_revision = "c47f8b1e39d2"
branch_labels = None
depends_on = None

SETTINGS = (
    "source_preprocessor",
    "target_preprocessor",
    "source_output_level",
    "target_output_level",
    "classifier_type",
    "top_k",
    "dependency_expansion_depth",
    "summarize_elements",
)

# What each stored column is called in the key.
KEY_NAMES = {"classifier_type": "classifier", "top_k": "n_results"}


def _key(row, with_kinds: bool) -> str:
    # A copy of config_key() as it stood at this revision, rather than an
    # import: a migration must keep producing the same keys after the
    # application's own function has moved on.
    names = (("source_kind", "target_kind") if with_kinds else ()) + SETTINGS
    parts = [f"{KEY_NAMES.get(name, name)}={row[name]}" for name in names]
    return sha256("|".join(parts).encode("utf-8")).hexdigest()[:16]


def _rekey(with_kinds: bool) -> None:
    connection = op.get_bind()
    rows = connection.execute(sa.text("SELECT * FROM project_configs")).mappings().all()
    for row in rows:
        connection.execute(
            sa.text("UPDATE project_configs SET config_key = :key WHERE config_id = :id"),
            {"key": _key(row, with_kinds), "id": row["config_id"]},
        )


def upgrade() -> None:
    op.add_column("project_configs", sa.Column("source_kind", sa.String(length=50), nullable=True))
    op.add_column("project_configs", sa.Column("target_kind", sa.String(length=50), nullable=True))

    # A configuration whose runs kept no files stays null: nothing recorded
    # what it was pointed at.
    for role in ("source", "target"):
        op.execute(sa.text(f"""
            UPDATE project_configs AS config
            SET {role}_kind = (
                SELECT artifact.artifact_type
                FROM artifacts AS artifact
                JOIN analyses AS analysis ON analysis.analysis_id = artifact.analysis_id
                WHERE analysis.config_id = config.config_id
                  AND artifact.role = '{role}'
                ORDER BY analysis.created_at DESC, analysis.analysis_id DESC
                LIMIT 1
            )
        """))

    _rekey(with_kinds=True)


def downgrade() -> None:
    _rekey(with_kinds=False)
    op.drop_column("project_configs", "target_kind")
    op.drop_column("project_configs", "source_kind")
