"""Backfill raw.openmeteo_forecast from the Open-Meteo Previous Runs API.

Fetches all configured locations per calendar month (one request), parses the archived
lead-1/lead-2 forecasts, and upserts them. Idempotent; one transaction per month with
per-chunk error isolation (a failed month is logged and an idempotent re-run heals it).

Usage:
    uv run python -m src.ingestion.openmeteo_backfill --start 2024-04-01 --end 2026-07-07
"""

from __future__ import annotations

import argparse
import datetime as dt
import logging
import time

import httpx

from src.db.engine import make_engine, make_session_factory
from src.db.repositories.openmeteo import upsert_forecasts
from src.ingestion.sources.openmeteo import fetch_previous_runs, parse_previous_runs

logger = logging.getLogger("openmeteo_backfill")

# The modelling matrix starts 2024-04-01 (Open-Meteo Previous Runs coverage bound, ADR-001/007).
DEFAULT_START = dt.date(2024, 4, 1)


def _month_chunks(start: dt.date, end: dt.date) -> list[tuple[dt.date, dt.date]]:
    """Split ``[start, end]`` into inclusive per-calendar-month ranges."""
    chunks: list[tuple[dt.date, dt.date]] = []
    cursor = start
    while cursor <= end:
        if cursor.month == 12:
            month_end = dt.date(cursor.year, 12, 31)
        else:
            month_end = dt.date(cursor.year, cursor.month + 1, 1) - dt.timedelta(days=1)
        chunks.append((cursor, min(month_end, end)))
        cursor = month_end + dt.timedelta(days=1)
    return chunks


def backfill_openmeteo(
    start: dt.date,
    end: dt.date,
    *,
    polite_delay_s: float = 0.5,
    client: httpx.Client | None = None,
) -> dict[str, int]:
    """Ingest every month in ``[start, end]`` (inclusive). Returns run counters."""
    if start > end:
        raise ValueError(f"start {start} is after end {end}")

    engine = make_engine()
    factory = make_session_factory(engine)
    owns_client = client is None
    client = client or httpx.Client()
    stats = {"chunks_ingested": 0, "chunks_missing": 0, "chunks_failed": 0, "rows": 0}

    try:
        for chunk_start, chunk_end in _month_chunks(start, end):
            try:
                response = fetch_previous_runs(chunk_start, chunk_end, client)
                observations = parse_previous_runs(response.payload) if response is not None else []
                if response is not None and observations:
                    with factory() as session:
                        written = upsert_forecasts(session, observations, response.source_ref)
                        session.commit()
                    stats["chunks_ingested"] += 1
                    stats["rows"] += written
                    logger.info("%s..%s: upserted %d rows", chunk_start, chunk_end, written)
                else:
                    stats["chunks_missing"] += 1
                    logger.warning("%s..%s: no data published", chunk_start, chunk_end)
            except Exception:
                stats["chunks_failed"] += 1
                logger.exception("%s..%s: ingestion failed; continuing", chunk_start, chunk_end)
            if polite_delay_s:
                time.sleep(polite_delay_s)
    finally:
        if owns_client:
            client.close()
        engine.dispose()

    logger.info(
        "done: %d chunks ingested, %d missing, %d failed, %d rows",
        stats["chunks_ingested"],
        stats["chunks_missing"],
        stats["chunks_failed"],
        stats["rows"],
    )
    return stats


def main() -> None:
    parser = argparse.ArgumentParser(description="Backfill raw.openmeteo_forecast.")
    parser.add_argument("--start", type=dt.date.fromisoformat, default=DEFAULT_START)
    parser.add_argument(
        "--end",
        type=dt.date.fromisoformat,
        default=dt.datetime.now(tz=dt.UTC).date() - dt.timedelta(days=1),
        help="inclusive; defaults to yesterday (UTC)",
    )
    parser.add_argument("--delay", type=float, default=0.5, help="polite delay between months (s)")
    args = parser.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    backfill_openmeteo(args.start, args.end, polite_delay_s=args.delay)


if __name__ == "__main__":
    main()
