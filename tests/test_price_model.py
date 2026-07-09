"""Tests for the quantile price model: pinball loss, model config, and backtest structure."""

from __future__ import annotations

import datetime as dt
import math

import pandas as pd
import pytest

from src.models.price_model import (
    PRICE_FEATURE_COLS,
    QUANTILES,
    _pinball,
    make_price_quantile_model,
    rolling_origin_price_backtest,
)


def test_pinball_loss_is_asymmetric() -> None:
    actual = pd.Series([10.0, 10.0])
    # For alpha=0.9, under-prediction (actual > predicted) is penalised 9x more than over.
    under = _pinball(actual, pd.Series([8.0, 8.0]), 0.9)  # delta = +2
    over = _pinball(actual, pd.Series([12.0, 12.0]), 0.9)  # delta = -2
    assert under == pytest.approx(0.9 * 2)
    assert over == pytest.approx(0.1 * 2)
    # At the median, pinball is half the absolute error.
    assert _pinball(actual, pd.Series([8.0, 8.0]), 0.5) == pytest.approx(0.5 * 2)


def test_quantile_model_config() -> None:
    model = make_price_quantile_model(0.9)
    assert model.get_params()["objective"] == "quantile"
    assert model.get_params()["alpha"] == 0.9
    assert QUANTILES == (0.1, 0.5, 0.9)


def _synthetic_price_matrix() -> pd.DataFrame:
    rows = []
    base = dt.date(2026, 1, 1)
    for day in range(56):
        issue = base + dt.timedelta(days=day)
        for hour in range(24):
            price = 60.0 + 25 * math.sin(hour / 24 * 2 * math.pi) + (day % 7) * 2
            row = {c: 0.0 for c in PRICE_FEATURE_COLS}
            row.update(
                hour=hour,
                dow=issue.weekday(),
                month=1,
                is_weekend=issue.weekday() >= 5,
                is_holiday=False,
                price_lag_24h=price * 1.05,  # a biased-but-decent persistence baseline
                price_lag_48h=price * 1.05,
                price_lag_168h=price * 1.1,
                day_d_price_mean=60.0,
            )
            row.update(y=price, issue_date=issue)
            rows.append(row)
    return pd.DataFrame(rows)


def test_price_backtest_structure_and_coverage() -> None:
    preds, metrics = rolling_origin_price_backtest(_synthetic_price_matrix(), oos_weeks=2)
    assert {"lgbm_p50", "persistence_24h", "seasonal_168h"} <= set(metrics.index)
    assert {"p10", "p50", "p90"} <= set(preds.columns)
    coverage = metrics.loc["lgbm_p50", "coverage_p10_p90"]
    assert 0.0 <= coverage <= 1.0
    # P10 <= P90 on average (the interval is a well-formed band, not crossed).
    assert (preds["p10"] <= preds["p90"]).mean() > 0.9
    assert metrics.loc["lgbm_p50", "MAE"] > 0  # finite, non-trivial
    # (The real backtest — not this tiny synthetic one — is where the P50 beats the baselines;
    # the production quantile config is regularised for ~15k training rows.)
