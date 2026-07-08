"""Tests for the backtest machinery: metrics, the rolling split, and — critically — that the
in-memory PreloadedRepo is legally identical to the DB AsOfRepo (a divergence would be silent
backtest leakage).
"""

from __future__ import annotations

import datetime as dt
import math

import pandas as pd
import pytest
from sqlalchemy import delete
from sqlalchemy.orm import Session

from src.db.models import RenRealised
from src.features import temporal
from src.features.asof_repo import AsOfRepo
from src.models.backtest import (
    FEATURE_COLS,
    PreloadedRepo,
    _mae,
    _mape,
    rolling_origin_backtest,
)


def test_mae_and_mape() -> None:
    actual = pd.Series([100.0, 200.0])
    predicted = pd.Series([110.0, 180.0])
    assert _mae(actual, predicted) == 15.0  # (10 + 20) / 2
    assert _mape(actual, predicted) == pytest.approx((10 / 100 + 20 / 200) / 2 * 100)


def _synthetic_matrix() -> pd.DataFrame:
    """8 weeks x 24h with a learnable temperature-driven signal and near-perfect lag baselines."""
    rows = []
    base = dt.date(2026, 1, 1)
    for day in range(56):
        issue = base + dt.timedelta(days=day)
        for hour in range(24):
            temp = 15 + 8 * math.sin(hour / 24 * 2 * math.pi)
            y = 5000.0 + 120 * temp + (600 if hour in (19, 20) else 0)
            row = {c: 0.0 for c in FEATURE_COLS}
            row.update(
                hour=hour,
                dow=issue.weekday(),
                month=1,
                is_weekend=issue.weekday() >= 5,
                is_holiday=False,
                temp=temp,
                hdd=max(0.0, 18 - temp),
                cdd=max(0.0, temp - 21),
                cons_lag_48h=y * 0.97,
                cons_lag_72h=y * 0.97,
                cons_lag_168h=y * 0.99,
                cons_lag_336h=y * 0.98,
                cons_recent_day_mean=5000.0,
            )
            row.update(y=y, issue_date=issue)
            rows.append(row)
    return pd.DataFrame(rows)


def test_rolling_origin_backtest_structure_and_gate() -> None:
    preds, metrics = rolling_origin_backtest(_synthetic_matrix(), oos_weeks=2)
    assert set(metrics.index) == {"lightgbm", "persistence_48h", "seasonal_168h"}
    assert (metrics["MAE"] > 0).all() and metrics["MAE"].notna().all()
    assert not preds.empty
    # On this clean signal the model should beat the weaker (persistence) baseline.
    assert metrics.loc["lightgbm", "MAE"] < metrics.loc["persistence_48h", "MAE"]


@pytest.mark.integration
@pytest.mark.leakage
def test_preloaded_repo_matches_asof_repo(pg_session: Session) -> None:
    # Seed consumption straddling the legality boundary at a far-future fold; both repos must
    # return the identical legal series (same temporal rules, DB vs in-memory).
    issue = dt.date(2099, 6, 10)
    t_issue = temporal.t_issue_for(issue)
    try:
        pg_session.add_all(
            [
                RenRealised(
                    series_name="Consumption",
                    ts_utc=ts,
                    resolution_minutes=15,
                    value_mw=v,
                    local_date=ts.date(),
                    period=1,
                    source_ref="backtest-test",
                )
                for ts, v in [
                    (dt.datetime(2099, 6, 9, 22, tzinfo=dt.UTC), 5000.0),  # legal
                    (dt.datetime(2099, 6, 10, 6, tzinfo=dt.UTC), 6000.0),  # illegal (day D)
                ]
            ]
        )
        pg_session.commit()

        from_db = AsOfRepo(pg_session).hourly_consumption(t_issue)
        from_memory = PreloadedRepo(pg_session).hourly_consumption(t_issue)
        pd.testing.assert_series_equal(
            from_db.sort_index(), from_memory.sort_index(), check_freq=False
        )
    finally:
        pg_session.rollback()
        pg_session.execute(
            delete(RenRealised).where(RenRealised.ts_utc >= dt.datetime(2099, 1, 1, tzinfo=dt.UTC))
        )
        pg_session.commit()
