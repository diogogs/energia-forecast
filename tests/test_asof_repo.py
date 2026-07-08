"""Integration + anti-leakage tests for the AsOfRepo (needs the live backfilled Postgres).

These assert temporal STRUCTURE (data-value-independent), so they stay valid as data grows:
  * legal consumption ends before t_issue (day D is incomplete), and nothing leaks;
  * legal day-ahead price legitimately includes day-D hours *after* t_issue, but never a
    delivery-day (D+1) price.
"""

from __future__ import annotations

import datetime as dt

import pytest
from sqlalchemy.orm import Session

from src.features import temporal
from src.features.asof_repo import AsOfRepo

pytestmark = pytest.mark.integration

_ISSUE = dt.date(2024, 6, 10)  # a normal summer day inside the backfill window


@pytest.mark.leakage
def test_consumption_is_legal_as_of_t_issue(pg_session: Session) -> None:
    t_issue = temporal.t_issue_for(_ISSUE)
    cons = AsOfRepo(pg_session).hourly_consumption(t_issue)

    assert not cons.empty
    # Day D is incomplete at 07:00 -> the series ends before t_issue.
    assert cons.index[-1].to_pydatetime() < t_issue
    # Every hour's modelled publication is <= t_issue (no leakage).
    assert all(temporal.ren_published_at(ts.to_pydatetime()) <= t_issue for ts in cons.index)
    # Sanity: plausible PT hourly demand (MW).
    assert 1000 < cons.min() and cons.max() < 15000


@pytest.mark.leakage
def test_price_is_legal_and_includes_published_day_ahead(pg_session: Session) -> None:
    t_issue = temporal.t_issue_for(_ISSUE)
    price = AsOfRepo(pg_session).hourly_price("PT", t_issue)

    assert not price.empty
    # Unlike consumption, legal day-ahead prices extend past t_issue (day-D delivery hours were
    # published the day before) ...
    assert price.index[-1].to_pydatetime() > t_issue
    # ... but never into the delivery day D+1 (its price is published on D, after t_issue).
    delivery_cet_start = dt.datetime.combine(
        temporal.delivery_date_for(_ISSUE), dt.time(), tzinfo=temporal.CET
    ).astimezone(dt.UTC)
    assert price.index[-1].to_pydatetime() < delivery_cet_start


def test_empty_when_before_any_data(pg_session: Session) -> None:
    # A t_issue before the backfill window yields an empty (not erroring) series.
    early = temporal.t_issue_for(dt.date(2019, 1, 1))
    assert AsOfRepo(pg_session).hourly_consumption(early).empty
