"""Tests for the daily self-healing runner: window computation, per-source isolation, dq events.

run_daily records each source's outcome to ops.dq_log through a real engine — these are unit
tests, so the DB seam (engine/factory) and the recorder are stubbed out. Regression guard: an
earlier version of this file stubbed only the sources, and the suite wrote fake dq events to
whatever database .env pointed at.
"""

from __future__ import annotations

import datetime as dt
from typing import Any

import pytest

from src.ingestion import daily


class _StubSession:
    def __enter__(self) -> _StubSession:
        return self

    def __exit__(self, *exc: object) -> None:
        return None

    def commit(self) -> None:
        return None


@pytest.fixture
def dq_events(monkeypatch: pytest.MonkeyPatch) -> list[dict[str, Any]]:
    """Stub the DB seam and capture record_dq_event calls (no real database touched)."""
    events: list[dict[str, Any]] = []
    monkeypatch.setattr(
        daily, "make_engine", lambda: type("E", (), {"dispose": lambda self: None})()
    )
    monkeypatch.setattr(daily, "make_session_factory", lambda engine: _StubSession)
    monkeypatch.setattr(daily, "record_dq_event", lambda session, **kwargs: events.append(kwargs))
    return events


def test_window_spans_days_back_and_all_sources_run(
    monkeypatch: pytest.MonkeyPatch, dq_events: list[dict[str, Any]]
) -> None:
    calls: dict[str, tuple[dt.date, dt.date]] = {}

    def make(name: str):
        def _fn(start: dt.date, end: dt.date) -> dict[str, int]:
            calls[name] = (start, end)
            return {"rows": 1}

        return _fn

    # Patch the backfill functions (not _SOURCES): the openmeteo lambda adds +1 day to the
    # window and that arithmetic is exactly what this test needs to observe.
    monkeypatch.setattr(daily, "backfill_omie", make("omie"))
    monkeypatch.setattr(daily, "backfill_ren", make("ren"))
    monkeypatch.setattr(daily, "backfill_energy_charts", make("energy_charts"))
    monkeypatch.setattr(daily, "backfill_openmeteo", make("openmeteo"))

    summary = daily.run_daily(days_back=3)

    assert set(summary) == {"omie", "ren", "energy_charts", "openmeteo"}
    today = dt.datetime.now(tz=dt.UTC).date()
    for name, (start, end) in calls.items():
        # Open-Meteo and OMIE reach into tomorrow: weather valid on the delivery day
        # (NaN-weather regression, 2026-07-11) and cleared D+1 prices published on day D
        # (same-day afternoon ingest, ADR-015). REN and Energy-Charts stop at today.
        expected_end = today + dt.timedelta(days=1) if name in ("openmeteo", "omie") else today
        assert end == expected_end, name
        assert start == today - dt.timedelta(days=3)
    # One durable dq event per source, all clean runs → severity info.
    assert [e["severity"] for e in dq_events] == ["info"] * 4


def test_one_source_failing_does_not_stop_the_others(
    monkeypatch: pytest.MonkeyPatch, dq_events: list[dict[str, Any]]
) -> None:
    def ok(start: dt.date, end: dt.date) -> dict[str, int]:
        return {"rows": 5}

    def partial(start: dt.date, end: dt.date) -> dict[str, int]:
        return {"rows": 5, "days_failed": 1}

    def boom(start: dt.date, end: dt.date) -> dict[str, int]:
        raise RuntimeError("source down")

    monkeypatch.setattr(daily, "backfill_omie", boom)
    monkeypatch.setattr(daily, "backfill_ren", partial)
    monkeypatch.setattr(daily, "backfill_energy_charts", ok)
    monkeypatch.setattr(daily, "backfill_openmeteo", ok)

    summary = daily.run_daily(days_back=1)

    assert summary["omie"] == "FAILED"
    assert summary["ren"] == {"rows": 5, "days_failed": 1}  # the others still ran
    assert summary["energy_charts"] == {"rows": 5}
    # dq severities: hard failure → error, survived-with-failed-days → warning, clean → info.
    by_source = {e["source"]: e for e in dq_events}
    assert by_source["omie"]["severity"] == "error"
    assert "source down" in by_source["omie"]["detail"]
    assert by_source["ren"]["severity"] == "warning"
    assert by_source["energy_charts"]["severity"] == "info"
    assert by_source["energy_charts"]["rows_written"] == 5
