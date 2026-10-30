from dataclasses import replace
from enum import Enum

import numpy as np
from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QComboBox,
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QProgressBar,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from core.device_service import DeviceInventory, DeviceService
from core.test_controller import ControllerBusyError, TestController
from core.test_models import MeasurementRecord, TestSessionConfig, TestSessionSnapshot
from core.test_state_machine import TestState


class DiagnosticMode(str, Enum):
    SOFTWARE = "software"
    ELECTRICAL = "electrical"
    ACOUSTIC = "acoustic"


_MODE_LABELS = {
    DiagnosticMode.SOFTWARE: "Software Loop（WASAPI Loopback）",
    DiagnosticMode.ELECTRICAL: "Electrical Loop（硬件电气环回）",
    DiagnosticMode.ACOUSTIC: "Acoustic Loop（声学环回）",
}

_MODE_DESCRIPTIONS = {
    DiagnosticMode.SOFTWARE: (
        "捕获所选播放设备的数字音频回环，不经过 DAC、耳机单元、空气或麦克风。"
        "结果仅用于检查软件音频链路、缓冲区和分析算法，不能代替真实声学结果。"
    ),
    DiagnosticMode.ELECTRICAL: (
        "将物理音频输出连接至 Line In 或 USB Audio Interface 输入。请使用合适的线路电平或衰减，"
        "从低输出电平开始并降低输入增益，避免过载。结果是所选播放—采集设备的电气往返延迟，"
        "不是耳机声学延迟。"
    ),
    DiagnosticMode.ACOUSTIC: (
        "使用耳机播放 Chirp，由外置麦克风采集。保持耳机与麦克风位置固定并降低环境噪声；"
        "结果包含播放设备、耳机单元、空气传播和麦克风采集的完整声学链路延迟。"
    ),
}

_RESULT_LABELS = {
    DiagnosticMode.SOFTWARE: "软件偏移（原始 · 未补偿）",
    DiagnosticMode.ELECTRICAL: "电气往返延迟（原始 · 未补偿）",
    DiagnosticMode.ACOUSTIC: "声学链路延迟（原始 · 未补偿）",
}

_STATE_LABELS = {
    TestState.IDLE: "空闲",
    TestState.DEVICE_CHECK: "检查设备",
    TestState.OPENING_AUDIO: "打开音频流",
    TestState.WARMUP: "连续流预热",
    TestState.READY: "就绪",
    TestState.RUNNING: "测量中",
    TestState.ANALYZING: "分析中",
    TestState.WAIT_NEXT: "等待处理",
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
    "software_clock_domain_missing": "播放或数字环回缺少共享时钟域标识",
    "software_clock_domain_mismatch": "播放与数字环回的时钟域不一致",
    "stream_clock_domain_missing": "播放或采集流没有已验证的共同时间基准",
    "stream_clock_domain_mismatch": "播放与采集流的时间基准不一致",
}

_TIMING_BASIS_LABELS = {
    "dac_adc_stream_timestamps": "DAC/ADC 流时间戳 + 样本索引映射",
    "dac_adc_stream_timestamps_unavailable": "DAC/ADC 时间锚点不可用",
    "wasapi_digital_loopback_stream_timestamps": "WASAPI 数字环回 DAC/ADC 流时间戳 + 样本索引映射",
    "wasapi_digital_loopback_timestamps_unavailable": "WASAPI 数字环回时间锚点或共享时钟域不可用",
    "cross_stream_clock_unverified": "独立流的共同时间基准未验证",
    "wasapi_digital_loopback_cross_stream_clock_unverified": "WASAPI 独立流的共同时间基准未验证",
    "relative_sample_index": "仅相对样本索引，缺少完整硬件时间映射",
}

