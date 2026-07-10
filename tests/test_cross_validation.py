"""Tests for the REN vs ENTSO-E cross-check: alignment, divergence stats, severity mapping."""

from __future__ import annotations

import pandas as pd
import pytest

from src.monitoring.cross_validation import (
    CORR_WARN,
    MAE_WARN_MW,
    MIN_OVERLAP_HOURS,
    compare_series,
)


def _hourly(values: list[float], start: str = "2026-06-01") -> pd.Series:
    idx = pd.date_range(start, periods=len(values), freq="1h", tz="UTC")
    return pd.Series(values, index=idx)


def test_identical_series_is_info() -> None:
    values = [5000.0 + 100 * (i % 24) for i in range(48)]
    result = compare_series(_hourly(values), _hourly(values))
    assert result is not None
    assert result.hours == 48
    assert result.mae_mw == 0.0
    assert result.severity == "info"


def test_small_aggregation_noise_stays_info() -> None:
    values = [5000.0 + 100 * (i % 24) for i in range(48)]
    noisy = [v + (10 if i % 2 else -10) for i, v in enumerate(values)]  # ±10 MW
    result = compare_series(_hourly(values), _hourly(noisy))
    assert result is not None
    assert result.mae_mw == pytest.approx(10.0)
    assert result.mae_mw <= MAE_WARN_MW
    assert result.severity == "info"


def test_systematic_divergence_is_warning() -> None:
    values = [5000.0 + 100 * (i % 24) for i in range(48)]
    shifted = [v + 500.0 for v in values]  # a 500 MW bias, e.g. a unit-scale bug upstream
    result = compare_series(_hourly(values), _hourly(shifted))
    assert result is not None
    assert result.mae_mw == pytest.approx(500.0)
    assert result.severity == "warning"


def test_decorrelation_is_warning_even_with_small_mae() -> None:
    # Same level but scrambled shape: corr collapses while MAE can stay modest.
    values = [5000.0 + 100 * (i % 24) for i in range(72)]
    scrambled = list(reversed(values))
    result = compare_series(_hourly(values), _hourly(scrambled))
    assert result is not None
    assert result.corr < CORR_WARN
    assert result.severity == "warning"


def test_insufficient_overlap_returns_none() -> None:
    short = [5000.0] * (MIN_OVERLAP_HOURS - 1)
    assert compare_series(_hourly(short), _hourly(short)) is None


def test_misaligned_indexes_use_intersection_only() -> None:
    a = _hourly([5000.0] * 48, start="2026-06-01")
    b = _hourly([5000.0] * 48, start="2026-06-01 12:00")  # 12h offset → 36h overlap
    result = compare_series(a, b)
    assert result is not None
    assert result.hours == 36
