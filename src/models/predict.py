"""Emit and persist the live Phase-1 consumption forecast for delivery day D+1.

Retrain-on-emit (training is seconds; weekly-refresh + artifact serving is a later refinement):
train on every fold whose delivery day already has realised truth, build the issue day's
features as-of ``t_issue``, and predict D+1 with the ML model plus the two baselines (all
first-class). Writes to pred.predictions INSERT-ONLY (the first emission of the day wins).

Usage:
    uv run python -m src.models.predict [--issue-date YYYY-MM-DD]
"""

from __future__ import annotations

import argparse
import datetime as dt
import logging

import pandas as pd
from sqlalchemy.orm import Session

from src.db.engine import make_engine, make_session_factory
from src.db.repositories.predictions import insert_predictions
from src.features import temporal
from src.features.build_features import build_consumption_features
from src.models import baselines
from src.models.backtest import (
    FEATURE_COLS,
    FIRST_ISSUE,
    PreloadedRepo,
    build_matrix,
    make_consumption_model,
)

logger = logging.getLogger("predict")

# A forecast emitted more than this after t_issue is flagged late and kept out of headline scoring.
LATE_GRACE = dt.timedelta(hours=2)


def emit_consumption_forecast(
    session: Session, issue_date: dt.date, now: dt.datetime | None = None
) -> dict[str, object]:
    """Train, predict D+1 consumption, and persist all models' forecasts. Returns a summary."""
    now = now or dt.datetime.now(tz=dt.UTC)
    t_issue = temporal.t_issue_for(issue_date)
    repo = PreloadedRepo(session)

    # Train on folds whose delivery day already has realised truth (delivery <= issue_date - 1).
    last_train_issue = issue_date - dt.timedelta(days=2)
    train_dates = [
        FIRST_ISSUE + dt.timedelta(days=i) for i in range((last_train_issue - FIRST_ISSUE).days + 1)
    ]
    matrix = build_matrix(repo, train_dates).dropna(
        subset=["y", "cons_lag_48h", "cons_lag_168h", "cons_lag_336h"]
    )
    model = make_consumption_model()
    model.fit(matrix[FEATURE_COLS].astype("float64"), matrix["y"])

    features = build_consumption_features(repo, issue_date)
    predictions: dict[str, pd.Series] = {
        "lightgbm": pd.Series(
            model.predict(features[FEATURE_COLS].astype("float64")), index=features.index
        ),
        "persistence_48h": baselines.persistence_consumption(features),
        "seasonal_168h": baselines.seasonal_weekly_consumption(features),
    }

    late = now > t_issue + LATE_GRACE
    rows: list[dict[str, object]] = []
    for model_name, y_hat in predictions.items():
        target_times = pd.DatetimeIndex(y_hat.index).to_pydatetime()
        for target_ts, value in zip(target_times, y_hat.to_numpy(), strict=True):
            if pd.isna(value):
                continue
            rows.append(
                {
                    "issue_date": issue_date,
                    "target_ts": target_ts,
                    "model_name": model_name,
                    "quantile": "point",
                    "target_name": "consumption",
                    "y_hat": float(value),
                    "issued_at": now,
                    "late_issue": late,
                }
            )
    written = insert_predictions(session, rows)
    session.commit()
    summary = {
        "issue_date": issue_date,
        "hours": len(features),
        "rows_written": written,
        "late": late,
    }
    logger.info("emitted consumption forecast: %s", summary)
    return summary


def main() -> None:
    parser = argparse.ArgumentParser(description="Emit the D+1 consumption forecast.")
    parser.add_argument(
        "--issue-date",
        type=dt.date.fromisoformat,
        default=dt.datetime.now(tz=dt.UTC).date(),
        help="issue day D (defaults to today UTC)",
    )
    args = parser.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    engine = make_engine()
    factory = make_session_factory(engine)
    try:
        with factory() as session:
            emit_consumption_forecast(session, args.issue_date)
    finally:
        engine.dispose()


if __name__ == "__main__":
    main()
