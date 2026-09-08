"""project versions, configs, sources and the living graph

Revision ID: b1c4e7a92f30
Revises: 86cf3c94ca31
Create Date: 2026-08-25 11:04:12.508311

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'b1c4e7a92f30'
down_revision: Union[str, Sequence[str], None] = '86cf3c94ca31'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.create_table(
        'project_sources',
        sa.Column('source_id', sa.Integer(), nullable=False),
        sa.Column('project_id', sa.Integer(), nullable=False),
        sa.Column('kind', sa.String(length=50), nullable=False),
        sa.Column('name', sa.String(length=255), nullable=False),
        sa.Column('origin', sa.String(length=20), nullable=False),
        sa.Column('location', sa.String(length=255), nullable=True),
        sa.Column('branch', sa.String(length=255), nullable=True),
        sa.Column('last_sync_ref', sa.String(length=255), nullable=True),
        sa.Column('last_synced_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(['project_id'], ['projects.project_id'], ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('source_id'),
    )
    op.create_index('ix_project_sources_project_id', 'project_sources', ['project_id'])

    op.create_table(
        'project_versions',
        sa.Column('version_id', sa.Integer(), nullable=False),
        sa.Column('project_id', sa.Integer(), nullable=False),
        sa.Column('version_number', sa.Integer(), nullable=False),
        sa.Column('note', sa.String(length=200), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(['project_id'], ['projects.project_id'], ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('version_id'),
        sa.UniqueConstraint('project_id', 'version_number'),
    )
    op.create_index('ix_project_versions_project_id', 'project_versions', ['project_id'])

    op.create_table(
        'version_sources',
        sa.Column('version_id', sa.Integer(), nullable=False),
        sa.Column('source_id', sa.Integer(), nullable=False),
        sa.Column('ref', sa.String(length=255), nullable=False),
        sa.ForeignKeyConstraint(
            ['version_id'], ['project_versions.version_id'], ondelete='CASCADE'
        ),
        sa.ForeignKeyConstraint(
            ['source_id'], ['project_sources.source_id'], ondelete='CASCADE'
        ),
        sa.PrimaryKeyConstraint('version_id', 'source_id'),
    )

    op.create_table(
        'project_configs',
        sa.Column('config_id', sa.Integer(), nullable=False),
        sa.Column('project_id', sa.Integer(), nullable=False),
        sa.Column('config_key', sa.String(length=16), nullable=False),
        sa.Column('is_default', sa.Boolean(), nullable=False),
        sa.Column('source_preprocessor', sa.String(length=50), nullable=False),
        sa.Column('target_preprocessor', sa.String(length=50), nullable=False),
        sa.Column('source_output_level', sa.String(length=50), nullable=True),
        sa.Column('target_output_level', sa.String(length=50), nullable=True),
        sa.Column('top_k', sa.Integer(), nullable=False),
        sa.Column('dependency_expansion_depth', sa.Integer(), nullable=False),
        sa.Column('classifier_type', sa.String(length=50), nullable=False),
        sa.Column('summarize_elements', sa.Boolean(), nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(['project_id'], ['projects.project_id'], ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('config_id'),
        sa.UniqueConstraint('project_id', 'config_key'),
    )
    op.create_index('ix_project_configs_project_id', 'project_configs', ['project_id'])

    op.create_table(
        'graph_nodes',
        sa.Column('node_id', sa.Integer(), nullable=False),
        sa.Column('config_id', sa.Integer(), nullable=False),
        sa.Column('kind', sa.String(length=50), nullable=False),
        sa.Column('identifier', sa.String(length=500), nullable=False),
        sa.Column('content_hash', sa.String(length=64), nullable=False),
        sa.Column('level', sa.String(length=50), nullable=False),
        sa.Column('parent_identifier', sa.String(length=500), nullable=True),
        sa.Column('first_seen_version_id', sa.Integer(), nullable=True),
        sa.Column('last_seen_version_id', sa.Integer(), nullable=True),
        sa.Column('is_active', sa.Boolean(), nullable=False),
        sa.ForeignKeyConstraint(
            ['config_id'], ['project_configs.config_id'], ondelete='CASCADE'
        ),
        sa.ForeignKeyConstraint(
            ['first_seen_version_id'], ['project_versions.version_id'], ondelete='SET NULL'
        ),
        sa.ForeignKeyConstraint(
            ['last_seen_version_id'], ['project_versions.version_id'], ondelete='SET NULL'
        ),
        sa.PrimaryKeyConstraint('node_id'),
        sa.UniqueConstraint('config_id', 'kind', 'identifier'),
    )
    op.create_index('ix_graph_nodes_config_id', 'graph_nodes', ['config_id'])
    # The differ asks "is anything here holding this content?" once per element
    # of every changed file, which is also how a rename is matched back.
    op.create_index('ix_graph_nodes_content_hash', 'graph_nodes', ['content_hash'])

    op.create_table(
        'graph_edges',
        sa.Column('edge_id', sa.Integer(), nullable=False),
        sa.Column('config_id', sa.Integer(), nullable=False),
        sa.Column('from_node_id', sa.Integer(), nullable=False),
        sa.Column('to_node_id', sa.Integer(), nullable=False),
        sa.Column('from_kind', sa.String(length=50), nullable=False),
        sa.Column('to_kind', sa.String(length=50), nullable=False),
        sa.Column('confidence', sa.Float(), nullable=False),
        sa.Column('confidence_level', sa.String(length=20), nullable=False),
        sa.Column('explanation', sa.Text(), nullable=True),
        sa.Column('status', sa.String(length=20), nullable=False),
        sa.Column('first_seen_version_id', sa.Integer(), nullable=True),
        sa.Column('last_verified_version_id', sa.Integer(), nullable=True),
        sa.ForeignKeyConstraint(
            ['config_id'], ['project_configs.config_id'], ondelete='CASCADE'
        ),
        sa.ForeignKeyConstraint(['from_node_id'], ['graph_nodes.node_id'], ondelete='CASCADE'),
        sa.ForeignKeyConstraint(['to_node_id'], ['graph_nodes.node_id'], ondelete='CASCADE'),
        sa.ForeignKeyConstraint(
            ['first_seen_version_id'], ['project_versions.version_id'], ondelete='SET NULL'
        ),
        sa.ForeignKeyConstraint(
            ['last_verified_version_id'], ['project_versions.version_id'], ondelete='SET NULL'
        ),
        sa.PrimaryKeyConstraint('edge_id'),
        sa.UniqueConstraint('config_id', 'from_node_id', 'to_node_id'),
    )
    op.create_index('ix_graph_edges_config_id', 'graph_edges', ['config_id'])
    op.create_index('ix_graph_edges_from_node_id', 'graph_edges', ['from_node_id'])
    op.create_index('ix_graph_edges_to_node_id', 'graph_edges', ['to_node_id'])

    op.create_table(
        'classification_cache',
        sa.Column('namespace', sa.String(length=200), nullable=False),
        sa.Column('pair_hash', sa.String(length=64), nullable=False),
        sa.Column('confidence', sa.Float(), nullable=False),
        sa.Column('explanation', sa.Text(), nullable=True),
        sa.Column('updated_at', sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint('namespace', 'pair_hash'),
    )

    # Existing rows predate all of this, so every added column is nullable and
    # stays null rather than being back-filled with a guess.
    op.add_column('analyses', sa.Column('version_id', sa.Integer(), nullable=True))
    op.add_column('analyses', sa.Column('config_id', sa.Integer(), nullable=True))
    op.create_index('ix_analyses_version_id', 'analyses', ['version_id'])
    op.create_index('ix_analyses_config_id', 'analyses', ['config_id'])
    op.create_foreign_key(
        'fk_analyses_version_id', 'analyses', 'project_versions',
        ['version_id'], ['version_id'], ondelete='SET NULL',
    )
    op.create_foreign_key(
        'fk_analyses_config_id', 'analyses', 'project_configs',
        ['config_id'], ['config_id'], ondelete='SET NULL',
    )

    op.add_column('artifacts', sa.Column('source_id', sa.Integer(), nullable=True))
    op.create_index('ix_artifacts_source_id', 'artifacts', ['source_id'])
    op.create_foreign_key(
        'fk_artifacts_source_id', 'artifacts', 'project_sources',
        ['source_id'], ['source_id'], ondelete='SET NULL',
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_constraint('fk_artifacts_source_id', 'artifacts', type_='foreignkey')
    op.drop_index('ix_artifacts_source_id', table_name='artifacts')
    op.drop_column('artifacts', 'source_id')

    op.drop_constraint('fk_analyses_config_id', 'analyses', type_='foreignkey')
    op.drop_constraint('fk_analyses_version_id', 'analyses', type_='foreignkey')
    op.drop_index('ix_analyses_config_id', table_name='analyses')
    op.drop_index('ix_analyses_version_id', table_name='analyses')
    op.drop_column('analyses', 'config_id')
    op.drop_column('analyses', 'version_id')

    op.drop_table('classification_cache')
    op.drop_table('graph_edges')
    op.drop_table('graph_nodes')
    op.drop_table('project_configs')
    op.drop_table('version_sources')
    op.drop_table('project_versions')
    op.drop_table('project_sources')
