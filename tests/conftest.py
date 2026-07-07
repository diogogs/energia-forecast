"""Shared pytest fixtures.

Integration tests (marker ``integration``) need a live Postgres. They read the
configured direct URL via Settings, so they run against the same Neon instance
locally and against the CI service container — and skip cleanly when neither is set.
"""

from __future__ import annotations

from collections.abc import Iterator

import pytest
from sqlalchemy import Engine
from sqlalchemy.orm import Session

from src.config import get_settings
from src.db.engine import make_engine, make_session_factory


@pytest.fixture(scope="session")
def pg_engine() -> Iterator[Engine]:
    """Engine for integration tests; skips the test if no database is configured."""
    settings = get_settings()
    url = settings.database_url_direct or settings.database_url
    if not url:
        pytest.skip("no DATABASE_URL(_DIRECT) configured — skipping Postgres integration test")
    engine = make_engine(url)
    try:
        yield engine
    finally:
        engine.dispose()


@pytest.fixture
def pg_session(pg_engine: Engine) -> Iterator[Session]:
    """A fresh session per test, committed/rolled back by the test itself."""
    factory = make_session_factory(pg_engine)
    with factory() as session:
        yield session
