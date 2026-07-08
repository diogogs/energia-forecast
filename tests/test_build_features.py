"""Tests for build_features + baselines (network-free, via a stub repo).

A stub AsOfRepo returns a known legal consumption series (value = the UTC hour), so lag lookups
are exactly verifiable and the leakage structure is asserted without a DB.
"""

from __future__ import annotations

import datetime as dt

import pandas as pd
import pytest

from src.features import temporal
from src.features.build_features import CONSUMPTION_LAGS_H, build_consumption_features
from src.models import baselines


class _StubRepo:
    """Minimal AsOfRepo stand-in exposing only what build_features uses."""

    def __init__(self, series: pd.Series, weather: pd.DataFrame | None = None) -> None:
        self._series = series
        self._weather = weather if weather is not None else pd.DataFrame()

    def hourly_consumption(self, t_issue: dt.datetime) -> pd.Series:
        # The stub series is already pre-t_issue by construction.
        return self._series

    def weather_forecast(
        self, t_issue: dt.datetime, target_hours: list[dt.datetime]
    ) -> pd.DataFrame:
        return (
            self._weather.reindex(pd.DatetimeIndex(target_hours))
            if not self._weather.empty
            else pd.DataFrame()
        )


def _legal_consumption(issue: dt.date) -> pd.Series:
    """Hourly UTC series ending before t_issue; value == the UTC hour (unique-enough to verify)."""
    t_issue = temporal.t_issue_for(issue)
    index = pd.date_range(
        t_issue - dt.timedelta(days=20), t_issue - dt.timedelta(hours=1), freq="h"
    )
    return pd.Series([ts.hour for ts in index], index=index, dtype="float64")


@pytest.mark.parametrize(
    ("issue", "n_hours"),
    [
        (dt.date(2024, 6, 10), 24),  # normal
        (dt.date(2024, 3, 30), 23),  # delivery 2024-03-31 spring-forward
        (dt.date(2024, 10, 26), 25),  # delivery 2024-10-27 fall-back
    ],
)
def test_feature_matrix_shape_follows_dst_delivery_day(issue: dt.date, n_hours: int) -> None:
    x = build_consumption_features(_StubRepo(_legal_consumption(issue)), issue)  # type: ignore[arg-type]
    assert len(x) == n_hours
    expected_hours = temporal.delivery_hours_utc(temporal.delivery_date_for(issue))
    assert list(x.index) == [pd.Timestamp(h) for h in expected_hours]


@pytest.mark.leakage
def test_lags_are_target_relative_and_pre_issue() -> None:
    issue = dt.date(2024, 6, 10)
    t_issue = temporal.t_issue_for(issue)
    series = _legal_consumption(issue)
    x = build_consumption_features(_StubRepo(series), issue)  # type: ignore[arg-type]

    for target_ts, row in x.iterrows():
        for lag_h in CONSUMPTION_LAGS_H:
            lagged = target_ts - dt.timedelta(hours=lag_h)
            # Every lag references data strictly before t_issue (legal) ...
            assert lagged.to_pydatetime() < t_issue
            # ... and equals the series value at exactly target - lag.
            assert row[f"cons_lag_{lag_h}h"] == series[lagged]
    # The 24h lag (leakage) is never a feature.
    assert "cons_lag_24h" not in x.columns


def test_calendar_features_follow_lisbon_time() -> None:
    # Delivery = the CET day 2025-01-01 (issue 2024-12-31). New Year's Day is a PT holiday, but
    # calendar features follow LISBON time: the CET day starts at 2024-12-31 23:00 UTC, which in
    # Lisbon (WET=UTC+0) is still 31 Dec — so that first hour is NOT the holiday, the rest are.
    issue = dt.date(2024, 12, 31)
    x = build_consumption_features(_StubRepo(_legal_consumption(issue)), issue)  # type: ignore[arg-type]
    expected = [
        ts.to_pydatetime().astimezone(temporal.LISBON).date() == dt.date(2025, 1, 1)
        for ts in x.index
    ]
    assert list(x["is_holiday"]) == expected
    assert not x["is_holiday"].iloc[0]  # the 1h PT/CET offset: first hour is still 31 Dec
    assert x["is_holiday"].iloc[1:].all()
    # A summer mid-week day is not a holiday and not a weekend.
    x2 = build_consumption_features(
        _StubRepo(_legal_consumption(dt.date(2024, 6, 11))),  # type: ignore[arg-type]
        dt.date(2024, 6, 11),
    )  # delivery Wed 2024-06-12
    assert not x2["is_holiday"].any()
    assert not x2["is_weekend"].any()


def test_weather_transforms() -> None:
    issue = dt.date(2024, 6, 10)
    hours = temporal.delivery_hours_utc(temporal.delivery_date_for(issue))
    # Canned weather: cold+windy first hour, hot+calm second, rest neutral.
    weather = pd.DataFrame(
        {
            "temperature_2m": [10.0, 30.0] + [20.0] * (len(hours) - 2),
            "wind_speed_100m": [60.0, 5.0] + [20.0] * (len(hours) - 2),  # 60 > cap 43
            "shortwave_radiation": [0.0, 800.0] + [100.0] * (len(hours) - 2),
        },
        index=pd.DatetimeIndex(hours),
    )
    x = build_consumption_features(_StubRepo(_legal_consumption(issue), weather), issue)  # type: ignore[arg-type]
    assert x["hdd"].iloc[0] == 8.0  # 18 - 10
    assert x["cdd"].iloc[0] == 0.0
    assert x["hdd"].iloc[1] == 0.0
    assert x["cdd"].iloc[1] == 9.0  # 30 - 21
    assert x["wind_cube"].iloc[0] == 43.0**3  # capped before cubing
    assert x["radiation"].iloc[1] == 800.0


def test_missing_weather_yields_nan_columns() -> None:
    issue = dt.date(2024, 6, 10)
    x = build_consumption_features(_StubRepo(_legal_consumption(issue)), issue)  # type: ignore[arg-type]
    assert {"temp", "hdd", "cdd", "wind_cube", "radiation"} <= set(x.columns)
    assert x["temp"].isna().all()  # no weather available -> NaN (LightGBM tolerates)


def test_baselines_select_the_legal_lag_columns() -> None:
    features = pd.DataFrame(
        {"cons_lag_48h": [100.0, 200.0], "cons_lag_168h": [110.0, 210.0]},
        index=pd.to_datetime(["2024-06-11T00:00Z", "2024-06-11T01:00Z"]),
    )
    assert list(baselines.persistence_consumption(features)) == [100.0, 200.0]
    assert list(baselines.seasonal_weekly_consumption(features)) == [110.0, 210.0]
