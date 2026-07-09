"""ops layer: ops.dq_log (durable data-quality / ingestion event log)

The daily self-healing ingest logged only to stdout (ephemeral GitHub Actions logs). This gives
the charter's "validação à entrada → ops.dq_log, nunca descartes silenciosos" a durable home: one
row per source per run, plus room for finer validation events. The ``ops`` schema already exists
(0001). No secondary index — the table is tiny (a few rows a day) and only scanned for recents.

Revision ID: 0008
Revises: 0007
Create Date: 2026-07-09
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0008"
down_revision = "0007"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "dq_log",
        sa.Column("id", sa.BigInteger(), sa.Identity(), nullable=False),
        sa.Column(
            "logged_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column("source", sa.String(length=32), nullable=False),
        sa.Column("check_name", sa.String(length=48), nullable=False),
        sa.Column("severity", sa.String(length=8), nullable=False),
        sa.Column("window_start", sa.Date(), nullable=True),
        sa.Column("window_end", sa.Date(), nullable=True),
        sa.Column("rows_written", sa.Integer(), nullable=True),
        sa.Column("detail", sa.String(), nullable=True),
        sa.PrimaryKeyConstraint("id", name="pk_dq_log"),
        schema="ops",
    )


def downgrade() -> None:
    op.drop_table("dq_log", schema="ops")
