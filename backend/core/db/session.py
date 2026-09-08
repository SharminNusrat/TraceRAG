"""Engine, session factory and the request-scoped session dependency.

PostgreSQL specifically: the embedding cache stores a native float array, which
has no portable equivalent, so there is no SQLite fallback.
"""

from pathlib import Path
from sqlalchemy import create_engine
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker
from config import settings


class Base(DeclarativeBase):
    pass


if not settings.database_url:
    # Opening the wrong database is not a small mistake here: the artifact
    # garbage collector asks it which files are still needed, and deletes the
    # rest. Better to refuse to start than to start against nothing.
    raise RuntimeError(
        "DATABASE_URL is not set. Point it at the PostgreSQL database in .env "
        "before starting the server."
    )

engine = create_engine(settings.database_url, pool_pre_ping=True)
SessionLocal = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)


BACKEND_DIR = Path(__file__).resolve().parents[2]


def init_db() -> None:
    """Bring the database up to the newest migration."""
    # Alembic, not create_all(): create_all never alters an existing table, so
    # a changed column is silently skipped and the schema drifts. Safe here
    # because this is a single process; behind workers, run it at deploy time.
    from alembic import command
    from alembic.config import Config

    config = Config(str(BACKEND_DIR / "alembic.ini"))
    # Absolute, so startup does not depend on which directory the server was
    # launched from.
    config.set_main_option("script_location", str(BACKEND_DIR / "migrations"))
    # The logging section of alembic.ini is for the CLI. Applying it here would
    # reset the root logger to WARNING and disable every logger the application
    # had already created, leaving the server silent for the rest of its life.
    config.attributes["configure_logger"] = False
    command.upgrade(config, "head")


def get_db():
    """FastAPI dependency: one session per request, always closed."""
    db: Session = SessionLocal()
    try:
        yield db
    finally:
        db.close()
