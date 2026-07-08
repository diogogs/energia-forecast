"""energy-charts layer: raw.energy_charts_power (ES features)

Revision ID: 0003
Revises: 0002
Create Date: 2026-07-08
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0003"
down_revision = "0002"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "energy_charts_power",
        sa.Column("country", sa.String(length=2), nullable=False),
        sa.Column("production_type", sa.String(length=48), nullable=False),
        sa.Column("ts_utc", sa.DateTime(timezone=True), nullable=False),
        sa.Column("resolution_minutes", sa.SmallInteger(), nullable=False),
        sa.Column("value_mw", sa.Float(), nullable=False),
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
            "country",
            "production_type",
            "ts_utc",
            "resolution_minutes",
            name="pk_energy_charts_power",
        ),
        schema="raw",
    )


def downgrade() -> None:
    op.drop_table("energy_charts_power", schema="raw")
