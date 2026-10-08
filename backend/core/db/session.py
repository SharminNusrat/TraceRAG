"""Engine, session factory and the request-scoped session dependency."""

from pathlib import Path
from sqlalchemy import create_engine
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker
from config import settings


class Base(DeclarativeBase):
    pass


if not settings.database_url:
    raise RuntimeError(
        "DATABASE_URL is not set. Point it at the PostgreSQL database in .env "
        "before starting the server."
    )

engine = create_engine(settings.database_url, pool_pre_ping=True)
SessionLocal = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)


BACKEND_DIR = Path(__file__).resolve().parents[2]


def init_db() -> None:
    """Bring the database up to the newest migration."""
    from alembic import command
    from alembic.config import Config

    config = Config(str(BACKEND_DIR / "alembic.ini"))
    config.set_main_option("script_location", str(BACKEND_DIR / "migrations"))
    config.attributes["configure_logger"] = False
    command.upgrade(config, "head")


def get_db():
    """FastAPI dependency: one session per request, always closed."""
    db: Session = SessionLocal()
    try:
        yield db
    finally:
        db.close()
