"""analyses own their versions and sides

A project used to hold the versions, the sources and the pairs, and every
analysis in it shared them. Each analysis now owns its own sides, its own
version history and its own pinned links, and the project is only a folder.
The living graph goes: what changed between two versions is worked out from
their runs when it is asked for.

The old rows cannot be split up after the fact - nothing records which runs
were meant to belong together - so the project tables are dropped and made
again empty. Accounts and the three caches have no link to a project and are
left exactly as they are, so nothing already embedded, summarised or
classified has to be paid for again.

Before running this on a database that has projects in it, delete the stored
files and the vector indexes that belonged to them: app_data/ and
chroma_data/ in the backend folder.

Revision ID: a1f0c7d3e5b9
Revises: e92b6d4a8c35
"""

from alembic import op
import sqlalchemy as sa

revision = "a1f0c7d3e5b9"
down_revision = "e92b6d4a8c35"
branch_labels = None
depends_on = None

# Every table that hangs off a project. Users and the caches are not here.
PROJECT_TABLES = (
    "jobs",
    "element_links",
    "graph_edges",
    "graph_nodes",
    "trace_links",
    "artifact_files",
    "artifacts",
    "version_sources",
    "analyses",
    "project_versions",
    "project_sources",
    "project_configs",
    "projects",
)


