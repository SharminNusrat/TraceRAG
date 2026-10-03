"""No expansion without a code target

Dependency expansion walks a call graph, so it only happens when the target is
code. The setting was stored as given all the same, so a run against a model or
a document claimed an expansion it never made. Those rows are set to 0, and the
keys that include the setting are recomputed.

Revision ID: e92b6d4a8c35
Revises: d81a5c3f7b24
"""

from hashlib import sha256

import sqlalchemy as sa
from alembic import op

revision = "e92b6d4a8c35"
down_revision = "d81a5c3f7b24"
branch_labels = None
depends_on = None

# The parts of a configuration's key, in order, as (name in the key, column).
KEY_PARTS = (
    ("source_kind", "source_kind"),
    ("target_kind", "target_kind"),
    ("source_preprocessor", "source_preprocessor"),
    ("target_preprocessor", "target_preprocessor"),
    ("source_output_level", "source_output_level"),
    ("target_output_level", "target_output_level"),
    ("classifier", "classifier_type"),
    ("n_results", "top_k"),
    ("dependency_expansion_depth", "dependency_expansion_depth"),
    ("summarize_elements", "summarize_elements"),
)

NOT_CODE = "target_kind IS NOT NULL AND target_kind <> 'code'"


def _key(row) -> str:
    # A copy of config_key() as it stood at this revision, rather than an
    # import: a migration must keep producing the same keys after the
    # application's own function has moved on.
    parts = [f"{name}={row[column]}" for name, column in KEY_PARTS]
    return sha256("|".join(parts).encode("utf-8")).hexdigest()[:16]


def upgrade() -> None:
    connection = op.get_bind()

    connection.execute(sa.text(f"""
        UPDATE analyses SET dependency_expansion_depth = 0
        WHERE dependency_expansion_depth <> 0
          AND config_id IN (SELECT config_id FROM project_configs WHERE {NOT_CODE})
    """))

    rows = connection.execute(sa.text(f"""
        SELECT * FROM project_configs
        WHERE dependency_expansion_depth <> 0 AND {NOT_CODE}
    """)).mappings().all()
    for row in rows:
        fixed = {**row, "dependency_expansion_depth": 0}
        connection.execute(
            sa.text("""
                UPDATE project_configs
                SET dependency_expansion_depth = 0, config_key = :key
                WHERE config_id = :id
            """),
            {"key": _key(fixed), "id": row["config_id"]},
        )


def downgrade() -> None:
    # What each row claimed before is not recorded, and it never described
    # what the run did, so there is nothing worth putting back.
    pass
