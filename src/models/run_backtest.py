"""Backtest/retrain job: score each target like-for-like against its baselines, persist the
fold-wise predictions to pred.backtest_predictions (the dashboard's simulated history), and log
params/metrics/feature-importance to MLflow. Weekly cadence; never in the serving path.

Usage:
    uv run python -m src.models.run_backtest [--oos-weeks 10] [--end YYYY-MM-DD]
                                             [--target both|consumption|price]
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
from src.models.price_model import (
    PRICE_FEATURE_COLS,
    build_price_matrix,
    price_backtest_rows,
    rolling_origin_price_backtest,
)
from src.models.tracking import backtest_run, log_feature_importance, log_metrics_frame

logger = logging.getLogger("run_backtest")


def _issue_dates(end: dt.date) -> list[dt.date]:
    return [FIRST_ISSUE + dt.timedelta(days=i) for i in range((end - FIRST_ISSUE).days + 1)]


def run_consumption_backtest(
    repo: PreloadedRepo, session: Session, end: dt.date, oos_weeks: int
) -> None:
    matrix = build_matrix(repo, _issue_dates(end))
    preds, metrics = rolling_origin_backtest(matrix, oos_weeks=oos_weeks)
    written = upsert_backtest_predictions(session, to_backtest_rows(preds))
    session.commit()
    logger.info("consumption: persisted %d rows\n%s", written, metrics.round(2).to_string())

    trainable = matrix.dropna(subset=["y", "cons_lag_48h", "cons_lag_168h", "cons_lag_336h"])
    model = make_consumption_model()
    model.fit(trainable[FEATURE_COLS].astype("float64"), trainable["y"])
    params: dict[str, object] = {
        "target": "consumption",
        "model": "lightgbm_regression_l1",
        "oos_weeks": oos_weeks,
        "n_oos_folds": int(preds["issue_date"].nunique()),
        "n_train_rows": len(trainable),
        "end": end.isoformat(),
    }
    with backtest_run(f"consumption-{end.isoformat()}", params):
        log_metrics_frame(metrics)
        log_feature_importance(FEATURE_COLS, list(model.feature_importances_))


def run_price_backtest(repo: PreloadedRepo, session: Session, end: dt.date, oos_weeks: int) -> None:
    matrix = build_price_matrix(repo, _issue_dates(end))
    preds, metrics = rolling_origin_price_backtest(matrix, oos_weeks=oos_weeks)
    written = upsert_backtest_predictions(session, price_backtest_rows(preds))
    session.commit()
    logger.info("price: persisted %d rows\n%s", written, metrics.round(2).to_string())

    params: dict[str, object] = {
        "target": "price",
        "model": "lightgbm_quantile_p10_p50_p90",
        "oos_weeks": oos_weeks,
        "n_oos_folds": int(preds["issue_date"].nunique()),
        "n_features": len(PRICE_FEATURE_COLS),
        "end": end.isoformat(),
    }
    with backtest_run(f"price-{end.isoformat()}", params):
        log_metrics_frame(metrics)


def main() -> None:
    parser = argparse.ArgumentParser(description="Run the backtests and track them.")
    parser.add_argument("--oos-weeks", type=int, default=10)
    parser.add_argument("--end", type=dt.date.fromisoformat, default=None)
    parser.add_argument("--target", choices=["both", "consumption", "price"], default="both")
    args = parser.parse_args()
    end = args.end or (dt.datetime.now(tz=dt.UTC).date() - dt.timedelta(days=2))

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    engine = make_engine()
    factory = make_session_factory(engine)
    try:
        with factory() as session:
            repo = PreloadedRepo(session)
            if args.target in ("both", "consumption"):
                run_consumption_backtest(repo, session, end, args.oos_weeks)
            if args.target in ("both", "price"):
                run_price_backtest(repo, session, end, args.oos_weeks)
    finally:
        engine.dispose()


if __name__ == "__main__":
    main()
