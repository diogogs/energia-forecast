"""Tests for the OMIE marginalpdbc parser.

Fixtures are real files from OMIE (public data), chosen to cover both resolutions
and every DST edge — where the timezone/period bugs hide.
"""

import datetime as dt
from itertools import pairwise
from pathlib import Path

import pytest

from src.ingestion.sources.omie import MarginalPrice, OmieParseError, parse_marginalpdbc

FIXTURES = Path(__file__).parent / "fixtures" / "omie"


def _load(ymd: str) -> list[MarginalPrice]:
    return parse_marginalpdbc((FIXTURES / f"marginalpdbc_{ymd}.1").read_text(encoding="utf-8"))


@pytest.mark.parametrize(
    ("ymd", "n_periods", "resolution"),
    [
        ("20250101", 24, 60),  # normal hourly
        ("20250330", 23, 60),  # spring-forward hourly (23h)
        ("20241027", 25, 60),  # fall-back hourly (25h)
        ("20260701", 96, 15),  # normal quarter-hourly
        ("20260329", 92, 15),  # spring-forward quarter-hourly (92q)
        ("20251026", 100, 15),  # fall-back quarter-hourly (100q)
    ],
)
def test_period_count_and_resolution(ymd: str, n_periods: int, resolution: int) -> None:
    records = _load(ymd)
    assert len(records) == n_periods
    assert {r.resolution_minutes for r in records} == {resolution}
    assert [r.period for r in records] == list(range(1, n_periods + 1))


def test_column_order_is_pt_then_es() -> None:
    # 2025-01-01 was a decoupled day (PT != ES); order verified vs Energy-Charts (ADR-007).
    p11 = _load("20250101")[10]
    assert p11.period == 11
    assert p11.price_pt == 50.01
    assert p11.price_es == 35.00


def test_timestamps_are_utc_and_evenly_spaced() -> None:
    records = _load("20260701")
    assert all(r.ts_utc.tzinfo == dt.UTC for r in records)
    step = dt.timedelta(minutes=15)
    for earlier, later in pairwise(records):
        assert later.ts_utc - earlier.ts_utc == step


def test_normal_quarter_hourly_start_in_summer() -> None:
    # 2026-07-01 (CEST, UTC+2): market 00:00 = 22:00 UTC the previous day.
    assert _load("20260701")[0].ts_utc == dt.datetime(2026, 6, 30, 22, 0, tzinfo=dt.UTC)


def test_dst_spring_forward_hourly() -> None:
    # 2025-03-30: 02:00 CET -> 03:00 CEST, a 23-hour day. Market 00:00 = 23:00 UTC (CET).
    records = _load("20250330")
    assert records[0].ts_utc == dt.datetime(2025, 3, 29, 23, 0, tzinfo=dt.UTC)
    span = records[-1].ts_utc - records[0].ts_utc + dt.timedelta(hours=1)
    assert span == dt.timedelta(hours=23)


def test_dst_fall_back_quarter_hourly() -> None:
    # 2025-10-26: clocks fall back, a 25-hour day = 100 quarters. Market 00:00 = 22:00 UTC (CEST).
    records = _load("20251026")
    assert records[0].ts_utc == dt.datetime(2025, 10, 25, 22, 0, tzinfo=dt.UTC)
    span = records[-1].ts_utc - records[0].ts_utc + dt.timedelta(minutes=15)
    assert span == dt.timedelta(hours=25)


@pytest.mark.parametrize(
    "bad",
    [
        "",
        "garbage without header",
        "MARGINALPDBC;\n2025;01;01;1;10;10;\n*",  # only 1 period -> invalid count
    ],
)
def test_malformed_input_raises(bad: str) -> None:
    with pytest.raises(OmieParseError):
        parse_marginalpdbc(bad)
