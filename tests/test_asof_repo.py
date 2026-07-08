"""Integration + anti-leakage tests for the AsOfRepo (needs a live Postgres).

Self-seeding: rather than depend on backfilled data (absent in CI's ephemeral DB), each test
inserts sentinel rows at a far-future issue day (2099) that straddle the legality boundary, then
asserts exactly which side survives. Real data (all published long before 2099) is legal too, so
these run identically against CI's empty DB and the live Neon backfill. Sentinels are deleted in
``finally``.
"""

from __future__ import annotations

import datetime as dt

import pytest
from sqlalchemy import delete
from sqlalchemy.orm import Session

from src.db.models import OmiePrice, RenRealised
from src.features import temporal
from src.features.asof_repo import AsOfRepo

pytestmark = pytest.mark.integration

_ISSUE = dt.date(2099, 6, 10)  # far-future sentinel fold — never collides with real data
# Lisbon day 2099-06-09 (last hour) — published by its next midnight, legal at t_issue.
_CONS_LEGAL_TS = dt.datetime(2099, 6, 9, 22, 0, tzinfo=dt.UTC)
# Lisbon day 2099-06-10 (day D morning) — not consolidated until next midnight, illegal.
_CONS_LEAK_TS = dt.datetime(2099, 6, 10, 6, 0, tzinfo=dt.UTC)
# Day-D delivery hour AFTER t_issue — the day-ahead price is already published, so legal.
_PRICE_LEGAL_TS = dt.datetime(2099, 6, 10, 10, 0, tzinfo=dt.UTC)
# Delivery day D+1 — its price is published on D (after t_issue), so illegal.
_PRICE_LEAK_TS = dt.datetime(2099, 6, 11, 10, 0, tzinfo=dt.UTC)


def _seed(session: Session) -> None:
    session.add_all(
        [
            RenRealised(
                series_name="Consumption",
                ts_utc=_CONS_LEGAL_TS,
                resolution_minutes=15,
                value_mw=5000.0,
                local_date=dt.date(2099, 6, 9),
                period=1,
                source_ref="asof-test",
            ),
            RenRealised(
                series_name="Consumption",
                ts_utc=_CONS_LEAK_TS,
                resolution_minutes=15,
                value_mw=6000.0,
                local_date=dt.date(2099, 6, 10),
                period=1,
                source_ref="asof-test",
            ),
            OmiePrice(
                zone="PT",
                ts_utc=_PRICE_LEGAL_TS,
                resolution_minutes=60,
                price_eur_mwh=50.0,
                market_date=dt.date(2099, 6, 10),
                period=1,
                source_file="asof-test",
            ),
            OmiePrice(
                zone="PT",
                ts_utc=_PRICE_LEAK_TS,
                resolution_minutes=60,
                price_eur_mwh=60.0,
                market_date=dt.date(2099, 6, 11),
                period=1,
                source_file="asof-test",
            ),
        ]
    )
    session.commit()


def _cleanup(session: Session) -> None:
    session.rollback()
    session.execute(
        delete(RenRealised).where(RenRealised.ts_utc >= dt.datetime(2099, 1, 1, tzinfo=dt.UTC))
    )
    session.execute(
        delete(OmiePrice).where(OmiePrice.ts_utc >= dt.datetime(2099, 1, 1, tzinfo=dt.UTC))
    )
    session.commit()


@pytest.mark.leakage
def test_consumption_excludes_incomplete_day_d(pg_session: Session) -> None:
    try:
        _seed(pg_session)
        t_issue = temporal.t_issue_for(_ISSUE)
        cons = AsOfRepo(pg_session).hourly_consumption(t_issue)

        assert _CONS_LEGAL_TS in cons.index  # Lisbon day D-1 close: legal
        assert _CONS_LEAK_TS not in cons.index  # day D morning: excluded (incomplete)
        # Global invariant: nothing in the result is published after t_issue.
        assert all(temporal.ren_published_at(ts.to_pydatetime()) <= t_issue for ts in cons.index)
    finally:
        _cleanup(pg_session)


@pytest.mark.leakage
def test_price_includes_published_day_ahead_but_not_delivery_day(pg_session: Session) -> None:
    try:
        _seed(pg_session)
        t_issue = temporal.t_issue_for(_ISSUE)
        price = AsOfRepo(pg_session).hourly_price("PT", t_issue)

        # A day-D delivery hour after 07:00 is legal (its day-ahead price was published on D-1) ...
        assert _PRICE_LEGAL_TS in price.index
        assert _PRICE_LEGAL_TS > t_issue
        # ... but the delivery day D+1's price is not yet published at t_issue.
        assert _PRICE_LEAK_TS not in price.index
    finally:
        _cleanup(pg_session)


def test_empty_before_any_data(pg_session: Session) -> None:
    # A t_issue before all data yields an empty (not erroring) series.
    early = temporal.t_issue_for(dt.date(1990, 1, 1))
    assert AsOfRepo(pg_session).hourly_consumption(early).empty
