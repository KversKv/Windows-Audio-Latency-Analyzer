import os

import numpy as np
import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QEventLoop, QTimer
from PySide6.QtWidgets import QApplication

from app.config import AudioConfig, CalibrationContext, MeasurementConfig
from audio.audio_backend import (
    AudioBackend,
    AudioBackendError,
    AudioCallbackTime,
    AudioDevice,
    AudioStream,
    AudioStreamStatus,
    SoftwareLoopbackCapability,
)
from audio.audio_device_manager import AudioDeviceManager
from core import test_controller as controller_module
from core.calibration_manager import CalibrationManager
from core.device_service import DeviceService
from core.test_controller import TestController as SessionController
from core.test_models import TestSessionConfig as SessionConfig
from core.test_state_machine import TestState as SessionState
from ui.diagnostics_panel import DiagnosticMode, DiagnosticsPanel


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


@pytest.fixture
def diagnostics_panel_factory(qt_application):
    panels = []

    def create(service):
        panel = DiagnosticsPanel(service)
        panels.append(panel)
        return panel

    yield create
    for panel in panels:
        panel.shutdown()
        panel.close()
        panel.deleteLater()
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
    stimulus_duration_ms=10.0,
    calibration_profile=None,
    output_device_name="",
    input_device_name="",
    diagnostic_mode=None,
):
    simulated = backend == "simulation"
    audio = AudioConfig(
        backend=backend,
        sample_rate=48000,
        output_device_id="sim-output" if simulated else 1,
        input_device_id=(
            None if diagnostic_mode == "software" else "sim-input" if simulated else 2
        ),
        blocksize=240,
        background_amplitude=0.001,
        capture_buffer_seconds=2.0,
    )
    measurement = MeasurementConfig(
        default_test_count=test_count,
        warmup_seconds=1.0,
        max_latency_ms=max_latency_ms,
        stimulus_duration_ms=stimulus_duration_ms,
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
        diagnostic_mode=diagnostic_mode,
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

    def __init__(
        self,
        sample_rate=48000,
        delay_frames=480,
        block_frames=240,
        block_pattern=None,
        clock_domain=None,
    ):
        self.sample_rate = sample_rate
        self.delay_frames = delay_frames
        self.block_frames = block_frames
        self.block_pattern = block_pattern
        self.clock_domain = clock_domain
        self.capture_clock_domain = clock_domain
        self.closed = False
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

    def close(self):
        self.closed = True

    def open_output_stream(self, **kwargs):
        self.output_open_count += 1
        self.output_stream = _FakeStream(self, kwargs["callback"], True)
        return self.output_stream

    def open_input_stream(self, **kwargs):
        self.input_open_count += 1
        self.input_device_id = kwargs["device_id"]
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
        frames = (
            self.block_frames
            if self.block_pattern is None
            else self.block_pattern[self.output_callbacks % len(self.block_pattern)]
        )
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
            clock_domain=self.clock_domain,
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
            clock_domain=self.capture_clock_domain,
        )
        self.input_stream.callback(captured, frames, input_time, input_status)
        self.input_frames += frames
        self.input_callbacks += 1


class _FailingInputBackend(_FakeDuplexBackend):
    def open_input_stream(self, **kwargs):
        raise AudioBackendError("模拟录音设备打开失败")


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


def test_worker_is_released_after_finished_signal(controller_factory, qt_application):
    controller = controller_factory(_simulated_service())
    workers_at_finished = []
    controller.finished.connect(lambda: workers_at_finished.append(controller._worker))
    controller.start(_session_config(test_count=1))

    assert _wait_for_signal(controller.finished) is True
    assert workers_at_finished == [controller._worker]
    assert controller._worker is not None
    qt_application.processEvents()
    assert controller._worker is None


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
    assert snapshot.audio_diagnostics is not None
    assert snapshot.audio_diagnostics.playback.requested_blocksize == 240
    assert snapshot.audio_diagnostics.playback.min_callback_frames == 240
    assert snapshot.audio_diagnostics.capture.max_callback_frames == 240
    for record in snapshot.records:
        measurement = record.measurement
        assert measurement.raw_latency_ms == pytest.approx(10.0, abs=0.1)
        assert measurement.playback_timestamp_seconds is not None
        assert measurement.capture_timestamp_seconds is not None
        assert measurement.timing_basis == "dac_adc_stream_timestamps"
        assert record.capture_window_start_sample is not None
        assert record.captured.size > record.reference.size


