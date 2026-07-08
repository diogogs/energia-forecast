"""Shared helper: resample (ts_utc, value) pairs onto the hourly UTC grid by mean."""

from __future__ import annotations

import datetime as dt

import pandas as pd


def to_hourly(pairs: list[tuple[dt.datetime, float]]) -> pd.Series[float]:
    """Hourly-mean UTC series from raw pairs; empty in -> empty out."""
    if not pairs:
        return pd.Series(dtype="float64")
    index = pd.DatetimeIndex([ts for ts, _ in pairs])
    series = pd.Series([value for _, value in pairs], index=index, dtype="float64").sort_index()
    return series.resample("1h").mean().dropna()
