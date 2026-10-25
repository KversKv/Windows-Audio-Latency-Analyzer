import os

import numpy as np
import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QEventLoop, QTimer
from PySide6.QtWidgets import QApplication

from app.config import AudioConfig, CalibrationContext, MeasurementConfig
from audio.audio_backend import (
    AudioBackend,
    AudioCallbackTime,
    AudioDevice,
    AudioStream,
    AudioStreamStatus,
)
from audio.audio_device_manager import AudioDeviceManager
from core.calibration_manager import CalibrationManager
from core.device_service import DeviceService
from core.test_controller import TestController as SessionController
from core.test_models import TestSessionConfig as SessionConfig
from core.test_state_machine import TestState as SessionState


@pytest.fixture
def qt_application():
    application = QApplication.instance() or QApplication([])
    yield application


@pytest.fixture
def controller_factory(qt_application):
    controllers = []

    def create(service):
        controller = SessionController(service)
        controllers.append(controller)
        return controller

    yield create
    for controller in controllers:
        controller.shutdown()
        controller.deleteLater()
    qt_application.processEvents()


def _wait_for_signal(signal, timeout_ms=5000):
    event_loop = QEventLoop()
    timer = QTimer()
    timer.setSingleShot(True)
    timer.timeout.connect(event_loop.quit)
    signal.connect(event_loop.quit)
    timer.start(timeout_ms)
    event_loop.exec()
    signal.disconnect(event_loop.quit)
    received = timer.isActive()
    timer.stop()
    return received


def _simulated_service(sample_rate=48000):
    from audio.audio_backend import SimulatedAudioBackend

    return DeviceService(AudioDeviceManager(SimulatedAudioBackend(sample_rate)))


def _session_config(
    *,
    backend="simulation",
    test_count=2,
    simulation_delay_ms=20.0,
    compensation_ms=0.0,
    max_latency_ms=200.0,
    calibration_profile=None,
    output_device_name="",
    input_device_name="",
):
    simulated = backend == "simulation"
    audio = AudioConfig(
        backend=backend,
        sample_rate=48000,
        output_device_id="sim-output" if simulated else 1,
        input_device_id="sim-input" if simulated else 2,
        blocksize=240,
        background_amplitude=0.001,
        capture_buffer_seconds=2.0,
    )
    measurement = MeasurementConfig(
        default_test_count=test_count,
        warmup_seconds=1.0,
        max_latency_ms=max_latency_ms,
        stimulus_lead_ms=20.0,
        capture_tail_ms=10.0,
        inter_measurement_delay_ms=0.0,
        simulation_delay_ms=simulation_delay_ms,
    )
    return SessionConfig(
        audio=audio,
        measurement=measurement,
        compensation_ms=compensation_ms,
        calibration_profile=calibration_profile,
        output_device_name=output_device_name,
        input_device_name=input_device_name,
    )


class _FakeStream(AudioStream):
    def __init__(self, backend, callback, is_output):
        self._backend = backend
        self.callback = callback
        self.is_output = is_output
        self.active = False
        self.closed = False
        self.start_count = 0
        self.stop_count = 0

    @property
    def is_active(self):
        return self.active and not self.closed

    @property
    def is_closed(self):
        return self.closed

    @property
    def callback_error(self):
        return None

    def start(self):
        self.active = True
        self.start_count += 1

    def stop(self):
        self.active = False
        self.stop_count += 1

    def close(self):
        self.active = False
        self.closed = True


