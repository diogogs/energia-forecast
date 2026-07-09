"""Backtest/retrain job: score the model like-for-like against the baselines, persist the
fold-wise predictions to pred.backtest_predictions (the dashboard's simulated history), and log
params/metrics/feature-importance to MLflow. Weekly cadence; never in the serving path.

Usage:
    uv run python -m src.models.run_backtest [--oos-weeks 10] [--end YYYY-MM-DD]
"""

from __future__ import annotations

import argparse
import datetime as dt
import logging

from sqlalchemy.orm import Session

from src.db.engine import make_engine, make_session_factory
from src.db.repositories.predictions import upsert_backtest_predictions
from src.models.backtest import (
    FEATURE_COLS,
    FIRST_ISSUE,
    PreloadedRepo,
    build_matrix,
    make_consumption_model,
    rolling_origin_backtest,
    to_backtest_rows,
)
from src.models.tracking import backtest_run, log_backtest_metrics, log_feature_importance

logger = logging.getLogger("run_backtest")

_TRAINABLE_SUBSET = ["y", "cons_lag_48h", "cons_lag_168h", "cons_lag_336h"]


def run_backtest(session: Session, oos_weeks: int = 10, end: dt.date | None = None) -> None:
    """Backtest to ``end`` (last fold with realised truth), persist folds, log to MLflow."""
    end = end or (dt.datetime.now(tz=dt.UTC).date() - dt.timedelta(days=2))
    issue_dates = [FIRST_ISSUE + dt.timedelta(days=i) for i in range((end - FIRST_ISSUE).days + 1)]
    repo = PreloadedRepo(session)
    matrix = build_matrix(repo, issue_dates)

    preds, metrics = rolling_origin_backtest(matrix, oos_weeks=oos_weeks)
    written = upsert_backtest_predictions(session, to_backtest_rows(preds))
    session.commit()
    logger.info("persisted %d backtest rows; metrics:\n%s", written, metrics.round(2).to_string())

    # A final model over the whole trainable matrix, for feature importance.
    trainable = matrix.dropna(subset=_TRAINABLE_SUBSET)
    final_model = make_consumption_model()
    final_model.fit(trainable[FEATURE_COLS].astype("float64"), trainable["y"])

    params: dict[str, object] = {
        "model": "lightgbm_regression_l1",
        "oos_weeks": oos_weeks,
        "n_oos_folds": int(preds["issue_date"].nunique()),
        "n_train_rows": len(trainable),
        "first_issue": FIRST_ISSUE.isoformat(),
        "end": end.isoformat(),
        "features": ",".join(FEATURE_COLS),
    }
    with backtest_run(f"backtest-{end.isoformat()}", params):
        log_backtest_metrics(metrics)
        log_feature_importance(FEATURE_COLS, list(final_model.feature_importances_))
    logger.info("logged backtest run to MLflow")


def main() -> None:
    parser = argparse.ArgumentParser(description="Run the consumption backtest and track it.")
    parser.add_argument("--oos-weeks", type=int, default=10)
    parser.add_argument("--end", type=dt.date.fromisoformat, default=None)
    args = parser.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    engine = make_engine()
    factory = make_session_factory(engine)
    try:
        with factory() as session:
            run_backtest(session, oos_weeks=args.oos_weeks, end=args.end)
    finally:
        engine.dispose()


if __name__ == "__main__":
    main()
