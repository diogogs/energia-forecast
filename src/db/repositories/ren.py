"""Upsert repository for raw.ren_realised.

Idempotent on the natural key ``(series_name, ts_utc, resolution_minutes)`` — the OMIE
pattern with ``series_name`` in place of ``zone``. Re-ingesting a slot updates the value,
provenance and ``last_seen_at`` but **never** ``first_seen_at`` (the publication-time proxy
the feature layer's as-of legality check depends on). The caller owns the transaction.
"""

from __future__ import annotations

from sqlalchemy import func
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.orm import Session

from src.db.models import RenRealised
from src.ingestion.sources.ren import RenObservation

_CONFLICT_KEYS = ["series_name", "ts_utc", "resolution_minutes"]


def upsert_ren_observations(
    session: Session, observations: list[RenObservation], source_ref: str
) -> int:
    """Upsert REN observations; return rows written. Preserves first_seen_at (immutable)."""
    rows: list[dict[str, object]] = [
        {
            "series_name": o.series_name,
            "ts_utc": o.ts_utc,
            "resolution_minutes": o.resolution_minutes,
            "value_mw": o.value_mw,
            "local_date": o.local_date,
            "period": o.period,
            "source_ref": source_ref,
        }
        for o in observations
    ]
    if not rows:
        return 0

    stmt = pg_insert(RenRealised).values(rows)
    stmt = stmt.on_conflict_do_update(
        index_elements=_CONFLICT_KEYS,
        set_={
            "value_mw": stmt.excluded["value_mw"],
            "local_date": stmt.excluded["local_date"],
            "period": stmt.excluded["period"],
            "source_ref": stmt.excluded["source_ref"],
            "last_seen_at": func.now(),
            # first_seen_at deliberately absent — never mutated after INSERT.
        },
    )
    session.execute(stmt)
    return len(rows)
