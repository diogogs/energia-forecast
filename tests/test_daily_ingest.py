"""Tests for the daily self-healing runner: window computation and per-source isolation."""

from __future__ import annotations

import datetime as dt

import pytest

from src.ingestion import daily


def test_window_spans_days_back_and_all_sources_run(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: dict[str, tuple[dt.date, dt.date]] = {}

    def make(name: str):
        def _fn(start: dt.date, end: dt.date) -> dict[str, int]:
            calls[name] = (start, end)
            return {"rows": 1}

        return _fn

    monkeypatch.setitem(daily._SOURCES, "omie", make("omie"))
    monkeypatch.setitem(daily._SOURCES, "ren", make("ren"))
    monkeypatch.setitem(daily._SOURCES, "energy_charts", make("energy_charts"))
    monkeypatch.setitem(daily._SOURCES, "openmeteo", make("openmeteo"))

    summary = daily.run_daily(days_back=3)

    assert set(summary) == {"omie", "ren", "energy_charts", "openmeteo"}
    today = dt.datetime.now(tz=dt.UTC).date()
    for start, end in calls.values():
        assert end == today
        assert (end - start).days == 3


def test_one_source_failing_does_not_stop_the_others(monkeypatch: pytest.MonkeyPatch) -> None:
    def ok(start: dt.date, end: dt.date) -> dict[str, int]:
        return {"rows": 5}

    def boom(start: dt.date, end: dt.date) -> dict[str, int]:
        raise RuntimeError("source down")

    monkeypatch.setitem(daily._SOURCES, "omie", boom)
    monkeypatch.setitem(daily._SOURCES, "ren", ok)
    monkeypatch.setitem(daily._SOURCES, "energy_charts", ok)
    monkeypatch.setitem(daily._SOURCES, "openmeteo", ok)

    summary = daily.run_daily(days_back=1)

    assert summary["omie"] == "FAILED"
    assert summary["ren"] == {"rows": 5}  # the others still ran
    assert summary["energy_charts"] == {"rows": 5}
