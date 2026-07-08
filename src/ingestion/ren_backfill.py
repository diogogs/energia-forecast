"""Backfill raw.ren_realised from the REN Data Hub ProductionBreakdown endpoint.

Idempotent end to end: fetch each Lisbon civil day, parse (DST-correct, ADR-008), upsert
every non-null (series, slot). Re-running never duplicates rows or moves ``first_seen_at``.
One transaction per day so a mid-run failure loses at most one day and a resume re-upserts it.

Usage:
    uv run python -m src.ingestion.ren_backfill --start 2024-01-01 --end 2026-07-07
"""

from __future__ import annotations

import argparse
import datetime as dt
import logging
import time

import httpx

from src.db.engine import make_engine, make_session_factory
from src.db.repositories.ren import upsert_ren_observations
from src.ingestion.sources.ren import fetch_production_breakdown, parse_production_breakdown

logger = logging.getLogger("ren_backfill")

# Open-Meteo Previous Runs bounds the modelling matrix at 2024-04-01, but REN reaches further
# back and consumption/generation are cheap, so we backfill from the start of 2024 by default.
DEFAULT_START = dt.date(2024, 1, 1)


def backfill_ren(
    start: dt.date,
    end: dt.date,
    *,
    polite_delay_s: float = 0.3,
    client: httpx.Client | None = None,
) -> dict[str, int]:
    """Ingest every Lisbon civil day in ``[start, end]`` (inclusive). Returns run counters."""
    if start > end:
        raise ValueError(f"start {start} is after end {end}")

    engine = make_engine()
    factory = make_session_factory(engine)
    owns_client = client is None
    client = client or httpx.Client()
    stats = {"days_ingested": 0, "days_missing": 0, "days_failed": 0, "rows": 0}

    try:
        day = start
        while day <= end:
            # One bad day must not kill the range: log it, count it, move on.
            # The upsert is idempotent, so a resume/re-run heals failed days.
            try:
                response = fetch_production_breakdown(day, client)
                observations = (
                    parse_production_breakdown(response.payload, day)
                    if response is not None
                    else []
                )
                if response is not None and observations:
                    with factory() as session:
                        written = upsert_ren_observations(
                            session, observations, response.source_ref
                        )
                        session.commit()
                    stats["days_ingested"] += 1
                    stats["rows"] += written
                    logger.info("%s: upserted %d rows", day, written)
                else:
                    stats["days_missing"] += 1
                    logger.warning("%s: no data published", day)
            except Exception:
                stats["days_failed"] += 1
                logger.exception("%s: ingestion failed; continuing", day)
            day += dt.timedelta(days=1)
            if polite_delay_s:
                time.sleep(polite_delay_s)
    finally:
        if owns_client:
            client.close()
        engine.dispose()

    logger.info(
        "done: %d days ingested, %d missing, %d failed, %d rows",
        stats["days_ingested"],
        stats["days_missing"],
        stats["days_failed"],
        stats["rows"],
    )
    return stats


def main() -> None:
    parser = argparse.ArgumentParser(description="Backfill raw.ren_realised from REN Data Hub.")
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
    backfill_ren(args.start, args.end, polite_delay_s=args.delay)


if __name__ == "__main__":
    main()
