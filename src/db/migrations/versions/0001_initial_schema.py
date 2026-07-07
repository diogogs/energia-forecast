"""initial schema: data layers + raw.omie_price

Revision ID: 0001
Revises:
Create Date: 2026-07-07
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0001"
down_revision = None
branch_labels = None
depends_on = None

# Data layers are Postgres schemas (raw -> clean -> features), plus pred/ops/meta.
SCHEMAS = ["raw", "clean", "features", "pred", "ops", "meta"]


def upgrade() -> None:
    for schema in SCHEMAS:
        op.execute(f"CREATE SCHEMA IF NOT EXISTS {schema}")

    op.create_table(
        "omie_price",
        sa.Column("zone", sa.String(length=2), nullable=False),
        sa.Column("ts_utc", sa.DateTime(timezone=True), nullable=False),
        sa.Column("resolution_minutes", sa.SmallInteger(), nullable=False),
        sa.Column("price_eur_mwh", sa.Float(), nullable=False),
        sa.Column("market_date", sa.Date(), nullable=False),
        sa.Column("period", sa.SmallInteger(), nullable=False),
        sa.Column("source_file", sa.String(), nullable=False),
        sa.Column(
            "first_seen_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column(
            "last_seen_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("zone", "ts_utc", "resolution_minutes", name="pk_omie_price"),
        schema="raw",
    )


def downgrade() -> None:
    op.drop_table("omie_price", schema="raw")
    for schema in reversed(SCHEMAS):
        op.execute(f"DROP SCHEMA IF EXISTS {schema}")
