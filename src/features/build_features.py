"""build_features — the legal feature matrix for the Phase-1 consumption forecast.

One row per delivery hour of the CET market day D+1, built strictly as-of ``t_issue`` via the
AsOfRepo (never a direct query). Consumption lags are relative to the TARGET hour and chosen so
they are always published by ``t_issue``: {48,72,168,336}h from a D+1 hour land in D-1 or
earlier (the charter's legal lags; the 24h lag would be leakage — day D is incomplete). Calendar
features follow **Lisbon** civil time, the physical driver of PT demand.
"""

from __future__ import annotations

import datetime as dt
from typing import Protocol

import holidays
import pandas as pd

from src.features import temporal


class FeatureRepo(Protocol):
    """The as-of read surface build_features depends on (satisfied by AsOfRepo and the
    preloaded backtest repo alike)."""

    def hourly_consumption(self, t_issue: dt.datetime) -> pd.Series[float]: ...

    def weather_forecast(
        self, t_issue: dt.datetime, target_hours: list[dt.datetime]
    ) -> pd.DataFrame: ...


# Legal consumption lags relative to the target hour (hours). 24h is deliberately excluded.
CONSUMPTION_LAGS_H = (48, 72, 168, 336)

# Weather transforms. Degree-hour bases for Iberia; wind is capped near a turbine's rated speed
# before cubing (power ~ wind^3 up to rated, flat after). Units: temp °C, wind km/h.
HDD_BASE_C = 18.0
CDD_BASE_C = 21.0
WIND_CAP_KMH = 43.0  # ~12 m/s

_PT_HOLIDAYS = holidays.country_holidays("PT")


def _add_weather_features(features: pd.DataFrame, weather: pd.DataFrame) -> pd.DataFrame:
    """Join location-averaged forecast weather and derive HDD/CDD/wind^3/radiation."""
    temp = weather["temperature_2m"] if "temperature_2m" in weather else pd.Series(dtype="float64")
    wind = (
        weather["wind_speed_100m"] if "wind_speed_100m" in weather else pd.Series(dtype="float64")
    )
    radiation = (
        weather["shortwave_radiation"]
        if "shortwave_radiation" in weather
        else pd.Series(dtype="float64")
    )
    derived = pd.DataFrame(index=features.index)
    derived["temp"] = temp.reindex(features.index)
    derived["hdd"] = (HDD_BASE_C - derived["temp"]).clip(lower=0)
    derived["cdd"] = (derived["temp"] - CDD_BASE_C).clip(lower=0)
    derived["wind_cube"] = wind.reindex(features.index).clip(upper=WIND_CAP_KMH) ** 3
    derived["radiation"] = radiation.reindex(features.index)
    return features.join(derived)


def build_consumption_features(repo: FeatureRepo, issue_date: dt.date) -> pd.DataFrame:
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

    features = pd.DataFrame(rows).set_index("target_ts")
    weather = repo.weather_forecast(t_issue, delivery_hours)
    return _add_weather_features(features, weather)
