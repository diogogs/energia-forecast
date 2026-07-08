"""Upsert repository for raw.openmeteo_forecast.

Idempotent on ``(location, variable, lead_days, ts_utc)`` — the OMIE/REN/EC pattern. Preserves
``first_seen_at``; the caller owns the transaction. Splits large inputs into param-safe batches
so a wide multi-location date range cannot exceed Postgres' bound-parameter limit.
"""

from __future__ import annotations

from sqlalchemy import func
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.orm import Session

from src.db.models import OpenMeteoForecast
from src.ingestion.sources.openmeteo import ForecastObservation

_CONFLICT_KEYS = ["location", "variable", "lead_days", "ts_utc"]

# 7 columns/row; keep each statement under Postgres' 65535 bound-parameter cap (7*8000=56k).
_MAX_ROWS_PER_STMT = 8000


def upsert_forecasts(
    session: Session, observations: list[ForecastObservation], source_ref: str
) -> int:
    """Upsert archived forecasts; return rows written. Preserves first_seen_at (immutable)."""
    rows: list[dict[str, object]] = [
        {
            "location": o.location,
            "variable": o.variable,
            "lead_days": o.lead_days,
            "ts_utc": o.ts_utc,
            "value": o.value,
            "unit": o.unit,
            "source_ref": source_ref,
        }
        for o in observations
    ]
    if not rows:
        return 0

    for batch_start in range(0, len(rows), _MAX_ROWS_PER_STMT):
        batch = rows[batch_start : batch_start + _MAX_ROWS_PER_STMT]
        stmt = pg_insert(OpenMeteoForecast).values(batch)
        stmt = stmt.on_conflict_do_update(
            index_elements=_CONFLICT_KEYS,
            set_={
                "value": stmt.excluded["value"],
                "unit": stmt.excluded["unit"],
                "source_ref": stmt.excluded["source_ref"],
                "last_seen_at": func.now(),
                # first_seen_at deliberately absent — never mutated after INSERT.
            },
        )
        session.execute(stmt)
    return len(rows)
