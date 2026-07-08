"""open-meteo layer: raw.openmeteo_forecast (archived forecasts for training)

Revision ID: 0005
Revises: 0004
Create Date: 2026-07-08
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0005"
down_revision = "0004"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "openmeteo_forecast",
        sa.Column("location", sa.String(length=16), nullable=False),
        sa.Column("variable", sa.String(length=32), nullable=False),
        sa.Column("lead_days", sa.SmallInteger(), nullable=False),
        sa.Column("ts_utc", sa.DateTime(timezone=True), nullable=False),
        sa.Column("value", sa.Float(), nullable=False),
        sa.Column("unit", sa.String(length=12), nullable=False),
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
            "location", "variable", "lead_days", "ts_utc", name="pk_openmeteo_forecast"
        ),
        schema="raw",
    )


def downgrade() -> None:
    op.drop_table("openmeteo_forecast", schema="raw")
