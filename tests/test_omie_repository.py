"""Integration test for the OMIE upsert repository (needs a live Postgres).

Proves the two non-negotiable properties of the raw layer:
  * idempotency on the natural key (re-ingesting never duplicates rows), and
  * ``first_seen_at`` immutability — the publication-time proxy survives every upsert,
    while the corrected price, source file and ``last_seen_at`` do update.

The test writes two rows keyed on a far-future sentinel timestamp that real ingestion
never produces, and deletes them in a ``finally`` so the table is left untouched.
"""

from __future__ import annotations

import datetime as dt

import pytest
from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from src.db.models import OmiePrice
from src.db.repositories.omie import upsert_omie_prices
from src.ingestion.sources.omie import MarginalPrice

pytestmark = pytest.mark.integration

# Sentinel key: year 2099 is never a real OMIE market day, so the test is self-isolating.
_SENTINEL_TS = dt.datetime(2099, 1, 1, 0, 0, tzinfo=dt.UTC)
_SENTINEL_DATE = dt.date(2099, 1, 1)


def _sample(price_pt: float, price_es: float) -> list[MarginalPrice]:
    return [
        MarginalPrice(
            ts_utc=_SENTINEL_TS,
            market_date=_SENTINEL_DATE,
            period=1,
            resolution_minutes=60,
            price_pt=price_pt,
            price_es=price_es,
        )
    ]


def _load(session: Session) -> dict[str, OmiePrice]:
    # Core-level upsert bypasses the identity map, and the factory keeps objects unexpired
    # after commit — so expire first to read the row's true DB state, not a cached copy.
    session.expire_all()
    stmt = select(OmiePrice).where(OmiePrice.ts_utc == _SENTINEL_TS)
    return {r.zone: r for r in session.execute(stmt).scalars().all()}


def test_upsert_is_idempotent_and_preserves_first_seen_at(pg_session: Session) -> None:
    try:
        # First ingestion: one period -> two rows (PT, ES).
        written = upsert_omie_prices(pg_session, _sample(50.0, 40.0), "marginalpdbc_test_v1.1")
        pg_session.commit()
        assert written == 2

        before = _load(pg_session)
        assert set(before) == {"PT", "ES"}
        assert before["PT"].price_eur_mwh == 50.0
        assert before["ES"].price_eur_mwh == 40.0
        # Capture scalars, not ORM objects: the identity map returns the SAME objects on
        # the next _load, so attribute reads after expire would see post-update values.
        first_seen = {zone: row.first_seen_at for zone, row in before.items()}
        last_seen_before = {zone: row.last_seen_at for zone, row in before.items()}

        # Re-ingestion (a corrected publication): same key, new prices + new source file.
        upsert_omie_prices(pg_session, _sample(55.0, 45.0), "marginalpdbc_test_v2.1")
        pg_session.commit()

        after = _load(pg_session)
        # Idempotent: still exactly the two rows, no duplicates.
        assert set(after) == {"PT", "ES"}
        # Mutable fields updated.
        assert after["PT"].price_eur_mwh == 55.0
        assert after["ES"].price_eur_mwh == 45.0
        assert {row.source_file for row in after.values()} == {"marginalpdbc_test_v2.1"}
        # first_seen_at frozen; last_seen_at STRICTLY advanced (>= would pass even if the
        # DO UPDATE stopped touching it, since both default to the same now() on INSERT).
        for zone, row in after.items():
            assert row.first_seen_at == first_seen[zone]
            assert row.last_seen_at > last_seen_before[zone]
    finally:
        # A failed assertion may leave the session in an aborted transaction; clear it so
        # the sentinel cleanup always runs instead of masking the original failure.
        pg_session.rollback()
        pg_session.execute(delete(OmiePrice).where(OmiePrice.ts_utc == _SENTINEL_TS))
        pg_session.commit()
