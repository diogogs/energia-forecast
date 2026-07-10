"""Read-only serving API (FastAPI) over pred.* — the dashboard's data source.

Stateless: all state lives in Neon. Uses the read-only role (DATABASE_URL_RO) when configured,
falling back to the pooled URL otherwise. Never writes; never touches MLflow or the models.
"""

from __future__ import annotations

import datetime as dt
from collections.abc import Iterator
from typing import Annotated, Literal

from fastapi import Depends, FastAPI, HTTPException
from sqlalchemy import func, select
from sqlalchemy.orm import Session, sessionmaker

from src.api.schemas import (
    BacktestPoint,
    Forecast,
    ForecastPoint,
    Health,
    HistoryPoint,
    ModelPerformance,
)
from src.config import get_settings
from src.db.engine import make_engine, make_session_factory
from src.db.models import BacktestPrediction, Prediction
from src.monitoring.watchdog import (
    DqEvent,
    RealisedError,
    SourceFreshness,
    data_freshness,
    realised_error,
    realised_hourly,
    recent_dq_events,
)

TargetName = Literal["consumption", "price"]

app = FastAPI(
    title="energia-forecast API",
    summary="Read-only forecasts for Portuguese demand and MIBEL price.",
    version="0.1.0",
)

# Lazy engine so importing the app never needs a database (only serving a request does). The
# read-only role (DATABASE_URL_RO) is used when configured, else the pooled URL.
_session_factory: sessionmaker[Session] | None = None


def get_session() -> Iterator[Session]:
    global _session_factory
    if _session_factory is None:
        settings = get_settings()
        engine = make_engine(settings.database_url_ro or settings.database_url or None)
        _session_factory = make_session_factory(engine)
    with _session_factory() as session:
        yield session


SessionDep = Annotated[Session, Depends(get_session)]


@app.get("/ping")
def ping() -> dict[str, str]:
    """Liveness only — keeps the web service warm WITHOUT touching the database.

    The every-10-minutes keepalive must hit this, not /health: /health queries Postgres, and
    on Neon's free plan that keeps the compute awake around the clock, exhausting the monthly
    compute allowance (100 CU-hours/project). Readiness checks stay on /health.
    """
    return {"status": "ok"}


@app.get("/health", response_model=Health)
def health(session: SessionDep) -> Health:
    latest = session.execute(select(func.max(Prediction.issue_date))).scalar_one_or_none()
    return Health(status="ok", database=True, latest_issue_date=latest)


@app.get("/forecast/{target_name}", response_model=Forecast)
def forecast(target_name: TargetName, session: SessionDep) -> Forecast:
    """The most recent issued D+1 forecast for ``target_name`` (all models and quantiles)."""
    latest = session.execute(
        select(func.max(Prediction.issue_date)).where(Prediction.target_name == target_name)
    ).scalar_one_or_none()
    if latest is None:
        raise HTTPException(status_code=404, detail=f"no {target_name} forecast yet")

    rows = (
        session.execute(
            select(Prediction)
            .where(Prediction.target_name == target_name, Prediction.issue_date == latest)
            .order_by(Prediction.target_ts, Prediction.model_name, Prediction.quantile)
        )
        .scalars()
        .all()
    )
    return Forecast(
        target_name=target_name,
        issue_date=latest,
        issued_at=rows[0].issued_at if rows else None,
        points=[ForecastPoint.model_validate(r) for r in rows],
    )


@app.get("/history/{target_name}", response_model=list[HistoryPoint])
def history(target_name: TargetName, session: SessionDep, days: int = 14) -> list[HistoryPoint]:
    """Live emitted forecasts paired with realised outcomes, hour by hour.

    The growing forecast-vs-actual record of the production system (as opposed to /backtest,
    which is the simulated pre-launch history). y_true is null until the outcome is ingested —
    consumption arrives intraday, day-ahead prices with the next morning's ingest. Excludes
    late emissions (headline record only).
    """
    latest = session.execute(
        select(func.max(Prediction.issue_date)).where(
            Prediction.target_name == target_name, Prediction.late_issue.is_(False)
        )
    ).scalar_one_or_none()
    if latest is None:
        return []
    cutoff = latest - dt.timedelta(days=days)
    rows = (
        session.execute(
            select(Prediction)
            .where(
                Prediction.target_name == target_name,
                Prediction.late_issue.is_(False),
                Prediction.issue_date >= cutoff,
            )
            .order_by(Prediction.target_ts, Prediction.model_name, Prediction.quantile)
        )
        .scalars()
        .all()
    )
    if not rows:
        return []
    lo = rows[0].target_ts
    hi = rows[-1].target_ts + dt.timedelta(hours=1)
    realised = realised_hourly(session, target_name, lo, hi)
    return [
        HistoryPoint(
            target_ts=r.target_ts,
            model_name=r.model_name,
            quantile=r.quantile,
            y_hat=r.y_hat,
            y_true=float(realised[r.target_ts]) if r.target_ts in realised.index else None,
        )
        for r in rows
    ]


@app.get("/backtest/{target_name}", response_model=list[BacktestPoint])
def backtest(target_name: TargetName, session: SessionDep, days: int = 30) -> list[BacktestPoint]:
    """Recent fold-wise backtest predictions + realised truth (the simulated history)."""
    max_issue = session.execute(
        select(func.max(BacktestPrediction.issue_date)).where(
            BacktestPrediction.target_name == target_name
        )
    ).scalar_one_or_none()
    if max_issue is None:
        return []
    cutoff = max_issue - dt.timedelta(days=days)
    rows = (
        session.execute(
            select(BacktestPrediction)
            .where(
                BacktestPrediction.target_name == target_name,
                BacktestPrediction.issue_date >= cutoff,
            )
            .order_by(BacktestPrediction.target_ts, BacktestPrediction.model_name)
        )
        .scalars()
        .all()
    )
    return [BacktestPoint.model_validate(r) for r in rows]


@app.get("/performance/{target_name}", response_model=list[ModelPerformance])
def performance(target_name: TargetName, session: SessionDep) -> list[ModelPerformance]:
    """Realised MAE per model over the whole backtest (rows with realised truth)."""
    err = func.abs(BacktestPrediction.y_hat - BacktestPrediction.y_true)
    rows = session.execute(
        select(
            BacktestPrediction.model_name,
            func.avg(err).label("mae"),
            func.count().label("n"),
        )
        .where(
            BacktestPrediction.target_name == target_name,
            BacktestPrediction.y_true.is_not(None),
        )
        .group_by(BacktestPrediction.model_name)
        .order_by(func.avg(err))
    ).all()
    return [ModelPerformance(model_name=r.model_name, mae=float(r.mae), n=int(r.n)) for r in rows]


@app.get("/monitoring/freshness", response_model=list[SourceFreshness])
def freshness(session: SessionDep) -> list[SourceFreshness]:
    """Per-source data freshness — the daily-ingest watchdog."""
    return data_freshness(session)


@app.get("/monitoring/error/{target_name}", response_model=RealisedError)
def monitoring_error(target_name: TargetName, session: SessionDep, days: int = 14) -> RealisedError:
    """MAE of the live emitted forecast vs realised outcomes over the last ``days``."""
    return realised_error(session, target_name, days=days)


@app.get("/monitoring/dq", response_model=list[DqEvent])
def monitoring_dq(session: SessionDep, limit: int = 20) -> list[DqEvent]:
    """Recent data-quality / ingestion events (ops.dq_log) — durable ingestion health."""
    return recent_dq_events(session, limit=limit)
