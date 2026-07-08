"""Open-Meteo Previous Runs ingestion — archived ECMWF forecasts for LEAKAGE-FREE training.

Training weather MUST be the forecast as it was known at issue time, never reanalysis/observed
(CLAUDE.md "Modelo temporal"). The Open-Meteo Previous Runs API archives past model runs: for
each valid time T it exposes ``{var}_previous_dayN`` = the value for T from the run initialized
N days before T's date. Verified live: these are tied to the valid time (identical regardless of
when queried), so they faithfully reconstruct what the pinned model predicted N days ahead.

We store only ``lead_days`` 1 and 2 — never the no-suffix "most recent run", which for a past
date is a near-analysis short-lead forecast (leakage). For a D+1 forecast issued at 07:00 UTC of
day D: ``lead_days=1`` is the run from D (freshest legal); ``lead_days=2`` is the run from D-1
(older legal / revision feature). Choosing the lead by ``t_issue`` legality is a features-layer
job — raw stores both faithfully (ADR-010).

Model pinned to ``ecmwf_ifs025`` (identical in training and production). Coverage from ~2024-03.
"""

from __future__ import annotations

import datetime as dt
from dataclasses import dataclass
from typing import Any

import httpx
from tenacity import retry, retry_if_exception, stop_after_attempt, wait_exponential

JsonObj = dict[str, Any]

_BASE_URL = "https://previous-runs-api.open-meteo.com/v1/forecast"

# Pinned model — the same run family in training and production (temporal rigor).
MODEL = "ecmwf_ifs025"

# Iberian points (requested coords; Open-Meteo snaps to its grid). Order is stable and maps
# response[i] -> slug: coastal north/centre + southern interior (PT demand & renewable spread).
LOCATIONS: dict[str, tuple[float, float]] = {
    "lisbon": (38.72, -9.14),
    "porto": (41.15, -8.61),
    "evora": (38.57, -7.91),
}
# Consumption (temp/HDD/CDD), wind generation proxy (100m), solar proxy (GHI).
VARIABLES = ("temperature_2m", "wind_speed_100m", "shortwave_radiation")
# Archived leads to store: 1 = freshest legal run, 2 = older legal run / revision feature.
LEADS = (1, 2)

_LEAD_MARKER = "_previous_day"


class OpenMeteoParseError(ValueError):
    """The Previous Runs payload is malformed."""


@dataclass(frozen=True, slots=True)
class ForecastResponse:
    """A fetched Previous Runs payload (one entry per location) plus its provenance string."""

    source_ref: str  # e.g. 'openmeteo:previous_runs:ecmwf_ifs025:2024-06-01..2024-06-30'
    payload: list[JsonObj]


@dataclass(frozen=True, slots=True)
class ForecastObservation:
    """One archived forecast value for one (location, variable, lead) at one valid hour."""

    location: str  # slug, e.g. 'lisbon'
    variable: str  # e.g. 'temperature_2m'
    lead_days: int  # 1 or 2 — the run was initialized this many days before the valid date
    ts_utc: dt.datetime  # valid time, tz-aware UTC
    value: float
    unit: str  # native Open-Meteo unit, e.g. '°C', 'km/h', 'W/m²'


def _hourly_param() -> str:
    """The comma-separated hourly variable list, e.g. 'temperature_2m_previous_day1,...'."""
    return ",".join(f"{v}{_LEAD_MARKER}{d}" for v in VARIABLES for d in LEADS)


def _split_variable_lead(key: str) -> tuple[str, int] | None:
    """'temperature_2m_previous_day1' -> ('temperature_2m', 1); None if not a lead key."""
    variable, marker, lead = key.rpartition(_LEAD_MARKER)
    if not marker or not lead.isdigit():
        return None
    return variable, int(lead)


