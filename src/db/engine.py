"""Database engine and session factory (psycopg 3 driver, Neon-friendly)."""

from __future__ import annotations

from sqlalchemy import Engine, create_engine
from sqlalchemy.orm import Session, sessionmaker

from src.config import get_settings


def to_sqlalchemy_url(url: str) -> str:
    """Force the psycopg (v3) driver: SQLAlchemy defaults ``postgresql://`` to psycopg2."""
    if url.startswith("postgresql://"):
        return url.replace("postgresql://", "postgresql+psycopg://", 1)
    return url


def make_engine(url: str | None = None, *, pooled: bool = True) -> Engine:
    """Create an Engine. `pooled` picks DATABASE_URL vs DATABASE_URL_DIRECT when `url` is None.

    ``pool_pre_ping`` transparently absorbs Neon's scale-to-zero cold starts.
    """
    settings = get_settings()
    resolved = url or (settings.database_url if pooled else settings.database_url_direct)
    if not resolved:
        raise RuntimeError(
            "No database URL configured — set DATABASE_URL / DATABASE_URL_DIRECT in .env."
        )
    return create_engine(to_sqlalchemy_url(resolved), pool_pre_ping=True)


def make_session_factory(engine: Engine) -> sessionmaker[Session]:
    """A session factory bound to `engine`."""
    return sessionmaker(bind=engine, expire_on_commit=False)
