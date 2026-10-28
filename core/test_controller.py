import logging
import math
import threading
import time
from dataclasses import replace
from datetime import datetime, timezone

import numpy as np
from PySide6.QtCore import QObject, QThread, Qt, QTimer, Signal, Slot

from audio.audio_backend import AudioBackendError, AudioDevice
from audio.audio_synchronizer import AudioSynchronizer, SampleTimeAnchor
from audio.background_generator import BackgroundGenerator
from audio.capture_engine import CaptureEngine
from audio.playback_engine import PlaybackEngine, ScheduledStimulus
from audio.pyaudiowpatch_backend import PyAudioWPatchBackend
from audio.stimulus_generator import ChirpGenerator
from audio.stream_common import AudioStreamError
from core.device_service import DeviceService
from core.latency_analyzer import LatencyAnalyzer, LatencyMeasurement
from core.offline_simulation import OfflineLatencySimulator
from core.statistics_engine import SessionStatistics, StatisticsEngine
from core.test_models import (
    MeasurementRecord,
    SessionAudioDiagnostics,
    TestSessionConfig,
    TestSessionSnapshot,
)
from core.test_state_machine import TestState, TestStateMachine

logger = logging.getLogger(__name__)

_POLL_INTERVAL_SECONDS = 0.02
_CAPTURE_TIMEOUT_MARGIN_SECONDS = 1.0


class ControllerBusyError(RuntimeError):
    pass


class SessionNotAvailableError(RuntimeError):
    pass


class _DeviceCheckError(RuntimeError):
    pass


