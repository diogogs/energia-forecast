"""Tests for the monitoring watchdog: freshness structure and realised-error scoring."""

from __future__ import annotations

import datetime as dt

import pytest
from sqlalchemy import delete
from sqlalchemy.orm import Session

from src.db.models import Prediction, RenRealised
from src.monitoring.watchdog import data_freshness, realised_error

pytestmark = pytest.mark.integration


def test_freshness_covers_all_sources(pg_session: Session) -> None:
    rows = data_freshness(pg_session)
    assert {r.source for r in rows} == {
        "omie_price",
        "ren_realised",
        "energy_charts_power",
        "openmeteo_forecast",
    }
    for r in rows:  # each source reports a stale flag (True on an empty CI DB, both are valid)
        assert isinstance(r.stale, bool)


def test_realised_error_scores_forecast_vs_outcome(pg_session: Session) -> None:
    target = dt.datetime(2099, 6, 11, 12, tzinfo=dt.UTC)
    try:
        pg_session.add_all(
            [
                Prediction(
                    issue_date=dt.date(2099, 6, 10),
                    target_ts=target,
                    target_name="consumption",
                    model_name="lightgbm",
                    quantile="point",
                    y_hat=5000.0,
                    issued_at=dt.datetime(2099, 6, 10, 7, tzinfo=dt.UTC),
                    late_issue=False,
                ),
                RenRealised(
                    series_name="Consumption",
                    ts_utc=target,
                    resolution_minutes=15,
                    value_mw=5100.0,  # realised 100 MW above the forecast
                    local_date=dt.date(2099, 6, 11),
                    period=1,
                    source_ref="monitoring-test",
                ),
            ]
        )
        pg_session.commit()

        result = realised_error(pg_session, "consumption", days=14)
        assert result.hours_scored == 1
        assert result.mae == pytest.approx(100.0)
    finally:
        pg_session.rollback()
        pg_session.execute(delete(Prediction).where(Prediction.issue_date == dt.date(2099, 6, 10)))
        pg_session.execute(delete(RenRealised).where(RenRealised.source_ref == "monitoring-test"))
        pg_session.commit()
