"""ORM models. Data layers are Postgres schemas: raw / clean / features / pred / ops / meta.

All timestamps are tz-aware UTC. Idempotency rule (temporal rigor): `first_seen_at` is
set once on INSERT and never touched by upserts — it is the publication-time proxy that
the feature layer's as-of legality check depends on.
"""

from __future__ import annotations

import datetime as dt

from sqlalchemy import Date, DateTime, Float, SmallInteger, String, func
from sqlalchemy.orm import Mapped, mapped_column

from src.db.base import Base


class OmiePrice(Base):
    """raw.omie_price — MIBEL day-ahead marginal prices at native resolution (PT and ES)."""

    __tablename__ = "omie_price"
    __table_args__ = {"schema": "raw"}  # noqa: RUF012 — SQLAlchemy config, not a mutable default

    zone: Mapped[str] = mapped_column(String(2), primary_key=True)  # 'PT' | 'ES'
    ts_utc: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), primary_key=True)
    resolution_minutes: Mapped[int] = mapped_column(SmallInteger, primary_key=True)  # 60 | 15

    price_eur_mwh: Mapped[float] = mapped_column(Float, nullable=False)
    market_date: Mapped[dt.date] = mapped_column(Date, nullable=False)
    period: Mapped[int] = mapped_column(SmallInteger, nullable=False)
    source_file: Mapped[str] = mapped_column(String, nullable=False)

    first_seen_at: Mapped[dt.datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    last_seen_at: Mapped[dt.datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
