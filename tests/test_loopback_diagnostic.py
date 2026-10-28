import json
import os
from datetime import datetime, timezone

import numpy as np

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QCoreApplication, QObject, Signal

from app import loopback_diagnostic as diagnostic_module
from app.config import AppConfig, AudioConfig, MeasurementConfig
from audio.audio_backend import AudioBackendError, AudioStreamInfo
from audio.stream_common import StreamDiagnostics, StreamTiming
from core.latency_analyzer import LatencyMeasurement
from core.test_models import (
    MeasurementRecord,
    SessionAudioDiagnostics,
    TestSessionConfig as SessionConfig,
    TestSessionSnapshot as SessionSnapshot,
)
from core.test_state_machine import TestState as SessionState


def _measurement(*, sequence: int, valid: bool) -> LatencyMeasurement:
    return LatencyMeasurement(
        raw_latency_ms=20.0 + sequence if valid else None,
        compensated_latency_ms=20.0 + sequence if valid else None,
        compensation_ms=0.0,
        correlation_lag_samples=960.0 if valid else None,
        correlation_peak=0.99 if valid else 0.0,
        correlation_ambiguity_ratio=0.1,
        signal_amplitude=0.1 if valid else 0.0,
        noise_floor=0.001,
        snr_db=40.0 if valid else None,
        is_valid=valid,
        invalid_reason=None if valid else "no_signal",
        timestamp=datetime(2026, 10, 28, tzinfo=timezone.utc),
        playback_timestamp_seconds=10.0 if valid else None,
        capture_timestamp_seconds=10.02 if valid else None,
        is_simulation=False,
        timing_basis="dac_adc_stream_timestamps",
        timing_limitations=("capture_clock_offset_uncalibrated",),
    )


def _config(count: int = 2) -> SessionConfig:
    return SessionConfig(
        audio=AudioConfig(
            sample_rate=48000,
            output_device_id=3,
            input_device_id=4,
            blocksize=480,
        ),
        measurement=MeasurementConfig(default_test_count=count),
        diagnostic_mode="electrical",
    )


def _record(config: SessionConfig, sequence: int, valid: bool) -> MeasurementRecord:
    reference = np.linspace(-0.1, 0.1, 16, dtype=np.float32)
    captured = np.zeros(64, dtype=np.float32)
    if valid:
        captured[8:24] = reference
    return MeasurementRecord(
        sequence=sequence,
        reference=reference,
        captured=captured,
        capture_window_start_sample=100,
        playback_sample_index=200,
        measurement=_measurement(sequence=sequence, valid=valid),
    )


def test_arguments_and_session_config_apply_overrides_without_calibration():
    args = diagnostic_module._build_argument_parser().parse_args(
        [
            "--mode",
            "software",
            "--output-device",
            "23",
            "--sample-rate",
            "48000",
            "--blocksize",
            "480",
            "--count",
            "20",
        ]
    )
    overridden = diagnostic_module._apply_audio_overrides(AppConfig(), args)

    session = diagnostic_module._make_session_config(
        overridden,
        mode=args.mode,
        count=args.count,
    )

    assert session.audio.output_device_id == 23
    assert session.audio.sample_rate == 48000
    assert session.audio.blocksize == 480
    assert session.test_count == 20
    assert session.diagnostic_mode == "software"
    assert session.compensation_ms == 0.0
    assert session.calibration_profile is None


def test_report_serializes_statistics_diagnostics_and_invalid_waveform(tmp_path):
    config = _config()
    records = (_record(config, 1, True), _record(config, 2, False))
    timing = StreamTiming(
        sample_index=0,
        frames=480,
        callback_count=10,
        sample_count=4800,
        current_time=1.0,
        input_adc_time=None,
        output_dac_time=1.0,
        clock_domain="test-clock",
        min_frames=240,
        max_frames=480,
    )
    stream_diagnostics = StreamDiagnostics.build(
        direction="output",
        requested_device_id=3,
        requested_sample_rate=48000,
        requested_channels=1,
        requested_blocksize=480,
        stream_info=AudioStreamInfo(
            device_id=3,
            device_name="测试输出",
            host_api="Windows WASAPI",
            sample_rate=48000.0,
        ),
        open_count=1,
        start_count=1,
        timing=timing,
    )
    snapshot = SessionSnapshot(
        config=config,
        records=records,
        terminal_state=SessionState.COMPLETED,
        started_at=config.started_at,
        finished_at=datetime.now(timezone.utc),
        is_simulation=False,
        audio_diagnostics=SessionAudioDiagnostics(playback=stream_diagnostics),
    )

    records_path, summary_path = diagnostic_module.write_diagnostic_report(
        tmp_path,
        config=config,
        snapshot=snapshot,
        records=records,
        terminal_state="COMPLETED",
        errors=(),
        timed_out=False,
        shutdown_completed=True,
    )

    lines = [json.loads(line) for line in records_path.read_text(encoding="utf-8").splitlines()]
    summary = json.loads(summary_path.read_text(encoding="utf-8"))
    assert len(lines) == 2
    assert lines[0]["measurement"]["raw_latency_ms"] == 21.0
    assert lines[1]["invalid_waveform"] == "invalid_0002.npz"
    assert summary["report_finalized"] is True
    assert summary["statistics"]["valid_count"] == 1
    assert summary["statistics"]["invalid_count"] == 1
    assert summary["statistics"]["raw"]["mean_ms"] == 21.0
    assert summary["statistics"]["raw"]["p50_ms"] == 21.0
    assert summary["audio_diagnostics"]["playback"]["min_callback_frames"] == 240
    assert summary["audio_diagnostics"]["playback"]["apo_state"] == "unknown"
    waveform_path = tmp_path / lines[1]["invalid_waveform"]
    with np.load(waveform_path, allow_pickle=False) as waveform:
        np.testing.assert_array_equal(waveform["reference"], records[1].reference)
        np.testing.assert_array_equal(waveform["captured"], records[1].captured)
        assert waveform["capture_window_start_sample"].item() == 100
        assert waveform["invalid_reason"].item() == "no_signal"
    assert not list(tmp_path.glob("*.db"))


