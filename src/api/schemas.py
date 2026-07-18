"""Pydantic v2 response models for the read-only serving API."""

from __future__ import annotations

import datetime as dt

from pydantic import BaseModel, ConfigDict


class Health(BaseModel):
    status: str
    database: bool
    latest_issue_date: dt.date | None = None


class ForecastPoint(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    target_ts: dt.datetime
    model_name: str
    quantile: str
    y_hat: float


class Forecast(BaseModel):
    target_name: str
    issue_date: dt.date
    issued_at: dt.datetime | None
    points: list[ForecastPoint]


class BacktestPoint(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    issue_date: dt.date
    target_ts: dt.datetime
    model_name: str
    y_hat: float
    y_true: float | None


class ModelPerformance(BaseModel):
    model_name: str
    mae: float
    n: int


class HistoryPoint(BaseModel):
    """A live emitted prediction paired with the realised outcome (null until it arrives)."""

    target_ts: dt.datetime
    model_name: str
    quantile: str
    y_hat: float
    y_true: float | None


class Emission(BaseModel):
    """One production emission: when a day's forecast was actually issued, per target.

    The autonomy record — late emissions are included and flagged, never hidden."""

    issue_date: dt.date
    target_name: str
    issued_at: dt.datetime
    late_issue: bool
    n_hours: int
