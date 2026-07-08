"""REN Data Hub ingestion — realised PT consumption + generation by technology.

One public endpoint, ``Electricity/ProductionBreakdown``, returns BOTH the Phase-1 target
(the ``Consumption`` series, realised MW) and every generation/flow technology on a shared
15-minute grid (ADR-008). No token. Verified live 2026-07-08.

Contract (discovered empirically):
  POST https://datahub.ren.pt/service/Electricity/ProductionBreakdown/{chart_id}
       ?culture=en-GB&dayToSearchString={ticks}
  body: ``{}`` (empty JSON). Response is Highcharts JSON:
  ``{"xAxis": {"categories": [...]}, "series": [{"name", "data": [float|null, ...]}, ...]}``.

Time model (the project's identity — see CLAUDE.md "Modelo temporal"):
  * REN slots are **Lisbon** civil time (WET/WEST), NOT CET. The ``data`` array length is
    DST-correct by construction: 96 normal, 92 spring-forward, 100 fall-back. We anchor at
    Lisbon local midnight and step 15 min in UTC for N slots — the same DST-safe method as
    the OMIE parser, retargeted to Europe/Lisbon (verified: slot UTC and MW match
    Energy-Charts PT load to the decimal, lag 0h, corr 1.0).
  * ``culture=en-GB`` is pinned so series names are stable English labels — the raw table's
    natural key depends on them.
"""

from __future__ import annotations

import datetime as dt
from dataclasses import dataclass
from typing import Any
from zoneinfo import ZoneInfo

import httpx
from tenacity import retry, retry_if_exception, stop_after_attempt, wait_exponential

# A decoded JSON object (the REN Highcharts payload).
JsonObj = dict[str, Any]

# REN reports in Portuguese civil time.
LISBON = ZoneInfo("Europe/Lisbon")

_SERVICE_URL = (
    "https://datahub.ren.pt/service/Electricity/ProductionBreakdown/{chart_id}"
    "?culture=en-GB&dayToSearchString={ticks}"
)
# Any ProductionBreakdown chart id returns the same underlying data; 1266 is the homepage one.
DEFAULT_CHART_ID = 1266

# .NET DateTime epoch (0001-01-01) — dayToSearchString is DateTime.Ticks (100 ns units).
_DOTNET_EPOCH = dt.datetime(1, 1, 1)  # noqa: DTZ001 — a fixed offset constant, not a wall clock

# Slots per day are DST-dependent: 92 (23h spring-forward), 96 (24h), 100 (25h fall-back).
# The parser derives the expected count from the Lisbon civil calendar rather than a set.
_RESOLUTION_MINUTES = 15


class RenParseError(ValueError):
    """The ProductionBreakdown payload is malformed or has an unexpected slot count."""


@dataclass(frozen=True, slots=True)
class RenResponse:
    """A fetched ProductionBreakdown payload plus the provenance string for its rows."""

    source_ref: str  # e.g. 'ren:ProductionBreakdown/1266:ticks=638527968000000000'
    payload: JsonObj


@dataclass(frozen=True, slots=True)
class RenObservation:
    """One realised value for one series at one 15-min slot."""

    series_name: str  # verbatim REN en-GB label, e.g. 'Consumption', 'Wind'
    ts_utc: dt.datetime  # slot start, tz-aware UTC
    resolution_minutes: int  # always 15 for this endpoint
    value_mw: float  # realised power in MW (signed — Imports/Battery can be negative)
    local_date: dt.date  # Lisbon civil day the slot belongs to (NOT the CET market day)
    period: int  # 1-based slot index within local_date (1..N)


def to_ticks(day: dt.date) -> int:
    """.NET DateTime.Ticks of ``day`` at midnight (the ``dayToSearchString`` value)."""
    delta = dt.datetime(day.year, day.month, day.day) - _DOTNET_EPOCH  # noqa: DTZ001 — date selector, tz-agnostic
    return int(delta.total_seconds()) * 10_000_000


