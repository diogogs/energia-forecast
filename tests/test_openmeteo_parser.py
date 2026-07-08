"""Tests for the Open-Meteo Previous Runs parser.

The fixture is a real 3-location, 3-variable, lead-1/2 payload. The critical invariants are:
only lead-suffixed variables are stored (never a leaky current-run value), leads parse
correctly, and valid times are tz-aware UTC.
"""

from __future__ import annotations

import datetime as dt
import json
from pathlib import Path

import pytest

from src.ingestion.sources.openmeteo import (
    LOCATIONS,
    OpenMeteoParseError,
    _split_variable_lead,
    parse_previous_runs,
)

FIXTURES = Path(__file__).parent / "fixtures" / "openmeteo"


def _payload() -> list[dict]:
    return json.loads((FIXTURES / "previous_runs_3loc_20240615.json").read_text(encoding="utf-8"))


def test_parses_all_locations_variables_and_leads() -> None:
    obs = parse_previous_runs(_payload())
    assert {o.location for o in obs} == set(LOCATIONS)  # lisbon, porto, evora
    assert {o.variable for o in obs} == {"temperature_2m", "wind_speed_100m", "shortwave_radiation"}
    assert {o.lead_days for o in obs} == {1, 2}
    # 3 loc * 3 vars * 2 leads * 24 h = 432 rows on this full day.
    assert len(obs) == 3 * 3 * 2 * 24


def test_valid_times_are_utc_and_hourly() -> None:
    obs = [o for o in parse_previous_runs(_payload()) if o.location == "lisbon"]
    lisbon_temp1 = sorted(
        (o for o in obs if o.variable == "temperature_2m" and o.lead_days == 1),
        key=lambda o: o.ts_utc,
    )
    assert lisbon_temp1[0].ts_utc == dt.datetime(2024, 6, 15, 0, 0, tzinfo=dt.UTC)
    assert lisbon_temp1[-1].ts_utc == dt.datetime(2024, 6, 15, 23, 0, tzinfo=dt.UTC)
    assert all(o.ts_utc.tzinfo == dt.UTC for o in lisbon_temp1)


def test_units_captured_natively() -> None:
    obs = parse_previous_runs(_payload())
    units = {o.variable: o.unit for o in obs}
    assert units["wind_speed_100m"] == "km/h"
    assert "C" in units["temperature_2m"]  # '°C' (encoding-agnostic check)


def test_only_lead_suffixed_variables_kept() -> None:
    # Inject a leaky no-suffix current-run series; it must be ignored (not a _previous_dayN key).
    payload = _payload()
    payload[0]["hourly"]["temperature_2m"] = [99.0] * len(payload[0]["hourly"]["time"])
    obs = parse_previous_runs(payload)
    assert all(o.value != 99.0 for o in obs if o.variable == "temperature_2m")


@pytest.mark.parametrize(
    ("key", "expected"),
    [
        ("temperature_2m_previous_day1", ("temperature_2m", 1)),
        ("wind_speed_100m_previous_day2", ("wind_speed_100m", 2)),
        ("temperature_2m", None),  # no lead suffix
        ("shortwave_radiation_previous_dayX", None),  # non-numeric lead
    ],
)
def test_split_variable_lead(key: str, expected: tuple[str, int] | None) -> None:
    assert _split_variable_lead(key) == expected


def test_wrong_location_count_raises() -> None:
    with pytest.raises(OpenMeteoParseError):
        parse_previous_runs(_payload()[:2])  # 2 entries but LOCATIONS has 3


def test_ragged_values_raise() -> None:
    payload = _payload()
    payload[0]["hourly"]["temperature_2m_previous_day1"] = [1.0, 2.0]  # shorter than time
    with pytest.raises(OpenMeteoParseError):
        parse_previous_runs(payload)
