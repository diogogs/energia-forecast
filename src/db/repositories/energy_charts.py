"""Upsert repository for raw.energy_charts_power.

Idempotent on the natural key ``(country, production_type, ts_utc, resolution_minutes)`` —
the same OMIE/REN pattern. Re-ingesting a slot updates the value, provenance and
``last_seen_at`` but never ``first_seen_at``. The caller owns the transaction.
"""

from __future__ import annotations

from sqlalchemy import func
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.orm import Session

from src.db.models import EnergyChartsPower
from src.ingestion.sources.energy_charts import PowerObservation

_CONFLICT_KEYS = ["country", "production_type", "ts_utc", "resolution_minutes"]

# Postgres binds at most 65535 parameters per statement. A row has 6 columns, and a monthly
# backfill chunk is ~50k rows, so we split the upsert into param-safe batches (6*8000=48k).
_MAX_ROWS_PER_STMT = 8000


def upsert_power_observations(
    session: Session, observations: list[PowerObservation], source_ref: str
) -> int:
    """Upsert Energy-Charts observations; return rows written. Preserves first_seen_at.

    Splits large inputs into multiple statements within the caller's transaction so a wide
    date range cannot exceed Postgres' bound-parameter limit.
    """
    rows: list[dict[str, object]] = [
        {
            "country": o.country,
            "production_type": o.production_type,
            "ts_utc": o.ts_utc,
            "resolution_minutes": o.resolution_minutes,
            "value_mw": o.value_mw,
            "source_ref": source_ref,
        }
        for o in observations
    ]
    if not rows:
        return 0

    for batch_start in range(0, len(rows), _MAX_ROWS_PER_STMT):
        batch = rows[batch_start : batch_start + _MAX_ROWS_PER_STMT]
        stmt = pg_insert(EnergyChartsPower).values(batch)
        stmt = stmt.on_conflict_do_update(
            index_elements=_CONFLICT_KEYS,
            set_={
                "value_mw": stmt.excluded["value_mw"],
                "source_ref": stmt.excluded["source_ref"],
                "last_seen_at": func.now(),
                # first_seen_at deliberately absent — never mutated after INSERT.
            },
        )
        session.execute(stmt)
    return len(rows)