class _FakeDuplexBackend(AudioBackend):
    display_name = "确定性测试双工后端"

    def __init__(self, sample_rate=48000, delay_frames=480, block_frames=240):
        self.sample_rate = sample_rate
        self.delay_frames = delay_frames
        self.block_frames = block_frames
        self.output_stream = None
        self.input_stream = None
        self.output_open_count = 0
        self.input_open_count = 0
        self.output_frames = 0
        self.input_frames = 0
        self.output_callbacks = 0
        self.input_callbacks = 0
        self.underflow_after = None
        self.overflow_after = None
        self._underflow_emitted = False
        self._overflow_emitted = False
        self._history = np.zeros(sample_rate * 2, dtype=np.float32)
        self._devices = [
            AudioDevice(1, "测试播放设备", "测试", 0, 1, float(sample_rate)),
            AudioDevice(2, "测试录音设备", "测试", 1, 0, float(sample_rate)),
        ]

    def list_devices(self):
        return list(self._devices)

    def open_output_stream(self, **kwargs):
        self.output_open_count += 1
        self.output_stream = _FakeStream(self, kwargs["callback"], True)
        return self.output_stream

    def open_input_stream(self, **kwargs):
        self.input_open_count += 1
        self.input_stream = _FakeStream(self, kwargs["callback"], False)
        return self.input_stream

    def pump(self):
        if (
            self.output_stream is None
            or self.input_stream is None
            or not self.output_stream.is_active
            or not self.input_stream.is_active
        ):
            return
        frames = self.block_frames
        output = np.zeros((frames, 1), dtype=np.float32)
        output_status = AudioStreamStatus(
            output_underflow=(
                self.underflow_after is not None
                and self.output_callbacks >= self.underflow_after
                and not self._underflow_emitted
            )
        )
        self._underflow_emitted = self._underflow_emitted or output_status.output_underflow
        output_time = AudioCallbackTime(
            current_time=100.0 + self.output_frames / self.sample_rate - 0.001,
            output_buffer_dac_time=100.0 + self.output_frames / self.sample_rate,
        )
        self.output_stream.callback(output, frames, output_time, output_status)
        indices = self.output_frames + np.arange(frames)
        self._history[indices % self._history.size] = output[:, 0]
        self.output_frames += frames
        self.output_callbacks += 1

        captured = np.zeros((frames, 1), dtype=np.float32)
        source_indices = self.input_frames + np.arange(frames) - self.delay_frames
        valid = source_indices >= 0
        captured[valid, 0] = self._history[source_indices[valid] % self._history.size]
        input_status = AudioStreamStatus(
            input_overflow=(
                self.overflow_after is not None
                and self.input_callbacks >= self.overflow_after
                and not self._overflow_emitted
            )
        )
        self._overflow_emitted = self._overflow_emitted or input_status.input_overflow
        input_time = AudioCallbackTime(
            current_time=100.0 + self.input_frames / self.sample_rate,
            input_buffer_adc_time=100.0 + self.input_frames / self.sample_rate,
        )
        self.input_stream.callback(captured, frames, input_time, input_status)
        self.input_frames += frames
        self.input_callbacks += 1


def test_simulated_controller_completes_and_preserves_compensation(controller_factory):
    controller = controller_factory(_simulated_service())
    states = []
    controller.state_changed.connect(states.append)
    context = CalibrationContext(
        output_device_id="sim-output",
        output_device="模拟耳机输出",
        input_device_id="sim-input",
        input_device="模拟有线麦克风",
        sample_rate=48000,
        audio_mode="simulation",
    )
    profile = CalibrationManager.create_profile(
        name="控制器校准追溯测试",
        context=context,
        compensation_ms=2.0,
        calibration_method="确定性测试输入",
    )
    config = _session_config(
        compensation_ms=2.0,
        calibration_profile=profile,
        output_device_name=context.output_device,
        input_device_name=context.input_device,
    )

    controller.start(config)

    assert _wait_for_signal(controller.finished) is True
    assert controller.state == SessionState.COMPLETED
    assert SessionState.OPENING_AUDIO not in states
    assert SessionState.WARMUP not in states
    snapshot = controller.snapshot
    assert snapshot is not None and snapshot.is_simulation is True
    assert snapshot.completed_count == snapshot.valid_count == 2
    assert snapshot.config.calibration_profile == profile
    assert snapshot.config.calibration_context == context
    assert snapshot.meets_minimum_valid is True
    statistics = controller.statistics
    assert statistics.valid_count == 2
    assert statistics.invalid_count == 0
    assert statistics.raw.count == statistics.compensated.count == 2
    assert statistics.raw.mean_latency_ms == pytest.approx(20.0, abs=0.1)
    assert statistics.compensated.mean_latency_ms == pytest.approx(18.0, abs=0.1)
    assert statistics.raw.std_latency_ms >= 0.0
    assert statistics.compensated.std_latency_ms == pytest.approx(
        statistics.raw.std_latency_ms,
        abs=1e-12,
    )
    assert snapshot.statistics == statistics
    for record in snapshot.records:
        assert record.measurement.is_simulation is True
        assert record.measurement.raw_latency_ms == pytest.approx(20.0, abs=0.1)
        assert record.measurement.compensated_latency_ms == pytest.approx(
            record.measurement.raw_latency_ms - 2.0,
            abs=1e-9,
        )

    save_requests = []
    controller.session_save_requested.connect(save_requests.append)
    controller.request_save()
    assert save_requests == [snapshot]
    assert controller.state == SessionState.COMPLETED
    controller.mark_saved()
    assert controller.state == SessionState.SAVED
    controller.clear()
    assert controller.state == SessionState.IDLE
    assert controller.records == ()
    assert controller.snapshot is None


def test_invalid_simulated_measurement_is_retained(controller_factory):
    controller = controller_factory(_simulated_service())
    config = _session_config(test_count=1, simulation_delay_ms=250.0)

    controller.start(config)

    assert _wait_for_signal(controller.finished) is True
    snapshot = controller.snapshot
    assert snapshot is not None
    assert snapshot.completed_count == 1
    assert snapshot.valid_count == 0
    assert snapshot.invalid_count == 1
    assert snapshot.meets_minimum_valid is False
    assert snapshot.statistics.raw.count == 0
    assert snapshot.statistics.compensated.count == 0
    assert snapshot.statistics.invalid_reason_counts == (("latency_out_of_range", 1),)
    measurement = snapshot.records[0].measurement
    assert measurement.raw_latency_ms == pytest.approx(250.0, abs=0.1)
    assert "latency_out_of_range" in measurement.invalid_reason
    assert snapshot.records[0].captured.size > 0


