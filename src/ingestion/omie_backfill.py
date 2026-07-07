"""Backfill raw.omie_price from OMIE public ``marginalpdbc`` files over a date range.

Idempotent end to end: fetch each market day, parse (resolution- and DST-aware,
ADR-006), and upsert both zones. Re-running never duplicates rows or moves
``first_seen_at``. One transaction per day so a mid-run failure loses at most one day
and a resume just re-upserts it. Days with no published file are counted, not fatal
(weekends and holidays still publish, but a not-yet-published future day returns none).

Usage:
    uv run python -m src.ingestion.omie_backfill --start 2024-01-01 --end 2026-07-06
"""

from __future__ import annotations

import argparse
import datetime as dt
import logging
import time

import httpx

from src.db.engine import make_engine, make_session_factory
from src.db.repositories.omie import upsert_omie_prices
from src.ingestion.sources.omie import get_prices

logger = logging.getLogger("omie_backfill")

# Modelling matrix starts 2024-04-01 (Open-Meteo), but prices are cheap and feed
# price lags/aggregates, so we backfill from the start of 2024 by default.
DEFAULT_START = dt.date(2024, 1, 1)


def backfill_omie(
    start: dt.date,
    end: dt.date,
    *,
    polite_delay_s: float = 0.3,
    client: httpx.Client | None = None,
) -> dict[str, int]:
    """Ingest every market day in ``[start, end]`` (inclusive). Returns run counters."""
    if start > end:
        raise ValueError(f"start {start} is after end {end}")

    engine = make_engine()
    factory = make_session_factory(engine)
    owns_client = client is None
    client = client or httpx.Client()
    stats = {"days_ingested": 0, "days_missing": 0, "rows": 0}

    try:
        day = start
        while day <= end:
            prices = get_prices(day, client)
            if prices:
                with factory() as session:
                    written = upsert_omie_prices(session, prices, f"marginalpdbc_{day:%Y%m%d}.1")
                    session.commit()
                stats["days_ingested"] += 1
                stats["rows"] += written
                logger.info("%s: upserted %d rows (%d periods)", day, written, written // 2)
            else:
                stats["days_missing"] += 1
                logger.warning("%s: no file published", day)
            day += dt.timedelta(days=1)
            if polite_delay_s:
                time.sleep(polite_delay_s)
    finally:
        if owns_client:
            client.close()
        engine.dispose()

    logger.info(
        "done: %d days ingested, %d missing, %d rows",
        stats["days_ingested"],
        stats["days_missing"],
        stats["rows"],
    )
    return stats


def main() -> None:
    parser = argparse.ArgumentParser(description="Backfill raw.omie_price from OMIE files.")
    parser.add_argument("--start", type=dt.date.fromisoformat, default=DEFAULT_START)
    parser.add_argument(
        "--end",
        type=dt.date.fromisoformat,
        default=dt.datetime.now(tz=dt.UTC).date() - dt.timedelta(days=1),
        help="inclusive; defaults to yesterday (UTC)",
    )
    parser.add_argument("--delay", type=float, default=0.3, help="polite delay between days (s)")
    args = parser.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    backfill_omie(args.start, args.end, polite_delay_s=args.delay)


if __name__ == "__main__":
    main()
