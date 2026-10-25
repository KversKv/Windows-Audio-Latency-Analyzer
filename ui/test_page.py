import logging
from dataclasses import replace

import numpy as np
import pyqtgraph as pg
from PySide6.QtCore import Qt, QThread, Signal
from PySide6.QtWidgets import (
    QComboBox,
    QDoubleSpinBox,
    QFrame,
    QGridLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QProgressBar,
    QPushButton,
    QSizePolicy,
    QSpinBox,
    QVBoxLayout,
    QWidget,
)

from app.config import AppConfig
from core.device_service import (
    AudioDevice,
    DeviceEnumerationWorker,
    DeviceInventory,
    DeviceService,
)
from core.test_controller import ControllerBusyError, SessionNotAvailableError, TestController
from core.test_models import MeasurementRecord, TestSessionConfig, TestSessionSnapshot
from core.test_state_machine import TestState

logger = logging.getLogger(__name__)

_STATE_LABELS = {
    TestState.IDLE: "空闲",
    TestState.DEVICE_CHECK: "检查设备",
    TestState.OPENING_AUDIO: "打开音频流",
    TestState.WARMUP: "连续流预热",
    TestState.READY: "就绪",
    TestState.RUNNING: "测量中",
    TestState.ANALYZING: "分析中",
    TestState.WAIT_NEXT: "等待下一次",
    TestState.COMPLETED: "已完成",
    TestState.SAVED: "已保存",
    TestState.DEVICE_ERROR: "设备错误",
    TestState.AUDIO_STREAM_ERROR: "音频流错误",
    TestState.CAPTURE_OVERFLOW: "采集溢出",
    TestState.PLAYBACK_UNDERFLOW: "播放欠载",
    TestState.NO_SIGNAL: "无信号",
    TestState.LOW_SNR: "低信噪比",
    TestState.USER_STOPPED: "用户已停止",
}

_INVALID_REASON_LABELS = {
    "no_signal": "无信号",
    "low_snr": "信噪比不足",
    "low_correlation": "相关性不足",
    "ambiguous_correlation": "相关峰值存在歧义",
    "latency_out_of_range": "延迟超出有效范围",
    "capture_timeout": "采集窗口超时",
    "capture_too_short": "采集波形过短",
    "non_finite_capture": "采集波形包含非有限值",
    "stimulus_not_completed": "刺激信号未完整播放",
    "capture_timeline_discontinuous": "采集时间轴不连续",
    "missing_playback_timestamp": "缺少播放 DAC 时间戳",
    "missing_capture_timestamp": "缺少采集 ADC 时间戳",
}


