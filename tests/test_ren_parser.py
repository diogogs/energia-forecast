"""Tests for the REN ProductionBreakdown parser.

Fixtures are real (trimmed) REN payloads covering every DST edge — where the timezone and
slot-count bugs hide. Trimmed to 4 series (target + generation + flow + storage), full-length
data arrays preserved so DST slot counts (92/96/100) stay authentic.
"""

from __future__ import annotations

import datetime as dt
import json
from itertools import pairwise
from pathlib import Path

import pytest

from src.ingestion.sources.ren import (
    RenParseError,
    parse_production_breakdown,
    to_ticks,
)

FIXTURES = Path(__file__).parent / "fixtures" / "ren"


def _payload(name: str) -> dict:
    return json.loads((FIXTURES / f"production_breakdown_{name}.json").read_text(encoding="utf-8"))


# (fixture, day, n_slots, day_hours, expected slot-0 UTC)
_DAYS = [
    (
        "normal_20240615",
        dt.date(2024, 6, 15),
        96,
        24,
        dt.datetime(2024, 6, 14, 23, 0, tzinfo=dt.UTC),
    ),
    (
        "spring_20240331",
        dt.date(2024, 3, 31),
        92,
        23,
        dt.datetime(2024, 3, 31, 0, 0, tzinfo=dt.UTC),
    ),
    (
        "fall_20241027",
        dt.date(2024, 10, 27),
        100,
        25,
        dt.datetime(2024, 10, 26, 23, 0, tzinfo=dt.UTC),
    ),
]


@pytest.mark.parametrize(("name", "day", "n_slots", "day_hours", "start_utc"), _DAYS)
def test_dst_slot_count_and_utc_anchor(
    name: str, day: dt.date, n_slots: int, day_hours: int, start_utc: dt.datetime
) -> None:
    payload = _payload(name)
    obs = parse_production_breakdown(payload, day)

    n_series = len(payload["series"])
    assert len(obs) == n_slots * n_series  # these are complete days: no nulls

    consumption = [o for o in obs if o.series_name == "Consumption"]
    assert len(consumption) == n_slots
    assert all(o.ts_utc.tzinfo == dt.UTC for o in consumption)
    assert consumption[0].ts_utc == start_utc
    assert consumption[0].period == 1
    # DST-correct wall-clock span.
    span = consumption[-1].ts_utc - consumption[0].ts_utc + dt.timedelta(minutes=15)
    assert span == dt.timedelta(hours=day_hours)
    # Evenly spaced 15-min within the series.
    assert {b.ts_utc - a.ts_utc for a, b in pairwise(consumption)} == {dt.timedelta(minutes=15)}


def test_values_round_trip_against_fixture() -> None:
    # Every parsed observation must equal the raw data[i] at its slot, with matching local_date.
    day = dt.date(2024, 6, 15)
    payload = _payload("normal_20240615")
    obs = parse_production_breakdown(payload, day)
    by_key = {(o.series_name, o.period): o for o in obs}
    for s in payload["series"]:
        for i, v in enumerate(s["data"]):
            o = by_key[(s["name"], i + 1)]
            assert o.value_mw == pytest.approx(v)
            assert o.local_date == day
            assert o.resolution_minutes == 15


def test_nulls_are_skipped_but_periods_stay_aligned() -> None:
    # A partial day: last two slots not yet published. They must produce no rows, and the
    # surviving slots must keep their true period/ts_utc (skip must not renumber).
    payload = {
        "xAxis": {"categories": ["00:00"] * 98},
        "series": [{"name": "Consumption", "data": [100.0, 200.0, None, 400.0] + [None] * 92}],
    }
    obs = parse_production_breakdown(payload, dt.date(2024, 6, 15))
    assert [(o.period, o.value_mw) for o in obs] == [(1, 100.0), (2, 200.0), (4, 400.0)]
    # period 4 -> slot index 3 -> 45 min after the local-midnight UTC anchor.
    p4 = next(o for o in obs if o.period == 4)
    assert p4.ts_utc == dt.datetime(2024, 6, 14, 23, 45, tzinfo=dt.UTC)


def test_signed_values_preserved() -> None:
    payload = {
        "xAxis": {"categories": ["00:00"] * 98},
        "series": [{"name": "Battery Injection", "data": [-5.0, 12.5] + [0.0] * 94}],
    }
    obs = parse_production_breakdown(payload, dt.date(2024, 6, 15))
    assert obs[0].value_mw == -5.0


@pytest.mark.parametrize(
    "payload",
    [
        {"series": []},  # no series
        {"xAxis": {}},  # no series key
        {
            "series": [{"name": "A", "data": [1.0] * 96}, {"name": "B", "data": [1.0] * 92}]
        },  # ragged
        {"series": [{"name": "A", "data": [1.0] * 50}]},  # invalid slot count
        {"series": [{"name": "", "data": [1.0] * 96}]},  # empty series name
        {
            "series": [{"name": "A", "data": [1.0] * 96}, {"name": "A", "data": [2.0] * 96}]
        },  # duplicate name -> repeated natural keys would break the single-statement upsert
    ],
)
def test_malformed_payload_raises(payload: dict) -> None:
    with pytest.raises(RenParseError):
        parse_production_breakdown(payload, dt.date(2024, 6, 15))


def test_slot_count_must_match_the_days_civil_length() -> None:
    # 92 slots is a valid DST count — but only on a 23h spring-forward day. On a normal
    # 96-slot day it must raise: accepting it would emit timestamps that stop 1h short
    # (or, for 96-on-92, spill into the next day and collide with its rows on upsert).
    payload = {"series": [{"name": "Consumption", "data": [1.0] * 92}]}
    with pytest.raises(RenParseError, match="does not match"):
        parse_production_breakdown(payload, dt.date(2024, 6, 15))
    # And the converse: 96 slots on the 92-slot spring-forward day.
    payload = {"series": [{"name": "Consumption", "data": [1.0] * 96}]}
    with pytest.raises(RenParseError, match="does not match"):
        parse_production_breakdown(payload, dt.date(2024, 3, 31))


def test_to_ticks_matches_dotnet_midnight() -> None:
    # Cross-checked against the live site's dayToSearchString for a midnight day.
    assert to_ticks(dt.date(2024, 6, 1)) == 638_527_968_000_000_000
