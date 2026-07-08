"""Integration test for the Energy-Charts upsert repository (needs a live Postgres).

Proves idempotency on the natural key and first_seen_at immutability, using a sentinel
production_type + far-future timestamp deleted in ``finally``.
"""

from __future__ import annotations

import datetime as dt

import pytest
from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from src.db.models import EnergyChartsPower
from src.db.repositories.energy_charts import upsert_power_observations
from src.ingestion.sources.energy_charts import PowerObservation

pytestmark = pytest.mark.integration

_SENTINEL_TYPE = "ZZ_TEST_SENTINEL"
_SENTINEL_TS = dt.datetime(2099, 1, 1, 0, 0, tzinfo=dt.UTC)


def _sample(value_mw: float) -> list[PowerObservation]:
    return [
        PowerObservation(
            country="es",
            production_type=_SENTINEL_TYPE,
            ts_utc=_SENTINEL_TS,
            resolution_minutes=15,
            value_mw=value_mw,
        )
    ]


def _load(session: Session) -> EnergyChartsPower | None:
    session.expire_all()
    stmt = select(EnergyChartsPower).where(EnergyChartsPower.production_type == _SENTINEL_TYPE)
    return session.execute(stmt).scalars().one_or_none()


def test_upsert_is_idempotent_and_preserves_first_seen_at(pg_session: Session) -> None:
    try:
        written = upsert_power_observations(pg_session, _sample(1000.0), "ec:test:v1")
        pg_session.commit()
        assert written == 1

        before = _load(pg_session)
        assert before is not None
        assert before.value_mw == 1000.0
        first_seen = before.first_seen_at
        last_seen_before = before.last_seen_at

        upsert_power_observations(pg_session, _sample(1100.0), "ec:test:v2")
        pg_session.commit()

        after = _load(pg_session)
        assert after is not None
        assert after.value_mw == 1100.0  # revised
        assert after.source_ref == "ec:test:v2"
        assert after.first_seen_at == first_seen  # frozen
        assert after.last_seen_at > last_seen_before  # strictly advanced
    finally:
        pg_session.rollback()
        pg_session.execute(
            delete(EnergyChartsPower).where(EnergyChartsPower.production_type == _SENTINEL_TYPE)
        )
        pg_session.commit()
