"""Target (label) readers. The label is the realised value scored against, NOT a feature —
so it is read fully, without the as-of legality filter that governs features.
"""

from __future__ import annotations

import datetime as dt

import pandas as pd
from sqlalchemy import select
from sqlalchemy.orm import Session

from src.db.models import OmiePrice, RenRealised
from src.features import temporal
from src.features.asof_repo import CONSUMPTION_SERIES
from src.features.hourly import to_hourly


def consumption_target(session: Session, delivery_date_cet: dt.date) -> pd.Series[float]:
    """Realised PT consumption (MW), hourly UTC, over the CET market day ``delivery_date_cet``.

    This is the Phase-1 label y — 23/24/25 hourly values following the CET civil day.
    """
    hours = temporal.delivery_hours_utc(delivery_date_cet)
    stmt = select(RenRealised.ts_utc, RenRealised.value_mw).where(
        RenRealised.series_name == CONSUMPTION_SERIES,
        RenRealised.ts_utc >= hours[0],
        RenRealised.ts_utc < hours[-1] + dt.timedelta(hours=1),
    )
    pairs = [(ts, value) for ts, value in session.execute(stmt).all()]
    return to_hourly(pairs)


def price_target(
    session: Session, delivery_date_cet: dt.date, zone: str = "PT"
) -> pd.Series[float]:
    """Day-ahead MIBEL price (EUR/MWh), hourly-mean UTC, over the CET market day.

    This is the Phase-2 label — the eventual day-ahead price, forecast at 07:00 before the SDAC
    close. Hourly mean of the native 15-min/hourly prices (ADR-002).
    """
    hours = temporal.delivery_hours_utc(delivery_date_cet)
    stmt = select(OmiePrice.ts_utc, OmiePrice.price_eur_mwh).where(
        OmiePrice.zone == zone,
        OmiePrice.ts_utc >= hours[0],
        OmiePrice.ts_utc < hours[-1] + dt.timedelta(hours=1),
    )
    pairs = [(ts, price) for ts, price in session.execute(stmt).all()]
    return to_hourly(pairs)
