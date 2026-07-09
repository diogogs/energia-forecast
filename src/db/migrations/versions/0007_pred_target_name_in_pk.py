"""add target_name to the pred.* primary keys

The same model name ('lightgbm', 'seasonal_168h', ...) serves both the consumption and price
targets, so target_name must be part of the natural key — otherwise a price forecast collides
with the consumption one for the same (issue_date, target_ts, model, quantile) and is silently
dropped by ON CONFLICT DO NOTHING. Widening a PK preserves uniqueness of existing rows.

Revision ID: 0007
Revises: 0006
Create Date: 2026-07-09
"""

from __future__ import annotations

from alembic import op

revision = "0007"
down_revision = "0006"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.drop_constraint("pk_predictions", "predictions", schema="pred")
    op.create_primary_key(
        "pk_predictions",
        "predictions",
        ["issue_date", "target_ts", "target_name", "model_name", "quantile"],
        schema="pred",
    )
    op.drop_constraint("pk_backtest_predictions", "backtest_predictions", schema="pred")
    op.create_primary_key(
        "pk_backtest_predictions",
        "backtest_predictions",
        ["issue_date", "target_ts", "target_name", "model_name"],
        schema="pred",
    )


def downgrade() -> None:
    op.drop_constraint("pk_backtest_predictions", "backtest_predictions", schema="pred")
    op.create_primary_key(
        "pk_backtest_predictions",
        "backtest_predictions",
        ["issue_date", "target_ts", "model_name"],
        schema="pred",
    )
    op.drop_constraint("pk_predictions", "predictions", schema="pred")
    op.create_primary_key(
        "pk_predictions",
        "predictions",
        ["issue_date", "target_ts", "model_name", "quantile"],
        schema="pred",
    )
