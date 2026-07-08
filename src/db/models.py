"""ORM models. Data layers are Postgres schemas: raw / clean / features / pred / ops / meta.

All timestamps are tz-aware UTC. Idempotency rule (temporal rigor): `first_seen_at` is
set once on INSERT and never touched by upserts — it is the publication-time proxy that
the feature layer's as-of legality check depends on.
"""

from __future__ import annotations

import datetime as dt

from sqlalchemy import Boolean, Date, DateTime, Float, SmallInteger, String, func, text
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


class RenRealised(Base):
    """raw.ren_realised — REN realised PT consumption + generation, one row per (series, slot).

    Tall layout mirroring raw.omie_price with ``series_name`` in place of ``zone`` (ADR-008).
    ``local_date`` is the Lisbon civil day, deliberately NOT the CET market day — the 1h
    PT/CET offset is resolved downstream. ``value_mw`` is signed (Imports/Battery may be < 0).
    """

    __tablename__ = "ren_realised"
    # Secondary index removed in 0004 to reclaim Neon free-tier space until the clean layer
    # exists and can justify one on real queries (ADR-009).
    __table_args__ = {"schema": "raw"}  # noqa: RUF012 — SQLAlchemy config, not a mutable default

    series_name: Mapped[str] = mapped_column(String(40), primary_key=True)
    ts_utc: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), primary_key=True)
    resolution_minutes: Mapped[int] = mapped_column(
        SmallInteger, primary_key=True, server_default=text("15")
    )

    value_mw: Mapped[float] = mapped_column(Float, nullable=False)  # signed MW
    local_date: Mapped[dt.date] = mapped_column(Date, nullable=False)
    period: Mapped[int] = mapped_column(SmallInteger, nullable=False)
    source_ref: Mapped[str] = mapped_column(String, nullable=False)

    first_seen_at: Mapped[dt.datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    last_seen_at: Mapped[dt.datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


class RenSeries(Base):
    """meta.ren_series — taxonomy of REN series (non-enforcing; no FK from raw).

    Lets the clean/features layer pick the target (``is_target``) and feature groups
    (``kind``) without hardcoded name lists. Ingestion never blocks on classification: a
    brand-new REN label lands in raw regardless and is logged as unclassified (ADR-008).
    """

    __tablename__ = "ren_series"
    __table_args__ = {"schema": "meta"}  # noqa: RUF012 — SQLAlchemy config, not a mutable default

    series_name: Mapped[str] = mapped_column(String(40), primary_key=True)
    series_code: Mapped[str] = mapped_column(String(32), nullable=False)  # canonical snake_case
    # 'load' | 'generation' | 'flow' | 'storage'
    kind: Mapped[str] = mapped_column(String(16), nullable=False)
    is_target: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default=text("false"))
    first_seen_at: Mapped[dt.datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


class EnergyChartsPower(Base):
    """raw.energy_charts_power — Energy-Charts load + generation by type (ES features).

    Tall layout, UTC-native (no local_date/period — unix_seconds are already UTC instants).
    ``value_mw`` is signed (cross-border trading, pumped-storage consumption go negative).
    ES-only today, but ``country`` is in the key so the table generalises (ADR-009).
    """

    __tablename__ = "energy_charts_power"
    # No secondary index yet: the clean layer (which would pivot by ts_utc range) is unbuilt,
    # and Neon free-tier space is scarce — add one when a real query needs it (ADR-009).
    __table_args__ = {"schema": "raw"}  # noqa: RUF012 — SQLAlchemy config, not a mutable default

    country: Mapped[str] = mapped_column(String(2), primary_key=True)  # ISO-2 lower-case
    production_type: Mapped[str] = mapped_column(String(48), primary_key=True)
    ts_utc: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), primary_key=True)
    resolution_minutes: Mapped[int] = mapped_column(SmallInteger, primary_key=True)

    value_mw: Mapped[float] = mapped_column(Float, nullable=False)  # signed MW
    source_ref: Mapped[str] = mapped_column(String, nullable=False)

    first_seen_at: Mapped[dt.datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    last_seen_at: Mapped[dt.datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


class OpenMeteoForecast(Base):
    """raw.openmeteo_forecast — archived ECMWF forecasts (leakage-free training weather).

    Tall, UTC-native. ``lead_days`` (1|2) is which archived run the value came from: the run
    initialised that many days before the valid date. Only lead 1/2 are stored (never the
    near-analysis current run — that would leak). The features layer picks the legal lead per
    ``t_issue`` (ADR-010). ``value``/``unit`` are native Open-Meteo (°C, km/h, W/m²).
    """

    __tablename__ = "openmeteo_forecast"
    __table_args__ = {"schema": "raw"}  # noqa: RUF012 — SQLAlchemy config, not a mutable default

    location: Mapped[str] = mapped_column(String(16), primary_key=True)  # slug, e.g. 'lisbon'
    variable: Mapped[str] = mapped_column(String(32), primary_key=True)
    lead_days: Mapped[int] = mapped_column(SmallInteger, primary_key=True)  # 1 | 2
    ts_utc: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), primary_key=True)

    value: Mapped[float] = mapped_column(Float, nullable=False)
    unit: Mapped[str] = mapped_column(String(12), nullable=False)
    source_ref: Mapped[str] = mapped_column(String, nullable=False)

    first_seen_at: Mapped[dt.datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    last_seen_at: Mapped[dt.datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
