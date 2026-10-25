from datetime import datetime, timezone

import numpy as np
import pytest

from audio.stimulus_generator import ChirpGenerator
from core.latency_analyzer import LatencyAnalyzer
from core.offline_simulation import OfflineLatencySimulator


def make_generator(sample_rate: int = 48000, window: str = "hann") -> ChirpGenerator:
    return ChirpGenerator(
        sample_rate=sample_rate,
        start_frequency_hz=500,
        end_frequency_hz=min(8000, sample_rate / 2 - 1),
        duration_ms=10,
        amplitude=0.1,
        window=window,
    )


def make_analyzer(
    *,
    min_snr_db: float = 6,
    max_latency_ms: float = 200,
) -> LatencyAnalyzer:
    return LatencyAnalyzer(
        min_snr_db=min_snr_db,
        min_correlation=0.35,
        min_latency_ms=0,
        max_latency_ms=max_latency_ms,
    )


def make_simulator(
    sample_rate: int = 48000,
    *,
    analyzer: LatencyAnalyzer | None = None,
) -> OfflineLatencySimulator:
    return OfflineLatencySimulator(
        generator=make_generator(sample_rate),
        analyzer=analyzer or make_analyzer(),
        seed=42,
    )


def test_chirp_is_deterministic_pre_generated_and_peak_limited():
    first = make_generator()
    second = make_generator()

    np.testing.assert_array_equal(first.generate(), second.generate())
    assert first.samples is first.generate()
    assert not first.samples.flags.writeable
    assert first.frame_count == 480
    assert np.max(np.abs(first.samples)) == pytest.approx(0.1, rel=1e-6)
    assert first.samples[0] == first.samples[-1] == 0


def test_chirp_validates_frequency_amplitude_duration_and_window():
    parameters = {
        "sample_rate": 48000,
        "start_frequency_hz": 500,
        "end_frequency_hz": 8000,
        "duration_ms": 10,
        "amplitude": 0.1,
        "window": "hann",
    }
    for key, value in (
        ("end_frequency_hz", 24000),
        ("amplitude", 1.01),
        ("duration_ms", 0),
        ("window", "unsupported"),
    ):
        with pytest.raises(ValueError):
            ChirpGenerator(**parameters | {key: value})


@pytest.mark.parametrize("sample_rate", [44100, 48000, 96000])
@pytest.mark.parametrize("delay_ms", [5, 10, 20, 50])
def test_known_delays_across_sample_rates(sample_rate, delay_ms):
    measurement = make_simulator(sample_rate).simulate(delay_ms, snr_db=20).measurement

    assert measurement.is_valid, measurement.invalid_reason
    assert measurement.raw_latency_ms == pytest.approx(delay_ms, abs=0.08)
    assert measurement.compensated_latency_ms == measurement.raw_latency_ms
    assert measurement.correlation_peak > 0.35
    assert measurement.snr_db > 6


@pytest.mark.parametrize("snr_db", [10, 20, 30])
def test_known_delay_with_multiple_valid_snr_levels(snr_db):
    measurement = make_simulator().simulate(20, snr_db=snr_db).measurement

    assert measurement.is_valid, measurement.invalid_reason
    assert measurement.raw_latency_ms == pytest.approx(20, abs=0.1)
    assert measurement.signal_amplitude > 0
    assert measurement.noise_floor > 0


@pytest.mark.parametrize("delay_ms", [10.137, 20.337])
def test_fractional_delay_uses_subsample_interpolation(delay_ms):
    measurement = make_simulator().simulate(delay_ms, snr_db=None).measurement

    assert measurement.is_valid, measurement.invalid_reason
    assert measurement.raw_latency_ms == pytest.approx(delay_ms, abs=0.02)
    assert measurement.correlation_lag_samples % 1 != 0


@pytest.mark.parametrize("gain", [0.2, -0.2])
def test_gain_variation_and_polarity_inversion_remain_detectable(gain):
    measurement = make_simulator().simulate(10, snr_db=None, gain=gain).measurement

    assert measurement.is_valid, measurement.invalid_reason
    assert measurement.raw_latency_ms == pytest.approx(10, abs=0.02)
    assert measurement.correlation_peak > 0.99


def test_low_snr_is_preserved_with_invalid_reason():
    measurement = make_simulator().simulate(20, snr_db=0).measurement

    assert not measurement.is_valid
    assert "low_snr" in measurement.invalid_reason
    assert measurement.raw_latency_ms is not None
    assert measurement.correlation_peak > 0


def test_no_signal_and_non_finite_capture_are_not_silently_discarded():
    simulator = make_simulator()
    no_signal = simulator.simulate(10, snr_db=None, gain=0).measurement
    non_finite = make_analyzer().analyze(
        make_generator().samples,
        np.array([0.0, np.nan, 0.0]),
        sample_rate=48000,
        is_simulation=True,
    )

    assert not no_signal.is_valid
    assert no_signal.invalid_reason == "no_signal"
    assert no_signal.raw_latency_ms is None
    assert not non_finite.is_valid
    assert non_finite.invalid_reason == "non_finite_capture"


def test_ambiguous_correlation_is_invalid():
    generator = make_generator()
    reference = generator.samples
    first_delay = round(generator.sample_rate * 0.01)
    second_delay = round(generator.sample_rate * 0.04)
    captured = np.zeros(second_delay + reference.size + 480, dtype=np.float32)
    captured[first_delay : first_delay + reference.size] += reference
    captured[second_delay : second_delay + reference.size] += reference

    measurement = make_analyzer().analyze(
        reference,
        captured,
        sample_rate=generator.sample_rate,
        is_simulation=True,
    )

    assert not measurement.is_valid
    assert "ambiguous_correlation" in measurement.invalid_reason
    assert measurement.correlation_ambiguity_ratio > 0.9


def test_out_of_range_latency_is_retained_and_marked_invalid():
    analyzer = make_analyzer(max_latency_ms=10)
    measurement = make_simulator(analyzer=analyzer).simulate(20, snr_db=None).measurement

    assert not measurement.is_valid
    assert "latency_out_of_range" in measurement.invalid_reason
    assert measurement.raw_latency_ms == pytest.approx(20, abs=0.02)


def test_compensation_and_measurement_timestamp_are_explicit():
    timestamp = datetime(2026, 10, 25, 12, 0, tzinfo=timezone.utc)
    simulator = make_simulator()
    result = simulator.simulate(10, snr_db=None, compensation_ms=1.5)
    measurement = simulator.analyzer.analyze(
        result.reference,
        result.captured,
        sample_rate=48000,
        compensation_ms=1.5,
        is_simulation=True,
        measurement_timestamp=timestamp,
    )

    assert measurement.raw_latency_ms == pytest.approx(10, abs=0.02)
    assert measurement.compensated_latency_ms == pytest.approx(
        measurement.raw_latency_ms - 1.5
    )
    assert measurement.compensation_ms == 1.5
    assert measurement.timestamp is timestamp


def test_offline_simulation_is_reproducible_and_explicitly_labelled():
    first = make_simulator().simulate(10, snr_db=20)
    second = make_simulator().simulate(10, snr_db=20)

    np.testing.assert_array_equal(first.captured, second.captured)
    assert first.is_simulation
    assert first.measurement.is_simulation
    assert first.measurement.timing_basis == "synthetic_sample_index"
    assert "synthetic_time_axis" in first.measurement.timing_limitations
    assert first.measurement.playback_timestamp_seconds is None
    assert first.measurement.capture_timestamp_seconds is None
    assert first.true_delay_ms == 10
