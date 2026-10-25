from dataclasses import replace

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QComboBox,
    QDoubleSpinBox,
    QFormLayout,
    QFrame,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QSpinBox,
    QVBoxLayout,
    QWidget,
)

from app.config import AppConfig


class SettingsPage(QWidget):
    save_requested = Signal()

    def __init__(self, config: AppConfig, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        root_layout = QVBoxLayout(self)
        root_layout.setContentsMargins(30, 26, 30, 24)
        root_layout.setSpacing(16)

        title = QLabel("设置")
        title.setObjectName("pageTitle")
        subtitle = QLabel("配置音频后端与测量默认值；补偿和校准配置将在后续阶段提供")
        subtitle.setObjectName("pageSubtitle")
        root_layout.addWidget(title)
        root_layout.addWidget(subtitle)

        audio_card = QFrame()
        audio_card.setObjectName("card")
        audio_layout = QVBoxLayout(audio_card)
        audio_layout.setContentsMargins(20, 18, 20, 20)
        audio_title = QLabel("音频设置")
        audio_title.setObjectName("sectionTitle")
        audio_layout.addWidget(audio_title)
        audio_form = QFormLayout()
        audio_form.setHorizontalSpacing(24)
        audio_form.setVerticalSpacing(14)
        self.backend_combo = QComboBox()
        self.backend_combo.addItem("真实音频设备（sounddevice / PortAudio）", "sounddevice")
        self.backend_combo.addItem("离线模拟（仅用于界面与流程验证）", "simulation")
        backend_index = self.backend_combo.findData(config.audio.backend)
        self.backend_combo.setCurrentIndex(max(backend_index, 0))
        audio_form.addRow("音频后端", self.backend_combo)

        self.sample_rate_spin = QSpinBox()
        self.sample_rate_spin.setRange(8000, 192000)
        self.sample_rate_spin.setSingleStep(1000)
        self.sample_rate_spin.setSuffix(" Hz")
        self.sample_rate_spin.setValue(config.audio.sample_rate)
        audio_form.addRow("首选采样率", self.sample_rate_spin)
        audio_layout.addLayout(audio_form)
        root_layout.addWidget(audio_card)

        measurement_card = QFrame()
        measurement_card.setObjectName("card")
        measurement_layout = QVBoxLayout(measurement_card)
        measurement_layout.setContentsMargins(20, 18, 20, 20)
        measurement_title = QLabel("测量默认值")
        measurement_title.setObjectName("sectionTitle")
        measurement_layout.addWidget(measurement_title)
        measurement_form = QFormLayout()
        measurement_form.setHorizontalSpacing(24)
        measurement_form.setVerticalSpacing(14)
        self.test_count_spin = QSpinBox()
        self.test_count_spin.setRange(1, 500)
        self.test_count_spin.setSuffix(" 次")
        self.test_count_spin.setValue(config.measurement.default_test_count)
        measurement_form.addRow("默认测试次数", self.test_count_spin)

        self.warmup_spin = QDoubleSpinBox()
        self.warmup_spin.setRange(1.0, 15.0)
        self.warmup_spin.setSingleStep(0.5)
        self.warmup_spin.setDecimals(1)
        self.warmup_spin.setSuffix(" s")
        self.warmup_spin.setValue(config.measurement.warmup_seconds)
        measurement_form.addRow("连续流预热时间", self.warmup_spin)
        measurement_layout.addLayout(measurement_form)
        root_layout.addWidget(measurement_card)

        note = QLabel("后端切换将在下次启动后生效。模拟设备不会生成或冒充真实测量数据。")
        note.setObjectName("mutedLabel")
        note.setWordWrap(True)
        root_layout.addWidget(note)
        root_layout.addStretch()

        action_layout = QHBoxLayout()
        self.save_status_label = QLabel()
        self.save_status_label.setObjectName("mutedLabel")
        self.save_button = QPushButton("保存设置")
        self.save_button.setObjectName("primaryButton")
        self.save_button.setCursor(Qt.CursorShape.PointingHandCursor)
        action_layout.addWidget(self.save_status_label, 1)
        action_layout.addWidget(self.save_button)
        root_layout.addLayout(action_layout)
        self.save_button.clicked.connect(self.save_requested)

    def apply_to(self, config: AppConfig) -> AppConfig:
        return replace(
            config,
            audio=replace(
                config.audio,
                backend=self.backend_combo.currentData(),
                sample_rate=self.sample_rate_spin.value(),
            ),
            measurement=replace(
                config.measurement,
                default_test_count=self.test_count_spin.value(),
                warmup_seconds=self.warmup_spin.value(),
            ),
        )

    def set_save_enabled(self, enabled: bool) -> None:
        self.save_button.setEnabled(enabled)

    def show_save_status(self, message: str) -> None:
        self.save_status_label.setText(message)
