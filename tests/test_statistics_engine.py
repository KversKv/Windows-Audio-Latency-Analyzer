from dataclasses import replace
from datetime import datetime, timezone

import pytest

from core.latency_analyzer import LatencyMeasurement
from core.statistics_engine import StatisticsEngine


_TIMESTAMP = datetime(2026, 10, 25, 12, 0, tzinfo=timezone.utc)


def _measurement(
    raw_latency_ms,
    *,
    compensation_ms=2.0,
    is_valid=True,
    invalid_reason=None,
):
    compensated_latency_ms = (
        raw_latency_ms - compensation_ms if raw_latency_ms is not None else None
    )
    return LatencyMeasurement(
        raw_latency_ms=raw_latency_ms,
        compensated_latency_ms=compensated_latency_ms,
        compensation_ms=compensation_ms,
        correlation_lag_samples=480.0,
        correlation_peak=0.9,
        correlation_ambiguity_ratio=0.1,
        signal_amplitude=0.1,
        noise_floor=0.01,
        snr_db=20.0,
        is_valid=is_valid,
        invalid_reason=invalid_reason,
        timestamp=_TIMESTAMP,
        playback_timestamp_seconds=10.0,
        capture_timestamp_seconds=10.02,
        is_simulation=True,
        timing_basis="synthetic_sample_index",
        timing_limitations=("synthetic_time_axis",),
    )


def test_statistics_use_only_valid_samples_and_include_all_metrics():
    measurements = [
        *[_measurement(value) for value in (10.0, 20.0, 30.0, 40.0)],
        _measurement(1000.0, is_valid=False, invalid_reason="low_snr;low_correlation"),
    ]

    result = StatisticsEngine.calculate(measurements, minimum_valid_count=4)

    assert result.total_count == 5
    assert result.valid_count == 4
    assert result.invalid_count == 1
    assert result.meets_minimum_valid is True
    assert result.raw.count == result.compensated.count == 4
    assert result.raw.mean_latency_ms == pytest.approx(25.0)
    assert result.raw.median_latency_ms == result.raw.p50_latency_ms == pytest.approx(25.0)
    assert result.raw.p90_latency_ms == pytest.approx(37.0)
    assert result.raw.p95_latency_ms == pytest.approx(38.5)
    assert result.raw.min_latency_ms == 10.0
    assert result.raw.max_latency_ms == 40.0
    assert result.raw.std_latency_ms == pytest.approx(11.1803398875)
    assert result.compensated.mean_latency_ms == pytest.approx(23.0)
    assert result.compensated.p95_latency_ms == pytest.approx(36.5)
    assert result.invalid_reason_counts == (("low_correlation", 1), ("low_snr", 1))


def test_statistics_preserve_valid_outliers_and_use_population_std():
    result = StatisticsEngine.calculate([_measurement(10.0), _measurement(1000.0)])

    assert result.valid_count == 2
    assert result.raw.mean_latency_ms == 505.0
    assert result.raw.min_latency_ms == 10.0
    assert result.raw.max_latency_ms == 1000.0
    assert result.raw.std_latency_ms == 495.0


def test_statistics_without_valid_samples_are_explicitly_empty():
    result = StatisticsEngine.calculate(
        [_measurement(None, is_valid=False, invalid_reason="no_signal")],
        minimum_valid_count=1,
    )

    assert result.total_count == result.invalid_count == 1
    assert result.valid_count == 0
    assert result.meets_minimum_valid is False
    assert result.raw.count == result.compensated.count == 0
    assert result.raw.mean_latency_ms is None
    assert result.raw.std_latency_ms is None
    assert result.compensated.mean_latency_ms is None
    assert result.invalid_reason_counts == (("no_signal", 1),)


def test_single_valid_sample_has_zero_population_std():
    result = StatisticsEngine.calculate([_measurement(20.0)])

    assert result.raw.std_latency_ms == 0.0
    assert result.raw.median_latency_ms == result.raw.p90_latency_ms == 20.0


def test_valid_measurement_with_missing_latency_is_not_silently_ignored():
    measurement = replace(
        _measurement(20.0),
        raw_latency_ms=None,
        compensated_latency_ms=None,
    )

    with pytest.raises(ValueError, match="有效测量缺少有限原始延迟"):
        StatisticsEngine.calculate([measurement])