def test_real_stream_controller_handles_uneven_blocks_and_non_integer_lag(controller_factory):
    backend = _FakeDuplexBackend(
        delay_frames=373,
        block_pattern=(173, 311, 97, 509),
    )
    controller = controller_factory(DeviceService(AudioDeviceManager(backend)))
    timer = QTimer()
    timer.setInterval(5)
    timer.timeout.connect(backend.pump)
    timer.start()
    try:
        controller.start(
            _session_config(
                backend="sounddevice",
                test_count=1,
                max_latency_ms=50.0,
                stimulus_duration_ms=10.137,
            )
        )
        assert _wait_for_signal(controller.finished, 10000) is True
    finally:
        timer.stop()

    snapshot = controller.snapshot
    assert controller.state == SessionState.COMPLETED
    assert snapshot is not None and snapshot.valid_count == 1
    record = snapshot.records[0]
    assert record.reference.size % 240 != 0
    assert record.measurement.raw_latency_ms == pytest.approx(373 * 1000 / 48000, abs=0.1)
    assert snapshot.audio_diagnostics.playback.min_callback_frames == 97
    assert snapshot.audio_diagnostics.playback.max_callback_frames == 509


def _software_controller(controller_factory, monkeypatch):
    service_backend = _FakeDuplexBackend()
    service_backend._devices = [
        AudioDevice(1, "测试播放设备", "Windows WASAPI", 0, 1, 48000.0)
    ]
    software_backend = _FakeDuplexBackend(clock_domain="shared-wasapi-clock")
    monkeypatch.setattr(
        controller_module,
        "PyAudioWPatchBackend",
        lambda output_device: software_backend,
    )
    controller = controller_factory(DeviceService(AudioDeviceManager(service_backend)))
    return controller, service_backend, software_backend


def test_software_loopback_measures_known_delay_with_shared_clock(
    controller_factory,
    monkeypatch,
):
    controller, service_backend, software_backend = _software_controller(
        controller_factory,
        monkeypatch,
    )
    timer = QTimer()
    timer.setInterval(5)
    timer.timeout.connect(software_backend.pump)
    timer.start()
    try:
        controller.start(
            _session_config(
                backend="sounddevice",
                test_count=1,
                max_latency_ms=50.0,
                output_device_name="测试播放设备",
                diagnostic_mode="software",
            )
        )
        assert _wait_for_signal(controller.finished, 10000) is True
    finally:
        timer.stop()

    assert controller.state == SessionState.COMPLETED
    assert service_backend.output_open_count == service_backend.input_open_count == 0
    assert software_backend.output_open_count == software_backend.input_open_count == 1
    assert software_backend.output_stream.start_count == software_backend.input_stream.start_count == 1
    assert software_backend.input_device_id is None
    assert software_backend.output_stream.is_closed and software_backend.input_stream.is_closed
    assert software_backend.closed is True
    record = controller.snapshot.records[0]
    assert record.measurement.is_valid is True
    assert record.measurement.raw_latency_ms == pytest.approx(10.0, abs=0.1)
    assert record.measurement.timing_basis == "wasapi_digital_loopback_stream_timestamps"
    assert "capture_clock_offset_uncalibrated" in record.measurement.timing_limitations


@pytest.mark.parametrize(
    ("capture_domain", "expected_reason"),
    [
        ("different-clock", "software_clock_domain_mismatch"),
        (None, "software_clock_domain_missing"),
    ],
)
def test_software_loopback_rejects_unverified_clock_domain(
    controller_factory,
    monkeypatch,
    capture_domain,
    expected_reason,
):
    controller, _, software_backend = _software_controller(controller_factory, monkeypatch)
    software_backend.capture_clock_domain = capture_domain
    timer = QTimer()
    timer.setInterval(5)
    timer.timeout.connect(software_backend.pump)
    timer.start()
    try:
        controller.start(
            _session_config(
                backend="sounddevice",
                test_count=1,
                max_latency_ms=50.0,
                diagnostic_mode="software",
            )
        )
        assert _wait_for_signal(controller.finished, 10000) is True
    finally:
        timer.stop()

    assert controller.state == SessionState.COMPLETED
    record = controller.snapshot.records[0]
    assert record.captured.size > 0
    assert record.measurement.is_valid is False
    assert record.measurement.raw_latency_ms is None
    assert record.measurement.compensated_latency_ms is None
    assert expected_reason in record.measurement.invalid_reason
    assert record.measurement.timing_basis == "wasapi_digital_loopback_timestamps_unavailable"
    assert software_backend.closed is True


