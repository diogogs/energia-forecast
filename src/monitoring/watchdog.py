"""Monitoring — freshness watchdog + realised error of the live forecasts.

Freshness answers "is the system still fed?" (did the daily cron run, how recent is each source's
data). Realised error scores the live emitted forecasts against the outcomes as they arrive
(distinct from the backtest's simulated history) — sparse at first, it fills in day by day.
"""

from __future__ import annotations

import datetime as dt

import pandas as pd
from pydantic import BaseModel
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from src.db.models import (
    EnergyChartsPower,
    OmiePrice,
    OpenMeteoForecast,
    Prediction,
    RenRealised,
)
from src.features.asof_repo import CONSUMPTION_SERIES
from src.features.hourly import to_hourly

# The daily ingest runs every ~24h; flag a source stale if nothing new landed within this margin.
STALE_HOURS = 30

# (latest valid time column, ingest-time column) per raw source.
_SOURCES = {
    "omie_price": (OmiePrice.ts_utc, OmiePrice.first_seen_at),
    "ren_realised": (RenRealised.ts_utc, RenRealised.first_seen_at),
    "energy_charts_power": (EnergyChartsPower.ts_utc, EnergyChartsPower.first_seen_at),
    "openmeteo_forecast": (OpenMeteoForecast.ts_utc, OpenMeteoForecast.first_seen_at),
}


class SourceFreshness(BaseModel):
    source: str
    latest_data_ts: dt.datetime | None
    last_ingest_at: dt.datetime | None
    hours_since_ingest: float | None
    stale: bool


class RealisedError(BaseModel):
    target_name: str
    model_name: str
    days: int
    hours_scored: int
    mae: float | None


def data_freshness(session: Session, now: dt.datetime | None = None) -> list[SourceFreshness]:
    """Per raw source: the latest valid time it covers and when we last ingested into it."""
    now = now or dt.datetime.now(tz=dt.UTC)
    out: list[SourceFreshness] = []
    for name, (ts_col, ingest_col) in _SOURCES.items():
        latest_data, last_ingest = session.execute(
            select(func.max(ts_col), func.max(ingest_col))
        ).one()
        hours = (now - last_ingest).total_seconds() / 3600 if last_ingest is not None else None
        out.append(
            SourceFreshness(
                source=name,
                latest_data_ts=latest_data,
                last_ingest_at=last_ingest,
                hours_since_ingest=hours,
                stale=hours is None or hours > STALE_HOURS,
            )
        )
    return out


def _realised_hourly(
    session: Session, target_name: str, lo: dt.datetime, hi: dt.datetime
) -> pd.Series[float]:
    """Hourly-mean realised outcome over [lo, hi): consumption from REN, price from OMIE PT."""
    if target_name == "consumption":
        stmt = select(RenRealised.ts_utc, RenRealised.value_mw).where(
            RenRealised.series_name == CONSUMPTION_SERIES,
            RenRealised.ts_utc >= lo,
            RenRealised.ts_utc < hi,
        )
    else:
        stmt = select(OmiePrice.ts_utc, OmiePrice.price_eur_mwh).where(
            OmiePrice.zone == "PT", OmiePrice.ts_utc >= lo, OmiePrice.ts_utc < hi
        )
    return to_hourly([(ts, v) for ts, v in session.execute(stmt).all()])


def realised_error(
    session: Session, target_name: str, days: int = 14, model_name: str = "lightgbm"
) -> RealisedError:
    """MAE of the live emitted point forecast (P50 for price) vs realised over the last days."""
    quantile = "p50" if target_name == "price" else "point"
    stmt = (
        select(Prediction.target_ts, Prediction.y_hat)
        .where(
            Prediction.target_name == target_name,
            Prediction.model_name == model_name,
            Prediction.quantile == quantile,
        )
        .order_by(Prediction.target_ts)
    )
    preds = [(ts, y) for ts, y in session.execute(stmt).all()]
    if not preds:
        return RealisedError(
            target_name=target_name, model_name=model_name, days=days, hours_scored=0, mae=None
        )

    lo = max(preds[0][0], preds[-1][0] - dt.timedelta(days=days))
    hi = preds[-1][0] + dt.timedelta(hours=1)
    realised = _realised_hourly(session, target_name, lo, hi)

    errors = [abs(y - realised[ts]) for ts, y in preds if ts >= lo and ts in realised.index]
    mae = float(sum(errors) / len(errors)) if errors else None
    return RealisedError(
        target_name=target_name,
        model_name=model_name,
        days=days,
        hours_scored=len(errors),
        mae=mae,
    )
