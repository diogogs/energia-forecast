"""Upsert repository for raw.omie_price.

Each parsed :class:`MarginalPrice` (one market period) expands into two rows — one
per bidding zone (PT, ES). The upsert is idempotent on the natural key
``(zone, ts_utc, resolution_minutes)``: re-ingesting the same period updates the
price, source file and ``last_seen_at`` but **never** ``first_seen_at``. That column
is the publication-time proxy the feature layer's as-of legality check depends on, so
it is written once on INSERT and excluded from every ``DO UPDATE`` (temporal rigor).

The caller owns the transaction: this function flushes via ``execute`` but does not
commit, so a backfill can batch many days or roll back cleanly on failure.
"""

from __future__ import annotations

from sqlalchemy import func
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.orm import Session

from src.db.models import OmiePrice
from src.ingestion.sources.omie import MarginalPrice

# Natural-key columns; conflict target for the upsert.
_CONFLICT_KEYS = ["zone", "ts_utc", "resolution_minutes"]


def upsert_omie_prices(session: Session, prices: list[MarginalPrice], source_file: str) -> int:
    """Upsert MIBEL day-ahead prices into raw.omie_price; return rows written (2 per period).

    Idempotent and safe to re-run. ``first_seen_at`` is preserved on conflict.
    """
    rows: list[dict[str, object]] = []
    for p in prices:
        for zone, price in (("PT", p.price_pt), ("ES", p.price_es)):
            rows.append(
                {
                    "zone": zone,
                    "ts_utc": p.ts_utc,
                    "resolution_minutes": p.resolution_minutes,
                    "price_eur_mwh": price,
                    "market_date": p.market_date,
                    "period": p.period,
                    "source_file": source_file,
                }
            )
    if not rows:
        return 0

    stmt = pg_insert(OmiePrice).values(rows)
    stmt = stmt.on_conflict_do_update(
        index_elements=_CONFLICT_KEYS,
        set_={
            "price_eur_mwh": stmt.excluded["price_eur_mwh"],
            "market_date": stmt.excluded["market_date"],
            "period": stmt.excluded["period"],
            "source_file": stmt.excluded["source_file"],
            "last_seen_at": func.now(),
            # first_seen_at deliberately absent — never mutated after INSERT.
        },
    )
    session.execute(stmt)
    return len(rows)
