"""Tests for the read-only serving API.

The 422 path-validation test needs no database; the data endpoints seed sentinel pred rows at a
far-future issue day (which becomes the 'latest') and assert the API surfaces them, then clean up.
"""

from __future__ import annotations

import datetime as dt

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import delete
from sqlalchemy.orm import Session

from src.api.main import app
from src.db.models import BacktestPrediction, Prediction

client = TestClient(app)

_ISSUE = dt.date(2099, 6, 10)
_TARGET_TS = dt.datetime(2099, 6, 11, 12, tzinfo=dt.UTC)


def test_invalid_target_is_rejected() -> None:
    # Literal path param — FastAPI validates before any DB access.
    assert client.get("/forecast/wrong").status_code == 422


@pytest.mark.integration
def test_health_ok(pg_session: Session) -> None:
    body = client.get("/health").json()
    assert body["status"] == "ok"
    assert body["database"] is True


@pytest.mark.integration
def test_forecast_and_performance_surface_seeded_rows(pg_session: Session) -> None:
    try:
        pg_session.add_all(
            [
                Prediction(
                    issue_date=_ISSUE,
                    target_ts=_TARGET_TS,
                    target_name="price",
                    model_name="lightgbm",
                    quantile=q,
                    y_hat=y,
                    issued_at=dt.datetime(2099, 6, 10, 7, tzinfo=dt.UTC),
                    late_issue=False,
                )
                for q, y in (("p10", 90.0), ("p50", 100.0), ("p90", 110.0))
            ]
            + [
                BacktestPrediction(
                    issue_date=_ISSUE,
                    target_ts=_TARGET_TS,
                    target_name="price",
                    model_name="lightgbm_p50",
                    y_hat=100.0,
                    y_true=104.0,
                )
            ]
        )
        pg_session.commit()

        forecast = client.get("/forecast/price").json()
        assert forecast["issue_date"] == "2099-06-10"  # the sentinel is the latest issue
        triplet = {
            p["quantile"]: p["y_hat"] for p in forecast["points"] if p["model_name"] == "lightgbm"
        }
        assert triplet == {"p10": 90.0, "p50": 100.0, "p90": 110.0}

        perf = {p["model_name"]: p for p in client.get("/performance/price").json()}
        assert "lightgbm_p50" in perf and perf["lightgbm_p50"]["mae"] > 0
    finally:
        pg_session.rollback()
        pg_session.execute(delete(Prediction).where(Prediction.issue_date == _ISSUE))
        pg_session.execute(
            delete(BacktestPrediction).where(BacktestPrediction.issue_date == _ISSUE)
        )
        pg_session.commit()