_TIMING_LIMITATION_LABELS = {
    "capture_clock_offset_uncalibrated": "采集时钟偏移未校准",
    "playback_sample_rate_drift_uncalibrated": "播放采样率漂移未校准",
    "capture_sample_rate_drift_uncalibrated": "采集采样率漂移未校准",
    "playback_or_capture_time_anchor_unavailable": "播放或采集时间锚点不可用",
    "software_clock_domain_missing": "播放或数字环回缺少共享时钟域标识",
    "software_clock_domain_mismatch": "播放与数字环回的时钟域不一致",
    "stream_clock_domain_missing": "播放或采集流没有已验证的共同时间基准",
    "stream_clock_domain_mismatch": "播放与采集流的时间基准不一致",
    "relative_sample_time_only": "只有相对样本时间",
}

_CLIPPING_WARNING_THRESHOLD = 0.98


class DiagnosticsPanel(QFrame):
    start_requested = Signal()
    finished = Signal()

    def __init__(
        self,
        device_service: DeviceService,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.setObjectName("card")
        self._inventory: DeviceInventory | None = None
        self._records: dict[DiagnosticMode, MeasurementRecord] = {}
        self._configs: dict[DiagnosticMode, TestSessionConfig] = {}
        self._active_mode: DiagnosticMode | None = None
        self._external_busy = False
        self._stop_requested = False
        self._shutting_down = False
        self.controller = TestController(device_service, self)
        self._build_ui()
        self.controller.state_changed.connect(self._on_state_changed)
        self.controller.progress_changed.connect(self._on_progress_changed)
        self.controller.warmup_progress.connect(self._on_warmup_progress)
        self.controller.measurement_ready.connect(self._on_measurement)
        self.controller.error_occurred.connect(self._on_error)
        self.controller.session_finished.connect(self._on_session_finished)
        self.controller.stop_requested.connect(self._on_stop_requested)
        self.controller.finished.connect(self._on_controller_finished)
        self.mode_combo.currentIndexChanged.connect(self._on_mode_changed)
        self.start_button.clicked.connect(self.start_requested.emit)
        self.stop_button.clicked.connect(self.stop)
        self._render_current_mode()

    @property
    def is_running(self) -> bool:
        return self.controller.is_running

    @property
    def current_mode(self) -> DiagnosticMode:
        data = self.mode_combo.currentData()
        return data if isinstance(data, DiagnosticMode) else DiagnosticMode(data)

    def record_for(self, mode: DiagnosticMode) -> MeasurementRecord | None:
        return self._records.get(mode)

    def config_for(self, mode: DiagnosticMode) -> TestSessionConfig | None:
        return self._configs.get(mode)

    def _build_ui(self) -> None:
        root_layout = QVBoxLayout(self)
        root_layout.setContentsMargins(20, 14, 20, 14)
        root_layout.setSpacing(9)

        title = QLabel("Loopback Diagnostics（环回自检）")
        title.setObjectName("sectionTitle")
        self.state_label = QLabel("空闲")
        self.state_label.setObjectName("statusBadge")
        header = QHBoxLayout()
        header.addWidget(title)
        header.addStretch()
        header.addWidget(self.state_label)
        root_layout.addLayout(header)

        subtitle = QLabel(
            "分别排查软件、电气和声学路径；每次单次测量且不应用校准补偿，结果仅保留在本面板，不进入历史统计。"
        )
        subtitle.setObjectName("mutedLabel")
        subtitle.setWordWrap(True)
        root_layout.addWidget(subtitle)

        mode_label = QLabel("诊断模式")
        mode_label.setObjectName("fieldLabel")
        self.mode_combo = QComboBox()
        for mode in DiagnosticMode:
            self.mode_combo.addItem(_MODE_LABELS[mode], mode)
        mode_row = QHBoxLayout()
        mode_row.addWidget(mode_label)
        mode_row.addWidget(self.mode_combo, 1)
        root_layout.addLayout(mode_row)

        self.mode_description_label = QLabel()
        self.mode_description_label.setObjectName("mutedLabel")
        self.mode_description_label.setWordWrap(True)
        root_layout.addWidget(self.mode_description_label)

        self.support_label = QLabel("正在读取设备与后端能力")
        self.support_label.setObjectName("mutedLabel")
        self.support_label.setWordWrap(True)
        root_layout.addWidget(self.support_label)

        results_grid = QGridLayout()
        results_grid.setHorizontalSpacing(16)
        results_grid.setVerticalSpacing(6)
        self.result_kind_label = QLabel()
        self.result_kind_label.setObjectName("fieldLabel")
        results_grid.addWidget(self.result_kind_label, 0, 0, 1, 4)
        self.latency_value_label = QLabel("—")
        self.correlation_value_label = QLabel("—")
        self.snr_value_label = QLabel("—")
        self.amplitude_value_label = QLabel("—")
        metric_widgets = (
            ("延迟", self.latency_value_label),
            ("相关峰值", self.correlation_value_label),
            ("SNR", self.snr_value_label),
            ("响应幅度（RMS）", self.amplitude_value_label),
        )
        for column, (name, value_label) in enumerate(metric_widgets):
            name_label = QLabel(name)
            name_label.setObjectName("metricName")
            value_label.setObjectName("metricValue")
            results_grid.addWidget(name_label, 1, column)
            results_grid.addWidget(value_label, 2, column)
        root_layout.addLayout(results_grid)

        self.quality_label = QLabel("尚未执行当前模式诊断")
        self.quality_label.setObjectName("mutedLabel")
        self.quality_label.setWordWrap(True)
        root_layout.addWidget(self.quality_label)

        self.invalid_reason_label = QLabel()
        self.invalid_reason_label.setObjectName("errorLabel")
        self.invalid_reason_label.setWordWrap(True)
        self.invalid_reason_label.hide()
        root_layout.addWidget(self.invalid_reason_label)

        self.config_label = QLabel("测试配置：—")
        self.config_label.setObjectName("mutedLabel")
        self.config_label.setWordWrap(True)
        root_layout.addWidget(self.config_label)

        self.timing_label = QLabel("时间依据：—")
        self.timing_label.setObjectName("mutedLabel")
        self.timing_label.setWordWrap(True)
        root_layout.addWidget(self.timing_label)

        self.advice_label = QLabel("诊断建议：等待测量结果")
        self.advice_label.setObjectName("mutedLabel")
        self.advice_label.setWordWrap(True)
        root_layout.addWidget(self.advice_label)

        self.progress_bar = QProgressBar()
        self.progress_bar.setRange(0, 1)
        self.progress_bar.setValue(0)
        self.progress_bar.setFormat("尚无诊断测量")
        root_layout.addWidget(self.progress_bar)

        self.error_label = QLabel()
        self.error_label.setObjectName("errorLabel")
        self.error_label.setWordWrap(True)
        self.error_label.hide()
        root_layout.addWidget(self.error_label)

        controls = QHBoxLayout()
        self.start_button = QPushButton("开始当前诊断")
        self.start_button.setObjectName("primaryButton")
        self.stop_button = QPushButton("停止诊断")
        for button in (self.start_button, self.stop_button):
            button.setCursor(Qt.CursorShape.PointingHandCursor)
            controls.addWidget(button)
        controls.addStretch()
        root_layout.addLayout(controls)

    def update_inventory(self, inventory: DeviceInventory) -> None:
        previous = self._inventory
        self._inventory = inventory
        if previous is not None and (
            previous.backend_name != inventory.backend_name
            or previous.is_simulation != inventory.is_simulation
        ):
            self._records.clear()
            self._configs.clear()
        self._render_current_mode()

    def set_external_busy(self, busy: bool) -> None:
        self._external_busy = busy
        self._update_controls()

    def start(self, config: TestSessionConfig) -> bool:
        if self.is_running:
            self.show_error("诊断仍在运行或正在收尾，请等待音频资源释放")
            return False
        if self._external_busy or self._shutting_down:
            self.show_error("请等待普通测量或设备刷新结束后再启动诊断")
            return False
        mode = self.current_mode
        unavailable = self._unavailable_reason(mode, config)
        if unavailable is not None:
            self.show_error(f"当前模式不可执行：{unavailable}")
            return False

        diagnostic_config = replace(
            config,
            audio=(
                replace(config.audio, input_device_id=None)
                if mode is DiagnosticMode.SOFTWARE
                else config.audio
            ),
            measurement=replace(
                config.measurement,
                default_test_count=1,
                minimum_valid_count=1,
            ),
            compensation_ms=0.0,
            calibration_profile=None,
            input_device_name="" if mode is DiagnosticMode.SOFTWARE else config.input_device_name,
            diagnostic_mode=mode.value,
            session_name=f"Loopback Diagnostics / {mode.value}",
            notes=_MODE_DESCRIPTIONS[mode],
        )
        self._records.pop(mode, None)
        self._configs[mode] = diagnostic_config
        self._active_mode = mode
        self._stop_requested = False
        self.error_label.hide()
        self._show_empty_result()
        self._update_config_summary(diagnostic_config)
        try:
            self.controller.start(diagnostic_config)
        except (ControllerBusyError, ValueError) as exc:
            self._active_mode = None
            self.show_error(f"无法启动诊断：{exc}")
            return False
        self._update_controls()
        return True

    def stop(self) -> None:
        self.controller.request_stop()

    def show_error(self, message: str) -> None:
        self.error_label.setText(message)
        self.error_label.show()

    def shutdown(self) -> bool:
        if self._shutting_down:
            return True
        if not self.controller.shutdown():
            self._update_controls()
            return False
        self._shutting_down = True
        self._update_controls()
        return True

    def resume_after_shutdown(self) -> None:
        self._shutting_down = False
        self._update_controls()

    def _unavailable_reason(
        self,
        mode: DiagnosticMode,
        config: TestSessionConfig | None = None,
    ) -> str | None:
        inventory = self._inventory
        if inventory is None:
            return "设备列表尚未读取完成"
        if inventory.is_simulation or (config is not None and config.is_simulation):
            return "离线模拟后端不提供真实环回诊断，不会生成替代测量结果"
        capability = inventory.software_loopback_capability
        if mode is DiagnosticMode.SOFTWARE and not capability.supported:
            return capability.detail
        if not inventory.output_devices:
            return "未检测到可用播放设备"
        if mode is not DiagnosticMode.SOFTWARE and not inventory.input_devices:
            return "未检测到可用采集设备"
        return None

    def _on_mode_changed(self, _index: int) -> None:
        self.error_label.hide()
        self._render_current_mode()

    def _render_current_mode(self) -> None:
        mode = self.current_mode
        self.mode_description_label.setText(_MODE_DESCRIPTIONS[mode])
        self.result_kind_label.setText(_RESULT_LABELS[mode])
        record = self._records.get(mode)
        config = self._configs.get(mode)
        if record is not None and config is not None:
            self._show_record(mode, record, config)
        else:
            self._show_empty_result()
            if config is not None:
                self._update_config_summary(config)
        if not self.is_running:
            self.progress_bar.setRange(0, 1)
            self.progress_bar.setValue(0)
            self.progress_bar.setFormat("尚无诊断测量")
        unavailable = self._unavailable_reason(mode)
        self.support_label.setText(
            f"当前不可执行：{unavailable}"
            if unavailable is not None
            else "当前模式可执行真实测量；不会自动切换到其他设备或模拟后端"
        )
        self._update_controls()

    def _show_empty_result(self) -> None:
        self.latency_value_label.setText("—")
        self.correlation_value_label.setText("—")
        self.snr_value_label.setText("—")
        self.amplitude_value_label.setText("—")
        self.quality_label.setText("尚未执行当前模式诊断")
        self.invalid_reason_label.hide()
        self.invalid_reason_label.clear()
        self.config_label.setText("测试配置：—")
        self.timing_label.setText("时间依据：—")
        self.timing_label.setToolTip("")
        self.advice_label.setText("诊断建议：等待测量结果")

    def _show_record(
        self,
        mode: DiagnosticMode,
        record: MeasurementRecord,
        config: TestSessionConfig,
    ) -> None:
        measurement = record.measurement
        self.result_kind_label.setText(_RESULT_LABELS[mode])
        self.latency_value_label.setText(self._format_ms(measurement.raw_latency_ms))
        self.correlation_value_label.setText(f"{measurement.correlation_peak:.3f}")
        self.snr_value_label.setText(self._format_snr(measurement.snr_db))
        self.amplitude_value_label.setText(f"{measurement.signal_amplitude:.4f}")
        self.quality_label.setText(
            "信号质量：有效，满足当前 SNR、相关性与延迟范围阈值"
            if measurement.is_valid
            else "信号质量：无效，仅作排查参考；原始记录已保留"
        )
        reasons = measurement.invalid_reason or ""
        reason_text = "、".join(
            _INVALID_REASON_LABELS.get(reason, reason)
            for reason in reasons.split(";")
            if reason
        )
        self.invalid_reason_label.setText(f"无效原因：{reason_text}")
        self.invalid_reason_label.setVisible(bool(reason_text))
        self._update_config_summary(config)
        basis = _TIMING_BASIS_LABELS.get(measurement.timing_basis, measurement.timing_basis)
        limitations = "、".join(
            _TIMING_LIMITATION_LABELS.get(item, item)
            for item in measurement.timing_limitations
        )
        self.timing_label.setText(
            f"时间依据：{basis}；限制：{limitations or '无额外标记'}；补偿：0.00 ms（未补偿）"
        )
        self.timing_label.setToolTip(
            f"播放时间戳：{measurement.playback_timestamp_seconds}\n"
            f"采集时间戳：{measurement.capture_timestamp_seconds}"
        )
        self.advice_label.setText(f"诊断建议：{self._diagnostic_advice(mode, record)}")

    def _update_config_summary(self, config: TestSessionConfig) -> None:
        audio = config.audio
        measurement = config.measurement
        blocksize = str(audio.blocksize) if audio.blocksize else "自动"
        route = (
            f"播放 {config.output_device_name}（ID {audio.output_device_id}） → 其 WASAPI 数字环回"
            if config.diagnostic_mode == "software"
            else f"播放 {config.output_device_name}（ID {audio.output_device_id}） → "
            f"采集 {config.input_device_name}（ID {audio.input_device_id}）"
        )
        self.config_label.setText(
            f"测试配置：{route}；"
            f"{audio.sample_rate} Hz，输出/输入 {audio.output_channels}/{audio.input_channels} 声道，"
            f"块大小 {blocksize}；预热 {measurement.warmup_seconds:g} s；"
            f"Chirp {measurement.stimulus_start_frequency_hz:g}–"
            f"{measurement.stimulus_end_frequency_hz:g} Hz / {measurement.stimulus_duration_ms:g} ms / "
            f"幅度 {measurement.stimulus_amplitude:g}；1 次，0 ms 未补偿"
        )

    def _diagnostic_advice(
        self,
        mode: DiagnosticMode,
        record: MeasurementRecord,
    ) -> str:
        measurement = record.measurement
        reasons = set((measurement.invalid_reason or "").split(";"))
        advice: list[str] = []
        if "no_signal" in reasons:
            if mode is DiagnosticMode.ELECTRICAL:
                advice.append("检查环回线、Line In/USB 接口输入选择、静音和增益")
            elif mode is DiagnosticMode.ACOUSTIC:
                advice.append("检查耳机发声、外置麦克风连接、采集输入选择和增益")
            else:
                advice.append("检查播放设备数字回环路由和采集格式")
        if "low_snr" in reasons:
            advice.append("提高有效测试信号相对环境噪声的比例，但不要使输入过载")
        if reasons & {"low_correlation", "ambiguous_correlation"}:
            advice.append("检查信号路由、反馈/回声和重复响应，保持测试布置稳定")
        if reasons & {"capture_timeout", "capture_too_short", "stimulus_not_completed"}:
            advice.append("检查播放/采集流是否持续运行以及是否发生设备断开或缓冲错误")
        if reasons & {
            "capture_timeline_discontinuous",
            "missing_playback_timestamp",
            "missing_capture_timestamp",
            "software_clock_domain_missing",
            "software_clock_domain_mismatch",
            "stream_clock_domain_missing",
            "stream_clock_domain_mismatch",
        }:
            advice.append("优先排查采集丢帧、DAC/ADC 时间锚点和共享时钟域，当前延迟不能作为链路结论")
        if "latency_out_of_range" in reasons:
            advice.append("核对设备选择、信号路由和有效延迟范围，原始超范围结果已保留")
        captured = record.captured
        if captured.size and float(np.max(np.abs(captured))) >= _CLIPPING_WARNING_THRESHOLD:
            advice.append("采集样本接近满幅，可能削波；请降低输出电平或输入增益后复测")
        if not advice:
            if mode is DiagnosticMode.ELECTRICAL:
                advice.append("结果只表示当前电气链路，不替代耳机声学延迟测试")
            elif mode is DiagnosticMode.ACOUSTIC:
                advice.append("结果包含完整声学路径，可与同设备电气结果对照，但需保留时钟误差边界")
            else:
                advice.append("结果只表示软件数字路径偏移，不包含 DAC、耳机、空气或麦克风")
        return "；".join(dict.fromkeys(advice))

    def _on_state_changed(self, state: TestState) -> None:
        self.state_label.setText(_STATE_LABELS.get(state, str(state)))
        if state is TestState.WARMUP:
            self.quality_label.setText("连续背景音预热中，暂不进行正式诊断测量")
        elif state is TestState.RUNNING:
            self.quality_label.setText("正在连续流中调度 Chirp 并采集响应")
        elif state is TestState.ANALYZING:
            self.quality_label.setText("正在后台线程执行互相关与信号质量分析")
        self._update_controls()

    def _on_progress_changed(self, completed: int, total: int) -> None:
        self.progress_bar.setRange(0, max(total, 1))
        self.progress_bar.setValue(completed if total else 0)
        self.progress_bar.setFormat("%v / %m 次" if total else "尚无诊断测量")

    def _on_warmup_progress(self, remaining: float, total: float) -> None:
        self.progress_bar.setRange(0, 1000)
        elapsed_ratio = min(max((total - remaining) / total, 0.0), 1.0)
        self.progress_bar.setValue(round(elapsed_ratio * 1000))
        self.progress_bar.setFormat(f"预热剩余 {remaining:.1f} s")

    def _on_measurement(self, record: MeasurementRecord) -> None:
        mode = self._active_mode
        if mode is None:
            return
        self._records[mode] = record
        config = self._configs[mode]
        if mode is self.current_mode:
            self._show_record(mode, record, config)

    def _on_error(self, message: str) -> None:
        self.show_error(f"设备 / 音频错误：{message}")

    def _on_session_finished(self, snapshot: TestSessionSnapshot) -> None:
        if snapshot.records:
            return
        if snapshot.terminal_state is TestState.COMPLETED:
            self.quality_label.setText("诊断结束但没有测量记录，请查看错误信息")
        elif snapshot.terminal_state is TestState.USER_STOPPED:
            self.quality_label.setText("诊断已停止，音频资源已释放；尚无已完成记录")

    def _on_stop_requested(self) -> None:
        self._stop_requested = True
        if self._active_mode is None or self._records.get(self._active_mode) is None:
            self.quality_label.setText("正在停止调度、结束处理并关闭音频流")
        self._update_controls()

    def _on_controller_finished(self) -> None:
        self._active_mode = None
        self._stop_requested = False
        self._update_controls()
        self.finished.emit()

    def _update_controls(self) -> None:
        if not hasattr(self, "start_button"):
            return
        active = self.is_running
        unavailable = self._unavailable_reason(self.current_mode)
        self.start_button.setEnabled(
            not active
            and not self._external_busy
            and not self._shutting_down
            and unavailable is None
        )
        self.stop_button.setEnabled(active and not self._stop_requested)
        self.mode_combo.setEnabled(not active and not self._shutting_down)

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
