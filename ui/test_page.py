import logging
from dataclasses import replace

import numpy as np
import pyqtgraph as pg
from PySide6.QtCore import Qt, QThread, QTimer, Signal
from PySide6.QtWidgets import (
    QComboBox,
    QDoubleSpinBox,
    QFrame,
    QGridLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QProgressBar,
    QPushButton,
    QScrollArea,
    QSizePolicy,
    QSpinBox,
    QVBoxLayout,
    QWidget,
)

from app.config import AppConfig, CalibrationContext, CalibrationProfile
from core.calibration_manager import CalibrationManager
from core.latency_analyzer import LatencyMeasurement
from core.device_service import (
    AudioDevice,
    DeviceEnumerationWorker,
    DeviceInventory,
    DeviceService,
)
from core.statistics_engine import SessionStatistics
from core.test_controller import ControllerBusyError, SessionNotAvailableError, TestController
from core.test_models import MeasurementRecord, TestSessionConfig, TestSessionSnapshot
from core.test_state_machine import TestState
from ui.diagnostics_panel import DiagnosticsPanel

logger = logging.getLogger(__name__)

_DEVICE_NAME_ROLE = Qt.ItemDataRole.UserRole + 1
_AUTO_REFRESH_INTERVAL_MS = 2000
_AUTO_REFRESH_MAX_INTERVAL_MS = 30000
_DEVICE_UNAVAILABLE_TEXT = "原设备不可用或无法唯一识别，请重新选择"

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
    calibration_changed = Signal(object)
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
        self._loading_calibration = False
        self._manual_compensation_ms = config.calibration.manual_compensation_ms
        self._stop_requested = False
        self._shutting_down = False
        self._session_sample_rate = config.audio.sample_rate
        self._preferred_output_id = config.audio.output_device_id
        self._preferred_input_id = config.audio.input_device_id
        self._preferred_output_device: AudioDevice | None = None
        self._preferred_input_device: AudioDevice | None = None
        self._last_inventory: DeviceInventory | None = None
        self._refresh_is_manual = True
        self._refresh_error_active = False
        self._consecutive_refresh_failures = 0
        self._auto_refresh_timer = QTimer(self)
        self._auto_refresh_timer.setSingleShot(True)
        self._auto_refresh_timer.timeout.connect(self._auto_refresh_devices)
        self.controller = TestController(device_service, self)
        self._build_ui()
        self._connect_controller()
        self.diagnostics_panel.start_requested.connect(self._start_diagnostics)
        self.diagnostics_panel.finished.connect(self._on_diagnostics_finished)
        self.refresh_button.clicked.connect(self.refresh_devices)
        self.output_combo.currentIndexChanged.connect(self._on_device_selection_changed)
        self.input_combo.currentIndexChanged.connect(self._on_device_selection_changed)
        self.start_button.clicked.connect(self.start_test)
        self.stop_button.clicked.connect(self.stop_test)
        self.clear_button.clicked.connect(self.clear_session)
        self.save_button.clicked.connect(self.request_save)
        self.calibration_profile_combo.currentIndexChanged.connect(
            self._on_calibration_selection_changed
        )
        self.compensation_spin.valueChanged.connect(self._on_manual_compensation_changed)
        self._update_controls()

    def _build_ui(self) -> None:
        outer_layout = QVBoxLayout(self)
        outer_layout.setContentsMargins(0, 0, 0, 0)
        outer_layout.setSpacing(0)
        self.page_scroll_area = QScrollArea()
        self.page_scroll_area.setObjectName("pageScrollArea")
        self.page_scroll_area.setWidgetResizable(True)
        self.page_scroll_area.setFrameShape(QFrame.Shape.NoFrame)
        self.page_scroll_area.setHorizontalScrollBarPolicy(
            Qt.ScrollBarPolicy.ScrollBarAlwaysOff
        )
        outer_layout.addWidget(self.page_scroll_area, 1)

        content = QWidget()
        self.page_scroll_area.setWidget(content)
        root_layout = QVBoxLayout(content)
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
        self.compensation_spin.setValue(self._manual_compensation_ms)
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
        calibration_label = QLabel("校准配置")
        calibration_label.setObjectName("fieldLabel")
        parameter_layout.addWidget(calibration_label, 3, 0)
        self.calibration_profile_combo = QComboBox()
        self.calibration_profile_combo.setMinimumContentsLength(28)
        parameter_layout.addWidget(self.calibration_profile_combo, 4, 0, 1, 2)

        minimum_valid_label = QLabel("最少有效样本")
        minimum_valid_label.setObjectName("fieldLabel")
        parameter_layout.addWidget(minimum_valid_label, 3, 2)
        self.minimum_valid_count_spin = QSpinBox()
        self.minimum_valid_count_spin.setRange(1, self.test_count_spin.value())
        self.minimum_valid_count_spin.setSuffix(" 次")
        self.minimum_valid_count_spin.setValue(self._config.measurement.minimum_valid_count)
        self.minimum_valid_count_spin.setToolTip("只判定有效样本是否充足，不会删除无效或异常样本")
        parameter_layout.addWidget(self.minimum_valid_count_spin, 4, 2)
        self.test_count_spin.valueChanged.connect(self.minimum_valid_count_spin.setMaximum)

        self.calibration_source_label = QLabel()
        self.calibration_source_label.setObjectName("mutedLabel")
        self.calibration_source_label.setWordWrap(True)
        parameter_layout.addWidget(self.calibration_source_label, 5, 0, 1, 5)

        headphone_label = QLabel("耳机名称")
        headphone_label.setObjectName("fieldLabel")
        parameter_layout.addWidget(headphone_label, 6, 0)
        self.headphone_name_edit = QLineEdit()
        self.headphone_name_edit.setPlaceholderText("用于历史记录搜索，例如：WH-1000XM5")
        self.headphone_name_edit.setClearButtonEnabled(True)
        parameter_layout.addWidget(self.headphone_name_edit, 7, 0, 1, 2)
        connection_label = QLabel("连接方式")
        connection_label.setObjectName("fieldLabel")
        parameter_layout.addWidget(connection_label, 6, 2)
        self.connection_type_combo = QComboBox()
        for text, value in (
            ("未知 / 未指定", "unknown"),
            ("有线", "wired"),
            ("USB", "usb"),
            ("2.4 GHz 无线", "2.4ghz"),
            ("蓝牙", "bluetooth"),
            ("其他", "other"),
        ):
            self.connection_type_combo.addItem(text, value)
        parameter_layout.addWidget(self.connection_type_combo, 7, 2, 1, 2)

        self._parameter_widgets = [
            self.test_count_spin,
            self.minimum_valid_count_spin,
            self.warmup_spin,
            self.compensation_spin,
            self.calibration_profile_combo,
            self.amplitude_spin,
            self.simulation_delay_spin,
            self.headphone_name_edit,
            self.connection_type_combo,
        ]
        root_layout.addWidget(parameter_card)

        self.diagnostics_panel = DiagnosticsPanel(self._device_service, parent=content)
        root_layout.addWidget(self.diagnostics_panel)

        overview = QGroupBox("测量概览")
        overview_layout = QGridLayout(overview)
        overview_layout.setContentsMargins(20, 22, 20, 14)
        overview_layout.setHorizontalSpacing(20)
        overview_layout.setVerticalSpacing(6)
        metric_titles = ("当前延迟", "平均值", "中位数 / P50", "P90", "P95", "最小值", "最大值", "标准差")
        for column, heading in enumerate(("统计指标", "原始延迟", "补偿后延迟")):
            heading_label = QLabel(heading)
            heading_label.setObjectName("fieldLabel")
            overview_layout.addWidget(heading_label, 0, column)
        self.metric_value_labels: list[QLabel] = []
        self.compensated_metric_value_labels: list[QLabel] = []
        for row, metric_title in enumerate(metric_titles, start=1):
            name_label = QLabel(metric_title)
            name_label.setObjectName("metricName")
            raw_value_label = QLabel("—")
            raw_value_label.setObjectName("metricValue")
            compensated_value_label = QLabel("—")
            compensated_value_label.setObjectName("metricValue")
            self.metric_value_labels.append(raw_value_label)
            self.compensated_metric_value_labels.append(compensated_value_label)
            overview_layout.addWidget(name_label, row, 0)
            overview_layout.addWidget(raw_value_label, row, 1)
            overview_layout.addWidget(compensated_value_label, row, 2)

        overview_layout.addWidget(QLabel("样本数（有效 / 无效）"), 9, 0)
        self.valid_count_label = QLabel("0")
        self.valid_count_label.setObjectName("metricValue")
        self.invalid_count_label = QLabel("0")
        self.invalid_count_label.setObjectName("metricValue")
        overview_layout.addWidget(self.valid_count_label, 9, 1)
        overview_layout.addWidget(self.invalid_count_label, 9, 2)
        self.quality_summary_label = QLabel("质量汇总：尚无样本")
        self.quality_summary_label.setObjectName("mutedLabel")
        self.quality_summary_label.setWordWrap(True)
        overview_layout.addWidget(self.quality_summary_label, 10, 0, 1, 3)
        self.compensation_status_label = QLabel()
        self.compensation_status_label.setObjectName("mutedLabel")
        self.compensation_status_label.setWordWrap(True)
        overview_layout.addWidget(self.compensation_status_label, 11, 0, 1, 3)
        self.measurement_status_label = QLabel("尚无测量结果；统计仅使用有效样本，全部原始记录均保留")
        self.measurement_status_label.setObjectName("mutedLabel")
        self.measurement_status_label.setWordWrap(True)
        overview_layout.addWidget(self.measurement_status_label, 12, 0, 1, 3)
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

        self.action_footer = QWidget()
        self.action_footer.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Fixed)
        footer_layout = QVBoxLayout(self.action_footer)
        footer_layout.setContentsMargins(30, 8, 30, 18)
        footer_layout.setSpacing(10)

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
        footer_layout.addLayout(status_layout)

        self.error_label = QLabel()
        self.error_label.setObjectName("errorLabel")
        self.error_label.setWordWrap(True)
        self.error_label.hide()
        footer_layout.addWidget(self.error_label)

        controls = QHBoxLayout()
        controls.setSpacing(10)
        self.start_button = QPushButton("开始测试")
        self.start_button.setObjectName("primaryButton")
        self.stop_button = QPushButton("停止")
        self.clear_button = QPushButton("清空当前会话")
        self.save_button = QPushButton("保存会话")
        self.save_button.setToolTip("将完整会话和全部单次原始结果保存到 SQLite")
        for button in (self.start_button, self.stop_button, self.clear_button, self.save_button):
            button.setCursor(Qt.CursorShape.PointingHandCursor)
            controls.addWidget(button)
        controls.addStretch()
        footer_layout.addLayout(controls)
        outer_layout.addWidget(self.action_footer)
        self._reload_calibration_profiles()

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

    def selected_output_device_name(self) -> str:
        return self._selected_device_name(self.output_combo)

    def selected_input_device_name(self) -> str:
        return self._selected_device_name(self.input_combo)

    @staticmethod
    def _selected_device_name(combo: QComboBox) -> str:
        name = combo.currentData(_DEVICE_NAME_ROLE)
        return str(name) if name else ""

    def calibration_context(self) -> CalibrationContext | None:
        output_id = self.selected_output_device_id()
        input_id = self.selected_input_device_id()
        if output_id is None or input_id is None:
            return None
        return CalibrationContext(
            output_device_id=output_id,
            output_device=self.selected_output_device_name(),
            input_device_id=input_id,
            input_device=self.selected_input_device_name(),
            sample_rate=self._config.audio.sample_rate,
            audio_mode=self._config.audio.audio_mode,
        )

    def selected_calibration_profile(self) -> CalibrationProfile | None:
        return self._config.calibration.profile_by_id(self.calibration_profile_combo.currentData())

    def session_config(self) -> TestSessionConfig:
        context = self.calibration_context()
        if context is None:
            raise ValueError("请先选择有效的播放和录音设备")
        audio = replace(
            self._config.audio,
            output_device_id=context.output_device_id,
            input_device_id=context.input_device_id,
        )
        measurement = replace(
            self._config.measurement,
            default_test_count=self.test_count_spin.value(),
            minimum_valid_count=self.minimum_valid_count_spin.value(),
            warmup_seconds=self.warmup_spin.value(),
            stimulus_amplitude=self.amplitude_spin.value(),
            simulation_delay_ms=self.simulation_delay_spin.value(),
        )
        profile = self.selected_calibration_profile()
        if profile is not None:
            profile.require_match(context)
        return TestSessionConfig(
            audio=audio,
            measurement=measurement,
            compensation_ms=(
                profile.compensation_ms if profile is not None else self.compensation_spin.value()
            ),
            calibration_profile=profile,
            output_device_name=context.output_device,
            input_device_name=context.input_device,
            headphone_name=self.headphone_name_edit.text().strip(),
            connection_type=self.connection_type_combo.currentData(),
        )

    def update_config(self, config: AppConfig) -> None:
        self._config = config
        if self.controller.is_running or self.diagnostics_panel.is_running:
            return
        self.test_count_spin.setValue(config.measurement.default_test_count)
        self.minimum_valid_count_spin.setMaximum(config.measurement.default_test_count)
        self.minimum_valid_count_spin.setValue(config.measurement.minimum_valid_count)
        self.warmup_spin.setValue(config.measurement.warmup_seconds)
        self.amplitude_spin.setValue(config.measurement.stimulus_amplitude)
        self.simulation_delay_spin.setValue(config.measurement.simulation_delay_ms)
        self._manual_compensation_ms = config.calibration.manual_compensation_ms
        self._reload_calibration_profiles()
        self._update_controls()

    def start_test(self) -> None:
        if (
            self.controller.is_running
            or self.diagnostics_panel.is_running
            or self._refresh_thread is not None
        ):
            return
        try:
            config = self.session_config()
            self._stop_requested = False
            self.controller.start(config)
        except (ControllerBusyError, ValueError) as exc:
            self._on_error(str(exc))
            return
        self._auto_refresh_timer.stop()
        self._session_sample_rate = config.audio.sample_rate
        self.error_label.hide()
        self.audio_parameters_changed.emit(config.audio.sample_rate, config.audio.sample_rate)
        self.status_message.emit("已开始测试会话")

    def stop_test(self) -> None:
        self.controller.request_stop()

    def _start_diagnostics(self) -> None:
        if (
            self.controller.is_running
            or self.diagnostics_panel.is_running
            or self._refresh_thread is not None
            or self._shutting_down
        ):
            return
        try:
            config = self.session_config()
        except ValueError as exc:
            self.diagnostics_panel.show_error(f"无法启动诊断：{exc}")
            return
        if self.diagnostics_panel.start(config):
            self._auto_refresh_timer.stop()
            self.error_label.hide()
            self.status_message.emit("已开始 Loopback 诊断（单次、未补偿）")
        self._update_controls()

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
        self._request_refresh(manual=True)

    def _auto_refresh_devices(self) -> None:
        self._request_refresh(manual=False)

    def _request_refresh(self, *, manual: bool) -> None:
        if (
            self.controller.is_running
            or self.diagnostics_panel.is_running
            or self._shutting_down
        ):
            return
        if self._refresh_thread is not None:
            return
        self._auto_refresh_timer.stop()
        current_output = self.selected_output_device_id()
        current_input = self.selected_input_device_id()
        if current_output is not None:
            self._preferred_output_id = current_output
        if current_input is not None:
            self._preferred_input_id = current_input

        self._refresh_is_manual = manual
        self.refresh_button.setEnabled(False)
        if manual:
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
        if (
            self._shutting_down
            or self.controller.is_running
            or self.diagnostics_panel.is_running
        ):
            return
        self.diagnostics_panel.update_inventory(inventory)
        recovered_from_error = self._refresh_error_active
        self._refresh_error_active = False
        self._consecutive_refresh_failures = 0
        inventory_changed = inventory != self._last_inventory
        previous_selection = (
            self.selected_output_device_id(),
            self.selected_input_device_id(),
        )
        if inventory_changed:
            self._loading_devices = True
            try:
                selected_output = self._populate_combo(
                    self.output_combo,
                    inventory.output_devices,
                    self._preferred_output_id,
                    self._preferred_output_device,
                    "未检测到播放设备",
                    is_output=True,
                )
                selected_input = self._populate_combo(
                    self.input_combo,
                    inventory.input_devices,
                    self._preferred_input_id,
                    self._preferred_input_device,
                    "未检测到录音设备",
                    is_output=False,
                )
            finally:
                self._loading_devices = False
            if selected_output is not None:
                self._preferred_output_id = selected_output.identifier
                self._preferred_output_device = selected_output
            if selected_input is not None:
                self._preferred_input_id = selected_input.identifier
                self._preferred_input_device = selected_input
            self._last_inventory = inventory
            self._reload_calibration_profiles()

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
        if inventory_changed or self._refresh_is_manual or recovered_from_error:
            self.devices_refreshed.emit(inventory)
        current_selection = (
            self.selected_output_device_id(),
            self.selected_input_device_id(),
        )
        if inventory_changed and current_selection != previous_selection:
            self.device_selection_changed.emit()
        self._update_controls()

    def _populate_combo(
        self,
        combo: QComboBox,
        devices: tuple[AudioDevice, ...],
        preferred_id: int | str | None,
        preferred_device: AudioDevice | None,
        empty_text: str,
        is_output: bool,
    ) -> AudioDevice | None:
        combo.clear()
        if not devices:
            combo.addItem(empty_text, None)
            combo.setEnabled(False)
            return None
        combo.setEnabled(True)
        for device in devices:
            combo.addItem(self._device_text(device, is_output), device.identifier)
            combo.setItemData(combo.count() - 1, device.name, _DEVICE_NAME_ROLE)
            combo.setItemData(
                combo.count() - 1,
                f"主机 API：{device.host_api}",
                Qt.ItemDataRole.ToolTipRole,
            )
        preferred_index = self._preferred_device_index(
            devices,
            preferred_id,
            preferred_device,
        )
        if preferred_index < 0 and (preferred_id is not None or preferred_device is not None):
            combo.insertItem(0, _DEVICE_UNAVAILABLE_TEXT, None)
            combo.setCurrentIndex(0)
            return None
        selected_index = preferred_index if preferred_index >= 0 else 0
        combo.setCurrentIndex(selected_index)
        return devices[selected_index]

    def _preferred_device_index(
        self,
        devices: tuple[AudioDevice, ...],
        preferred_id: int | str | None,
        preferred_device: AudioDevice | None,
    ) -> int:
        if preferred_device is not None:
            for index, device in enumerate(devices):
                if device.identifier == preferred_id and self._device_matches(
                    device, preferred_device
                ):
                    return index
            matches = [
                index
                for index, device in enumerate(devices)
                if self._device_matches(device, preferred_device)
            ]
            return matches[0] if len(matches) == 1 else -1
        if preferred_id is not None:
            return next(
                (
                    index
                    for index, device in enumerate(devices)
                    if device.identifier == preferred_id
                ),
                -1,
            )
        return -1

    @staticmethod
    def _device_matches(left: AudioDevice, right: AudioDevice) -> bool:
        return (
            left.name,
            left.host_api,
            left.max_input_channels,
            left.max_output_channels,
            left.is_simulation,
        ) == (
            right.name,
            right.host_api,
            right.max_input_channels,
            right.max_output_channels,
            right.is_simulation,
        )

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
        if self._shutting_down:
            return
        self._consecutive_refresh_failures += 1
        was_error_active = self._refresh_error_active
        self._refresh_error_active = True
        self.device_status_label.setText(f"设备刷新失败：{message}")
        if self._refresh_is_manual or not was_error_active:
            self.refresh_failed.emit(message)

    def _on_refresh_thread_finished(self) -> None:
        if self.sender() is self._refresh_thread:
            self._refresh_thread = None
            self._refresh_worker = None
            self._update_controls()
            if (
                not self._shutting_down
                and not self.controller.is_running
                and not self.diagnostics_panel.is_running
            ):
                failure_exponent = min(max(self._consecutive_refresh_failures - 1, 0), 4)
                delay_ms = min(
                    _AUTO_REFRESH_INTERVAL_MS * (2 ** failure_exponent),
                    _AUTO_REFRESH_MAX_INTERVAL_MS,
                )
                self._auto_refresh_timer.start(delay_ms)
            self.refresh_finished.emit()

    def _on_device_selection_changed(self) -> None:
        if self._loading_devices:
            return
        if self._last_inventory is not None:
            output_id = self.selected_output_device_id()
            input_id = self.selected_input_device_id()
            output_device = next(
                (
                    device
                    for device in self._last_inventory.output_devices
                    if device.identifier == output_id
                ),
                None,
            )
            input_device = next(
                (
                    device
                    for device in self._last_inventory.input_devices
                    if device.identifier == input_id
                ),
                None,
            )
            if output_device is not None:
                self._preferred_output_id = output_device.identifier
                self._preferred_output_device = output_device
            if input_device is not None:
                self._preferred_input_id = input_device.identifier
                self._preferred_input_device = input_device
        self._reload_calibration_profiles()
        self.device_selection_changed.emit()
        self._update_controls()

    def _reload_calibration_profiles(self) -> None:
        self._loading_calibration = True
        try:
            self.calibration_profile_combo.clear()
            self.calibration_profile_combo.addItem("手动补偿（未选择校准配置）", None)
            context = self.calibration_context()
            if context is not None:
                for profile in self._config.calibration.profiles:
                    if profile.matches(context):
                        self.calibration_profile_combo.addItem(
                            f"{profile.name} · {profile.compensation_ms:.2f} ms",
                            profile.profile_id,
                        )
            selected_index = self.calibration_profile_combo.findData(
                self._config.calibration.selected_profile_id
            )
            self.calibration_profile_combo.setCurrentIndex(max(selected_index, 0))
            profile = self.selected_calibration_profile()
            self.compensation_spin.setValue(
                profile.compensation_ms if profile is not None else self._manual_compensation_ms
            )
        finally:
            self._loading_calibration = False
        self._update_calibration_details()
        self._update_compensation_status()

    def _on_calibration_selection_changed(self, _index: int) -> None:
        if self._loading_calibration:
            return
        profile = self.selected_calibration_profile()
        calibration = CalibrationManager.select_profile(
            self._config.calibration,
            profile.profile_id if profile is not None else None,
        )
        self._config = replace(self._config, calibration=calibration)
        self._loading_calibration = True
        try:
            self.compensation_spin.setValue(
                profile.compensation_ms if profile is not None else self._manual_compensation_ms
            )
        finally:
            self._loading_calibration = False
        self._update_calibration_details()
        self._update_compensation_status()
        self._update_controls()
        self.calibration_changed.emit(calibration)

    def _on_manual_compensation_changed(self, value: float) -> None:
        if self._loading_calibration or self.selected_calibration_profile() is not None:
            return
        self._manual_compensation_ms = value
        calibration = CalibrationManager.use_manual_compensation(self._config.calibration, value)
        self._config = replace(self._config, calibration=calibration)
        self._update_compensation_status()
        self.calibration_changed.emit(calibration)

    def _update_calibration_details(self) -> None:
        profile = self.selected_calibration_profile()
        if profile is None:
            self.calibration_source_label.setText(
                "未选择校准配置：使用手动补偿；0 ms 明确表示未补偿"
            )
            self.calibration_source_label.setToolTip("")
            return
        notes = profile.notes or "无备注"
        self.calibration_source_label.setText(
            f"校准来源：{profile.calibration_method} · 日期：{profile.calibration_date} · 备注：{notes}"
        )
        self.calibration_source_label.setToolTip(
            f"播放：{profile.output_device}（ID {profile.output_device_id}）\n"
            f"录音：{profile.input_device}（ID {profile.input_device_id}）\n"
            f"采样率：{profile.sample_rate} Hz · 音频模式：{profile.audio_mode}"
        )

    def _update_compensation_status(
        self,
        measurement: LatencyMeasurement | None = None,
    ) -> None:
        profile = self.selected_calibration_profile()
        compensation_ms = (
            measurement.compensation_ms if measurement is not None else self.compensation_spin.value()
        )
        if profile is not None:
            source = f"校准配置“{profile.name}”"
        elif compensation_ms != 0:
            source = "手动补偿"
        else:
            source = "未补偿"
        compensated_text = (
            f" · 当前补偿后 {measurement.compensated_latency_ms:.2f} ms"
            if measurement is not None and measurement.compensated_latency_ms is not None
            else ""
        )
        simulation_text = (
            " · 模拟结果" if measurement is not None and measurement.is_simulation else ""
        )
        self.compensation_status_label.setText(
            f"补偿：{compensation_ms:.2f} ms（{source}）{compensated_text}{simulation_text}"
        )

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
        self.compensated_metric_value_labels[0].setText(
            self._format_ms(measurement.compensated_latency_ms)
        )
        self._update_statistics(self.controller.statistics)
        self._update_compensation_status(measurement)
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

    def _update_statistics(self, statistics: SessionStatistics) -> None:
        for labels, values in (
            (self.metric_value_labels, statistics.raw),
            (self.compensated_metric_value_labels, statistics.compensated),
        ):
            aggregate_values = (
                values.mean_latency_ms,
                values.median_latency_ms,
                values.p90_latency_ms,
                values.p95_latency_ms,
                values.min_latency_ms,
                values.max_latency_ms,
                values.std_latency_ms,
            )
            for label, value in zip(labels[1:], aggregate_values, strict=True):
                label.setText(self._format_ms(value))

        self.valid_count_label.setText(str(statistics.valid_count))
        self.invalid_count_label.setText(str(statistics.invalid_count))
        summary = (
            f"质量汇总：有效 {statistics.valid_count} / {statistics.total_count}，"
            f"无效 {statistics.invalid_count}；统计仅使用有效样本"
        )
        if statistics.invalid_reason_counts:
            reasons = "、".join(
                f"{_INVALID_REASON_LABELS.get(reason, reason)} × {count}"
                for reason, count in statistics.invalid_reason_counts
            )
            summary += f"；无效原因：{reasons}"
        if statistics.total_count and not statistics.meets_minimum_valid:
            summary += f"；有效样本不足最低要求的 {statistics.minimum_valid_count} 次"
        self.quality_summary_label.setText(summary)

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
        self._update_statistics(snapshot.statistics)
        if snapshot.terminal_state == TestState.COMPLETED:
            simulation_text = "（离线模拟，非真实测量）" if snapshot.is_simulation else ""
            minimum_text = (
                ""
                if snapshot.meets_minimum_valid
                else f"；有效样本不足最低要求的 {snapshot.statistics.minimum_valid_count} 次"
            )
            self.session_status_label.setText(
                f"完成 {snapshot.completed_count} 次：有效 {snapshot.valid_count}，"
                f"无效 {snapshot.invalid_count}{simulation_text}{minimum_text}；音频资源已释放"
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
        self.session_status_label.setText("正在后台保存会话和全部单次结果到 SQLite…")
        self._update_controls()
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
        self._auto_refresh_devices()

    def _on_diagnostics_finished(self) -> None:
        self._update_controls()
        self._auto_refresh_devices()

    def _clear_results(self) -> None:
        for label in (*self.metric_value_labels, *self.compensated_metric_value_labels):
            label.setText("—")
        self.valid_count_label.setText("0")
        self.invalid_count_label.setText("0")
        self.quality_summary_label.setText("质量汇总：尚无样本；统计仅使用有效样本")
        self._update_compensation_status()
        self.measurement_status_label.setText("尚无测量结果；统计仅使用有效样本，全部原始记录均保留")
        self.measurement_status_label.setToolTip("")
        self.session_status_label.setText("等待开始")
        self.error_label.hide()
        self.reference_curve.setData([], [])
        self.response_curve.setData([], [])
        self.peak_line.hide()

    def _update_controls(self) -> None:
        active = self.controller.is_running
        diagnostic_active = self.diagnostics_panel.is_running
        refreshing = self._refresh_thread is not None and self._refresh_thread.isRunning()
        self.diagnostics_panel.set_external_busy(
            active or refreshing or self._shutting_down
        )
        has_output = self.output_combo.count() > 0 and self.selected_output_device_id() is not None
        has_input = self.input_combo.count() > 0 and self.selected_input_device_id() is not None
        has_output_choices = any(
            self.output_combo.itemData(index) is not None
            for index in range(self.output_combo.count())
        )
        has_input_choices = any(
            self.input_combo.itemData(index) is not None
            for index in range(self.input_combo.count())
        )
        can_start = (
            has_output
            and has_input
            and not active
            and not diagnostic_active
            and not refreshing
            and not self.controller.save_pending
            and not self._shutting_down
        )
        self.start_button.setEnabled(can_start)
        self.stop_button.setEnabled(active and not self._stop_requested)
        self.clear_button.setEnabled(
            not active
            and not self.controller.save_pending
            and (bool(self.controller.records) or self.controller.state != TestState.IDLE)
        )
        snapshot = self.controller.snapshot
        self.save_button.setEnabled(
            not active
            and not self.controller.save_pending
            and self.controller.state in {TestState.COMPLETED, TestState.USER_STOPPED}
            and snapshot is not None
            and bool(snapshot.records)
        )
        audio_busy = active or diagnostic_active
        self.refresh_button.setEnabled(
            not audio_busy and not refreshing and not self._shutting_down
        )
        self.output_combo.setEnabled(has_output_choices and not audio_busy)
        self.input_combo.setEnabled(has_input_choices and not audio_busy)
        for widget in self._parameter_widgets:
            widget.setEnabled(not audio_busy and not self._shutting_down)
        self.compensation_spin.setEnabled(
            not audio_busy
            and not self._shutting_down
            and self.selected_calibration_profile() is None
        )

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
        self._auto_refresh_timer.stop()
        self.diagnostics_panel.shutdown()
        self.controller.shutdown()
        thread = self._refresh_thread
        if thread is not None and thread.isRunning():
            thread.quit()
            if not thread.wait(2000):
                logger.warning("设备枚举线程未在 2 秒内结束")
        self._update_controls()
