"""Cross-validation of REN consumption against ENTSO-E — an independent referee.

Our demand target comes from the REN Data Hub. ENTSO-E's Transparency Platform publishes
the same quantity through a separate pipeline, so silent corruption or drift in the primary
source shows up here as divergence. This is monitoring, not a data source: it never feeds
features (ADR-007 keeps ENTSO-E off the critical path) and it never blocks ingestion — the
outcome lands in ops.dq_log with a severity, like every other health signal.

Usage:
    uv run python -m src.monitoring.cross_validation [--days-back 3]

Exits 0 even on divergence or ENTSO-E downtime (the dq_log entry is the alert); only truly
unexpected local failures exit non-zero. Without ENTSOE_API_TOKEN it logs a skip and exits 0,
so the CI step is safe to run unconditionally.
"""

from __future__ import annotations

import argparse
import datetime as dt
import logging
import sys
from dataclasses import dataclass

import pandas as pd

from src.config import get_settings
from src.db.engine import make_engine, make_session_factory
from src.db.repositories.dq_log import record_dq_event
from src.monitoring.watchdog import realised_hourly

logger = logging.getLogger("cross_validation")

# REN and ENTSO-E publish the same TSO measurement; small aggregation differences are normal,
# a real divergence is not. Thresholds are deliberately loose to avoid alert fatigue.
MAE_WARN_MW = 100.0
CORR_WARN = 0.995
MIN_OVERLAP_HOURS = 24


@dataclass(frozen=True)
class CrossCheckResult:
    hours: int
    mae_mw: float
    max_abs_mw: float
    corr: float

    @property
    def severity(self) -> str:
        return "warning" if (self.mae_mw > MAE_WARN_MW or self.corr < CORR_WARN) else "info"

    def detail(self) -> str:
        return (
            f"hours={self.hours} mae={self.mae_mw:.1f}MW "
            f"max_abs={self.max_abs_mw:.1f}MW corr={self.corr:.5f}"
        )


def compare_series(ours: pd.Series, theirs: pd.Series) -> CrossCheckResult | None:
    """Align two hourly series on their common index and measure the divergence."""
    joined = pd.concat([ours.rename("ours"), theirs.rename("theirs")], axis=1).dropna()
    if len(joined) < MIN_OVERLAP_HOURS:
        return None
    diff = (joined["ours"] - joined["theirs"]).abs()
    return CrossCheckResult(
        hours=len(joined),
        mae_mw=float(diff.mean()),
        max_abs_mw=float(diff.max()),
        corr=float(joined["ours"].corr(joined["theirs"])),
    )


def fetch_entsoe_load_hourly(token: str, lo: dt.datetime, hi: dt.datetime) -> pd.Series:
    """PT actual load from ENTSO-E over [lo, hi), resampled to hourly means in UTC."""
    # Imported lazily: only this module needs it. The ignore covers entsoe's implicit re-export.
    from entsoe import EntsoePandasClient  # type: ignore[attr-defined]

    client = EntsoePandasClient(api_key=token)
    raw = client.query_load(
        "PT", start=pd.Timestamp(lo).tz_convert("UTC"), end=pd.Timestamp(hi).tz_convert("UTC")
    )
    series = raw["Actual Load"] if isinstance(raw, pd.DataFrame) else raw
    return series.tz_convert("UTC").resample("1h").mean().dropna()


def run_crosscheck(days_back: int = 3) -> CrossCheckResult | None:
    """Compare the last ``days_back`` days of REN consumption against ENTSO-E; log to dq_log."""
    settings = get_settings()
    if not settings.entsoe_api_token:
        logger.info("ENTSOE_API_TOKEN not configured — cross-check skipped")
        return None

    now = dt.datetime.now(tz=dt.UTC)
    hi = now.replace(minute=0, second=0, microsecond=0)
    lo = hi - dt.timedelta(days=days_back)

    engine = make_engine()
    factory = make_session_factory(engine)
    try:
        with factory() as session:
            ours = realised_hourly(session, "consumption", lo, hi)

        severity, detail, result = "error", "", None
        try:
            theirs = fetch_entsoe_load_hourly(settings.entsoe_api_token, lo, hi)
            result = compare_series(ours, theirs)
            if result is None:
                severity = "warning"
                detail = f"insufficient overlap (<{MIN_OVERLAP_HOURS}h) between REN and ENTSO-E"
            else:
                severity = result.severity
                detail = result.detail()
        except Exception as exc:
            # ENTSO-E being down must not fail the pipeline; the dq entry is the alert.
            detail = f"ENTSO-E fetch failed: {exc!r}"
            logger.exception("cross-check fetch failed")

        with factory() as session:
            record_dq_event(
                session,
                source="entsoe",
                check_name="ren_load_crosscheck",
                severity=severity,
                window_start=lo.date(),
                window_end=hi.date(),
                rows_written=result.hours if result else None,
                detail=detail,
            )
            session.commit()
        logger.info("cross-check %s: %s", severity, detail)
        return result
    finally:
        engine.dispose()


def main() -> None:
    parser = argparse.ArgumentParser(description="Cross-check REN consumption vs ENTSO-E load.")
    parser.add_argument("--days-back", type=int, default=3, help="window size (days)")
    args = parser.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    run_crosscheck(args.days_back)
    sys.exit(0)


if __name__ == "__main__":
    main()
