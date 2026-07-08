"""Energy-Charts (Fraunhofer ISE) ingestion — Spanish load + generation as FEATURES.

REN is PT-only; Energy-Charts fills the ES gap (ADR-007). It re-exposes ENTSO-E data with
no token (CC-BY 4.0). Used only for ES *features* (Load + generation by technology), never a
target — an outage degrades features, never the labels.

Contract (verified live 2026-07-08):
  GET https://api.energy-charts.info/public_power?country=es&start=YYYY-MM-DD&end=YYYY-MM-DD
  Response: ``{"unix_seconds": [...], "production_types": [{"name", "data": [float|null]}, ...]}``.

Time model — the easy case: ``unix_seconds`` are already UTC instants, so there is NO
local-midnight anchoring and DST is automatic (a 23h spring-forward day simply has 92 slots).
The slot resolution is derived from the timestamp spacing (900s = 15-min across our window).

Scope: this table stores MW power series only. The two derived percentage series
("Renewable share of load/generation") are skipped — they are a different unit and trivially
recomputed downstream. ``end`` is inclusive; the ``data`` values are MW (signed: cross-border
trading and pumped-storage consumption go negative).
"""

from __future__ import annotations

import datetime as dt
from dataclasses import dataclass
from typing import Any

import httpx
from tenacity import retry, retry_if_exception, stop_after_attempt, wait_exponential

JsonObj = dict[str, Any]

_BASE_URL = "https://api.energy-charts.info/public_power"
DEFAULT_COUNTRY = "es"

# We keep only the ES series that carry forecasting signal, not all ~17 production types
# (ADR-009). Rationale: at t_issue the target-day ES generation is unpublished, so these
# enter models only as LAGGED features — ES demand + renewable proxies + net position are
# enough; storing every technology at 15-min would blow the Neon free-tier budget. Reversible:
# widen this set and re-run the (idempotent) backfill. Also drops the %-share series (not MW).
FEATURE_TYPES = frozenset(
    {
        "Load",  # ES demand (correlates with PT demand; drives MIBEL price)
        "Solar",  # ES solar — midday price depressor, PT-correlated
        "Wind onshore",  # ES wind — price driver, PT-correlated
        "Cross border electricity trading",  # ES net import/export position (signed)
    }
)


class EnergyChartsParseError(ValueError):
    """The public_power payload is malformed."""


@dataclass(frozen=True, slots=True)
class PublicPowerResponse:
    """A fetched public_power payload plus the provenance string for its rows."""

    source_ref: str  # e.g. 'energy_charts:public_power:es:2024-01-01..2024-01-31'
    payload: JsonObj


@dataclass(frozen=True, slots=True)
class PowerObservation:
    """One realised value for one production type at one slot."""

    country: str  # ISO-2 lower-case, e.g. 'es'
    production_type: str  # verbatim Energy-Charts label, e.g. 'Load', 'Wind onshore'
    ts_utc: dt.datetime  # slot start, tz-aware UTC (native from unix_seconds)
    resolution_minutes: int  # derived from timestamp spacing (15 across our window)
    value_mw: float  # realised power in MW (signed)


def _resolutions(unix_seconds: list[int]) -> list[int]:
    """Per-slot resolution in minutes, derived from the gap to the next slot.

    The last slot reuses the preceding gap. Robust to a resolution change inside a range
    (e.g. an hourly→15-min boundary), which our 2024+ window never actually crosses.
    """
    n = len(unix_seconds)
    if n < 2:
        raise EnergyChartsParseError("need at least two timestamps to derive resolution")
    gaps = [unix_seconds[i + 1] - unix_seconds[i] for i in range(n - 1)]
    gaps.append(gaps[-1])
    out: list[int] = []
    for g in gaps:
        if g <= 0 or g % 60 != 0:
            raise EnergyChartsParseError(f"non-positive or sub-minute timestamp gap: {g}s")
        out.append(g // 60)
    return out


def parse_public_power(payload: JsonObj, country: str = DEFAULT_COUNTRY) -> list[PowerObservation]:
    """Parse a public_power payload into UTC-native observations (skips %-share series)."""
    unix_seconds = payload.get("unix_seconds")
    production_types = payload.get("production_types")
    if not isinstance(unix_seconds, list) or not isinstance(production_types, list):
        raise EnergyChartsParseError("payload missing unix_seconds/production_types")
    if not unix_seconds:
        return []  # empty range (e.g. not yet published) — not an error

    names = [p.get("name") for p in production_types]
    if any(not isinstance(n, str) or not n for n in names):
        raise EnergyChartsParseError("a production_type has no name")
    if len(set(names)) != len(names):
        raise EnergyChartsParseError(f"duplicate production_type names: {sorted(names)}")

    resolutions = _resolutions(unix_seconds)
    ts = [dt.datetime.fromtimestamp(s, tz=dt.UTC) for s in unix_seconds]

    observations: list[PowerObservation] = []
    for p in production_types:
        name = p["name"]
        if name not in FEATURE_TYPES:
            continue  # not a curated feature series (or a %-share series) — skip
        data = p.get("data")
        if not isinstance(data, list) or len(data) != len(unix_seconds):
            raise EnergyChartsParseError(f"series {name!r} length != unix_seconds length")
        for i, value in enumerate(data):
            if value is None:
                continue
            observations.append(
                PowerObservation(
                    country=country,
                    production_type=name,
                    ts_utc=ts[i],
                    resolution_minutes=resolutions[i],
                    value_mw=float(value),
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


def fetch_public_power(
    start: dt.date,
    end: dt.date,
    country: str = DEFAULT_COUNTRY,
    client: httpx.Client | None = None,
) -> PublicPowerResponse | None:
    """Download public_power for ``[start, end]`` (inclusive); None if no usable payload."""
    owns_client = client is None
    client = client or httpx.Client()
    params = {"country": country, "start": start.isoformat(), "end": end.isoformat()}
    try:
        response = _get(client, params)
        if response.status_code == 404:
            return None
        try:
            payload = response.json()
        except ValueError:
            return None
        if not isinstance(payload, dict) or "unix_seconds" not in payload:
            return None
        source_ref = f"energy_charts:public_power:{country}:{start.isoformat()}..{end.isoformat()}"
        return PublicPowerResponse(source_ref=source_ref, payload=payload)
    finally:
        if owns_client:
            client.close()


def get_observations(
    start: dt.date,
    end: dt.date,
    country: str = DEFAULT_COUNTRY,
    client: httpx.Client | None = None,
) -> list[PowerObservation]:
    """Fetch and parse a date range; empty list if nothing is published."""
    response = fetch_public_power(start, end, country, client)
    return parse_public_power(response.payload, country) if response is not None else []
