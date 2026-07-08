"""Tests for the Energy-Charts public_power parser.

Fixtures are real (trimmed) ES payloads: a normal 96-slot day and a 92-slot spring-forward
day. Because timestamps are UTC-native, DST needs no anchoring — the slot count just drops.
"""

from __future__ import annotations

import datetime as dt
import json
from itertools import pairwise
from pathlib import Path

import pytest

from src.ingestion.sources.energy_charts import (
    EnergyChartsParseError,
    parse_public_power,
)

FIXTURES = Path(__file__).parent / "fixtures" / "energy_charts"


def _payload(name: str) -> dict:
    return json.loads((FIXTURES / f"public_power_es_{name}.json").read_text(encoding="utf-8"))


# (fixture, n_slots, first-slot UTC)
_DAYS = [
    ("normal_20240615", 96, dt.datetime(2024, 6, 14, 22, 0, tzinfo=dt.UTC)),
    ("spring_20260329", 92, dt.datetime(2026, 3, 28, 23, 0, tzinfo=dt.UTC)),
]


@pytest.mark.parametrize(("name", "n_slots", "first_utc"), _DAYS)
def test_slot_count_and_utc(name: str, n_slots: int, first_utc: dt.datetime) -> None:
    payload = _payload(name)
    obs = parse_public_power(payload)

    load = [o for o in obs if o.production_type == "Load"]
    assert len(load) == n_slots
    assert load[0].ts_utc == first_utc
    assert all(o.ts_utc.tzinfo == dt.UTC for o in load)
    assert all(o.resolution_minutes == 15 for o in load)
    assert all(o.country == "es" for o in load)
    assert {b.ts_utc - a.ts_utc for a, b in pairwise(load)} == {dt.timedelta(minutes=15)}


def test_only_curated_feature_types_are_kept() -> None:
    # The fixture carries Load, Wind onshore, Solar, Cross border AND a %-share series;
    # only the curated FEATURE_TYPES survive (the %-share and any non-feature type are dropped).
    payload = _payload("normal_20240615")
    obs = parse_public_power(payload)
    types = {o.production_type for o in obs}
    assert types == {"Load", "Solar", "Wind onshore", "Cross border electricity trading"}
    assert "Renewable share of load" not in types


def test_values_round_trip_and_signed() -> None:
    payload = _payload("normal_20240615")
    obs = parse_public_power(payload)
    by_key = {(o.production_type, o.ts_utc): o.value_mw for o in obs}
    kept = {o.production_type for o in obs}
    for p in payload["production_types"]:
        if p["name"] not in kept:
            continue
        for i, v in enumerate(p["data"]):
            ts = dt.datetime.fromtimestamp(payload["unix_seconds"][i], tz=dt.UTC)
            assert by_key[(p["name"], ts)] == pytest.approx(v)
    # Cross-border trading legitimately goes negative — sign preserved.
    xborder = [o.value_mw for o in obs if o.production_type == "Cross border electricity trading"]
    assert min(xborder) < 0


def test_derived_resolution_from_hourly_spacing() -> None:
    payload = {
        "unix_seconds": [1_700_000_000, 1_700_003_600, 1_700_007_200],  # 3600s spacing
        "production_types": [{"name": "Load", "data": [100.0, 110.0, 120.0]}],
    }
    obs = parse_public_power(payload)
    assert all(o.resolution_minutes == 60 for o in obs)


def test_empty_range_returns_no_rows() -> None:
    assert parse_public_power({"unix_seconds": [], "production_types": []}) == []


@pytest.mark.parametrize(
    "payload",
    [
        {"production_types": []},  # missing unix_seconds
        {"unix_seconds": [1, 2]},  # missing production_types
        {"unix_seconds": [1_700_000_000], "production_types": [{"name": "Load", "data": [1.0]}]},
        {  # duplicate production_type names
            "unix_seconds": [1_700_000_000, 1_700_000_900],
            "production_types": [
                {"name": "Load", "data": [1.0, 2.0]},
                {"name": "Load", "data": [3.0, 4.0]},
            ],
        },
        {  # data length mismatch
            "unix_seconds": [1_700_000_000, 1_700_000_900],
            "production_types": [{"name": "Load", "data": [1.0]}],
        },
    ],
)
def test_malformed_payload_raises(payload: dict) -> None:
    with pytest.raises(EnergyChartsParseError):
        parse_public_power(payload)