def test_software_loopback_never_uses_offline_simulation(controller_factory):
    controller = controller_factory(_simulated_service())
    controller.start(_session_config(test_count=1, diagnostic_mode="software"))

    assert _wait_for_signal(controller.finished) is True
    assert controller.state == SessionState.DEVICE_ERROR
    assert controller.records == ()


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


def test_input_open_failure_closes_already_opened_output(controller_factory):
    backend = _FailingInputBackend()
    controller = controller_factory(DeviceService(AudioDeviceManager(backend)))
    errors = []
    controller.error_occurred.connect(errors.append)

    controller.start(_session_config(backend="sounddevice", test_count=1))

    assert _wait_for_signal(controller.finished) is True
    assert controller.state == SessionState.AUDIO_STREAM_ERROR
    assert backend.output_stream is not None and backend.output_stream.is_closed
    assert backend.input_stream is None
    assert any("模拟录音设备打开失败" in error for error in errors)


def test_unexpected_output_stop_closes_resources(controller_factory):
    backend = _FakeDuplexBackend()
    controller = controller_factory(DeviceService(AudioDeviceManager(backend)))
    errors = []
    controller.error_occurred.connect(errors.append)
    timer = QTimer()
    timer.setInterval(5)
    timer.timeout.connect(backend.pump)
    timer.start()
    try:
        controller.start(_session_config(backend="sounddevice", test_count=100, max_latency_ms=50.0))
        assert _wait_for_signal(controller.measurement_ready, 10000) is True
        backend.output_stream.stop()
        assert _wait_for_signal(controller.finished, 10000) is True
    finally:
        timer.stop()

    assert controller.state == SessionState.AUDIO_STREAM_ERROR
    assert 1 <= len(controller.records) < 100
    assert backend.output_stream.is_closed and backend.input_stream.is_closed
    assert any("播放流已意外停止" in error for error in errors)


def test_software_diagnostics_requires_output_but_no_input_device(
    diagnostics_panel_factory,
    monkeypatch,
):
    backend = _FakeDuplexBackend()
    backend._devices = [AudioDevice(1, "仅输出耳机", "Windows WASAPI", 0, 1, 48000.0)]
    monkeypatch.setattr(
        backend,
        "software_loopback_capability",
        lambda: SoftwareLoopbackCapability(True, "测试环回可用"),
    )
    service = DeviceService(AudioDeviceManager(backend))
    panel = diagnostics_panel_factory(service)
    panel.update_inventory(service.scan())
    panel.mode_combo.setCurrentIndex(panel.mode_combo.findData(DiagnosticMode.SOFTWARE))
    started = []
    monkeypatch.setattr(panel.controller, "start", started.append)

    assert panel.start_button.isEnabled() is True
    assert panel.start(
        _session_config(
            backend="sounddevice",
            test_count=1,
            output_device_name="仅输出耳机",
            input_device_name="不应保留的麦克风",
            diagnostic_mode="software",
        )
    ) is True

    config = panel.config_for(DiagnosticMode.SOFTWARE)
    assert config is not None
    assert config.audio.input_device_id is None
    assert config.input_device_name == ""
    assert config.diagnostic_mode == "software"
    assert "WASAPI 数字环回" in panel.config_label.text()
    assert "麦克风" not in panel.config_label.text()
    panel.mode_combo.setCurrentIndex(panel.mode_combo.findData(DiagnosticMode.ELECTRICAL))
    assert "未检测到可用采集设备" in panel.support_label.text()


