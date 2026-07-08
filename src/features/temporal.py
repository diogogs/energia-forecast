"""Temporal primitives — the project's identity (CLAUDE.md "Modelo temporal").

Everything that decides *when* a datum was available lives here, so leakage bugs have exactly
one place to hide and be tested. Two concerns:

1. **Issue/delivery grid.** ``t_issue`` is a fixed nominal cutoff (07:00 UTC of the issue day D),
   never ``now()``. The delivery day is the CET market day D+1 — 23/24/25 hours on DST days.

2. **Modelled publication time.** A feature may only use data with ``published_at <= t_issue``.
   For backfilled history ``first_seen_at`` is our ingest time (useless), so we model each
   source's real publication time. Rules are **conservative** — never earlier than reality — so
   they cannot cause leakage (ADR-011).
"""

from __future__ import annotations

import datetime as dt
from zoneinfo import ZoneInfo

CET = ZoneInfo("Europe/Madrid")  # OMIE / market civil time
LISBON = ZoneInfo("Europe/Lisbon")  # REN civil time

# Fixed nominal issue cutoff: 07:00 UTC of the issue day D (constant, never now()).
T_ISSUE_UTC_HOUR = 7

# ECMWF ifs025 00Z run dissemination (UTC). The 00Z HRES is disseminated well before 07:00;
# 06:00 is a conservative "available" mark for the pinned Open-Meteo archive. If a run is not
# reliably up by t_issue, the as-of read simply falls back to the older lead (ADR-011).
ECMWF_RUN_AVAILABLE = dt.time(6, 0)


def t_issue_for(issue_date: dt.date) -> dt.datetime:
    """The fixed cutoff for issue day D: 07:00 UTC, tz-aware."""
    return dt.datetime.combine(issue_date, dt.time(T_ISSUE_UTC_HOUR), tzinfo=dt.UTC)


def delivery_date_for(issue_date: dt.date) -> dt.date:
    """Delivery day = the CET market day D+1 (calendar next day)."""
    return issue_date + dt.timedelta(days=1)


def delivery_hours_utc(delivery_date_cet: dt.date) -> list[dt.datetime]:
    """The UTC hour-starts of the CET market day ``delivery_date_cet``.

    23 hours on the spring-forward day, 25 on fall-back, 24 otherwise — the target vector length
    follows the CET civil day, computed by stepping in UTC between its CET midnights.
    """
    start = dt.datetime.combine(delivery_date_cet, dt.time(), tzinfo=CET).astimezone(dt.UTC)
    end = dt.datetime.combine(
        delivery_date_cet + dt.timedelta(days=1), dt.time(), tzinfo=CET
    ).astimezone(dt.UTC)
    hours: list[dt.datetime] = []
    current = start
    while current < end:
        hours.append(current)
        current += dt.timedelta(hours=1)
    return hours


def _next_midnight_utc(valid_ts_utc: dt.datetime, tz: dt.tzinfo) -> dt.datetime:
    """UTC instant of the midnight following ``valid_ts_utc``'s civil day in ``tz``."""
    local_date = valid_ts_utc.astimezone(tz).date()
    return dt.datetime.combine(local_date + dt.timedelta(days=1), dt.time(), tzinfo=tz).astimezone(
        dt.UTC
    )


def ren_published_at(valid_ts_utc: dt.datetime) -> dt.datetime:
    """REN realised data: consolidated by the next Lisbon midnight (conservative).

    Well inside the charter's >=48h consumption lag, so it never gates the specified features
    while guaranteeing day D is treated as incomplete at 07:00 UTC of D.
    """
    return _next_midnight_utc(valid_ts_utc, LISBON)


def energy_charts_published_at(valid_ts_utc: dt.datetime) -> dt.datetime:
    """ENTSO-E-sourced ES realised data: available by the next UTC midnight (conservative)."""
    return _next_midnight_utc(valid_ts_utc, dt.UTC)


def omie_published_at(market_date: dt.date) -> dt.datetime:
    """Day-ahead price for a delivery day is published the day before, ~12:45 CET after SDAC.

    Conservative: 13:00 CET on ``market_date - 1``. So at 07:00 UTC of D, day-D prices are known
    (published D-1) but D+1 prices are not — exactly the >=24h price-lag legality.
    """
    prev = market_date - dt.timedelta(days=1)
    return dt.datetime.combine(prev, dt.time(13), tzinfo=CET).astimezone(dt.UTC)


def openmeteo_published_at(valid_ts_utc: dt.datetime, lead_days: int) -> dt.datetime:
    """Archived run initialised ``(valid_date - lead_days)`` 00Z, disseminated ~06:00 UTC."""
    valid_date = valid_ts_utc.astimezone(dt.UTC).date()
    run_date = valid_date - dt.timedelta(days=lead_days)
    return dt.datetime.combine(run_date, ECMWF_RUN_AVAILABLE, tzinfo=dt.UTC)
