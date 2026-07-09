"""Daily self-healing ingestion — re-ingest the sliding window [today-N, today] for every source.

Idempotent by construction (every source upserts on its natural key and never mutates
first_seen_at), so re-running the last few days heals gaps and captures late revisions without
duplicating rows. Each source is isolated: one failing source is logged and does not stop the
others, and the process exits non-zero if any source failed (so the cron surfaces it).

Usage:
    uv run python -m src.ingestion.daily [--days-back 3]
"""

from __future__ import annotations

import argparse
import datetime as dt
import logging
import sys
from collections.abc import Callable

from src.db.engine import make_engine, make_session_factory
from src.db.repositories.dq_log import record_dq_event
from src.ingestion.energy_charts_backfill import backfill_energy_charts
from src.ingestion.omie_backfill import backfill_omie
from src.ingestion.openmeteo_backfill import backfill_openmeteo
from src.ingestion.ren_backfill import backfill_ren

logger = logging.getLogger("daily_ingest")

# Each source over an inclusive [start, end] window; all are token-free and idempotent.
_SOURCES: dict[str, Callable[[dt.date, dt.date], dict[str, int]]] = {
    "omie": lambda start, end: backfill_omie(start, end),
    "ren": lambda start, end: backfill_ren(start, end),
    "energy_charts": lambda start, end: backfill_energy_charts(start, end),
    "openmeteo": lambda start, end: backfill_openmeteo(start, end),
}


def _severity(stats: dict[str, int]) -> str:
    """info if a source ran clean; warning if it survived but some days failed."""
    return "warning" if stats.get("days_failed") else "info"


def run_daily(days_back: int = 3) -> dict[str, dict[str, int] | str]:
    """Re-ingest [today-days_back, today] (UTC) for every source. Returns a per-source summary.

    Each source's outcome is also persisted to ops.dq_log (durable, unlike the stdout logs), so
    the monitoring API/dashboard can show ingestion health without scraping Actions logs.
    """
    end = dt.datetime.now(tz=dt.UTC).date()
    start = end - dt.timedelta(days=days_back)
    logger.info("daily self-healing ingest over [%s, %s]", start, end)

    engine = make_engine()
    factory = make_session_factory(engine)
    summary: dict[str, dict[str, int] | str] = {}
    try:
        for name, backfill in _SOURCES.items():
            try:
                stats = backfill(start, end)
                summary[name] = stats
                severity, rows, detail = _severity(stats), stats.get("rows"), str(stats)
                logger.info("%s OK: %s", name, stats)
            except Exception as exc:
                summary[name] = "FAILED"
                severity, rows, detail = "error", None, repr(exc)
                logger.exception("%s FAILED", name)
            # Record the outcome durably; a logging failure must not mask the ingest result.
            try:
                with factory() as session:
                    record_dq_event(
                        session,
                        source=name,
                        check_name="ingest_run",
                        severity=severity,
                        window_start=start,
                        window_end=end,
                        rows_written=rows,
                        detail=detail,
                    )
                    session.commit()
            except Exception:
                logger.exception("failed to record dq_log event for %s", name)
    finally:
        engine.dispose()
    return summary


def main() -> None:
    parser = argparse.ArgumentParser(description="Daily self-healing ingestion for all sources.")
    parser.add_argument("--days-back", type=int, default=3, help="sliding window size (days)")
    args = parser.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    summary = run_daily(args.days_back)

    failed = [name for name, result in summary.items() if result == "FAILED"]
    if failed:
        logger.error("daily ingest finished with failures: %s", failed)
        sys.exit(1)
    logger.info("daily ingest complete — all sources OK")


if __name__ == "__main__":
    main()
