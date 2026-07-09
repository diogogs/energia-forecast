"""Emit and persist the live D+1 forecasts (consumption + MIBEL price) for delivery day D+1.

Retrain-on-emit (training is seconds; weekly-refresh + artifact serving is a later refinement):
train on every fold whose delivery day already has realised truth, build the issue day's
features as-of ``t_issue``, and predict D+1 with the ML model(s) plus the baselines (all
first-class). Consumption is a point forecast; price is the P10/P50/P90 quantile triplet.
Writes to pred.predictions INSERT-ONLY (the first emission of the day wins).

Usage:
    uv run python -m src.models.predict [--issue-date YYYY-MM-DD] [--target both|consumption|price]
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
from src.features.build_features import build_consumption_features, build_price_features
from src.models import baselines
from src.models.backtest import (
    FEATURE_COLS,
    FIRST_ISSUE,
    PreloadedRepo,
    build_matrix,
    make_consumption_model,
)
from src.models.price_model import (
    PRICE_FEATURE_COLS,
    QUANTILES,
    build_price_matrix,
    make_price_quantile_model,
)

logger = logging.getLogger("predict")

# A forecast emitted more than this after t_issue is flagged late and kept out of headline scoring.
LATE_GRACE = dt.timedelta(hours=2)


def _train_issue_dates(issue_date: dt.date) -> list[dt.date]:
    """Folds whose delivery day already has realised truth (delivery <= issue_date - 1)."""
    last = issue_date - dt.timedelta(days=2)
    return [FIRST_ISSUE + dt.timedelta(days=i) for i in range((last - FIRST_ISSUE).days + 1)]


def _rows_from_series(
    y_hat: pd.Series,
    issue_date: dt.date,
    model_name: str,
    quantile: str,
    target_name: str,
    now: dt.datetime,
    late: bool,
) -> list[dict[str, object]]:
    """Turn a per-hour prediction series into pred.predictions rows (skipping NaNs)."""
    rows: list[dict[str, object]] = []
    target_times = pd.DatetimeIndex(y_hat.index).to_pydatetime()
    for target_ts, value in zip(target_times, y_hat.to_numpy(), strict=True):
        if pd.isna(value):
            continue
        rows.append(
            {
                "issue_date": issue_date,
                "target_ts": target_ts,
                "model_name": model_name,
                "quantile": quantile,
                "target_name": target_name,
                "y_hat": float(value),
                "issued_at": now,
                "late_issue": late,
            }
        )
    return rows


def emit_consumption_forecast(
    session: Session,
    issue_date: dt.date,
    now: dt.datetime | None = None,
    repo: PreloadedRepo | None = None,
) -> dict[str, object]:
    """Train, predict D+1 consumption, and persist all models' point forecasts."""
    now = now or dt.datetime.now(tz=dt.UTC)
    late = now > temporal.t_issue_for(issue_date) + LATE_GRACE
    repo = repo or PreloadedRepo(session)

    matrix = build_matrix(repo, _train_issue_dates(issue_date)).dropna(
        subset=["y", "cons_lag_48h", "cons_lag_168h", "cons_lag_336h"]
    )
    model = make_consumption_model()
    model.fit(matrix[FEATURE_COLS].astype("float64"), matrix["y"])

    features = build_consumption_features(repo, issue_date)
    ml = pd.Series(model.predict(features[FEATURE_COLS].astype("float64")), index=features.index)

    rows = _rows_from_series(ml, issue_date, "lightgbm", "point", "consumption", now, late)
    rows += _rows_from_series(
        baselines.persistence_consumption(features),
        issue_date,
        "persistence_48h",
        "point",
        "consumption",
        now,
        late,
    )
    rows += _rows_from_series(
        baselines.seasonal_weekly_consumption(features),
        issue_date,
        "seasonal_168h",
        "point",
        "consumption",
        now,
        late,
    )
    written = insert_predictions(session, rows)
    session.commit()
    summary = {
        "target": "consumption",
        "hours": len(features),
        "rows_written": written,
        "late": late,
    }
    logger.info("emitted consumption forecast: %s", summary)
    return summary


def emit_price_forecast(
    session: Session,
    issue_date: dt.date,
    now: dt.datetime | None = None,
    repo: PreloadedRepo | None = None,
    zone: str = "PT",
) -> dict[str, object]:
    """Train, predict D+1 MIBEL price P10/P50/P90 + baselines, and persist them."""
    now = now or dt.datetime.now(tz=dt.UTC)
    late = now > temporal.t_issue_for(issue_date) + LATE_GRACE
    repo = repo or PreloadedRepo(session)

    matrix = build_price_matrix(repo, _train_issue_dates(issue_date)).dropna(
        subset=["y", "price_lag_24h", "price_lag_168h"]
    )
    x_train = matrix[PRICE_FEATURE_COLS].astype("float64")
    features = build_price_features(repo, issue_date, zone)
    x = features[PRICE_FEATURE_COLS].astype("float64")

    rows: list[dict[str, object]] = []
    for alpha in QUANTILES:
        model = make_price_quantile_model(alpha)
        model.fit(x_train, matrix["y"])
        y_hat = pd.Series(model.predict(x), index=features.index)
        rows += _rows_from_series(
            y_hat, issue_date, "lightgbm", f"p{int(alpha * 100)}", "price", now, late
        )
    rows += _rows_from_series(
        baselines.persistence_price(features),
        issue_date,
        "persistence_24h",
        "point",
        "price",
        now,
        late,
    )
    rows += _rows_from_series(
        baselines.seasonal_weekly_price(features),
        issue_date,
        "seasonal_168h",
        "point",
        "price",
        now,
        late,
    )
    written = insert_predictions(session, rows)
    session.commit()
    summary = {"target": "price", "hours": len(features), "rows_written": written, "late": late}
    logger.info("emitted price forecast: %s", summary)
    return summary


def main() -> None:
    parser = argparse.ArgumentParser(description="Emit the D+1 forecasts (consumption + price).")
    parser.add_argument(
        "--issue-date",
        type=dt.date.fromisoformat,
        default=dt.datetime.now(tz=dt.UTC).date(),
        help="issue day D (defaults to today UTC)",
    )
    parser.add_argument("--target", choices=["both", "consumption", "price"], default="both")
    args = parser.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    engine = make_engine()
    factory = make_session_factory(engine)
    try:
        with factory() as session:
            repo = PreloadedRepo(session)  # preload once, shared across targets
            if args.target in ("both", "consumption"):
                emit_consumption_forecast(session, args.issue_date, repo=repo)
            if args.target in ("both", "price"):
                emit_price_forecast(session, args.issue_date, repo=repo)
    finally:
        engine.dispose()


if __name__ == "__main__":
    main()
