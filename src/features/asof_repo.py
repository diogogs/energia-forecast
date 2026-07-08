"""AsOfRepo — the ONLY sanctioned read path for feature data (CLAUDE.md "Modelo temporal").

Given a fixed ``t_issue``, it returns each raw series legally: only rows whose *modelled*
publication time (temporal.py) is ``<= t_issue``, resampled to the hourly UTC grid ("clean"
computed on the fly, not materialised — ADR-011). Feature code must never query raw directly;
legality lives here and is guarded by the ``leakage`` tests.
"""

from __future__ import annotations

import datetime as dt

import pandas as pd
from sqlalchemy import select
from sqlalchemy.orm import Session

from src.db.models import OmiePrice, RenRealised
from src.features import temporal

CONSUMPTION_SERIES = "Consumption"  # the Phase-1 target series in raw.ren_realised


def _to_hourly(pairs: list[tuple[dt.datetime, float]]) -> pd.Series[float]:
    """Resample (ts_utc, value) pairs to the hourly UTC grid by mean; empty in -> empty out."""
    if not pairs:
        return pd.Series(dtype="float64")
    index = pd.DatetimeIndex([ts for ts, _ in pairs])
    series = pd.Series([value for _, value in pairs], index=index, dtype="float64").sort_index()
    return series.resample("1h").mean().dropna()


class AsOfRepo:
    """Legal, hourly, as-of-``t_issue`` reads over the raw layer."""

    def __init__(self, session: Session) -> None:
        self._session = session

    def hourly_consumption(self, t_issue: dt.datetime) -> pd.Series[float]:
        """Realised PT consumption (MW), hourly UTC, published by ``t_issue``.

        REN publishes by the next Lisbon midnight, so at 07:00 UTC of D this ends at the close
        of Lisbon day D-1 — day D is incomplete and excluded (guards the 24h-lag leak). A legal
        value always has ``ts_utc < t_issue``, so that is a sound coarse filter.
        """
        stmt = select(RenRealised.ts_utc, RenRealised.value_mw).where(
            RenRealised.series_name == CONSUMPTION_SERIES,
            RenRealised.ts_utc < t_issue,
        )
        legal = [
            (ts, value)
            for ts, value in self._session.execute(stmt).all()
            if temporal.ren_published_at(ts) <= t_issue
        ]
        return _to_hourly(legal)

    def hourly_price(self, zone: str, t_issue: dt.datetime) -> pd.Series[float]:
        """MIBEL day-ahead price (EUR/MWh) for ``zone``, hourly-mean UTC, published by ``t_issue``.

        Day-ahead prices are published the day before delivery (~13:00 CET), so unlike
        consumption a legal price can have ``ts_utc`` in the future relative to ``t_issue`` — the
        coarse filter is on ``market_date``, and legality is decided by the publication time.
        """
        stmt = select(OmiePrice.ts_utc, OmiePrice.market_date, OmiePrice.price_eur_mwh).where(
            OmiePrice.zone == zone,
            OmiePrice.market_date <= t_issue.date(),
        )
        legal = [
            (ts, price)
            for ts, market_date, price in self._session.execute(stmt).all()
            if temporal.omie_published_at(market_date) <= t_issue
        ]
        return _to_hourly(legal)
