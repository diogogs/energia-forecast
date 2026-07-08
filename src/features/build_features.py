"""build_features — the legal feature matrix for the Phase-1 consumption forecast.

One row per delivery hour of the CET market day D+1, built strictly as-of ``t_issue`` via the
AsOfRepo (never a direct query). Consumption lags are relative to the TARGET hour and chosen so
they are always published by ``t_issue``: {48,72,168,336}h from a D+1 hour land in D-1 or
earlier (the charter's legal lags; the 24h lag would be leakage — day D is incomplete). Calendar
features follow **Lisbon** civil time, the physical driver of PT demand.
"""

from __future__ import annotations

import datetime as dt

import holidays
import pandas as pd

from src.features import temporal
from src.features.asof_repo import AsOfRepo

# Legal consumption lags relative to the target hour (hours). 24h is deliberately excluded.
CONSUMPTION_LAGS_H = (48, 72, 168, 336)

_PT_HOLIDAYS = holidays.country_holidays("PT")


def build_consumption_features(repo: AsOfRepo, issue_date: dt.date) -> pd.DataFrame:
    """Feature matrix (index = delivery-hour ts_utc) for the consumption forecast issued on D."""
    t_issue = temporal.t_issue_for(issue_date)
    delivery_hours = temporal.delivery_hours_utc(temporal.delivery_date_for(issue_date))
    legal_consumption = repo.hourly_consumption(t_issue)

    # A recent-level scalar: mean over the last complete legal day (up to end of Lisbon D-1).
    recent_day_mean = (
        float(legal_consumption.tail(24).mean()) if len(legal_consumption) >= 24 else float("nan")
    )

    rows: list[dict[str, object]] = []
    for target_ts in delivery_hours:
        local = target_ts.astimezone(temporal.LISBON)
        row: dict[str, object] = {
            "target_ts": target_ts,
            "hour": local.hour,
            "dow": local.weekday(),
            "month": local.month,
            "is_weekend": local.weekday() >= 5,
            "is_holiday": bool(local.date() in _PT_HOLIDAYS),
            "cons_recent_day_mean": recent_day_mean,
        }
        for lag_h in CONSUMPTION_LAGS_H:
            key = pd.Timestamp(target_ts - dt.timedelta(hours=lag_h))
            row[f"cons_lag_{lag_h}h"] = float(legal_consumption.get(key, float("nan")))
        rows.append(row)

    return pd.DataFrame(rows).set_index("target_ts")
