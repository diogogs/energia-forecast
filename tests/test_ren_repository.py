"""Integration test for the REN upsert repository (needs a live Postgres).

Proves idempotency on the natural key and ``first_seen_at`` immutability, using a sentinel
series name + far-future timestamp that real ingestion never produces, deleted in ``finally``.
"""

from __future__ import annotations

import datetime as dt

import pytest
from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from src.db.models import RenRealised
from src.db.repositories.ren import upsert_ren_observations
from src.ingestion.sources.ren import RenObservation

pytestmark = pytest.mark.integration

_SENTINEL_SERIES = "ZZ_TEST_SENTINEL"
_SENTINEL_TS = dt.datetime(2099, 1, 1, 0, 0, tzinfo=dt.UTC)


def _sample(value_mw: float) -> list[RenObservation]:
    return [
        RenObservation(
            series_name=_SENTINEL_SERIES,
            ts_utc=_SENTINEL_TS,
            resolution_minutes=15,
            value_mw=value_mw,
            local_date=dt.date(2099, 1, 1),
            period=1,
        )
    ]


def _load(session: Session) -> RenRealised | None:
    session.expire_all()  # Core upsert bypasses the identity map; read true DB state.
    stmt = select(RenRealised).where(RenRealised.series_name == _SENTINEL_SERIES)
    return session.execute(stmt).scalars().one_or_none()


def test_upsert_is_idempotent_and_preserves_first_seen_at(pg_session: Session) -> None:
    try:
        written = upsert_ren_observations(pg_session, _sample(500.0), "ren:test:v1")
        pg_session.commit()
        assert written == 1

        before = _load(pg_session)
        assert before is not None
        assert before.value_mw == 500.0
        # Scalars, not the ORM object — the identity map hands back the same instance later.
        first_seen = before.first_seen_at
        last_seen_before = before.last_seen_at

        # Re-ingestion (a revised value + new provenance): same key.
        upsert_ren_observations(pg_session, _sample(555.0), "ren:test:v2")
        pg_session.commit()

        after = _load(pg_session)
        assert after is not None
        assert after.value_mw == 555.0  # revised
        assert after.source_ref == "ren:test:v2"  # provenance updated
        assert after.first_seen_at == first_seen  # frozen
        assert after.last_seen_at > last_seen_before  # strictly advanced by the DO UPDATE
    finally:
        # Clear any aborted-transaction state so the sentinel cleanup always runs.
        pg_session.rollback()
        pg_session.execute(delete(RenRealised).where(RenRealised.series_name == _SENTINEL_SERIES))
        pg_session.commit()
