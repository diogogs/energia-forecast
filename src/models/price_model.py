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

# Conformalised Quantile Regression (Romano et al. 2019) for the P10-P90 interval: the raw
# quantile models under-cover, so we hold out the most recent CALIB_WEEKS of the training window,
# measure how far the truth falls outside [P10, P90] there, and widen the interval by that
# quantile. ALPHA=0.2 -> nominal 80% central interval. The P50 point forecast stays on the FULL
# training window (unchanged MAE); only the interval uses the fit/calibration split.
ALPHA = 0.2
CALIB_WEEKS = 3


def conformal_correction(
    y_calib: np.ndarray, lo_calib: np.ndarray, hi_calib: np.ndarray, alpha: float = ALPHA
) -> float:
    """The CQR widening: the finite-sample (1-alpha) quantile of the interval nonconformity."""
    scores = np.maximum(lo_calib - y_calib, y_calib - hi_calib)
    n = len(scores)
    if n == 0:
        return 0.0
    level = min(1.0, (1 - alpha) * (1 + 1 / n))
    return float(np.quantile(scores, level, method="higher"))


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
    interval (48% coverage). This config both lowers the P50 MAE and widens the base interval to
    ~75%; conformal calibration (``calibrated_price_triplet``) then nudges it toward the nominal
    80% (the residual gap is price-regime non-stationarity, which breaks CQR's exchangeability).
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


def calibrated_price_triplet(
    train: pd.DataFrame,
    x_predict: pd.DataFrame,
    calib_weeks: int = CALIB_WEEKS,
    alpha: float = ALPHA,
) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """Fit the P50 on the full window and a CQR-calibrated P10/P90; return (p10, p50, p90)."""
    p50_model = make_price_quantile_model(0.5)
    p50_model.fit(train[PRICE_FEATURE_COLS].astype("float64"), train["y"])
    p50 = p50_model.predict(x_predict)

    calib_start = train["issue_date"].max() - dt.timedelta(weeks=calib_weeks)
    fit = train[train["issue_date"] < calib_start]
    calib = train[train["issue_date"] >= calib_start]
    if fit.empty:  # not enough history to hold out a calibration slice — skip calibration
        fit, calib = train, train.iloc[:0]
    lo_model = make_price_quantile_model(0.1)
    hi_model = make_price_quantile_model(0.9)
    lo_model.fit(fit[PRICE_FEATURE_COLS].astype("float64"), fit["y"])
    hi_model.fit(fit[PRICE_FEATURE_COLS].astype("float64"), fit["y"])

    q = 0.0
    if not calib.empty:
        x_calib = calib[PRICE_FEATURE_COLS].astype("float64")
        q = conformal_correction(
            calib["y"].to_numpy(), lo_model.predict(x_calib), hi_model.predict(x_calib), alpha
        )
    return lo_model.predict(x_predict) - q, p50, hi_model.predict(x_predict) + q


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


# Each price model's contribution to pred.backtest_predictions (the quantiles are kept as
# distinct model names so the dashboard can plot the P10-P90 band from the simulated history).
_PRICE_BACKTEST_MODEL_COLS = {
    "lightgbm_p10": "p10",
    "lightgbm_p50": "p50",
    "lightgbm_p90": "p90",
    "persistence_24h": "price_lag_24h",
    "seasonal_168h": "price_lag_168h",
}


def price_backtest_rows(preds: pd.DataFrame) -> list[dict[str, object]]:
    """Reshape price backtest predictions into pred.backtest_predictions rows (target 'price')."""
    records = preds.rename_axis("target_ts").reset_index().to_dict("records")
    rows: list[dict[str, object]] = []
    for model_name, col in _PRICE_BACKTEST_MODEL_COLS.items():
        for rec in records:
            y_hat = rec[col]
            if pd.isna(y_hat):
                continue
            y_true = rec["y"]
            rows.append(
                {
                    "issue_date": rec["issue_date"],
                    "target_ts": rec["target_ts"].to_pydatetime(),
                    "model_name": model_name,
                    "target_name": "price",
                    "y_hat": float(y_hat),
                    "y_true": None if pd.isna(y_true) else float(y_true),
                }
            )
    return rows


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
            x_test = test[PRICE_FEATURE_COLS].astype("float64")
            p10, p50, p90 = calibrated_price_triplet(train, x_test)
            out = test.copy()
            out["p10"], out["p50"], out["p90"] = p10, p50, p90
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
