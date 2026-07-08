"""Rolling-origin backtesting for the Phase-1 consumption model — the like-for-like gate.

The fold is the issue day. Each OOS week the model is refreshed on all issue days strictly
before it (expanding window), then predicts that week's D+1 days. The model and the persistence
/ seasonal baselines are scored on exactly the same folds; a model that does not beat both is
not accepted (CLAUDE.md). No random splits — every split is chronological.

A ``PreloadedRepo`` holds the legal as-of read surface in memory (consumption + weather loaded
once), so building ~800 folds' features is fast without per-fold DB queries. Legality is
identical to the DB AsOfRepo — the same temporal.py publication rules are applied in memory.
"""

from __future__ import annotations

import datetime as dt

import pandas as pd
from lightgbm import LGBMRegressor
from sqlalchemy import select
from sqlalchemy.orm import Session

from src.db.models import OpenMeteoForecast, RenRealised
from src.features import temporal
from src.features.asof_repo import CONSUMPTION_SERIES
from src.features.build_features import build_consumption_features
from src.features.hourly import to_hourly

FEATURE_COLS = [
    "hour",
    "dow",
    "month",
    "is_weekend",
    "is_holiday",
    "cons_recent_day_mean",
    "cons_lag_48h",
    "cons_lag_72h",
    "cons_lag_168h",
    "cons_lag_336h",
    "temp",
    "hdd",
    "cdd",
    "wind_cube",
    "radiation",
]


class PreloadedRepo:
    """In-memory as-of repo: same legality as AsOfRepo, but data loaded once (fast backtests)."""

    def __init__(self, session: Session) -> None:
        cons_rows = session.execute(
            select(RenRealised.ts_utc, RenRealised.value_mw).where(
                RenRealised.series_name == CONSUMPTION_SERIES
            )
        ).all()
        self._cons = to_hourly([(ts, value) for ts, value in cons_rows])
        self._cons_pub = pd.to_datetime(
            [temporal.ren_published_at(ts.to_pydatetime()) for ts in self._cons.index], utc=True
        )

        weather_rows = session.execute(
            select(
                OpenMeteoForecast.lead_days,
                OpenMeteoForecast.variable,
                OpenMeteoForecast.ts_utc,
                OpenMeteoForecast.value,
            )
        ).all()
        wdf = pd.DataFrame(weather_rows, columns=["lead_days", "variable", "ts_utc", "value"])
        avg = wdf.groupby(["lead_days", "ts_utc", "variable"], as_index=False)["value"].mean()
        self._weather: dict[object, pd.DataFrame] = {
            lead: part.pivot(index="ts_utc", columns="variable", values="value")
            for lead, part in avg.groupby("lead_days")
        }

    def hourly_consumption(self, t_issue: dt.datetime) -> pd.Series:
        return self._cons[self._cons_pub <= pd.Timestamp(t_issue)]

    def weather_forecast(
        self, t_issue: dt.datetime, target_hours: list[dt.datetime]
    ) -> pd.DataFrame:
        index = pd.DatetimeIndex(target_hours)
        w1 = self._weather.get(1, pd.DataFrame()).reindex(index)
        w2 = self._weather.get(2, pd.DataFrame()).reindex(index)
        legal1 = pd.Series(
            [temporal.openmeteo_published_at(ts.to_pydatetime(), 1) <= t_issue for ts in index],
            index=index,
        )
        return pd.DataFrame(w1.where(legal1, w2))

    def realised_consumption(self, delivery_date_cet: dt.date) -> pd.Series:
        hours = temporal.delivery_hours_utc(delivery_date_cet)
        return self._cons.reindex(pd.DatetimeIndex(hours))


def build_matrix(repo: PreloadedRepo, issue_dates: list[dt.date]) -> pd.DataFrame:
    """Stack per-fold features + realised target, tagged with issue_date, over ``issue_dates``."""
    frames = []
    for issue in issue_dates:
        features = build_consumption_features(repo, issue)
        target = repo.realised_consumption(temporal.delivery_date_for(issue)).rename("y")
        frame = features.join(target)
        frame["issue_date"] = issue
        frames.append(frame)
    return pd.concat(frames)


def _mae(actual: pd.Series, predicted: pd.Series) -> float:
    return float((actual - predicted).abs().mean())


def _mape(actual: pd.Series, predicted: pd.Series) -> float:
    return float(((actual - predicted).abs() / actual).mean() * 100)


def rolling_origin_backtest(
    matrix: pd.DataFrame, oos_weeks: int = 10
) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Weekly-refresh expanding-window backtest. Returns (predictions, metrics-by-model)."""
    matrix = matrix.dropna(subset=["y", "cons_lag_48h", "cons_lag_168h"]).copy()
    last_issue = matrix["issue_date"].max()
    first_oos = last_issue - dt.timedelta(weeks=oos_weeks)

    predictions = []
    week_start = first_oos
    while week_start <= last_issue:
        week_end = week_start + dt.timedelta(days=7)
        train = matrix[matrix["issue_date"] < week_start]
        test = matrix[(matrix["issue_date"] >= week_start) & (matrix["issue_date"] < week_end)]
        if not test.empty and len(train) > 500:
            model = LGBMRegressor(
                objective="regression_l1",
                n_estimators=500,
                learning_rate=0.05,
                num_leaves=63,
                min_child_samples=50,
                subsample=0.8,
                subsample_freq=1,
                colsample_bytree=0.8,
                random_state=42,
                n_jobs=1,
                verbosity=-1,
            )
            model.fit(train[FEATURE_COLS].astype("float64"), train["y"])
            test = test.assign(y_hat=model.predict(test[FEATURE_COLS].astype("float64")))
            predictions.append(test)
        week_start = week_end

    preds = pd.concat(predictions)
    metrics = pd.DataFrame(
        [
            {
                "model": "lightgbm",
                "MAE": _mae(preds["y"], preds["y_hat"]),
                "MAPE": _mape(preds["y"], preds["y_hat"]),
            },
            {
                "model": "persistence_48h",
                "MAE": _mae(preds["y"], preds["cons_lag_48h"]),
                "MAPE": _mape(preds["y"], preds["cons_lag_48h"]),
            },
            {
                "model": "seasonal_168h",
                "MAE": _mae(preds["y"], preds["cons_lag_168h"]),
                "MAPE": _mape(preds["y"], preds["cons_lag_168h"]),
            },
        ]
    ).set_index("model")
    return preds, metrics
