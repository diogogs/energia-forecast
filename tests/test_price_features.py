"""Tests for build_price_features + price baselines (network-free, via a stub price repo)."""

from __future__ import annotations

import datetime as dt

import pandas as pd
import pytest

from src.features import temporal
from src.features.build_features import PRICE_LAGS_H, build_price_features
from src.models import baselines


class _StubPriceRepo:
    """Stand-in exposing hourly_price + weather_forecast. PT price = the UTC hour; ES = PT - 5."""

    def __init__(self, issue: dt.date) -> None:
        # Legal day-ahead prices extend to the end of day D (published on D-1), not just to
        # t_issue — mirror that so the day-D lag lookups resolve.
        start = temporal.t_issue_for(issue) - dt.timedelta(days=12)
        end = temporal.delivery_hours_utc(issue)[-1]  # last hour of CET day D
        index = pd.date_range(start, end, freq="h")
        self._pt = pd.Series([ts.hour for ts in index], index=index, dtype="float64")

    def hourly_price(self, zone: str, t_issue: dt.datetime) -> pd.Series:
        return self._pt if zone == "PT" else self._pt - 5.0

    def weather_forecast(
        self, t_issue: dt.datetime, target_hours: list[dt.datetime]
    ) -> pd.DataFrame:
        return pd.DataFrame()


def test_price_lags_and_spread() -> None:
    issue = dt.date(2024, 6, 10)
    repo = _StubPriceRepo(issue)
    pt = repo.hourly_price("PT", temporal.t_issue_for(issue))
    x = build_price_features(repo, issue)  # type: ignore[arg-type]

    for target_ts, row in x.iterrows():
        for lag_h in PRICE_LAGS_H:
            assert row[f"price_lag_{lag_h}h"] == pt[target_ts - dt.timedelta(hours=lag_h)]
        # ES lag is PT - 5, so the PT/ES spread lag-24 is a constant +5.
        assert row["es_lag_24h"] == row["price_lag_24h"] - 5.0
        assert row["spread_lag_24h"] == 5.0
    # 24h lag IS a feature for price (legal — day-D price published D-1).
    assert "price_lag_24h" in x.columns


def test_day_d_aggregates_present() -> None:
    issue = dt.date(2024, 6, 10)
    x = build_price_features(_StubPriceRepo(issue), issue)  # type: ignore[arg-type]
    for col in ("day_d_price_mean", "day_d_price_min", "day_d_price_max", "day_d_price_std"):
        assert col in x.columns
    # Day-D prices are the hour-of-day (0..23), so the mean sits between min and max.
    assert (
        x["day_d_price_min"].iloc[0]
        <= x["day_d_price_mean"].iloc[0]
        <= x["day_d_price_max"].iloc[0]
    )


def test_price_baselines_select_legal_lags() -> None:
    features = pd.DataFrame(
        {"price_lag_24h": [50.0, 60.0], "price_lag_168h": [40.0, 45.0]},
        index=pd.to_datetime(["2024-06-11T00:00Z", "2024-06-11T01:00Z"]),
    )
    assert list(baselines.persistence_price(features)) == [50.0, 60.0]
    assert list(baselines.seasonal_weekly_price(features)) == [40.0, 45.0]


@pytest.mark.parametrize("n_hours_issue", [(dt.date(2024, 3, 30), 23), (dt.date(2024, 10, 26), 25)])
def test_price_features_follow_dst_delivery_day(n_hours_issue: tuple[dt.date, int]) -> None:
    issue, n_hours = n_hours_issue
    x = build_price_features(_StubPriceRepo(issue), issue)  # type: ignore[arg-type]
    assert len(x) == n_hours
