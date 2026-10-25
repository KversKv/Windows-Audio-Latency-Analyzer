import numpy as np
import pytest

from audio.audio_synchronizer import AudioSynchronizer, SampleTimeAnchor
from audio.stream_common import StreamTiming
from audio.stimulus_generator import ChirpGenerator
from core.latency_analyzer import LatencyAnalyzer


def make_timing(
    *,
    sample_index: int,
    current_time: float,
    dac_time: float | None = None,
    adc_time: float | None = None,
) -> StreamTiming:
    return StreamTiming(
        sample_index=sample_index,
        frames=128,
        callback_count=1,
        sample_count=sample_index + 128,
        current_time=current_time,
        input_adc_time=adc_time,
        output_dac_time=dac_time,
    )


def test_stream_timestamp_mapping_uses_dac_adc_and_explicit_offset():
    playback = make_timing(sample_index=1000, current_time=999, dac_time=10)
    capture = make_timing(sample_index=2000, current_time=888, adc_time=20)
    synchronizer = AudioSynchronizer.from_stream_timings(
        playback,
        capture,
        sample_rate=1000,
        capture_clock_offset_seconds=10,
        playback_drift_ppm=0,
        capture_drift_ppm=0,
    )

    result = synchronizer.synchronize(
        playback_sample_index=1500,
        capture_sample_index=2550,
    )

    assert result.playback_timestamp_seconds == pytest.approx(10.5)
    assert result.capture_timestamp_seconds == pytest.approx(10.55)
    assert result.raw_latency_ms == pytest.approx(50)
    assert result.clock_offset_calibrated
    assert result.capture_clock_offset_ms == 10000
    assert result.limitations == ()


def test_independent_capture_clock_drift_is_corrected_without_fake_precision():
    synchronizer = AudioSynchronizer(
        playback_anchor=SampleTimeAnchor(0, 100, 48000),
        capture_anchor=SampleTimeAnchor(0, 200, 48000),
        capture_clock_offset_seconds=100,
        playback_drift_ppm=0,
        capture_drift_ppm=100,
    )

    result = synchronizer.synchronize(
        playback_sample_index=4800,
        capture_sample_index=7200.72,
    )

    assert result.raw_latency_ms == pytest.approx(50, abs=1e-9)
    assert result.limitations == ()


def test_uncalibrated_offset_and_drift_are_explicit_limitations():
    synchronizer = AudioSynchronizer(
        playback_anchor=SampleTimeAnchor(0, 100, 48000),
        capture_anchor=SampleTimeAnchor(0, 200, 48000),
    )

    result = synchronizer.synchronize(
        playback_sample_index=0,
        capture_sample_index=480,
    )

    assert not result.clock_offset_calibrated
    assert result.capture_clock_offset_ms is None
    assert set(result.limitations) == {
        "capture_clock_offset_uncalibrated",
        "playback_sample_rate_drift_uncalibrated",
        "capture_sample_rate_drift_uncalibrated",
    }


def test_missing_hardware_timestamps_do_not_fall_back_to_current_time():
    playback_without_dac = make_timing(sample_index=0, current_time=123)
    capture_without_adc = make_timing(sample_index=0, current_time=123)
    valid_playback = make_timing(sample_index=0, current_time=123, dac_time=10)
    valid_capture = make_timing(sample_index=0, current_time=123, adc_time=10)

    with pytest.raises(ValueError, match="DAC"):
        AudioSynchronizer.from_stream_timings(
            playback_without_dac,
            valid_capture,
            sample_rate=48000,
        )
    with pytest.raises(ValueError, match="ADC"):
        AudioSynchronizer.from_stream_timings(
            valid_playback,
            capture_without_adc,
            sample_rate=48000,
        )


def test_analyzer_combines_correlation_with_synthetic_stream_timing():
    sample_rate = 48000
    generator = ChirpGenerator(
        sample_rate=sample_rate,
        start_frequency_hz=500,
        end_frequency_hz=8000,
        duration_ms=10,
        amplitude=0.1,
        window="hann",
    )
    analyzer = LatencyAnalyzer(
        min_snr_db=6,
        min_correlation=0.35,
        min_latency_ms=0,
        max_latency_ms=200,
    )
    playback_sample = 10000
    capture_window_start = 9000
    response_lag = 1480
    captured = np.zeros(response_lag + generator.frame_count + 480, dtype=np.float32)
    captured[response_lag : response_lag + generator.frame_count] = generator.samples
    synchronizer = AudioSynchronizer(
        playback_anchor=SampleTimeAnchor(0, 50, sample_rate),
        capture_anchor=SampleTimeAnchor(0, 50, sample_rate),
        capture_clock_offset_seconds=0,
        playback_drift_ppm=0,
        capture_drift_ppm=0,
    )

    measurement = analyzer.analyze(
        generator.samples,
        captured,
        sample_rate=sample_rate,
        is_simulation=True,
        synchronizer=synchronizer,
        playback_sample_index=playback_sample,
        capture_window_start_sample=capture_window_start,
    )

    assert measurement.is_valid, measurement.invalid_reason
    assert measurement.raw_latency_ms == pytest.approx(10, abs=0.02)
    assert measurement.playback_timestamp_seconds == pytest.approx(50 + 10000 / 48000)
    assert measurement.capture_timestamp_seconds == pytest.approx(50 + 10480 / 48000)
    assert measurement.timing_basis == "synthetic_dac_adc_timestamps"
    assert measurement.timing_limitations == ("synthetic_time_axis",)