def upgrade() -> None:
    for table in PROJECT_TABLES:
        op.execute(f'DROP TABLE IF EXISTS "{table}" CASCADE')

    op.create_table(
        "projects",
        sa.Column("project_id", sa.Integer(), nullable=False),
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column("project_name", sa.String(length=255), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["user_id"], ["users.user_id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("project_id"),
    )
    op.create_index("ix_projects_user_id", "projects", ["user_id"])

    op.create_table(
        "project_configs",
        sa.Column("config_id", sa.Integer(), nullable=False),
        sa.Column("project_id", sa.Integer(), nullable=False),
        sa.Column("source_kind", sa.String(length=50), nullable=True),
        sa.Column("target_kind", sa.String(length=50), nullable=True),
        sa.Column("source_preprocessor", sa.String(length=50), nullable=False),
        sa.Column("target_preprocessor", sa.String(length=50), nullable=False),
        sa.Column("source_output_level", sa.String(length=50), nullable=True),
        sa.Column("target_output_level", sa.String(length=50), nullable=True),
        sa.Column("top_k", sa.Integer(), nullable=False),
        sa.Column("dependency_expansion_depth", sa.Integer(), nullable=False),
        sa.Column("classifier_type", sa.String(length=50), nullable=False),
        sa.Column("summarize_elements", sa.Boolean(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["project_id"], ["projects.project_id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("config_id"),
    )
    op.create_index("ix_project_configs_project_id", "project_configs", ["project_id"])

    op.create_table(
        "project_sources",
        sa.Column("source_id", sa.Integer(), nullable=False),
        sa.Column("config_id", sa.Integer(), nullable=False),
        sa.Column("role", sa.String(length=20), nullable=False),
        sa.Column("kind", sa.String(length=50), nullable=False),
        sa.Column("name", sa.String(length=255), nullable=False),
        sa.Column("origin", sa.String(length=20), nullable=False),
        sa.Column("location", sa.String(length=255), nullable=True),
        sa.Column("branch", sa.String(length=255), nullable=True),
        sa.Column("access_token", sa.Text(), nullable=True),
        sa.Column("checked_ref", sa.String(length=255), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["config_id"], ["project_configs.config_id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("source_id"),
        sa.UniqueConstraint("config_id", "role"),
    )
    op.create_index("ix_project_sources_config_id", "project_sources", ["config_id"])

    op.create_table(
        "project_versions",
        sa.Column("version_id", sa.Integer(), nullable=False),
        sa.Column("config_id", sa.Integer(), nullable=False),
        sa.Column("version_number", sa.Integer(), nullable=False),
        sa.Column("note", sa.String(length=200), nullable=True),
        sa.Column("changes_json", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["config_id"], ["project_configs.config_id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("version_id"),
        sa.UniqueConstraint("config_id", "version_number"),
    )
    op.create_index("ix_project_versions_config_id", "project_versions", ["config_id"])

    op.create_table(
        "analyses",
        sa.Column("analysis_id", sa.Integer(), nullable=False),
        sa.Column("project_id", sa.Integer(), nullable=False),
        sa.Column("config_id", sa.Integer(), nullable=False),
        sa.Column("version_id", sa.Integer(), nullable=False),
        sa.Column("note", sa.String(length=200), nullable=True),
        sa.Column("execution_duration", sa.Float(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("snapshot_json", sa.Text(), nullable=False),
        sa.ForeignKeyConstraint(["project_id"], ["projects.project_id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["config_id"], ["project_configs.config_id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["version_id"], ["project_versions.version_id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("analysis_id"),
    )
    op.create_index("ix_analyses_project_id", "analyses", ["project_id"])
    op.create_index("ix_analyses_config_id", "analyses", ["config_id"])
    op.create_index("ix_analyses_version_id", "analyses", ["version_id"])

    op.create_table(
        "artifacts",
        sa.Column("artifact_id", sa.Integer(), nullable=False),
        sa.Column("version_id", sa.Integer(), nullable=False),
        sa.Column("name", sa.String(length=255), nullable=False),
        sa.Column("artifact_type", sa.String(length=50), nullable=False),
        sa.Column("role", sa.String(length=20), nullable=False),
        sa.Column("origin", sa.String(length=20), nullable=False),
        sa.Column("ref", sa.String(length=255), nullable=True),
        sa.Column("file_count", sa.Integer(), nullable=False),
        sa.Column("byte_size", sa.Integer(), nullable=False),
        sa.Column("uploaded_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["version_id"], ["project_versions.version_id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("artifact_id"),
    )
    op.create_index("ix_artifacts_version_id", "artifacts", ["version_id"])

    op.create_table(
        "artifact_files",
        sa.Column("file_id", sa.Integer(), nullable=False),
        sa.Column("artifact_id", sa.Integer(), nullable=False),
        sa.Column("relative_path", sa.String(length=500), nullable=False),
        sa.Column("sha256", sa.String(length=64), nullable=False),
        sa.Column("byte_size", sa.Integer(), nullable=False),
        sa.ForeignKeyConstraint(["artifact_id"], ["artifacts.artifact_id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("file_id"),
    )
    op.create_index("ix_artifact_files_artifact_id", "artifact_files", ["artifact_id"])
    op.create_index("ix_artifact_files_sha256", "artifact_files", ["sha256"])

    op.create_table(
        "trace_links",
        sa.Column("trace_id", sa.Integer(), nullable=False),
        sa.Column("analysis_id", sa.Integer(), nullable=False),
        sa.Column("source_id", sa.String(length=500), nullable=False),
        sa.Column("source_content", sa.Text(), nullable=True),
        sa.Column("target_id", sa.String(length=500), nullable=False),
        sa.Column("target_content", sa.Text(), nullable=True),
        sa.Column("similarity_score", sa.Float(), nullable=False),
        sa.Column("confidence_level", sa.String(length=20), nullable=False),
        sa.Column("explanation", sa.Text(), nullable=True),
        sa.ForeignKeyConstraint(["analysis_id"], ["analyses.analysis_id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("trace_id"),
    )
    op.create_index("ix_trace_links_analysis_id", "trace_links", ["analysis_id"])

    op.create_table(
        "element_links",
        sa.Column("element_link_id", sa.Integer(), nullable=False),
        sa.Column("config_id", sa.Integer(), nullable=False),
        sa.Column("source_identifier", sa.String(length=500), nullable=False),
        sa.Column("target_identifier", sa.String(length=500), nullable=False),
        sa.ForeignKeyConstraint(["config_id"], ["project_configs.config_id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("element_link_id"),
        sa.UniqueConstraint("config_id", "source_identifier", "target_identifier"),
    )
    op.create_index("ix_element_links_config_id", "element_links", ["config_id"])

    op.create_table(
        "jobs",
        sa.Column("job_id", sa.Integer(), nullable=False),
        sa.Column("user_id", sa.Integer(), nullable=True),
        sa.Column("token", sa.String(length=64), nullable=False),
        sa.Column("project_id", sa.Integer(), nullable=True),
        sa.Column("config_id", sa.Integer(), nullable=True),
        sa.Column("kind", sa.String(length=50), nullable=False),
        sa.Column("state", sa.String(length=20), nullable=False),
        sa.Column("stage", sa.String(length=200), nullable=True),
        sa.Column("progress_current", sa.Integer(), nullable=False),
        sa.Column("progress_total", sa.Integer(), nullable=False),
        sa.Column("result_json", sa.Text(), nullable=True),
        sa.Column("error", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(["user_id"], ["users.user_id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["project_id"], ["projects.project_id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["config_id"], ["project_configs.config_id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("job_id"),
    )
    op.create_index("ix_jobs_user_id", "jobs", ["user_id"])
    op.create_index("ix_jobs_token", "jobs", ["token"])
    op.create_index("ix_jobs_project_id", "jobs", ["project_id"])
    op.create_index("ix_jobs_config_id", "jobs", ["config_id"])
    op.create_index("ix_jobs_state", "jobs", ["state"])


def downgrade() -> None:
    # The old shape cannot be rebuilt from this one: nothing here says which
    # project-wide source or version a run would have belonged to. A database
    # that needs the old shape back is restored from a dump instead.
    raise NotImplementedError("This migration resets the project tables and cannot be undone.")
