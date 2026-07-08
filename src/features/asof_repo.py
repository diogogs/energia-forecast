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

from src.db.models import OmiePrice, OpenMeteoForecast, RenRealised
from src.features import temporal
from src.features.hourly import to_hourly

CONSUMPTION_SERIES = "Consumption"  # the Phase-1 target series in raw.ren_realised


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
        return to_hourly(legal)

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
        return to_hourly(legal)

    def weather_forecast(
        self, t_issue: dt.datetime, target_hours: list[dt.datetime]
    ) -> pd.DataFrame:
        """Archived weather forecast for the target hours, legal as-of ``t_issue``.

        For each (location, variable, hour) we take the FRESHEST lead whose run was disseminated
        by ``t_issue`` (lead 1 for a D+1 hour, else lead 2), then average over locations. Columns
        are the Open-Meteo variables; the index is ``target_hours``. These are forecasts, so
        their valid times are legitimately in the future relative to ``t_issue``.
        """
        if not target_hours:
            return pd.DataFrame()
        stmt = select(
            OpenMeteoForecast.location,
            OpenMeteoForecast.variable,
            OpenMeteoForecast.lead_days,
            OpenMeteoForecast.ts_utc,
            OpenMeteoForecast.value,
        ).where(
            OpenMeteoForecast.ts_utc >= target_hours[0],
            OpenMeteoForecast.ts_utc <= target_hours[-1],
        )
        legal = [
            {"location": loc, "variable": var, "lead_days": lead, "ts_utc": ts, "value": value}
            for loc, var, lead, ts, value in self._session.execute(stmt).all()
            if temporal.openmeteo_published_at(ts, lead) <= t_issue
        ]
        index = pd.DatetimeIndex(target_hours)
        if not legal:
            return pd.DataFrame(index=index)
        frame = pd.DataFrame(legal)
        # Freshest legal lead per (location, variable, hour), then mean over locations.
        freshest = frame.sort_values("lead_days").drop_duplicates(
            ["location", "variable", "ts_utc"], keep="first"
        )
        wide = freshest.groupby(["ts_utc", "variable"])["value"].mean().unstack("variable")
        return wide.reindex(index)
