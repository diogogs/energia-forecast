"""Tests for prediction persistence: backtest-row reshaping (pure) and the insert-only rule."""

from __future__ import annotations

import datetime as dt

import pandas as pd
import pytest
from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from src.db.models import Prediction
from src.db.repositories.predictions import insert_predictions
from src.models.backtest import to_backtest_rows


def test_to_backtest_rows_maps_each_model_and_keeps_truth() -> None:
    idx = pd.to_datetime(["2026-06-02T00:00Z", "2026-06-02T01:00Z"])
    preds = pd.DataFrame(
        {
            "y": [5000.0, 4800.0],
            "y_hat": [5050.0, 4750.0],  # lightgbm
            "cons_lag_48h": [5200.0, 4900.0],  # persistence
            "cons_lag_168h": [5100.0, 4850.0],  # seasonal
            "issue_date": [dt.date(2026, 6, 1)] * 2,
        },
        index=idx,
    )
    rows = to_backtest_rows(preds)
    assert len(rows) == 6  # 3 models x 2 hours
    by_model = {(r["model_name"], r["target_ts"]): r for r in rows}
    assert by_model[("lightgbm", idx[0].to_pydatetime())]["y_hat"] == 5050.0
    assert by_model[("persistence_48h", idx[0].to_pydatetime())]["y_hat"] == 5200.0
    assert by_model[("seasonal_168h", idx[1].to_pydatetime())]["y_hat"] == 4850.0
    assert all(r["y_true"] in (5000.0, 4800.0) for r in rows)  # realised truth carried through


@pytest.mark.integration
def test_predictions_are_insert_only(pg_session: Session) -> None:
    # A far-future sentinel forecast; a second emission must NOT overwrite the first (issued_at).
    target = dt.datetime(2099, 6, 11, 12, tzinfo=dt.UTC)
    first = dt.datetime(2099, 6, 10, 7, 0, tzinfo=dt.UTC)
    second = dt.datetime(2099, 6, 10, 9, 30, tzinfo=dt.UTC)

    def row(issued_at: dt.datetime, y_hat: float) -> dict[str, object]:
        return {
            "issue_date": dt.date(2099, 6, 10),
            "target_ts": target,
            "model_name": "lightgbm",
            "quantile": "point",
            "target_name": "consumption",
            "y_hat": y_hat,
            "issued_at": issued_at,
            "late_issue": False,
        }

    try:
        insert_predictions(pg_session, [row(first, 5000.0)])
        pg_session.commit()
        insert_predictions(pg_session, [row(second, 9999.0)])  # a re-emission, must be ignored
        pg_session.commit()

        pg_session.expire_all()
        stored = (
            pg_session.execute(
                select(Prediction).where(Prediction.issue_date == dt.date(2099, 6, 10))
            )
            .scalars()
            .all()
        )
        assert len(stored) == 1
        assert stored[0].issued_at == first  # first emission preserved
        assert stored[0].y_hat == 5000.0  # not overwritten
    finally:
        pg_session.rollback()
        pg_session.execute(delete(Prediction).where(Prediction.issue_date == dt.date(2099, 6, 10)))
        pg_session.commit()


@pytest.mark.integration
def test_consumption_and_price_share_a_model_name_without_colliding(pg_session: Session) -> None:
    # 'seasonal_168h'/'point' exists for BOTH targets; target_name is in the key so both persist.
    issue, target = dt.date(2099, 6, 10), dt.datetime(2099, 6, 11, 12, tzinfo=dt.UTC)
    now = dt.datetime(2099, 6, 10, 7, tzinfo=dt.UTC)

    def row(target_name: str, y_hat: float) -> dict[str, object]:
        return {
            "issue_date": issue,
            "target_ts": target,
            "model_name": "seasonal_168h",
            "quantile": "point",
            "target_name": target_name,
            "y_hat": y_hat,
            "issued_at": now,
            "late_issue": False,
        }

    try:
        insert_predictions(pg_session, [row("consumption", 5000.0), row("price", 60.0)])
        pg_session.commit()
        pg_session.expire_all()
        stored = (
            pg_session.execute(select(Prediction).where(Prediction.issue_date == issue))
            .scalars()
            .all()
        )
        stored_pairs = {(r.target_name, r.y_hat) for r in stored}
        assert stored_pairs == {("consumption", 5000.0), ("price", 60.0)}
    finally:
        pg_session.rollback()
        pg_session.execute(delete(Prediction).where(Prediction.issue_date == issue))
        pg_session.commit()


def test_early_emission_is_refused() -> None:
    # Insert-only means an early emission would WIN the day with staler data (seen live
    # 2026-07-11: a mis-timezoned external cron fired at 06:05 UTC). Before t_issue → refuse.
    import datetime as dt

    from src.models.predict import too_early

    issue = dt.date(2026, 7, 11)
    assert too_early(issue, dt.datetime(2026, 7, 11, 6, 5, tzinfo=dt.UTC))
    assert not too_early(issue, dt.datetime(2026, 7, 11, 7, 5, tzinfo=dt.UTC))
