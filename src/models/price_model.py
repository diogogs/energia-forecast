"""Phase-2 MIBEL price model — quantile triplet P10/P50/P90 with a rolling-origin gate.

Three LightGBM quantile regressors (the P50 is the point forecast). Evaluated with MAE (never
MAPE — prices go to zero/negative), pinball loss per quantile, and empirical coverage of the
P10-P90 interval (~80% target). The P50 must beat the price baselines on the same folds, or the
baseline is published instead (CLAUDE.md). No random splits.
"""

from __future__ import annotations

import datetime as dt

import numpy as np
import pandas as pd
from lightgbm import LGBMRegressor

from src.features import temporal
from src.features.build_features import build_price_features
from src.models.backtest import PreloadedRepo

QUANTILES = (0.1, 0.5, 0.9)

PRICE_FEATURE_COLS = [
    "hour",
    "dow",
    "month",
    "is_weekend",
    "is_holiday",
    "es_lag_24h",
    "spread_lag_24h",
    "day_d_price_mean",
    "day_d_price_min",
    "day_d_price_max",
    "day_d_price_std",
    "price_lag_24h",
    "price_lag_48h",
    "price_lag_168h",
    "temp",
    "hdd",
    "cdd",
    "wind_cube",
    "radiation",
]

_TRAINABLE_SUBSET = ["y", "price_lag_24h", "price_lag_168h"]


def make_price_quantile_model(alpha: float) -> LGBMRegressor:
    """A LightGBM quantile regressor for quantile ``alpha`` (pinned config, shared everywhere).

    Deliberately shallow/regularised: deep trees overfit the median and collapse the P10-P90
    interval (48% coverage). This config both lowers the P50 MAE and widens the interval to ~75%
    empirical coverage (nominal 80%; the last few points want a conformal calibration — future).
    """
    return LGBMRegressor(
        objective="quantile",
        alpha=alpha,
        n_estimators=400,
        learning_rate=0.03,
        num_leaves=10,
        min_child_samples=800,
        random_state=42,
        n_jobs=1,
        verbosity=-1,
    )


def build_price_matrix(repo: PreloadedRepo, issue_dates: list[dt.date]) -> pd.DataFrame:
    """Stack per-fold price features + realised price target, tagged with issue_date."""
    frames = []
    for issue in issue_dates:
        features = build_price_features(repo, issue)
        target = repo.realised_price(temporal.delivery_date_for(issue)).rename("y")
        frame = features.join(target)
        frame["issue_date"] = issue
        frames.append(frame)
    return pd.concat(frames)


def _pinball(actual: pd.Series, predicted: pd.Series, alpha: float) -> float:
    delta = actual - predicted
    return float(np.maximum(alpha * delta, (alpha - 1) * delta).mean())


def rolling_origin_price_backtest(
    matrix: pd.DataFrame, oos_weeks: int = 10
) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Weekly-refresh expanding-window quantile backtest. Returns (predictions, metrics)."""
    matrix = matrix.dropna(subset=_TRAINABLE_SUBSET).copy()
    last_issue = matrix["issue_date"].max()
    first_oos = last_issue - dt.timedelta(weeks=oos_weeks)

    predictions = []
    week_start = first_oos
    while week_start <= last_issue:
        week_end = week_start + dt.timedelta(days=7)
        train = matrix[matrix["issue_date"] < week_start]
        test = matrix[(matrix["issue_date"] >= week_start) & (matrix["issue_date"] < week_end)]
        if not test.empty and len(train) > 500:
            x_train = train[PRICE_FEATURE_COLS].astype("float64")
            x_test = test[PRICE_FEATURE_COLS].astype("float64")
            out = test.copy()
            for alpha in QUANTILES:
                model = make_price_quantile_model(alpha)
                model.fit(x_train, train["y"])
                out[f"p{int(alpha * 100)}"] = model.predict(x_test)
            predictions.append(out)
        week_start = week_end

    preds = pd.concat(predictions)
    coverage = float(((preds["y"] >= preds["p10"]) & (preds["y"] <= preds["p90"])).mean())
    metrics = pd.DataFrame(
        [
            {
                "model": "lgbm_p50",
                "MAE": float((preds["y"] - preds["p50"]).abs().mean()),
                "pinball_p10": _pinball(preds["y"], preds["p10"], 0.1),
                "pinball_p50": _pinball(preds["y"], preds["p50"], 0.5),
                "pinball_p90": _pinball(preds["y"], preds["p90"], 0.9),
                "coverage_p10_p90": coverage,
            },
            {
                "model": "persistence_24h",
                "MAE": float((preds["y"] - preds["price_lag_24h"]).abs().mean()),
                "pinball_p10": np.nan,
                "pinball_p50": _pinball(preds["y"], preds["price_lag_24h"], 0.5),
                "pinball_p90": np.nan,
                "coverage_p10_p90": np.nan,
            },
            {
                "model": "seasonal_168h",
                "MAE": float((preds["y"] - preds["price_lag_168h"]).abs().mean()),
                "pinball_p10": np.nan,
                "pinball_p50": _pinball(preds["y"], preds["price_lag_168h"], 0.5),
                "pinball_p90": np.nan,
                "coverage_p10_p90": np.nan,
            },
        ]
    ).set_index("model")
    return preds, metrics