def parse_previous_runs(
    payload: list[JsonObj], locations: dict[str, tuple[float, float]] = LOCATIONS
) -> list[ForecastObservation]:
    """Parse a Previous Runs payload (one entry per location, in request order) into observations.

    Null values are skipped. Non-lead hourly keys (should not occur given our request) are
    ignored; the ``time`` axis is required.
    """
    slugs = list(locations)
    if len(payload) != len(slugs):
        raise OpenMeteoParseError(f"expected {len(slugs)} locations, got {len(payload)}")

    observations: list[ForecastObservation] = []
    for slug, entry in zip(slugs, payload, strict=True):
        hourly = entry.get("hourly")
        units = entry.get("hourly_units", {})
        if not isinstance(hourly, dict) or "time" not in hourly:
            raise OpenMeteoParseError(f"location {slug!r}: missing hourly.time")
        times = hourly["time"]
        ts = [dt.datetime.fromisoformat(t).replace(tzinfo=dt.UTC) for t in times]

        for key, values in hourly.items():
            if key == "time":
                continue
            split = _split_variable_lead(key)
            if split is None:
                continue
            variable, lead = split
            if not isinstance(values, list) or len(values) != len(times):
                raise OpenMeteoParseError(f"{slug}/{key}: values length != time length")
            unit = units.get(key, "")
            for i, value in enumerate(values):
                if value is None:
                    continue
                observations.append(
                    ForecastObservation(
                        location=slug,
                        variable=variable,
                        lead_days=lead,
                        ts_utc=ts[i],
                        value=float(value),
                        unit=unit,
                    )
                )
    return observations


def _is_transient(exc: BaseException) -> bool:
    """Network failures and transient server statuses (5xx, 429) are worth retrying."""
    if isinstance(exc, httpx.TransportError):
        return True
    return isinstance(exc, httpx.HTTPStatusError) and (
        exc.response.status_code >= 500 or exc.response.status_code == 429
    )


@retry(
    reraise=True,
    stop=stop_after_attempt(5),
    wait=wait_exponential(multiplier=1, min=1, max=30),
    retry=retry_if_exception(_is_transient),
)
def _get(client: httpx.Client, params: dict[str, str]) -> httpx.Response:
    response = client.get(_BASE_URL, params=params, timeout=60.0, follow_redirects=True)
    if response.status_code != 404:
        response.raise_for_status()
    return response


def fetch_previous_runs(
    start: dt.date,
    end: dt.date,
    client: httpx.Client | None = None,
    locations: dict[str, tuple[float, float]] = LOCATIONS,
) -> ForecastResponse | None:
    """Fetch archived forecasts for all locations over ``[start, end]``; None if no payload."""
    owns_client = client is None
    client = client or httpx.Client()
    params = {
        "latitude": ",".join(str(lat) for lat, _ in locations.values()),
        "longitude": ",".join(str(lon) for _, lon in locations.values()),
        "hourly": _hourly_param(),
        "models": MODEL,
        "start_date": start.isoformat(),
        "end_date": end.isoformat(),
        "timezone": "UTC",
    }
    try:
        response = _get(client, params)
        if response.status_code == 404:
            return None
        try:
            payload = response.json()
        except ValueError:
            return None
        # A single location returns an object; we always request several -> a list.
        entries = payload if isinstance(payload, list) else [payload]
        if not entries or not isinstance(entries[0], dict) or "hourly" not in entries[0]:
            return None
        source_ref = f"openmeteo:previous_runs:{MODEL}:{start.isoformat()}..{end.isoformat()}"
        return ForecastResponse(source_ref=source_ref, payload=entries)
    finally:
        if owns_client:
            client.close()


def get_observations(
    start: dt.date,
    end: dt.date,
    client: httpx.Client | None = None,
    locations: dict[str, tuple[float, float]] = LOCATIONS,
) -> list[ForecastObservation]:
    """Fetch and parse a date range; empty list if nothing is published."""
    response = fetch_previous_runs(start, end, client, locations)
    return parse_previous_runs(response.payload, locations) if response is not None else []
