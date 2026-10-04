"""The reset migration run on a database that already holds data."""

from alembic import command
from alembic.config import Config
from alembic.script import ScriptDirectory
from sqlalchemy import create_engine, inspect, text

from config import settings
from core.db.session import BACKEND_DIR, Base, engine
from tests.recorder import case

# Made for this test and dropped at its end; never the test or development database.
SCRATCH = "tracerag_migration_test"
RESET = "a1f0c7d3e5b9"

# One row in each table, in the shape the tables had before the reset.
OLD_ROWS = [
    "INSERT INTO users (user_id, full_name, email, password_hash, created_at)"
    " VALUES (1, 'Old User', 'old@example.com', 'hash', now())",
    "INSERT INTO projects (project_id, user_id, project_name, created_at, updated_at)"
    " VALUES (1, 1, 'old project', now(), now())",
    "INSERT INTO project_configs (config_id, project_id, config_key, is_default, source_preprocessor,"
    " target_preprocessor, top_k, dependency_expansion_depth, classifier_type, summarize_elements, created_at)"
    " VALUES (1, 1, 'k', true, 'single', 'method', 10, 1, 'reasoning', false, now())",
    "INSERT INTO project_versions (version_id, project_id, version_number, created_at) VALUES (1, 1, 1, now())",
    "INSERT INTO analyses (analysis_id, project_id, source_preprocessor, target_preprocessor, top_k,"
    " dependency_expansion_depth, classifier_type, created_at, snapshot_json, summarize_elements)"
    " VALUES (1, 1, 'single', 'method', 10, 1, 'reasoning', now(), '{}', false)",
    "INSERT INTO trace_links (trace_id, analysis_id, source_id, target_id, similarity_score, confidence_level)"
    " VALUES (1, 1, 'UC1.txt', 'Auth.java::Auth::login()', 0.9, 'high')",
    "INSERT INTO embedding_cache (namespace, text_hash, embedding, updated_at)"
    " VALUES ('model', 'e1', ARRAY[0.25, 0.5], now())",
    "INSERT INTO summary_cache (namespace, text_hash, summary, updated_at) VALUES ('model', 's1', 'a summary', now())",
    "INSERT INTO classification_cache (namespace, pair_hash, linked, updated_at) VALUES ('model', 'p1', true, now())",
]
KEPT = {
    "users": "SELECT email FROM users",
    "embedding_cache": "SELECT embedding FROM embedding_cache",
    "summary_cache": "SELECT summary FROM summary_cache",
    "classification_cache": "SELECT linked FROM classification_cache",
}
PROJECT_TABLES = ("projects", "project_configs", "project_versions", "project_sources", "analyses",
                  "trace_links", "artifacts", "artifact_files", "element_links", "jobs")


@case(
    id="G-28",
    feature="Database / the reset migration on a database with data",
    level="graph",
    priority="High",
    why="The demo machine's database was made before the redesign. The reset migration must keep every account and every cached embedding, summary and verdict - they cost model calls to rebuild - and start the project tables empty in their new shape.",
    preconditions=f"A scratch database '{SCRATCH}', made for this test and dropped at its end",
    input="Migrate the scratch database to the revision before the reset, insert a user, a project with an analysis, a version and a link, and one row in each of the three caches; then migrate to head",
    expected="The database is at head. The user and the three cache rows are still there with their values. Every project table exists, is empty, and has the columns the models declare (the old graph tables are gone)",
)
def test_reset_migration_keeps_users_and_caches(monkeypatch, record):
    """Accounts and caches survive the reset migration; project tables come back empty in the new shape."""
    server = create_engine(engine.url.set(database="postgres"), isolation_level="AUTOCOMMIT")
    with server.connect() as connection:
        connection.execute(text(f'DROP DATABASE IF EXISTS "{SCRATCH}"'))
        connection.execute(text(f'CREATE DATABASE "{SCRATCH}"'))
    url = engine.url.set(database=SCRATCH)
    # migrations/env.py reads the address from settings.
    monkeypatch.setattr(settings, "database_url", url.render_as_string(hide_password=False))
    config = Config(str(BACKEND_DIR / "alembic.ini"))
    config.set_main_option("script_location", str(BACKEND_DIR / "migrations"))
    config.attributes["configure_logger"] = False
    before_reset = ScriptDirectory.from_config(config).get_revision(RESET).down_revision
    scratch = create_engine(url)
    try:
        command.upgrade(config, before_reset)
        with scratch.begin() as connection:
            for statement in OLD_ROWS:
                connection.execute(text(statement))
        command.upgrade(config, "head")

        with scratch.connect() as connection:
            at = connection.execute(text("SELECT version_num FROM alembic_version")).scalar()
            kept = {table: [list(row) for row in connection.execute(text(query))] for table, query in KEPT.items()}
            rows = {table: connection.execute(text(f'SELECT count(*) FROM "{table}"')).scalar() for table in PROJECT_TABLES}
        tables = set(inspect(scratch).get_table_names())
        wrong = [table for table in PROJECT_TABLES
                 if {c["name"] for c in inspect(scratch).get_columns(table)}
                 != {column.name for column in Base.metadata.tables[table].columns}]
    finally:
        scratch.dispose()
        with server.connect() as connection:
            connection.execute(text(f'DROP DATABASE IF EXISTS "{SCRATCH}"'))
        server.dispose()

    head = ScriptDirectory.from_config(config).get_current_head()
    record(f"migrated {before_reset} -> {at} (head {head})")
    record(f"kept: {kept}")
    record(f"project table rows after: {rows}; tables whose columns differ from the models: {wrong or 'none'}")
    record(f"old graph tables still there: {sorted(tables & {'graph_nodes', 'graph_edges', 'version_sources'}) or 'none'}")

    assert at == head
    assert kept == {
        "users": [["old@example.com"]], "embedding_cache": [[[0.25, 0.5]]],
        "summary_cache": [["a summary"]], "classification_cache": [[True]],
    }
    assert set(rows.values()) == {0}
    assert wrong == []
    assert not tables & {"graph_nodes", "graph_edges", "version_sources"}
