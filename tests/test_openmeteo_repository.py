"""Integration test for the Open-Meteo upsert repository (needs a live Postgres).

Proves idempotency on the natural key and first_seen_at immutability, using a sentinel
location + far-future timestamp deleted in ``finally``.
"""

from __future__ import annotations

import datetime as dt

import pytest
from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from src.db.models import OpenMeteoForecast
from src.db.repositories.openmeteo import upsert_forecasts
from src.ingestion.sources.openmeteo import ForecastObservation

pytestmark = pytest.mark.integration

_SENTINEL_LOCATION = "zz_test"
_SENTINEL_TS = dt.datetime(2099, 1, 1, 0, 0, tzinfo=dt.UTC)


def _sample(value: float) -> list[ForecastObservation]:
    return [
        ForecastObservation(
            location=_SENTINEL_LOCATION,
            variable="temperature_2m",
            lead_days=1,
            ts_utc=_SENTINEL_TS,
            value=value,
            unit="°C",
        )
    ]


def _load(session: Session) -> OpenMeteoForecast | None:
    session.expire_all()
    stmt = select(OpenMeteoForecast).where(OpenMeteoForecast.location == _SENTINEL_LOCATION)
    return session.execute(stmt).scalars().one_or_none()


def test_upsert_is_idempotent_and_preserves_first_seen_at(pg_session: Session) -> None:
    try:
        written = upsert_forecasts(pg_session, _sample(15.0), "openmeteo:test:v1")
        pg_session.commit()
        assert written == 1

        before = _load(pg_session)
        assert before is not None
        assert before.value == 15.0
        first_seen = before.first_seen_at
        last_seen_before = before.last_seen_at

        upsert_forecasts(pg_session, _sample(16.5), "openmeteo:test:v2")
        pg_session.commit()

        after = _load(pg_session)
        assert after is not None
        assert after.value == 16.5  # revised
        assert after.source_ref == "openmeteo:test:v2"
        assert after.first_seen_at == first_seen  # frozen
        assert after.last_seen_at > last_seen_before  # strictly advanced
    finally:
        pg_session.rollback()
        pg_session.execute(
            delete(OpenMeteoForecast).where(OpenMeteoForecast.location == _SENTINEL_LOCATION)
        )
        pg_session.commit()
