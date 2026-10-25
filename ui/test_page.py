import logging

from PySide6.QtCore import Qt, QThread, Signal
from PySide6.QtWidgets import (
    QComboBox,
    QFrame,
    QGridLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QSizePolicy,
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

logger = logging.getLogger(__name__)


class TestPage(QWidget):
    devices_refreshed = Signal(object)
    refresh_failed = Signal(str)
    refresh_finished = Signal()
    device_selection_changed = Signal()

    def __init__(
        self,
        device_service: DeviceService,
        config: AppConfig,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self._device_service = device_service
        self._refresh_thread: QThread | None = None
        self._refresh_worker: DeviceEnumerationWorker | None = None
        self._loading_devices = False
        self._preferred_output_id = config.audio.output_device_id
        self._preferred_input_id = config.audio.input_device_id
        self._build_ui()
        self.refresh_button.clicked.connect(self.refresh_devices)
        self.output_combo.currentIndexChanged.connect(self._on_device_selection_changed)
        self.input_combo.currentIndexChanged.connect(self._on_device_selection_changed)

    def _build_ui(self) -> None:
        root_layout = QVBoxLayout(self)
        root_layout.setContentsMargins(30, 26, 30, 24)
        root_layout.setSpacing(16)

        title = QLabel("声学延迟测试")
        title.setObjectName("pageTitle")
        subtitle = QLabel("在连续音频流中测量耳机输出到麦克风响应的端到端延迟")
        subtitle.setObjectName("pageSubtitle")
        root_layout.addWidget(title)
        root_layout.addWidget(subtitle)

        device_card = QFrame()
        device_card.setObjectName("card")
        device_layout = QGridLayout(device_card)
        device_layout.setContentsMargins(20, 18, 20, 18)
        device_layout.setHorizontalSpacing(16)
        device_layout.setVerticalSpacing(12)

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

        self.simulation_label = QLabel("离线模拟模式：当前设备仅用于界面验证，不产生真实声学测量结果。")
        self.simulation_label.setObjectName("simulationBanner")
        self.simulation_label.setWordWrap(True)
        self.simulation_label.hide()
        root_layout.addWidget(self.simulation_label)

        overview = QGroupBox("测量概览")
        overview_layout = QGridLayout(overview)
        overview_layout.setContentsMargins(20, 24, 20, 18)
        overview_layout.setHorizontalSpacing(20)
        overview_layout.setVerticalSpacing(8)
        metric_titles = ["当前延迟", "平均延迟", "中位数", "P95", "最小值", "最大值", "标准差", "有效样本"]
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
        root_layout.addWidget(overview)

        waveform_placeholder = QFrame()
        waveform_placeholder.setObjectName("placeholderPanel")
        waveform_layout = QVBoxLayout(waveform_placeholder)
        waveform_layout.setContentsMargins(20, 20, 20, 20)
        waveform_title = QLabel("波形与相关分析")
        waveform_title.setObjectName("sectionTitle")
        waveform_text = QLabel("参考波形、麦克风响应与相关峰值将在测量模块接入后显示。")
        waveform_text.setObjectName("mutedLabel")
        waveform_text.setAlignment(Qt.AlignmentFlag.AlignCenter)
        waveform_layout.addWidget(waveform_title)
        waveform_layout.addStretch()
        waveform_layout.addWidget(waveform_text)
        waveform_layout.addStretch()
        root_layout.addWidget(waveform_placeholder, 1)

        controls = QHBoxLayout()
        controls.setSpacing(10)
        self.start_button = QPushButton("开始测试")
        self.start_button.setObjectName("primaryButton")
        self.stop_button = QPushButton("停止")
        self.clear_button = QPushButton("清空当前会话")
        self.save_button = QPushButton("保存会话")
        for button in (self.start_button, self.stop_button, self.clear_button, self.save_button):
            button.setEnabled(False)
            button.setToolTip("测试控制器将在后续阶段接入")
            controls.addWidget(button)
        controls.addStretch()
        root_layout.addLayout(controls)

    def selected_output_device_id(self) -> int | str | None:
        return self.output_combo.currentData()

    def selected_input_device_id(self) -> int | str | None:
        return self.input_combo.currentData()

    def refresh_devices(self) -> None:
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
        self.device_status_label.setText(
            f"{inventory.backend_name} · 检测到 {len(inventory.output_devices)} 个播放设备、"
            f"{len(inventory.input_devices)} 个录音设备"
        )
        self.devices_refreshed.emit(inventory)
        self.device_selection_changed.emit()

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
            combo.setItemData(combo.count() - 1, f"主机 API：{device.host_api}", Qt.ItemDataRole.ToolTipRole)
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
            self.refresh_button.setEnabled(True)
            self.refresh_finished.emit()

    def _on_device_selection_changed(self) -> None:
        if self._loading_devices:
            return
        self._preferred_output_id = self.selected_output_device_id()
        self._preferred_input_id = self.selected_input_device_id()
        self.device_selection_changed.emit()

    def shutdown(self) -> None:
        thread = self._refresh_thread
        if thread is not None and thread.isRunning():
            thread.quit()
            if not thread.wait(2000):
                logger.warning("设备枚举线程未在 2 秒内结束")