def test_diagnostics_panel_measures_real_modes_and_isolates_results(
    diagnostics_panel_factory,
):
    backend = _FakeDuplexBackend()
    service = DeviceService(AudioDeviceManager(backend))
    panel = diagnostics_panel_factory(service)
    panel.update_inventory(service.scan())
    context = CalibrationContext(
        output_device_id=1,
        output_device="测试播放设备",
        input_device_id=2,
        input_device="测试录音设备",
        sample_rate=48000,
        audio_mode="wasapi_shared",
    )
    profile = CalibrationManager.create_profile(
        name="诊断校准隔离测试",
        context=context,
        compensation_ms=4.0,
        calibration_method="确定性测试输入",
    )
    config = _session_config(
        backend="sounddevice",
        test_count=5,
        compensation_ms=4.0,
        calibration_profile=profile,
        max_latency_ms=50.0,
        output_device_name=context.output_device,
        input_device_name=context.input_device,
    )
    save_requests = []
    panel.controller.session_save_requested.connect(save_requests.append)
    timer = QTimer()
    timer.setInterval(5)
    timer.timeout.connect(backend.pump)
    timer.start()
    try:
        panel.mode_combo.setCurrentIndex(
            panel.mode_combo.findData(DiagnosticMode.ELECTRICAL)
        )
        assert panel.start(config) is True
        assert _wait_for_signal(panel.finished, 10000) is True

        first_output_stream = backend.output_stream
        first_input_stream = backend.input_stream
        electrical = panel.record_for(DiagnosticMode.ELECTRICAL)
        assert electrical is not None
        assert electrical.measurement.raw_latency_ms == pytest.approx(10.0, abs=0.1)
        assert electrical.measurement.compensated_latency_ms == pytest.approx(
            electrical.measurement.raw_latency_ms,
            abs=1e-9,
        )
        assert electrical.measurement.playback_timestamp_seconds is not None
        assert electrical.measurement.capture_timestamp_seconds is not None
        assert electrical.measurement.timing_basis == "dac_adc_stream_timestamps"
        assert electrical.measurement.correlation_peak > 0.0
        assert electrical.measurement.signal_amplitude > 0.0
        assert electrical.measurement.is_valid is True
        assert panel.latency_value_label.text() == "10.00 ms"
        assert "电气往返延迟" in panel.result_kind_label.text()
        assert "信号质量：有效" in panel.quality_label.text()
        assert "DAC/ADC 流时间戳" in panel.timing_label.text()
        assert "未补偿" in panel.timing_label.text()
        electrical_config = panel.config_for(DiagnosticMode.ELECTRICAL)
        assert electrical_config is not None
        assert electrical_config.measurement.default_test_count == 1
        assert electrical_config.measurement.minimum_valid_count == 1
        assert electrical_config.compensation_ms == 0.0
        assert electrical_config.calibration_profile is None
        assert electrical_config.diagnostic_mode == "electrical"
        assert backend.output_open_count == backend.input_open_count == 1
        assert first_output_stream.start_count == first_input_stream.start_count == 1
        assert first_output_stream.is_closed and first_input_stream.is_closed

        panel.mode_combo.setCurrentIndex(
            panel.mode_combo.findData(DiagnosticMode.ACOUSTIC)
        )
        assert panel.record_for(DiagnosticMode.ACOUSTIC) is None
        assert panel.latency_value_label.text() == "—"
        assert panel.start(config) is True
        assert _wait_for_signal(panel.finished, 10000) is True

        acoustic = panel.record_for(DiagnosticMode.ACOUSTIC)
        assert acoustic is not None and acoustic is not electrical
        assert acoustic.measurement.raw_latency_ms == pytest.approx(10.0, abs=0.1)
        assert acoustic.measurement.timing_basis == "dac_adc_stream_timestamps"
        assert panel.record_for(DiagnosticMode.ELECTRICAL) is electrical
        assert "声学链路延迟" in panel.result_kind_label.text()
        acoustic_config = panel.config_for(DiagnosticMode.ACOUSTIC)
        assert acoustic_config is not None
        assert acoustic_config.notes != electrical_config.notes
        assert acoustic_config.measurement.default_test_count == 1
        assert acoustic_config.compensation_ms == 0.0
        assert acoustic_config.calibration_profile is None
        assert acoustic_config.diagnostic_mode == "acoustic"
        assert backend.output_open_count == backend.input_open_count == 2
        assert backend.output_stream.start_count == backend.input_stream.start_count == 1
        assert backend.output_stream.is_closed and backend.input_stream.is_closed
        assert first_output_stream.start_count == first_input_stream.start_count == 1

        panel.mode_combo.setCurrentIndex(
            panel.mode_combo.findData(DiagnosticMode.ELECTRICAL)
        )
        assert panel.latency_value_label.text() == "10.00 ms"
        assert "电气往返延迟" in panel.result_kind_label.text()
        panel.mode_combo.setCurrentIndex(
            panel.mode_combo.findData(DiagnosticMode.SOFTWARE)
        )
        assert panel.record_for(DiagnosticMode.SOFTWARE) is None
        assert panel.latency_value_label.text() == "—"
        assert panel.start_button.isEnabled() is False
        assert panel.start(config) is False
        assert backend.output_open_count == backend.input_open_count == 2
        assert panel.record_for(DiagnosticMode.SOFTWARE) is None
        assert panel.latency_value_label.text() == "—"
        assert "当前模式不可执行" in panel.error_label.text()
    finally:
        timer.stop()

    assert save_requests == []