class TestPage(QWidget):
    devices_refreshed = Signal(object)
    refresh_failed = Signal(str)
    refresh_finished = Signal()
    device_selection_changed = Signal()
    state_changed = Signal(object)
    progress_changed = Signal(int, int)
    audio_parameters_changed = Signal(int, int)
    status_message = Signal(str)
    session_save_requested = Signal(object)
    session_finished = Signal(object)

    def __init__(
        self,
        device_service: DeviceService,
        config: AppConfig,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self._device_service = device_service
        self._config = config
        self._refresh_thread: QThread | None = None
        self._refresh_worker: DeviceEnumerationWorker | None = None
        self._loading_devices = False
        self._stop_requested = False
        self._shutting_down = False
        self._session_sample_rate = config.audio.sample_rate
        self._preferred_output_id = config.audio.output_device_id
        self._preferred_input_id = config.audio.input_device_id
        self.controller = TestController(device_service, self)
        self._build_ui()
        self._connect_controller()
        self.refresh_button.clicked.connect(self.refresh_devices)
        self.output_combo.currentIndexChanged.connect(self._on_device_selection_changed)
        self.input_combo.currentIndexChanged.connect(self._on_device_selection_changed)
        self.start_button.clicked.connect(self.start_test)
        self.stop_button.clicked.connect(self.stop_test)
        self.clear_button.clicked.connect(self.clear_session)
        self.save_button.clicked.connect(self.request_save)
        self._update_controls()

    def _build_ui(self) -> None:
        root_layout = QVBoxLayout(self)
        root_layout.setContentsMargins(30, 22, 30, 20)
        root_layout.setSpacing(12)

        title = QLabel("声学延迟测试")
        title.setObjectName("pageTitle")
        subtitle = QLabel("在连续音频流中测量耳机输出到独立麦克风响应的端到端延迟")
        subtitle.setObjectName("pageSubtitle")
        root_layout.addWidget(title)
        root_layout.addWidget(subtitle)

        device_card = QFrame()
        device_card.setObjectName("card")
        device_layout = QGridLayout(device_card)
        device_layout.setContentsMargins(20, 14, 20, 14)
        device_layout.setHorizontalSpacing(16)
        device_layout.setVerticalSpacing(9)

        device_title = QLabel("音频设备")
        device_title.setObjectName("sectionTitle")
        self.backend_label = QLabel("正在初始化")
        self.backend_label.setObjectName("statusBadge")
        device_header = QHBoxLayout()
        device_header.addWidget(device_title)
        device_header.addStretch()
        device_header.addWidget(self.backend_label)
        device_layout.addLayout(device_header, 0, 0, 1, 2)

        output_label = QLabel("播放设备")
        output_label.setObjectName("fieldLabel")
        self.output_combo = QComboBox()
        self.output_combo.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self.output_combo.setMinimumContentsLength(36)
        device_layout.addWidget(output_label, 1, 0)
        device_layout.addWidget(self.output_combo, 1, 1)

        input_label = QLabel("录音设备")
        input_label.setObjectName("fieldLabel")
        self.input_combo = QComboBox()
        self.input_combo.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self.input_combo.setMinimumContentsLength(36)
        device_layout.addWidget(input_label, 2, 0)
        device_layout.addWidget(self.input_combo, 2, 1)

        self.refresh_button = QPushButton("刷新设备")
        self.refresh_button.setObjectName("secondaryButton")
        self.refresh_button.setCursor(Qt.CursorShape.PointingHandCursor)
        device_layout.addWidget(self.refresh_button, 3, 1, Qt.AlignmentFlag.AlignRight)

        self.device_status_label = QLabel("尚未读取设备列表")
        self.device_status_label.setObjectName("mutedLabel")
        self.device_status_label.setWordWrap(True)
        device_layout.addWidget(self.device_status_label, 4, 0, 1, 2)
        root_layout.addWidget(device_card)

        self.simulation_label = QLabel(
            "离线模拟模式：使用已知延迟的合成波形验证界面与分析流程；所有结果均为模拟，非真实声学测量。"
        )
        self.simulation_label.setObjectName("simulationBanner")
        self.simulation_label.setWordWrap(True)
        self.simulation_label.hide()
        root_layout.addWidget(self.simulation_label)

        parameter_card = QFrame()
        parameter_card.setObjectName("card")
        parameter_layout = QGridLayout(parameter_card)
        parameter_layout.setContentsMargins(20, 12, 20, 14)
        parameter_layout.setHorizontalSpacing(16)
        parameter_layout.setVerticalSpacing(7)
        parameter_title = QLabel("本次测试参数")
        parameter_title.setObjectName("sectionTitle")
        parameter_layout.addWidget(parameter_title, 0, 0, 1, 5)

        self.test_count_spin = QSpinBox()
        self.test_count_spin.setRange(1, 500)
        self.test_count_spin.setValue(self._config.measurement.default_test_count)
        self.test_count_spin.setSuffix(" 次")
        self.warmup_spin = QDoubleSpinBox()
        self.warmup_spin.setRange(1.0, 15.0)
        self.warmup_spin.setDecimals(1)
        self.warmup_spin.setSingleStep(0.5)
        self.warmup_spin.setSuffix(" s")
        self.warmup_spin.setValue(self._config.measurement.warmup_seconds)
        self.compensation_spin = QDoubleSpinBox()
        self.compensation_spin.setRange(-1000.0, 1000.0)
        self.compensation_spin.setDecimals(2)
        self.compensation_spin.setSuffix(" ms")
        self.compensation_spin.setValue(0.0)
        self.compensation_spin.setToolTip("手动补偿值；0 ms 表示未补偿，不代表已完成校准")
        self.amplitude_spin = QDoubleSpinBox()
        self.amplitude_spin.setRange(0.001, 1.0)
        self.amplitude_spin.setDecimals(3)
        self.amplitude_spin.setSingleStep(0.01)
        self.amplitude_spin.setValue(self._config.measurement.stimulus_amplitude)
        self.simulation_delay_spin = QDoubleSpinBox()
        self.simulation_delay_spin.setRange(0.0, 1000.0)
        self.simulation_delay_spin.setDecimals(2)
        self.simulation_delay_spin.setSuffix(" ms")
        self.simulation_delay_spin.setValue(self._config.measurement.simulation_delay_ms)
        self.simulation_delay_spin.hide()

        parameter_widgets = (
            ("测试次数", self.test_count_spin),
            ("预热时间", self.warmup_spin),
            ("手动补偿", self.compensation_spin),
            ("Chirp 幅度", self.amplitude_spin),
        )
        for column, (label_text, widget) in enumerate(parameter_widgets):
            label = QLabel(label_text)
            label.setObjectName("fieldLabel")
            parameter_layout.addWidget(label, 1, column)
            parameter_layout.addWidget(widget, 2, column)
        self.simulation_delay_label = QLabel("模拟真实延迟")
        self.simulation_delay_label.setObjectName("fieldLabel")
        self.simulation_delay_label.hide()
        parameter_layout.addWidget(self.simulation_delay_label, 1, 4)
        parameter_layout.addWidget(self.simulation_delay_spin, 2, 4)
        self._parameter_widgets = [
            self.test_count_spin,
            self.warmup_spin,
            self.compensation_spin,
            self.amplitude_spin,
            self.simulation_delay_spin,
        ]
        root_layout.addWidget(parameter_card)

        overview = QGroupBox("测量概览")
        overview_layout = QGridLayout(overview)
        overview_layout.setContentsMargins(20, 22, 20, 14)
        overview_layout.setHorizontalSpacing(20)
        overview_layout.setVerticalSpacing(6)
        metric_titles = [
            "当前原始延迟",
            "平均延迟",
            "中位数",
            "P95",
            "最小值",
            "最大值",
            "标准差",
            "有效样本",
        ]
        self.metric_value_labels: list[QLabel] = []
        for index, metric_title in enumerate(metric_titles):
            row = (index // 4) * 2
            column = index % 4
            name_label = QLabel(metric_title)
            name_label.setObjectName("metricName")
            value_label = QLabel("—")
            value_label.setObjectName("metricValue")
            self.metric_value_labels.append(value_label)
            overview_layout.addWidget(name_label, row, column)
            overview_layout.addWidget(value_label, row + 1, column)
        self.compensation_status_label = QLabel("补偿：0.00 ms（未补偿）")
        self.compensation_status_label.setObjectName("mutedLabel")
        overview_layout.addWidget(self.compensation_status_label, 4, 0, 1, 4)
        self.measurement_status_label = QLabel("尚无测量结果；统计指标将在后续阶段接入")
        self.measurement_status_label.setObjectName("mutedLabel")
        self.measurement_status_label.setWordWrap(True)
        overview_layout.addWidget(self.measurement_status_label, 5, 0, 1, 4)
        root_layout.addWidget(overview)

        waveform_panel = QFrame()
        waveform_panel.setObjectName("card")
        waveform_layout = QVBoxLayout(waveform_panel)
        waveform_layout.setContentsMargins(16, 12, 16, 12)
        waveform_layout.setSpacing(8)
        waveform_title = QLabel("参考波形与麦克风响应")
        waveform_title.setObjectName("sectionTitle")
        waveform_layout.addWidget(waveform_title)
        plots_layout = QHBoxLayout()
        plots_layout.setSpacing(12)
        self.reference_plot = self._make_plot("输出参考 Chirp")
        self.response_plot = self._make_plot("麦克风采集窗口")
        self.reference_curve = self.reference_plot.plot(
            pen=pg.mkPen("#48b8c7", width=1.4),
            name="参考波形",
        )
        self.response_curve = self.response_plot.plot(
            pen=pg.mkPen("#d7a64a", width=1.4),
            name="麦克风响应",
        )
        self.peak_line = pg.InfiniteLine(
            angle=90,
            movable=False,
            pen=pg.mkPen("#e66f75", width=1.2, style=Qt.PenStyle.DashLine),
        )
        self.response_plot.addItem(self.peak_line)
        self.peak_line.hide()
        plots_layout.addWidget(self.reference_plot)
        plots_layout.addWidget(self.response_plot)
        waveform_layout.addLayout(plots_layout)
        waveform_caption = QLabel("横轴为各窗口相对时间；红色虚线标出相关峰值，延迟仍由 DAC/ADC 时间映射计算。")
        waveform_caption.setObjectName("mutedLabel")
        waveform_layout.addWidget(waveform_caption)
        root_layout.addWidget(waveform_panel, 1)

        status_layout = QHBoxLayout()
        self.state_label = QLabel("空闲")
        self.state_label.setObjectName("statusBadge")
        self.progress_bar = QProgressBar()
        self.progress_bar.setRange(0, 1)
        self.progress_bar.setValue(0)
        self.progress_bar.setFormat("尚无测量")
        self.progress_bar.setTextVisible(True)
        self.session_status_label = QLabel("等待开始")
        self.session_status_label.setObjectName("mutedLabel")
        self.session_status_label.setWordWrap(True)
        status_layout.addWidget(self.state_label)
        status_layout.addWidget(self.progress_bar)
        status_layout.addWidget(self.session_status_label, 1)
        root_layout.addLayout(status_layout)

        self.error_label = QLabel()
        self.error_label.setObjectName("errorLabel")
        self.error_label.setWordWrap(True)
        self.error_label.hide()
        root_layout.addWidget(self.error_label)

        controls = QHBoxLayout()
        controls.setSpacing(10)
        self.start_button = QPushButton("开始测试")
        self.start_button.setObjectName("primaryButton")
        self.stop_button = QPushButton("停止")
        self.clear_button = QPushButton("清空当前会话")
        self.save_button = QPushButton("保存会话")
        self.save_button.setToolTip("生成完整会话保存请求；SQLite 持久化将在后续阶段接入")
        for button in (self.start_button, self.stop_button, self.clear_button, self.save_button):
            button.setCursor(Qt.CursorShape.PointingHandCursor)
            controls.addWidget(button)
        controls.addStretch()
        root_layout.addLayout(controls)

    @staticmethod
    def _make_plot(title: str) -> pg.PlotWidget:
        plot = pg.PlotWidget(background="#141922")
        plot.setTitle(title, color="#96a2b3", size="11pt")
        plot.showGrid(x=True, y=True, alpha=0.18)
        plot.setLabel("bottom", "窗口相对时间", units="ms")
        plot.setLabel("left", "幅度")
        plot.setMinimumHeight(150)
        plot.getPlotItem().getAxis("bottom").setPen("#5f6a79")
        plot.getPlotItem().getAxis("left").setPen("#5f6a79")
        return plot

    def _connect_controller(self) -> None:
        self.controller.state_changed.connect(self._on_state_changed)
        self.controller.progress_changed.connect(self._on_progress_changed)
        self.controller.warmup_progress.connect(self._on_warmup_progress)
        self.controller.measurement_ready.connect(self._on_measurement)
        self.controller.error_occurred.connect(self._on_error)
        self.controller.session_finished.connect(self._on_session_finished)
        self.controller.session_cleared.connect(self._clear_results)
        self.controller.stop_requested.connect(self._on_stop_requested)
        self.controller.session_save_requested.connect(self._on_save_requested)
        self.controller.session_saved.connect(self._on_session_saved)
        self.controller.save_failed.connect(self._on_save_failed)
        self.controller.finished.connect(self._on_controller_finished)

    def selected_output_device_id(self) -> int | str | None:
        return self.output_combo.currentData()

    def selected_input_device_id(self) -> int | str | None:
        return self.input_combo.currentData()

    def session_config(self) -> TestSessionConfig:
        audio = replace(
            self._config.audio,
            output_device_id=self.selected_output_device_id(),
            input_device_id=self.selected_input_device_id(),
        )
        measurement = replace(
            self._config.measurement,
            default_test_count=self.test_count_spin.value(),
            warmup_seconds=self.warmup_spin.value(),
            stimulus_amplitude=self.amplitude_spin.value(),
            simulation_delay_ms=self.simulation_delay_spin.value(),
        )
        return TestSessionConfig(
            audio=audio,
            measurement=measurement,
            compensation_ms=self.compensation_spin.value(),
            output_device_name=self.output_combo.currentText(),
            input_device_name=self.input_combo.currentText(),
        )

    def update_config(self, config: AppConfig) -> None:
        self._config = config
        if self.controller.is_running:
            return
        self.test_count_spin.setValue(config.measurement.default_test_count)
        self.warmup_spin.setValue(config.measurement.warmup_seconds)
        self.amplitude_spin.setValue(config.measurement.stimulus_amplitude)
        self.simulation_delay_spin.setValue(config.measurement.simulation_delay_ms)

    def start_test(self) -> None:
        if self.controller.is_running:
            return
        try:
            config = self.session_config()
            self._stop_requested = False
            self.controller.start(config)
        except (ControllerBusyError, ValueError) as exc:
            self._on_error(str(exc))
            return
        self._session_sample_rate = config.audio.sample_rate
        self.error_label.hide()
        self.audio_parameters_changed.emit(config.audio.sample_rate, config.audio.sample_rate)
        self.status_message.emit("已开始测试会话")

    def stop_test(self) -> None:
        self.controller.request_stop()

    def clear_session(self) -> None:
        try:
            self.controller.clear()
        except ControllerBusyError as exc:
            self._on_error(str(exc))

    def request_save(self) -> None:
        try:
            self.controller.request_save()
        except SessionNotAvailableError as exc:
            self._on_error(str(exc))

    def refresh_devices(self) -> None:
        if self.controller.is_running or self._shutting_down:
            return
        if self._refresh_thread is not None and self._refresh_thread.isRunning():
            return
        current_output = self.selected_output_device_id()
        current_input = self.selected_input_device_id()
        if current_output is not None:
            self._preferred_output_id = current_output
        if current_input is not None:
            self._preferred_input_id = current_input

        self.refresh_button.setEnabled(False)
        self.device_status_label.setText("正在读取音频设备…")
        self._update_controls()
        thread = QThread(self)
        worker = DeviceEnumerationWorker(self._device_service)
        worker.moveToThread(thread)
        thread.started.connect(worker.run)
        worker.succeeded.connect(self._apply_devices)
        worker.failed.connect(self._show_refresh_error)
        worker.finished.connect(thread.quit)
        worker.finished.connect(worker.deleteLater)
        thread.finished.connect(self._on_refresh_thread_finished)
        thread.finished.connect(thread.deleteLater)
        self._refresh_thread = thread
        self._refresh_worker = worker
        thread.start()

    def _apply_devices(self, inventory: DeviceInventory) -> None:
        self._loading_devices = True
        self._populate_combo(
            self.output_combo,
            inventory.output_devices,
            self._preferred_output_id,
            "未检测到播放设备",
            is_output=True,
        )
        self._populate_combo(
            self.input_combo,
            inventory.input_devices,
            self._preferred_input_id,
            "未检测到录音设备",
            is_output=False,
        )
        self._loading_devices = False

        self.backend_label.setText("离线模拟" if inventory.is_simulation else "真实音频设备")
        self.simulation_label.setVisible(inventory.is_simulation)
        self.simulation_delay_label.setVisible(inventory.is_simulation)
        self.simulation_delay_spin.setVisible(inventory.is_simulation)
        self.warmup_spin.setToolTip(
            "离线合成不打开音频流，无需预热"
            if inventory.is_simulation
            else "真实测量前保持连续背景音播放"
        )
        self.device_status_label.setText(
            f"{inventory.backend_name} · 检测到 {len(inventory.output_devices)} 个播放设备、"
            f"{len(inventory.input_devices)} 个录音设备"
        )
        self.devices_refreshed.emit(inventory)
        self.device_selection_changed.emit()
        self._update_controls()

    def _populate_combo(
        self,
        combo: QComboBox,
        devices: tuple[AudioDevice, ...],
        preferred_id: int | str | None,
        empty_text: str,
        is_output: bool,
    ) -> None:
        combo.clear()
        if not devices:
            combo.addItem(empty_text, None)
            combo.setEnabled(False)
            return
        combo.setEnabled(True)
        for device in devices:
            combo.addItem(self._device_text(device, is_output), device.identifier)
            combo.setItemData(
                combo.count() - 1,
                f"主机 API：{device.host_api}",
                Qt.ItemDataRole.ToolTipRole,
            )
        preferred_index = combo.findData(preferred_id)
        combo.setCurrentIndex(preferred_index if preferred_index >= 0 else 0)

    @staticmethod
    def _device_text(device: AudioDevice, is_output: bool) -> str:
        channels = device.max_output_channels if is_output else device.max_input_channels
        sample_rate = (
            f"{device.default_sample_rate / 1000:g} kHz"
            if device.default_sample_rate > 0
            else "采样率未知"
        )
        return f"{device.name}  |  ID {device.identifier} · {sample_rate} · {channels} 声道"

    def _show_refresh_error(self, message: str) -> None:
        self.device_status_label.setText(f"设备刷新失败：{message}")
        self.refresh_failed.emit(message)

    def _on_refresh_thread_finished(self) -> None:
        if self.sender() is self._refresh_thread:
            self._refresh_thread = None
            self._refresh_worker = None
            self._update_controls()
            self.refresh_finished.emit()

    def _on_device_selection_changed(self) -> None:
        if self._loading_devices:
            return
        self._preferred_output_id = self.selected_output_device_id()
        self._preferred_input_id = self.selected_input_device_id()
        self.device_selection_changed.emit()
        self._update_controls()

    def _on_state_changed(self, state: TestState) -> None:
        self.state_label.setText(_STATE_LABELS[state])
        state_messages = {
            TestState.IDLE: "等待开始",
            TestState.DEVICE_CHECK: "正在重新检查所选设备",
            TestState.OPENING_AUDIO: "正在打开独立播放与采集流",
            TestState.WARMUP: "连续背景音预热中，暂不进行正式测量",
            TestState.READY: "设备就绪",
            TestState.RUNNING: "正在连续流中调度 Chirp 并采集响应",
            TestState.ANALYZING: "正在后台线程执行相关分析",
            TestState.COMPLETED: "测量完成，音频流已关闭",
            TestState.SAVED: "会话已保存",
            TestState.USER_STOPPED: "已停止并关闭音频流，已完成样本保留",
        }
        if state in state_messages:
            self.session_status_label.setText(state_messages[state])
        self.state_changed.emit(state)
        self._update_controls()

    def _on_progress_changed(self, completed: int, total: int) -> None:
        self.progress_bar.setRange(0, max(total, 1))
        self.progress_bar.setValue(completed if total else 0)
        self.progress_bar.setFormat("%v / %m 次" if total else "尚无测量")
        self.progress_changed.emit(completed, total)

    def _on_warmup_progress(self, remaining: float, total: float) -> None:
        self.session_status_label.setText(f"连续流预热：剩余 {remaining:.1f} / {total:.1f} s")

    def _on_measurement(self, record: MeasurementRecord) -> None:
        measurement = record.measurement
        self.metric_value_labels[0].setText(self._format_ms(measurement.raw_latency_ms))
        valid_count = sum(item.measurement.is_valid for item in self.controller.records)
        self.metric_value_labels[7].setText(str(valid_count))
        compensation_text = (
            f"{measurement.compensation_ms:.2f} ms（手动补偿）"
            if measurement.compensation_ms != 0
            else "0.00 ms（未补偿）"
        )
        compensated_text = (
            f" · 当前补偿后 {measurement.compensated_latency_ms:.2f} ms"
            if measurement.compensated_latency_ms is not None
            else ""
        )
        simulation_text = " · 模拟结果" if measurement.is_simulation else ""
        self.compensation_status_label.setText(
            f"补偿：{compensation_text}{compensated_text}{simulation_text}"
        )
        if measurement.is_valid:
            snr_text = self._format_snr(measurement.snr_db)
            self.measurement_status_label.setText(
                f"第 {record.sequence} 次有效 · 原始 {self._format_ms(measurement.raw_latency_ms)}"
                f" · SNR {snr_text} · 相关系数 {measurement.correlation_peak:.3f}"
            )
        else:
            reason = measurement.invalid_reason or "unknown"
            labels = [
                _INVALID_REASON_LABELS.get(item, item) for item in reason.split(";")
            ]
            self.measurement_status_label.setText(
                f"第 {record.sequence} 次无效：{'、'.join(labels)}（原始记录已保留）"
            )
            self.measurement_status_label.setToolTip(reason)
        self._update_waveforms(record)

    def _update_waveforms(self, record: MeasurementRecord) -> None:
        sample_rate = self._session_sample_rate
        reference_x = np.arange(record.reference.size, dtype=np.float64) * 1000 / sample_rate
        captured_x = np.arange(record.captured.size, dtype=np.float64) * 1000 / sample_rate
        self.reference_curve.setData(reference_x, record.reference)
        self.response_curve.setData(captured_x, record.captured)
        lag = record.measurement.correlation_lag_samples
        self.peak_line.setVisible(lag is not None)
        if lag is not None:
            self.peak_line.setPos(lag * 1000 / sample_rate)

    def _on_session_finished(self, snapshot: TestSessionSnapshot) -> None:
        if snapshot.terminal_state == TestState.COMPLETED:
            simulation_text = "（离线模拟，非真实测量）" if snapshot.is_simulation else ""
            self.session_status_label.setText(
                f"完成 {snapshot.completed_count} 次：有效 {snapshot.valid_count}，"
                f"无效 {snapshot.invalid_count}{simulation_text}；音频资源已释放"
            )
        self.session_finished.emit(snapshot)
        self._update_controls()

    def _on_error(self, message: str) -> None:
        self.error_label.setText(f"设备 / 音频错误：{message}")
        self.error_label.show()
        self.status_message.emit(message)
        self._update_controls()

    def _on_stop_requested(self) -> None:
        self._stop_requested = True
        self.session_status_label.setText("正在停止调度、结束处理并关闭音频流；已完成样本会保留")
        self._update_controls()

    def _on_save_requested(self, snapshot: TestSessionSnapshot) -> None:
        self.session_status_label.setText("保存请求已生成；SQLite 持久化尚未接入，当前仍未写入数据库")
        self.session_save_requested.emit(snapshot)

    def _on_session_saved(self, _snapshot: TestSessionSnapshot) -> None:
        self.session_status_label.setText("会话已保存")
        self._update_controls()

    def _on_save_failed(self, message: str) -> None:
        self.session_status_label.setText(f"保存失败：{message}")
        self._update_controls()

    def _on_controller_finished(self) -> None:
        self._stop_requested = False
        self._update_controls()

    def _clear_results(self) -> None:
        for label in self.metric_value_labels:
            label.setText("—")
        self.compensation_status_label.setText("补偿：0.00 ms（未补偿）")
        self.measurement_status_label.setText("尚无测量结果；统计指标将在后续阶段接入")
        self.measurement_status_label.setToolTip("")
        self.session_status_label.setText("等待开始")
        self.error_label.hide()
        self.reference_curve.setData([], [])
        self.response_curve.setData([], [])
        self.peak_line.hide()

    def _update_controls(self) -> None:
        active = self.controller.is_running
        refreshing = self._refresh_thread is not None and self._refresh_thread.isRunning()
        has_output = self.output_combo.count() > 0 and self.selected_output_device_id() is not None
        has_input = self.input_combo.count() > 0 and self.selected_input_device_id() is not None
        can_start = has_output and has_input and not active and not refreshing and not self._shutting_down
        self.start_button.setEnabled(can_start)
        self.stop_button.setEnabled(active and not self._stop_requested)
        self.clear_button.setEnabled(
            not active and (bool(self.controller.records) or self.controller.state != TestState.IDLE)
        )
        snapshot = self.controller.snapshot
        self.save_button.setEnabled(
            not active
            and self.controller.state in {TestState.COMPLETED, TestState.USER_STOPPED}
            and snapshot is not None
            and bool(snapshot.records)
        )
        self.refresh_button.setEnabled(not active and not refreshing and not self._shutting_down)
        self.output_combo.setEnabled(has_output and not active and not refreshing)
        self.input_combo.setEnabled(has_input and not active and not refreshing)
        for widget in self._parameter_widgets:
            widget.setEnabled(not active and not self._shutting_down)

    @staticmethod
    def _format_ms(value: float | None) -> str:
        return f"{value:.2f} ms" if value is not None else "—"

    @staticmethod
    def _format_snr(value: float | None) -> str:
        if value is None:
            return "—"
        if np.isinf(value):
            return "∞"
        return f"{value:.1f} dB"

    def shutdown(self) -> None:
        if self._shutting_down:
            return
        self._shutting_down = True
        self.controller.shutdown()
        thread = self._refresh_thread
        if thread is not None and thread.isRunning():
            thread.quit()
            if not thread.wait(2000):
                logger.warning("设备枚举线程未在 2 秒内结束")
        self._update_controls()
