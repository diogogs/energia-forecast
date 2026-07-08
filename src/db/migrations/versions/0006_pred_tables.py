"""pred layer: pred.predictions (insert-only) + pred.backtest_predictions

Revision ID: 0006
Revises: 0005
Create Date: 2026-07-09
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0006"
down_revision = "0005"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "predictions",
        sa.Column("issue_date", sa.Date(), nullable=False),
        sa.Column("target_ts", sa.DateTime(timezone=True), nullable=False),
        sa.Column("model_name", sa.String(length=32), nullable=False),
        sa.Column(
            "quantile", sa.String(length=8), server_default=sa.text("'point'"), nullable=False
        ),
        sa.Column("target_name", sa.String(length=16), nullable=False),
        sa.Column("y_hat", sa.Float(), nullable=False),
        sa.Column(
            "issued_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column("late_issue", sa.Boolean(), server_default=sa.text("false"), nullable=False),
        sa.PrimaryKeyConstraint(
            "issue_date", "target_ts", "model_name", "quantile", name="pk_predictions"
        ),
        schema="pred",
    )
    op.create_table(
        "backtest_predictions",
        sa.Column("issue_date", sa.Date(), nullable=False),
        sa.Column("target_ts", sa.DateTime(timezone=True), nullable=False),
        sa.Column("model_name", sa.String(length=32), nullable=False),
        sa.Column("target_name", sa.String(length=16), nullable=False),
        sa.Column("y_hat", sa.Float(), nullable=False),
        sa.Column("y_true", sa.Float(), nullable=True),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.PrimaryKeyConstraint(
            "issue_date", "target_ts", "model_name", name="pk_backtest_predictions"
        ),
        schema="pred",
    )


def downgrade() -> None:
    op.drop_table("backtest_predictions", schema="pred")
    op.drop_table("predictions", schema="pred")
