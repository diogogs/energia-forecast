"""drop raw.ren_realised secondary index to reclaim Neon free-tier space

The (ts_utc, series_name) index roughly doubled the table's index footprint. Its consumer —
the clean-layer pivot / [now-3d, now] cross-series scan — does not exist yet, and the primary
key already serves per-series range scans. Re-add a purpose-built index when the clean layer's
real queries justify it (ADR-009).

Revision ID: 0004
Revises: 0003
Create Date: 2026-07-08
"""

from __future__ import annotations

from alembic import op

revision = "0004"
down_revision = "0003"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.drop_index("ix_ren_realised_ts_utc_series_name", table_name="ren_realised", schema="raw")


def downgrade() -> None:
    op.create_index(
        "ix_ren_realised_ts_utc_series_name",
        "ren_realised",
        ["ts_utc", "series_name"],
        schema="raw",
    )