def parse_production_breakdown(payload: JsonObj, day: dt.date) -> list[RenObservation]:
    """Parse a ProductionBreakdown payload for ``day`` into DST-correct observations.

    Null slots (not-yet-published / non-existent series) are skipped, so a row exists iff
    REN published a realised value for that (series, slot).
    """
    series = payload.get("series")
    if not isinstance(series, list) or not series:
        raise RenParseError("payload has no series")

    names: list[str] = []
    for s in series:
        name = s.get("name")
        if not isinstance(name, str) or not name:
            raise RenParseError(f"series without a name: {s!r}")
        names.append(name)
    if len(set(names)) != len(names):
        # Duplicates would produce repeated natural keys inside one INSERT — Postgres
        # rejects that ("ON CONFLICT DO UPDATE cannot affect row a second time").
        raise RenParseError(f"duplicate series names in payload: {sorted(names)}")

    lengths = {len(s.get("data", [])) for s in series}
    if len(lengths) != 1:
        raise RenParseError(f"series have inconsistent slot counts: {sorted(lengths)}")
    n_slots = next(iter(lengths))

    # Lisbon local midnight is unambiguous (DST flips at 01:00/02:00 local, never midnight).
    start_utc = dt.datetime(day.year, day.month, day.day, tzinfo=LISBON).astimezone(dt.UTC)
    step = dt.timedelta(minutes=_RESOLUTION_MINUTES)

    # The slot count must match THIS day's civil length (92/96/100 encodes the 23/24/25h
    # day). A valid-looking count on the wrong day (e.g. 96 slots on a 23h day) would emit
    # timestamps that spill into the next day and collide with its rows on upsert.
    next_day = day + dt.timedelta(days=1)
    end_utc = dt.datetime(next_day.year, next_day.month, next_day.day, tzinfo=LISBON).astimezone(
        dt.UTC
    )
    expected_slots = int((end_utc - start_utc) / step)
    if n_slots != expected_slots:
        raise RenParseError(
            f"slot count {n_slots} does not match the {expected_slots}-slot Lisbon day {day}"
        )

    observations: list[RenObservation] = []
    for s in series:
        name = s["name"]
        for i, value in enumerate(s["data"]):
            if value is None:
                continue  # not-yet-published slot or absent series — emit no row
            observations.append(
                RenObservation(
                    series_name=name,
                    ts_utc=start_utc + i * step,
                    resolution_minutes=_RESOLUTION_MINUTES,
                    value_mw=float(value),
                    local_date=day,
                    period=i + 1,
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
def _post(client: httpx.Client, url: str) -> httpx.Response:
    response = client.post(
        url,
        content="{}",
        headers={"Content-Type": "application/json"},
        timeout=40.0,
        follow_redirects=True,
    )
    # 404 is a legitimate signal (day not available) — returned, never raised/retried.
    if response.status_code != 404:
        response.raise_for_status()
    return response


def fetch_production_breakdown(
    day: dt.date, client: httpx.Client | None = None, chart_id: int = DEFAULT_CHART_ID
) -> RenResponse | None:
    """Download one day's ProductionBreakdown; None if the service returns no usable payload."""
    owns_client = client is None
    client = client or httpx.Client()
    ticks = to_ticks(day)
    try:
        response = _post(client, _SERVICE_URL.format(chart_id=chart_id, ticks=ticks))
        if response.status_code == 404:
            return None
        try:
            payload = response.json()
        except ValueError:  # non-JSON 200 (maintenance page etc.) — no usable payload
            return None
        if not isinstance(payload, dict) or "series" not in payload:
            return None
        source_ref = f"ren:ProductionBreakdown/{chart_id}:ticks={ticks}"
        return RenResponse(source_ref=source_ref, payload=payload)
    finally:
        if owns_client:
            client.close()


def get_observations(
    day: dt.date, client: httpx.Client | None = None, chart_id: int = DEFAULT_CHART_ID
) -> list[RenObservation]:
    """Fetch and parse one day; empty list if nothing is published."""
    response = fetch_production_breakdown(day, client, chart_id)
    return parse_production_breakdown(response.payload, day) if response is not None else []