def test_diagnostics_panel_retains_invalid_real_measurement(
    diagnostics_panel_factory,
):
    backend = _FakeDuplexBackend()
    service = DeviceService(AudioDeviceManager(backend))
    panel = diagnostics_panel_factory(service)
    panel.update_inventory(service.scan())
    panel.mode_combo.setCurrentIndex(
        panel.mode_combo.findData(DiagnosticMode.ELECTRICAL)
    )
    timer = QTimer()
    timer.setInterval(5)
    timer.timeout.connect(backend.pump)
    timer.start()
    try:
        assert panel.start(
            _session_config(backend="sounddevice", test_count=1, max_latency_ms=5.0)
        ) is True
        assert _wait_for_signal(panel.finished, 10000) is True
    finally:
        timer.stop()

    record = panel.record_for(DiagnosticMode.ELECTRICAL)
    assert record is not None
    assert record.captured.size > 0
    assert record.measurement.raw_latency_ms == pytest.approx(10.0, abs=0.1)
    assert record.measurement.is_valid is False
    assert "latency_out_of_range" in record.measurement.invalid_reason
    assert "无效" in panel.quality_label.text()
    assert "原始记录已保留" in panel.quality_label.text()
    assert "延迟超出有效范围" in panel.invalid_reason_label.text()
    assert "原始超范围结果已保留" in panel.advice_label.text()
    assert backend.output_stream.is_closed and backend.input_stream.is_closed


def test_diagnostics_panel_stop_releases_streams_and_allows_next_mode(
    diagnostics_panel_factory,
    qt_application,
):
    backend = _FakeDuplexBackend()
    service = DeviceService(AudioDeviceManager(backend))
    panel = diagnostics_panel_factory(service)
    panel.update_inventory(service.scan())
    panel.mode_combo.setCurrentIndex(
        panel.mode_combo.findData(DiagnosticMode.ELECTRICAL)
    )
    config = _session_config(backend="sounddevice", test_count=1, max_latency_ms=50.0)

    def stop_on_warmup(state):
        if state is SessionState.WARMUP:
            panel.stop()

    panel.controller.state_changed.connect(stop_on_warmup)
    finished_sessions = []
    panel.finished.connect(lambda: finished_sessions.append(True))
    timer = QTimer()
    timer.setInterval(5)
    timer.timeout.connect(backend.pump)
    timer.start()
    try:
        assert panel.start(config) is True
        assert _wait_for_signal(panel.controller.stop_requested, 5000) is True
        assert panel.controller.shutdown(timeout_ms=10000) is True
        qt_application.processEvents()
        panel.controller.state_changed.disconnect(stop_on_warmup)

        assert finished_sessions == [True]
        assert panel.controller.state is SessionState.USER_STOPPED
        assert panel.record_for(DiagnosticMode.ELECTRICAL) is None
        assert backend.output_stream.is_closed and backend.input_stream.is_closed
        assert "诊断已停止" in panel.quality_label.text()

        panel.mode_combo.setCurrentIndex(
            panel.mode_combo.findData(DiagnosticMode.ACOUSTIC)
        )
        assert panel.start(config) is True
        assert _wait_for_signal(panel.finished, 10000) is True
    finally:
        timer.stop()

    assert panel.controller.state is SessionState.COMPLETED
    assert panel.record_for(DiagnosticMode.ACOUSTIC) is not None
    assert backend.output_open_count == backend.input_open_count == 2
    assert backend.output_stream.is_closed and backend.input_stream.is_closed
