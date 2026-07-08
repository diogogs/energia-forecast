"""ren realised layer: raw.ren_realised + meta.ren_series (seeded)

Revision ID: 0002
Revises: 0001
Create Date: 2026-07-08
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0002"
down_revision = "0001"
branch_labels = None
depends_on = None

# REN ProductionBreakdown series taxonomy (verified 2026-07-08). Non-enforcing dimension:
# raw ingestion never blocks on this; a brand-new label is logged, not rejected (ADR-008).
# (series_name, series_code, kind, is_target)
_SERIES_SEED = [
    ("Consumption", "consumption", "load", True),
    ("Consumption + Storage", "consumption_storage", "load", False),
    ("Solar", "solar", "generation", False),
    ("Hydro", "hydro", "generation", False),
    ("Wind", "wind", "generation", False),
    ("Gas", "gas", "generation", False),
    ("Coal", "coal", "generation", False),
    ("Biomass", "biomass", "generation", False),
    ("Other Thermal", "other_thermal", "generation", False),
    ("Wave", "wave", "generation", False),
    ("Imports", "imports", "flow", False),
    ("Battery Injection", "battery_injection", "storage", False),
]


def upgrade() -> None:
    op.create_table(
        "ren_realised",
        sa.Column("series_name", sa.String(length=40), nullable=False),
        sa.Column("ts_utc", sa.DateTime(timezone=True), nullable=False),
        sa.Column(
            "resolution_minutes", sa.SmallInteger(), server_default=sa.text("15"), nullable=False
        ),
        sa.Column("value_mw", sa.Float(), nullable=False),
        sa.Column("local_date", sa.Date(), nullable=False),
        sa.Column("period", sa.SmallInteger(), nullable=False),
        sa.Column("source_ref", sa.String(), nullable=False),
        sa.Column(
            "first_seen_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column(
            "last_seen_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.PrimaryKeyConstraint(
            "series_name", "ts_utc", "resolution_minutes", name="pk_ren_realised"
        ),
        schema="raw",
    )
    op.create_index(
        "ix_ren_realised_ts_utc_series_name",
        "ren_realised",
        ["ts_utc", "series_name"],
        schema="raw",
    )

    ren_series = op.create_table(
        "ren_series",
        sa.Column("series_name", sa.String(length=40), nullable=False),
        sa.Column("series_code", sa.String(length=32), nullable=False),
        sa.Column("kind", sa.String(length=16), nullable=False),
        sa.Column("is_target", sa.Boolean(), server_default=sa.text("false"), nullable=False),
        sa.Column(
            "first_seen_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("series_name", name="pk_ren_series"),
        schema="meta",
    )
    op.bulk_insert(
        ren_series,
        [
            {"series_name": n, "series_code": c, "kind": k, "is_target": t}
            for (n, c, k, t) in _SERIES_SEED
        ],
    )


def downgrade() -> None:
    op.drop_table("ren_series", schema="meta")
    op.drop_index("ix_ren_realised_ts_utc_series_name", table_name="ren_realised", schema="raw")
    op.drop_table("ren_realised", schema="raw")