def test_real_stream_controller_measures_known_delay_without_reopening(controller_factory):
    backend = _FakeDuplexBackend()
    controller = controller_factory(DeviceService(AudioDeviceManager(backend)))
    timer = QTimer()
    timer.setInterval(5)
    timer.timeout.connect(backend.pump)
    timer.start()
    try:
        controller.start(_session_config(backend="sounddevice", test_count=2, max_latency_ms=50.0))

        assert _wait_for_signal(controller.finished, 10000) is True
    finally:
        timer.stop()

    assert controller.state == SessionState.COMPLETED
    assert backend.output_open_count == backend.input_open_count == 1
    assert backend.output_stream.start_count == backend.input_stream.start_count == 1
    assert backend.output_stream.is_closed and backend.input_stream.is_closed
    snapshot = controller.snapshot
    assert snapshot is not None and snapshot.is_simulation is False
    assert snapshot.completed_count == snapshot.valid_count == 2
    for record in snapshot.records:
        measurement = record.measurement
        assert measurement.raw_latency_ms == pytest.approx(10.0, abs=0.1)
        assert measurement.playback_timestamp_seconds is not None
        assert measurement.capture_timestamp_seconds is not None
        assert measurement.timing_basis == "dac_adc_stream_timestamps"
        assert record.capture_window_start_sample is not None
        assert record.captured.size > record.reference.size


def test_stop_closes_streams_and_preserves_completed_records(controller_factory):
    backend = _FakeDuplexBackend()
    controller = controller_factory(DeviceService(AudioDeviceManager(backend)))
    timer = QTimer()
    timer.setInterval(5)
    timer.timeout.connect(backend.pump)
    timer.start()
    try:
        controller.start(_session_config(backend="sounddevice", test_count=100, max_latency_ms=50.0))
        received = _wait_for_signal(controller.measurement_ready, 10000)
        if not received:
            ring = backend.input_stream.callback.__self__._ring_buffer
            pytest.fail(
                "未收到测量结果；"
                f"状态={controller.state.name}，覆盖={ring.overwritten_frames}，"
                f"跳过={ring.skipped_frames}"
            )
        controller.request_stop()
        assert _wait_for_signal(controller.finished, 10000) is True
    finally:
        timer.stop()

    assert controller.state == SessionState.USER_STOPPED
    assert 1 <= len(controller.records) < 100
    snapshot = controller.snapshot
    assert snapshot is not None
    assert snapshot.records == controller.records
    assert backend.output_open_count == backend.input_open_count == 1
    assert backend.output_stream.is_closed and backend.input_stream.is_closed


@pytest.mark.parametrize(
    ("fault", "expected_state", "message"),
    [
        ("underflow", SessionState.PLAYBACK_UNDERFLOW, "输出欠载"),
        ("overflow", SessionState.CAPTURE_OVERFLOW, "输入溢出"),
    ],
)
def test_audio_faults_reach_error_state_and_close_resources(
    controller_factory,
    fault,
    expected_state,
    message,
):
    backend = _FakeDuplexBackend()
    if fault == "underflow":
        backend.underflow_after = 5
    else:
        backend.overflow_after = 5
    controller = controller_factory(DeviceService(AudioDeviceManager(backend)))
    errors = []
    controller.error_occurred.connect(errors.append)
    timer = QTimer()
    timer.setInterval(5)
    timer.timeout.connect(backend.pump)
    timer.start()
    try:
        controller.start(_session_config(backend="sounddevice", test_count=1))
        assert _wait_for_signal(controller.finished, 10000) is True
    finally:
        timer.stop()

    assert controller.state == expected_state
    assert any(message in error for error in errors)
    assert backend.output_stream.is_closed and backend.input_stream.is_closed


def test_disconnected_device_fails_without_opening_stream(controller_factory):
    service = _simulated_service()
    controller = controller_factory(service)
    config = _session_config(test_count=1)
    config = SessionConfig(
        audio=AudioConfig(
            backend="simulation",
            output_device_id="removed-output",
            input_device_id="sim-input",
        ),
        measurement=config.measurement,
    )
    errors = []
    controller.error_occurred.connect(errors.append)

    controller.start(config)

    assert _wait_for_signal(controller.finished) is True
    assert controller.state == SessionState.DEVICE_ERROR
    assert controller.records == ()
    assert any("播放设备不可用" in error for error in errors)