class _TimeoutController(QObject):
    error_occurred = Signal(str)
    measurement_ready = Signal(object)
    session_finished = Signal(object)
    finished = Signal()

    def __init__(self):
        super().__init__()
        self.state = SessionState.RUNNING
        self.snapshot = None
        self.records = ()
        self.stop_requested = False
        self.shutdown_timeouts = []

    def start(self, config):
        return None

    def request_stop(self):
        self.stop_requested = True

    def shutdown(self, timeout_ms=3000):
        self.shutdown_timeouts.append(timeout_ms)
        return False


def test_timeout_requests_stop_preserves_report_and_fails(tmp_path, monkeypatch):
    QCoreApplication.instance() or QCoreApplication([])
    controller = _TimeoutController()
    monkeypatch.setattr(diagnostic_module, "TestController", lambda service: controller)

    result = diagnostic_module.run_diagnostic(
        AppConfig(),
        mode="electrical",
        count=1,
        output_root=tmp_path,
        timeout_seconds=0.01,
    )

    assert result.timed_out is True
    assert result.shutdown_completed is False
    assert result.success is False
    assert controller.stop_requested is True
    assert controller.shutdown_timeouts == [1000]
    summary = json.loads(result.summary_path.read_text(encoding="utf-8"))
    assert summary["timed_out"] is True
    assert summary["shutdown_completed"] is False
    assert summary["statistics"]["valid_count"] == 0
    assert result.records_path.exists()


class _StartFailureController(_TimeoutController):
    def __init__(self):
        super().__init__()
        self.state = SessionState.DEVICE_CHECK

    def start(self, config):
        raise AudioBackendError("测试开流失败")

    def shutdown(self, timeout_ms=3000):
        self.shutdown_timeouts.append(timeout_ms)
        return True


def test_start_failure_is_reported_after_bounded_cleanup(tmp_path, monkeypatch):
    QCoreApplication.instance() or QCoreApplication([])
    controller = _StartFailureController()
    monkeypatch.setattr(diagnostic_module, "TestController", lambda service: controller)

    result = diagnostic_module.run_diagnostic(
        AppConfig(),
        mode="software",
        count=1,
        output_root=tmp_path,
        timeout_seconds=1.0,
    )

    assert result.success is False
    assert result.timed_out is False
    assert result.shutdown_completed is True
    assert controller.shutdown_timeouts == [1000]
    assert "测试开流失败" in result.errors[0]
    summary = json.loads(result.summary_path.read_text(encoding="utf-8"))
    assert summary["terminal_state"] == "DEVICE_CHECK"
    assert summary["errors"]


class _MeasurementTimeoutController(_TimeoutController):
    def start(self, config):
        record = _record(config, 1, True)
        self.records = (record,)
        self.measurement_ready.emit(record)


def test_measurement_is_persisted_before_final_report(tmp_path, monkeypatch):
    QCoreApplication.instance() or QCoreApplication([])
    controller = _MeasurementTimeoutController()
    monkeypatch.setattr(diagnostic_module, "TestController", lambda service: controller)
    write_report = diagnostic_module.write_diagnostic_report
    finalized_states = []

    def spy_write_report(*args, **kwargs):
        finalized_states.append(kwargs.get("finalized", True))
        return write_report(*args, **kwargs)

    monkeypatch.setattr(diagnostic_module, "write_diagnostic_report", spy_write_report)
    result = diagnostic_module.run_diagnostic(
        AppConfig(),
        mode="software",
        count=1,
        output_root=tmp_path,
        timeout_seconds=0.01,
    )

    assert finalized_states == [False, True]
    assert result.valid_count == 1
    assert result.timed_out is True
    summary = json.loads(result.summary_path.read_text(encoding="utf-8"))
    assert summary["report_finalized"] is True
    assert summary["statistics"]["valid_count"] == 1