class TestSessionWorker(QObject):
    state_changed = Signal(object)
    progress_changed = Signal(int, int)
    warmup_progress = Signal(float, float)
    measurement_ready = Signal(object)
    error_occurred = Signal(str)
    session_finished = Signal(object)
    finished = Signal()

    def __init__(self, service: DeviceService, config: TestSessionConfig) -> None:
        super().__init__()
        self._service = service
        self._config = config
        self._stop_event = threading.Event()
        self._records: list[MeasurementRecord] = []
        self._playback: PlaybackEngine | None = None
        self._capture: CaptureEngine | None = None
        self._diagnostic_backend: PyAudioWPatchBackend | None = None
        self._output_device: AudioDevice | None = None
        self._is_simulation = config.is_simulation

    def request_stop(self) -> None:
        self._stop_event.set()

    @Slot()
    def run(self) -> None:
        terminal_state = TestState.COMPLETED
        error_messages: list[str] = []
        try:
            terminal_state = self._execute()
        except _DeviceCheckError as exc:
            terminal_state = TestState.DEVICE_ERROR
            error_messages.append(str(exc))
            logger.error("测试设备检查失败", exc_info=True)
        except (AudioBackendError, AudioStreamError) as exc:
            terminal_state = self._audio_error_state()
            error_messages.append(str(exc))
            logger.error("测试音频链路失败", exc_info=True)
        except Exception as exc:
            terminal_state = TestState.AUDIO_STREAM_ERROR
            error_messages.append(f"测试会话失败：{exc}")
            logger.error("测试会话 Worker 发生未处理异常", exc_info=True)
        finally:
            audio_diagnostics = self._collect_audio_diagnostics()
            close_error = self._close_engines()
            if close_error is not None:
                error_messages.append(f"关闭音频资源失败：{close_error}")
                if terminal_state in {TestState.COMPLETED, TestState.USER_STOPPED}:
                    terminal_state = TestState.AUDIO_STREAM_ERROR

        for message in error_messages:
            self.error_occurred.emit(message)
        snapshot = TestSessionSnapshot(
            config=self._config,
            records=tuple(self._records),
            terminal_state=terminal_state,
            started_at=self._config.started_at,
            finished_at=datetime.now(timezone.utc),
            is_simulation=self._is_simulation,
            audio_diagnostics=audio_diagnostics,
        )
        self.state_changed.emit(terminal_state)
        self.session_finished.emit(snapshot)
        self.finished.emit()

    def _execute(self) -> TestState:
        try:
            inventory = self._service.scan()
        except Exception as exc:
            raise _DeviceCheckError(f"无法重新检查音频设备：{exc}") from exc
        self._is_simulation = inventory.is_simulation
        self._validate_devices(inventory.output_devices, inventory.input_devices)
        if self._stop_event.is_set():
            return TestState.USER_STOPPED

        try:
            generator = self._make_generator()
            analyzer = self._make_analyzer()
        except ValueError as exc:
            raise _DeviceCheckError(f"测量配置无效：{exc}") from exc

        if inventory.is_simulation:
            if self._config.diagnostic_mode == "software":
                raise _DeviceCheckError("离线模拟后端不提供真实 WASAPI 数字环回")
            self.state_changed.emit(TestState.READY)
            return self._run_simulation(generator, analyzer)

        self.state_changed.emit(TestState.OPENING_AUDIO)
        self._open_engines(generator)
        self.state_changed.emit(TestState.WARMUP)
        if not self._warmup():
            return TestState.USER_STOPPED
        self.state_changed.emit(TestState.READY)
        return self._run_real_measurements(generator, analyzer)

    def _validate_devices(
        self,
        outputs: tuple[AudioDevice, ...],
        inputs: tuple[AudioDevice, ...],
    ) -> None:
        output = self._find_device(outputs, self._config.audio.output_device_id)
        if output is None:
            raise _DeviceCheckError("所选播放设备不可用，请刷新设备列表")
        if output.max_output_channels < self._config.audio.output_channels:
            raise _DeviceCheckError("所选播放设备不支持当前输出声道数")
        self._output_device = output
        if self._config.diagnostic_mode == "software":
            if output.is_simulation or "wasapi" not in output.host_api.lower():
                raise _DeviceCheckError("Software Loop 仅支持真实 Windows WASAPI 播放设备")
            return
        input_device = self._find_device(inputs, self._config.audio.input_device_id)
        if input_device is None:
            raise _DeviceCheckError("所选录音设备不可用，请刷新设备列表")
        if input_device.max_input_channels < self._config.audio.input_channels:
            raise _DeviceCheckError("所选录音设备不支持当前输入声道数")

    @staticmethod
    def _find_device(
        devices: tuple[AudioDevice, ...],
        identifier: int | str | None,
    ) -> AudioDevice | None:
        if identifier is None:
            return devices[0] if devices else None
        return next((device for device in devices if device.identifier == identifier), None)

    def _make_generator(self) -> ChirpGenerator:
        audio = self._config.audio
        measurement = self._config.measurement
        return ChirpGenerator(
            sample_rate=audio.sample_rate,
            start_frequency_hz=measurement.stimulus_start_frequency_hz,
            end_frequency_hz=measurement.stimulus_end_frequency_hz,
            duration_ms=measurement.stimulus_duration_ms,
            amplitude=measurement.stimulus_amplitude,
            window=measurement.stimulus_window,
        )

    def _make_analyzer(self) -> LatencyAnalyzer:
        measurement = self._config.measurement
        return LatencyAnalyzer(
            min_snr_db=measurement.min_snr_db,
            min_correlation=measurement.min_correlation,
            min_latency_ms=measurement.min_latency_ms,
            max_latency_ms=measurement.max_latency_ms,
        )

    def _open_engines(self, generator: ChirpGenerator) -> None:
        audio = self._config.audio
        measurement = self._config.measurement
        background = BackgroundGenerator(
            sample_rate=audio.sample_rate,
            channels=audio.output_channels,
            amplitude=audio.background_amplitude,
            kind=audio.background_kind,
        )
        backend = self._service.backend
        capture_device_id = audio.input_device_id
        if self._config.diagnostic_mode == "software":
            if self._output_device is None:
                raise _DeviceCheckError("Software Loop 缺少已校验的播放设备")
            self._diagnostic_backend = PyAudioWPatchBackend(self._output_device)
            backend = self._diagnostic_backend
            capture_device_id = None
        self._playback = PlaybackEngine(
            backend,
            device_id=audio.output_device_id,
            sample_rate=audio.sample_rate,
            channels=audio.output_channels,
            blocksize=audio.blocksize,
            background=background,
            warmup_seconds=measurement.warmup_seconds,
        )
        self._capture = CaptureEngine(
            backend,
            device_id=capture_device_id,
            sample_rate=audio.sample_rate,
            channels=audio.input_channels,
            blocksize=audio.blocksize,
            capacity_frames=math.ceil(audio.sample_rate * audio.capture_buffer_seconds),
        )
        if generator.frame_count < 3:
            raise ValueError("参考波形样本数不足")
        self._playback.open()
        self._capture.open()
        self._capture.start()
        self._playback.start()

    def _warmup(self) -> bool:
        assert self._playback is not None and self._capture is not None
        total_seconds = self._config.measurement.warmup_seconds
        while not self._stop_event.is_set():
            self._playback.check_health()
            self._capture.check_health()
            self._capture.read_all_with_metadata()
            remaining = self._playback.warmup_remaining_seconds
            self.warmup_progress.emit(remaining, total_seconds)
            if self._playback.is_warmed_up:
                self.warmup_progress.emit(0.0, total_seconds)
                return True
            self._stop_event.wait(_POLL_INTERVAL_SECONDS)
        return False

    def _run_real_measurements(
        self,
        generator: ChirpGenerator,
        analyzer: LatencyAnalyzer,
    ) -> TestState:
        assert self._playback is not None and self._capture is not None
        audio = self._config.audio
        measurement = self._config.measurement
        output_reference = np.repeat(
            generator.samples[:, np.newaxis],
            audio.output_channels,
            axis=1,
        )
        lead_frames = round(measurement.stimulus_lead_ms * audio.sample_rate / 1000)
        tail_frames = round(measurement.capture_tail_ms * audio.sample_rate / 1000)
        latency_frames = math.ceil(measurement.max_latency_ms * audio.sample_rate / 1000)
        required_frames = lead_frames + generator.frame_count + latency_frames + tail_frames

        for sequence in range(1, measurement.default_test_count + 1):
            if self._stop_event.is_set():
                return TestState.USER_STOPPED
            self.state_changed.emit(TestState.RUNNING)
            self.progress_changed.emit(sequence - 1, measurement.default_test_count)
            self._playback.check_health()
            self._capture.check_health()
            discarded = self._capture.read_all_with_metadata()
            capture_start = discarded.end_sample
            scheduled = self._playback.schedule_stimulus(
                output_reference,
                lead_frames=lead_frames,
            )
            captured, capture_start, capture_complete = self._collect_capture_window(
                required_frames,
                capture_start,
            )
            if self._stop_event.is_set():
                return TestState.USER_STOPPED

            self.state_changed.emit(TestState.ANALYZING)
            result = self._analyze_real(
                analyzer,
                generator.samples,
                captured,
                scheduled,
                capture_start,
                capture_complete,
            )
            record = MeasurementRecord(
                sequence=sequence,
                reference=generator.samples,
                captured=captured,
                capture_window_start_sample=capture_start,
                playback_sample_index=scheduled.start_sample,
                measurement=result,
            )
            self._append_record(record)
            self._emit_quality_state(result)
            if self._stop_event.is_set():
                return TestState.USER_STOPPED
            if sequence == measurement.default_test_count:
                return TestState.COMPLETED
            self.state_changed.emit(TestState.WAIT_NEXT)
            if self._stop_event.wait(measurement.inter_measurement_delay_ms / 1000):
                return TestState.USER_STOPPED
        return TestState.COMPLETED

    def _collect_capture_window(
        self,
        required_frames: int,
        initial_start_sample: int | None,
    ) -> tuple[np.ndarray, int | None, bool]:
        assert self._playback is not None and self._capture is not None
        chunks: list[np.ndarray] = []
        frame_count = 0
        window_start = initial_start_sample
        timeline_valid = initial_start_sample is not None
        expected_duration = required_frames / self._config.audio.sample_rate
        deadline = time.monotonic() + expected_duration + _CAPTURE_TIMEOUT_MARGIN_SECONDS

        while frame_count < required_frames and not self._stop_event.is_set():
            self._playback.check_health()
            self._capture.check_health()
            block = self._capture.read_all_with_metadata()
            self._capture.check_health()
            if block.samples.shape[0]:
                if window_start is None and frame_count == 0:
                    window_start = block.start_sample
                if block.start_sample is None or block.start_sample != (
                    (window_start + frame_count) if window_start is not None else None
                ):
                    timeline_valid = False
                chunks.append(block.samples[:, 0])
                frame_count += block.samples.shape[0]
            if frame_count < required_frames and time.monotonic() >= deadline:
                break
            if frame_count < required_frames:
                self._stop_event.wait(_POLL_INTERVAL_SECONDS)

        captured = (
            np.concatenate(chunks).astype(np.float32, copy=False)
            if chunks
            else np.empty(0, dtype=np.float32)
        )
        return captured, window_start if timeline_valid else None, frame_count >= required_frames

    def _analyze_real(
        self,
        analyzer: LatencyAnalyzer,
        reference: np.ndarray,
        captured: np.ndarray,
        scheduled: ScheduledStimulus,
        capture_start: int | None,
        capture_complete: bool,
    ) -> LatencyMeasurement:
        reasons: list[str] = []
        clock_domain_error = self._software_clock_domain_error(scheduled)
        if clock_domain_error is not None:
            reasons.append(clock_domain_error)
        synchronizer = self._make_synchronizer(scheduled, capture_start)
        if not capture_complete:
            reasons.append("capture_timeout")
        if scheduled.state != "completed":
            reasons.append("stimulus_not_completed")
        if capture_start is None:
            reasons.append("capture_timeline_discontinuous")

        if synchronizer is not None and capture_start is not None:
            measurement = analyzer.analyze(
                reference,
                captured,
                sample_rate=self._config.audio.sample_rate,
                compensation_ms=self._config.compensation_ms,
                synchronizer=synchronizer,
                playback_sample_index=scheduled.start_sample,
                capture_window_start_sample=capture_start,
            )
        else:
            measurement = analyzer.analyze(
                reference,
                captured,
                sample_rate=self._config.audio.sample_rate,
                compensation_ms=self._config.compensation_ms,
            )
            if scheduled.playback_time_seconds is None:
                reasons.append("missing_playback_timestamp")
            elif capture_start is not None and clock_domain_error is None:
                reasons.append("missing_capture_timestamp")
            measurement = replace(
                measurement,
                raw_latency_ms=None,
                compensated_latency_ms=None,
                playback_timestamp_seconds=scheduled.playback_time_seconds,
                capture_timestamp_seconds=None,
                timing_basis=(
                    "wasapi_digital_loopback_timestamps_unavailable"
                    if self._config.diagnostic_mode == "software"
                    else "dac_adc_stream_timestamps_unavailable"
                ),
                timing_limitations=(
                    clock_domain_error or "playback_or_capture_time_anchor_unavailable",
                ),
            )
        return self._with_invalid_reasons(measurement, reasons)

    def _make_synchronizer(
        self,
        scheduled: ScheduledStimulus,
        capture_start: int | None,
    ) -> AudioSynchronizer | None:
        if self._capture is None or capture_start is None:
            return None
        capture_timing = self._capture.latest_timing
        if scheduled.playback_time_seconds is None or capture_timing.input_adc_time is None:
            return None
        is_software = self._config.diagnostic_mode == "software"
        if is_software and self._software_clock_domain_error(scheduled) is not None:
            return None
        return AudioSynchronizer(
            playback_anchor=SampleTimeAnchor(
                sample_index=scheduled.start_sample,
                device_timestamp_seconds=scheduled.playback_time_seconds,
                sample_rate=self._config.audio.sample_rate,
            ),
            capture_anchor=SampleTimeAnchor(
                sample_index=capture_timing.sample_index,
                device_timestamp_seconds=capture_timing.input_adc_time,
                sample_rate=self._config.audio.sample_rate,
            ),
            capture_clock_offset_seconds=None,
            timing_basis="wasapi_digital_loopback_stream_timestamps" if is_software else None,
        )

    def _software_clock_domain_error(self, scheduled: ScheduledStimulus) -> str | None:
        if self._config.diagnostic_mode != "software" or self._capture is None:
            return None
        capture_domain = self._capture.latest_timing.clock_domain
        if not scheduled.clock_domain or not capture_domain:
            return "software_clock_domain_missing"
        if scheduled.clock_domain != capture_domain:
            return "software_clock_domain_mismatch"
        return None

    def _run_simulation(
        self,
        generator: ChirpGenerator,
        analyzer: LatencyAnalyzer,
    ) -> TestState:
        measurement_config = self._config.measurement
        for sequence in range(1, measurement_config.default_test_count + 1):
            if self._stop_event.is_set():
                return TestState.USER_STOPPED
            self.state_changed.emit(TestState.RUNNING)
            self.progress_changed.emit(sequence - 1, measurement_config.default_test_count)
            if self._stop_event.wait(0):
                return TestState.USER_STOPPED
            self.state_changed.emit(TestState.ANALYZING)
            simulator = OfflineLatencySimulator(generator=generator, analyzer=analyzer, seed=sequence)
            simulated = simulator.simulate(
                measurement_config.simulation_delay_ms,
                snr_db=measurement_config.simulation_snr_db,
                compensation_ms=self._config.compensation_ms,
            )
            record = MeasurementRecord(
                sequence=sequence,
                reference=simulated.reference,
                captured=simulated.captured,
                capture_window_start_sample=0,
                playback_sample_index=0,
                measurement=simulated.measurement,
            )
            self._append_record(record)
            self._emit_quality_state(simulated.measurement)
            if self._stop_event.is_set():
                return TestState.USER_STOPPED
            if sequence == measurement_config.default_test_count:
                return TestState.COMPLETED
            self.state_changed.emit(TestState.WAIT_NEXT)
            if self._stop_event.wait(measurement_config.inter_measurement_delay_ms / 1000):
                return TestState.USER_STOPPED
        return TestState.COMPLETED

    def _append_record(self, record: MeasurementRecord) -> None:
        self._records.append(record)
        self.measurement_ready.emit(record)
        self.progress_changed.emit(record.sequence, self._config.test_count)

    @staticmethod
    def _with_invalid_reasons(
        measurement: LatencyMeasurement,
        reasons: list[str],
    ) -> LatencyMeasurement:
        if not reasons:
            return measurement
        existing = measurement.invalid_reason.split(";") if measurement.invalid_reason else []
        combined = list(dict.fromkeys([*existing, *reasons]))
        return replace(
            measurement,
            is_valid=False,
            invalid_reason=";".join(combined),
        )

    def _emit_quality_state(self, measurement: LatencyMeasurement) -> None:
        reasons = set(measurement.invalid_reason.split(";")) if measurement.invalid_reason else set()
        if "no_signal" in reasons:
            self.state_changed.emit(TestState.NO_SIGNAL)
        elif "low_snr" in reasons:
            self.state_changed.emit(TestState.LOW_SNR)

    def _audio_error_state(self) -> TestState:
        if self._capture is not None and (
            self._capture.overflow_count or self._capture.dropped_frames
        ):
            return TestState.CAPTURE_OVERFLOW
        if self._playback is not None and self._playback.underflow_count:
            return TestState.PLAYBACK_UNDERFLOW
        return TestState.AUDIO_STREAM_ERROR

    def _collect_audio_diagnostics(self) -> SessionAudioDiagnostics | None:
        playback = None
        capture = None
        for name, engine in (("播放", self._playback), ("采集", self._capture)):
            if engine is None:
                continue
            try:
                diagnostics = engine.diagnostics()
            except Exception:
                logger.error("读取%s流诊断信息失败", name, exc_info=True)
                continue
            if name == "播放":
                playback = diagnostics
            else:
                capture = diagnostics
        if playback is None and capture is None:
            return None
        return SessionAudioDiagnostics(playback=playback, capture=capture)

    def _close_engines(self) -> str | None:
        errors: list[str] = []
        for engine in (self._playback, self._capture):
            if engine is None:
                continue
            try:
                engine.close()
            except Exception as exc:
                errors.append(str(exc) or type(exc).__name__)
                logger.error("关闭测试音频引擎失败", exc_info=True)
        self._playback = None
        self._capture = None
        if self._diagnostic_backend is not None:
            try:
                self._diagnostic_backend.close()
            except Exception as exc:
                errors.append(str(exc) or type(exc).__name__)
                logger.error("关闭数字环回后端失败", exc_info=True)
            finally:
                self._diagnostic_backend = None
        return "；".join(errors) if errors else None


