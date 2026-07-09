"""Write repositories for the pred layer.

``pred.predictions`` is INSERT-ONLY: the first emission for a (issue_date, target, model,
quantile) key wins and is never mutated (``ON CONFLICT DO NOTHING``), honouring the immutable
``issued_at`` rule. ``pred.backtest_predictions`` is a rewritable materialisation of the
rolling-origin backtest (upsert).
"""

from __future__ import annotations

from sqlalchemy import func
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.orm import Session

from src.db.models import BacktestPrediction, Prediction

_MAX_ROWS_PER_STMT = 8000


def insert_predictions(session: Session, rows: list[dict[str, object]]) -> int:
    """Insert live predictions, keeping the first emission per key (never overwrites)."""
    if not rows:
        return 0
    stmt = pg_insert(Prediction).values(rows)
    stmt = stmt.on_conflict_do_nothing(
        index_elements=["issue_date", "target_ts", "target_name", "model_name", "quantile"]
    )
    session.execute(stmt)
    return len(rows)  # rows submitted; conflicts are silently kept (insert-only)


def upsert_backtest_predictions(session: Session, rows: list[dict[str, object]]) -> int:
    """Upsert fold-wise backtest predictions (rewritable materialisation)."""
    written = 0
    for start in range(0, len(rows), _MAX_ROWS_PER_STMT):
        batch = rows[start : start + _MAX_ROWS_PER_STMT]
        stmt = pg_insert(BacktestPrediction).values(batch)
        stmt = stmt.on_conflict_do_update(
            index_elements=["issue_date", "target_ts", "target_name", "model_name"],
            set_={
                "target_name": stmt.excluded["target_name"],
                "y_hat": stmt.excluded["y_hat"],
                "y_true": stmt.excluded["y_true"],
                "created_at": func.now(),
            },
        )
        session.execute(stmt)
        written += len(batch)
    return written
