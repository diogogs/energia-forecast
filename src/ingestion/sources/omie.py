"""OMIE MIBEL day-ahead marginal price ingestion.

We parse the public ``marginalpdbc`` text files ourselves: the OMIEData library
silently truncates post-2025-10-01 15-minute files, relabelling quarter-hours as
hours (ADR-006). Price column order verified empirically against Energy-Charts
(ADR-007): field 5 = Portugal, field 6 = Spain.

The files carry no timezone. Periods are in market time (CET/CEST). We convert to
UTC here so the raw layer stores tz-aware UTC timestamps like every other source.
"""

from __future__ import annotations

import datetime as dt
from dataclasses import dataclass
from zoneinfo import ZoneInfo

import httpx
from tenacity import retry, retry_if_exception_type, stop_after_attempt, wait_exponential

# OMIE market time is CET/CEST — the same civil time as Madrid.
MARKET_TZ = ZoneInfo("Europe/Madrid")

_DOWNLOAD_URL = (
    "https://www.omie.es/en/file-download?parents%5B0%5D=marginalpdbc&filename={filename}"
)
_FILENAME = "marginalpdbc_{ymd}.{version}"
_HEADER = "MARGINALPDBC"

# OMIE names each day's file marginalpdbc_YYYYMMDD.V where V is the publication version.
# .1 is the norm, but a re-issue can withdraw .1 (and .2) and publish only a higher V —
# empirically seen up to .3 (e.g. 2025-10-30). We take the LOWEST version that exists, so
# normal days still hit .1 in one request; we scan up to this cap before declaring a day absent.
_MAX_VERSION = 5

# Number of periods in a file -> market-day resolution in minutes.
#   Hourly (pre-2025-10-01):      24 normal, 23 spring-forward, 25 fall-back.
#   Quarter-hourly (15-min MTU):  96 normal, 92 spring-forward, 100 fall-back.
_PERIODS_TO_RESOLUTION = {23: 60, 24: 60, 25: 60, 92: 15, 96: 15, 100: 15}


class OmieParseError(ValueError):
    """The marginalpdbc payload is malformed or has an unexpected period count."""


@dataclass(frozen=True, slots=True)
class MarginalpdbcFile:
    """A fetched marginalpdbc file plus the exact filename (incl. version) it came from."""

    filename: str
    text: str


@dataclass(frozen=True, slots=True)
class MarginalPrice:
    """MIBEL day-ahead price for one market period."""

    ts_utc: dt.datetime  # start of the period, tz-aware UTC
    market_date: dt.date  # OMIE delivery day (CET calendar)
    period: int  # 1-based period index within the market day
    resolution_minutes: int  # 60 or 15
    price_pt: float  # EUR/MWh (Portugal)
    price_es: float  # EUR/MWh (Spain)


def parse_marginalpdbc(text: str) -> list[MarginalPrice]:
    """Parse a marginalpdbc file body into resolution- and DST-aware records."""
    lines = [line.strip() for line in text.strip().splitlines()]
    if not lines or not lines[0].startswith(_HEADER):
        raise OmieParseError("missing MARGINALPDBC header")

    rows: list[tuple[int, int, int, int, float, float]] = []
    for line in lines[1:]:
        if not line or line.startswith("*"):
            continue
        fields = line.rstrip(";").split(";")
        if len(fields) < 6 or not fields[3].isdigit():
            raise OmieParseError(f"malformed data line: {line!r}")
        rows.append(
            (
                int(fields[0]),
                int(fields[1]),
                int(fields[2]),
                int(fields[3]),
                float(fields[4].replace(",", ".")),
                float(fields[5].replace(",", ".")),
            )
        )

    if not rows:
        raise OmieParseError("no data rows")

    resolution = _PERIODS_TO_RESOLUTION.get(len(rows))
    if resolution is None:
        raise OmieParseError(
            f"unexpected period count {len(rows)} (not a valid hourly/quarter-hourly day)"
        )
    if [r[3] for r in rows] != list(range(1, len(rows) + 1)):
        raise OmieParseError("periods are not the contiguous sequence 1..N")

    year, month, day = rows[0][0], rows[0][1], rows[0][2]
    if any((r[0], r[1], r[2]) != (year, month, day) for r in rows):
        raise OmieParseError("file spans multiple market dates")
    market_date = dt.date(year, month, day)

    # Local midnight is always unambiguous (DST shifts happen at 02:00/03:00).
    # Stepping in UTC by the resolution for exactly N periods yields DST-correct
    # wall-clock coverage, because N already encodes the 23/24/25-hour day length.
    start_utc = dt.datetime(year, month, day, tzinfo=MARKET_TZ).astimezone(dt.UTC)
    step = dt.timedelta(minutes=resolution)
    return [
        MarginalPrice(
            ts_utc=start_utc + i * step,
            market_date=market_date,
            period=period,
            resolution_minutes=resolution,
            price_pt=price_pt,
            price_es=price_es,
        )
        for i, (_, _, _, period, price_pt, price_es) in enumerate(rows)
    ]


@retry(
    reraise=True,
    stop=stop_after_attempt(5),
    wait=wait_exponential(multiplier=1, min=1, max=30),
    retry=retry_if_exception_type(httpx.TransportError),
)
def _get(client: httpx.Client, url: str) -> httpx.Response:
    return client.get(url, timeout=40.0, follow_redirects=True)


def fetch_marginalpdbc(day: dt.date, client: httpx.Client | None = None) -> MarginalpdbcFile | None:
    """Download one market day's file, trying versions .1..N; None if none is published."""
    owns_client = client is None
    client = client or httpx.Client()
    ymd = day.strftime("%Y%m%d")
    try:
        for version in range(1, _MAX_VERSION + 1):
            filename = _FILENAME.format(ymd=ymd, version=version)
            response = _get(client, _DOWNLOAD_URL.format(filename=filename))
            if response.status_code == 404:
                continue
            response.raise_for_status()
            if _HEADER in response.text:
                return MarginalpdbcFile(filename=filename, text=response.text)
        return None
    finally:
        if owns_client:
            client.close()


def get_prices(day: dt.date, client: httpx.Client | None = None) -> list[MarginalPrice]:
    """Fetch and parse one market day; empty list if no version is published yet."""
    file = fetch_marginalpdbc(day, client)
    return parse_marginalpdbc(file.text) if file is not None else []