class TestController(QObject):
    state_changed = Signal(object)
    progress_changed = Signal(int, int)
    warmup_progress = Signal(float, float)
    measurement_ready = Signal(object)
    error_occurred = Signal(str)
    session_finished = Signal(object)
    session_cleared = Signal()
    session_save_requested = Signal(object)
    session_saved = Signal(object)
    save_failed = Signal(str)
    stop_requested = Signal()
    finished = Signal()

    def __init__(self, service: DeviceService, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self._service = service
        self._state_machine = TestStateMachine()
        self._thread: QThread | None = None
        self._worker: TestSessionWorker | None = None
        self._records: list[MeasurementRecord] = []
        self._snapshot: TestSessionSnapshot | None = None
        self._session_config: TestSessionConfig | None = None
        self._save_pending = False

    @property
    def state(self) -> TestState:
        return self._state_machine.state

    @property
    def is_running(self) -> bool:
        thread_running = self._thread is not None and self._thread.isRunning()
        return self._state_machine.is_active or thread_running

    @property
    def save_pending(self) -> bool:
        return self._save_pending

    @property
    def records(self) -> tuple[MeasurementRecord, ...]:
        return tuple(self._records)

    @property
    def snapshot(self) -> TestSessionSnapshot | None:
        return self._snapshot

    @property
    def statistics(self) -> SessionStatistics:
        minimum_valid_count = (
            self._session_config.measurement.minimum_valid_count
            if self._session_config is not None
            else 1
        )
        return StatisticsEngine.calculate(
            (record.measurement for record in self._records),
            minimum_valid_count=minimum_valid_count,
        )

    def start(self, config: TestSessionConfig) -> None:
        if self.is_running:
            raise ControllerBusyError("测试仍在运行或正在收尾")
        self._reset_session()
        self._session_config = config
        self._transition(TestState.DEVICE_CHECK)

        thread = QThread(self)
        worker = TestSessionWorker(self._service, config)
        worker.moveToThread(thread)
        thread.started.connect(worker.run)
        worker.state_changed.connect(self._transition)
        worker.progress_changed.connect(self.progress_changed)
        worker.warmup_progress.connect(self.warmup_progress)
        worker.measurement_ready.connect(self._append_measurement)
        worker.error_occurred.connect(self.error_occurred)
        worker.session_finished.connect(self._on_session_finished)
        worker.finished.connect(thread.quit, Qt.ConnectionType.DirectConnection)
        worker.finished.connect(worker.deleteLater)
        thread.finished.connect(self._on_thread_finished)
        thread.finished.connect(thread.deleteLater)
        self._thread = thread
        self._worker = worker
        thread.start()

    def request_stop(self) -> None:
        if not self.is_running or self._worker is None:
            return
        self._worker.request_stop()
        self.stop_requested.emit()

    def clear(self) -> None:
        if self.is_running:
            raise ControllerBusyError("请先停止测试并等待音频资源释放")
        self._reset_session()

    def request_save(self) -> None:
        if self.is_running or self._snapshot is None or not self._snapshot.records:
            raise SessionNotAvailableError("当前没有可保存的已完成测量")
        if self.state not in {TestState.COMPLETED, TestState.USER_STOPPED}:
            raise SessionNotAvailableError("当前会话已保存或处于不可保存状态")
        if self._save_pending:
            return
        self._save_pending = True
        self.session_save_requested.emit(self._snapshot)

    def mark_saved(self) -> None:
        if self._snapshot is None:
            raise SessionNotAvailableError("当前没有会话")
        self._save_pending = False
        if self.state == TestState.SAVED:
            return
        self._transition(TestState.SAVED)
        self._snapshot = replace(self._snapshot, terminal_state=TestState.SAVED)
        self.session_saved.emit(self._snapshot)

    def notify_save_failed(self, message: str) -> None:
        self._save_pending = False
        self.save_failed.emit(message)

    def shutdown(self, timeout_ms: int = 3000) -> bool:
        self.request_stop()
        thread = self._thread
        if thread is not None and thread.isRunning():
            if not thread.wait(timeout_ms):
                logger.warning("测试会话线程未在 %d ms 内结束", timeout_ms)
                return False
        return True

    @Slot(object)
    def _transition(self, target: TestState) -> None:
        self._state_machine.transition(target)
        self.state_changed.emit(target)

    @Slot(object)
    def _append_measurement(self, record: MeasurementRecord) -> None:
        self._records.append(record)
        self.measurement_ready.emit(record)

    @Slot(object)
    def _on_session_finished(self, snapshot: TestSessionSnapshot) -> None:
        self._snapshot = snapshot
        self.session_finished.emit(snapshot)

    def _reset_session(self) -> None:
        if self.state != TestState.IDLE:
            self._transition(TestState.IDLE)
        self._records.clear()
        self._snapshot = None
        self._session_config = None
        self._save_pending = False
        self.progress_changed.emit(0, 0)
        self.session_cleared.emit()

    @Slot()
    def _on_thread_finished(self) -> None:
        if self.sender() is self._thread:
            self._thread = None
            worker = self._worker
            if worker is not None:
                QTimer.singleShot(0, lambda: self._release_finished_worker(worker))
            self.finished.emit()

    def _release_finished_worker(self, worker: TestSessionWorker) -> None:
        if self._worker is worker:
            self._worker = None
