"""Tests for the temporal primitives — the leakage-critical core.

These pin the issue/delivery grid (DST-correct) and the modelled publication times that decide
feature legality. The `leakage` marker cases assert the non-negotiable rule: at 07:00 UTC of D,
day-D consumption is NOT yet published and D+1 prices are NOT yet published.
"""

from __future__ import annotations

import datetime as dt
from itertools import pairwise

import pytest

from src.features.temporal import (
    delivery_date_for,
    delivery_hours_utc,
    energy_charts_published_at,
    omie_published_at,
    openmeteo_published_at,
    ren_published_at,
    t_issue_for,
)


def test_t_issue_is_fixed_0700_utc() -> None:
    ti = t_issue_for(dt.date(2024, 6, 10))
    assert ti == dt.datetime(2024, 6, 10, 7, 0, tzinfo=dt.UTC)
    assert delivery_date_for(dt.date(2024, 6, 10)) == dt.date(2024, 6, 11)


@pytest.mark.parametrize(
    ("delivery", "n_hours", "first_utc"),
    [
        (dt.date(2024, 6, 11), 24, dt.datetime(2024, 6, 10, 22, tzinfo=dt.UTC)),  # CEST = UTC+2
        (dt.date(2024, 1, 11), 24, dt.datetime(2024, 1, 10, 23, tzinfo=dt.UTC)),  # CET = UTC+1
        (dt.date(2024, 3, 31), 23, dt.datetime(2024, 3, 30, 23, tzinfo=dt.UTC)),  # spring-forward
        (dt.date(2024, 10, 27), 25, dt.datetime(2024, 10, 26, 22, tzinfo=dt.UTC)),  # fall-back
    ],
)
def test_delivery_hours_are_dst_correct(
    delivery: dt.date, n_hours: int, first_utc: dt.datetime
) -> None:
    hours = delivery_hours_utc(delivery)
    assert len(hours) == n_hours
    assert hours[0] == first_utc
    assert all(b - a == dt.timedelta(hours=1) for a, b in pairwise(hours))
    # The span covers exactly the CET civil day.
    assert hours[-1] + dt.timedelta(hours=1) - hours[0] == dt.timedelta(hours=n_hours)


def test_omie_publication_is_day_before_1300_cet() -> None:
    # Delivery day 2024-06-11 prices are published 2024-06-10 13:00 CEST = 11:00 UTC.
    assert omie_published_at(dt.date(2024, 6, 11)) == dt.datetime(2024, 6, 10, 11, tzinfo=dt.UTC)
    # Winter: 13:00 CET = 12:00 UTC.
    assert omie_published_at(dt.date(2024, 1, 11)) == dt.datetime(2024, 1, 10, 12, tzinfo=dt.UTC)


def test_ren_publication_is_next_lisbon_midnight() -> None:
    # A summer valid time 2024-06-10 15:00 UTC -> next Lisbon midnight = 2024-06-11 00:00 WEST
    # = 2024-06-10 23:00 UTC.
    assert ren_published_at(dt.datetime(2024, 6, 10, 15, tzinfo=dt.UTC)) == dt.datetime(
        2024, 6, 10, 23, tzinfo=dt.UTC
    )


def test_openmeteo_publication_tracks_run_date() -> None:
    # A valid time on 2024-06-12, lead 1 -> run from 2024-06-11 00Z, available 06:00 UTC.
    t = dt.datetime(2024, 6, 12, 18, tzinfo=dt.UTC)
    assert openmeteo_published_at(t, 1) == dt.datetime(2024, 6, 11, 6, tzinfo=dt.UTC)
    assert openmeteo_published_at(t, 2) == dt.datetime(2024, 6, 10, 6, tzinfo=dt.UTC)


@pytest.mark.leakage
def test_day_d_consumption_is_unpublished_at_t_issue() -> None:
    # Issue day D = 2024-06-10, t_issue = 07:00 UTC. A consumption value at D 06:00 UTC must
    # NOT be considered published yet (day D is incomplete) — this guards the 24h-lag leak.
    t_issue = t_issue_for(dt.date(2024, 6, 10))
    d_morning = dt.datetime(2024, 6, 10, 6, tzinfo=dt.UTC)
    assert ren_published_at(d_morning) > t_issue  # published only next midnight
    # Whereas D-1 consumption IS published by t_issue.
    d_minus_1 = dt.datetime(2024, 6, 9, 12, tzinfo=dt.UTC)
    assert ren_published_at(d_minus_1) <= t_issue


@pytest.mark.leakage
def test_delivery_day_prices_are_unpublished_at_t_issue() -> None:
    # Forecasting D+1 price at 07:00 of D: the D+1 day-ahead price is published on D ~13:00 CET,
    # AFTER t_issue — so it cannot be a feature (only <=24h-lag, i.e. day-D prices, are legal).
    issue = dt.date(2024, 6, 10)
    t_issue = t_issue_for(issue)
    delivery = delivery_date_for(issue)  # 2024-06-11
    assert omie_published_at(delivery) > t_issue  # D+1 price not yet public
    assert omie_published_at(issue) <= t_issue  # day-D price is public (published D-1)


@pytest.mark.leakage
def test_weather_lead_selection_at_t_issue() -> None:
    # For D+1 delivery hours at t_issue of D: the D-00Z run (lead 1) is available (~06:00 D),
    # and the D-1 run (lead 2) too; a hypothetical D+1-00Z run (lead 0) would NOT be.
    issue = dt.date(2024, 6, 10)
    t_issue = t_issue_for(issue)
    delivery_hour = dt.datetime(2024, 6, 11, 18, tzinfo=dt.UTC)
    assert openmeteo_published_at(delivery_hour, 1) <= t_issue  # run from D (2024-06-10 06:00)
    assert openmeteo_published_at(delivery_hour, 2) <= t_issue  # run from D-1
    assert openmeteo_published_at(delivery_hour, 0) > t_issue  # run from D+1 — future


def test_energy_charts_publication_next_utc_midnight() -> None:
    assert energy_charts_published_at(dt.datetime(2024, 6, 10, 15, tzinfo=dt.UTC)) == dt.datetime(
        2024, 6, 11, 0, tzinfo=dt.UTC
    )
