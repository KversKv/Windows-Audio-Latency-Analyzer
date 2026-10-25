from dataclasses import replace

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QComboBox,
    QDoubleSpinBox,
    QFormLayout,
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QSpinBox,
    QVBoxLayout,
    QWidget,
)

from app.config import AppConfig, CalibrationContext
from core.calibration_manager import CalibrationManager


class SettingsPage(QWidget):
    save_requested = Signal()
    calibration_profile_save_requested = Signal(object)

    def __init__(self, config: AppConfig, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._config = config
        self._calibration_context: CalibrationContext | None = None
        root_layout = QVBoxLayout(self)
        root_layout.setContentsMargins(30, 26, 30, 24)
        root_layout.setSpacing(16)

        title = QLabel("设置")
        title.setObjectName("pageTitle")
        subtitle = QLabel("配置音频后端、测量默认值和可追溯的手动校准配置")
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

        self.minimum_valid_count_spin = QSpinBox()
        self.minimum_valid_count_spin.setRange(1, config.measurement.default_test_count)
        self.minimum_valid_count_spin.setSuffix(" 次")
        self.minimum_valid_count_spin.setValue(config.measurement.minimum_valid_count)
        self.minimum_valid_count_spin.setToolTip("仅用于判定本次有效样本是否充足，不会删除无效样本")
        measurement_form.addRow("最少有效样本", self.minimum_valid_count_spin)
        self.test_count_spin.valueChanged.connect(self.minimum_valid_count_spin.setMaximum)

        self.warmup_spin = QDoubleSpinBox()
        self.warmup_spin.setRange(1.0, 15.0)
        self.warmup_spin.setSingleStep(0.5)
        self.warmup_spin.setDecimals(1)
        self.warmup_spin.setSuffix(" s")
        self.warmup_spin.setValue(config.measurement.warmup_seconds)
        measurement_form.addRow("连续流预热时间", self.warmup_spin)
        measurement_layout.addLayout(measurement_form)
        root_layout.addWidget(measurement_card)

        calibration_card = QFrame()
        calibration_card.setObjectName("card")
        calibration_layout = QGridLayout(calibration_card)
        calibration_layout.setContentsMargins(20, 16, 20, 18)
        calibration_layout.setHorizontalSpacing(14)
        calibration_layout.setVerticalSpacing(8)
        calibration_title = QLabel("校准配置")
        calibration_title.setObjectName("sectionTitle")
        calibration_layout.addWidget(calibration_title, 0, 0, 1, 3)

        self.calibration_context_label = QLabel("尚未关联当前测试设备")
        self.calibration_context_label.setObjectName("mutedLabel")
        self.calibration_context_label.setWordWrap(True)
        calibration_layout.addWidget(self.calibration_context_label, 1, 0, 1, 3)

        self.calibration_profile_view_combo = QComboBox()
        self.calibration_profile_view_combo.setMinimumContentsLength(24)
        calibration_layout.addWidget(self.calibration_profile_view_combo, 2, 0)
        self.calibration_detail_label = QLabel("尚无校准配置；未选择校准时补偿为 0 ms")
        self.calibration_detail_label.setObjectName("mutedLabel")
        self.calibration_detail_label.setWordWrap(True)
        calibration_layout.addWidget(self.calibration_detail_label, 2, 1, 1, 2)

        self.calibration_name_edit = QLineEdit()
        self.calibration_name_edit.setPlaceholderText("例如：声卡环回 48 kHz")
        self.calibration_compensation_spin = QDoubleSpinBox()
        self.calibration_compensation_spin.setRange(-1000.0, 1000.0)
        self.calibration_compensation_spin.setDecimals(2)
        self.calibration_compensation_spin.setSuffix(" ms")
        self.calibration_compensation_spin.setValue(0.0)
        self.calibration_method_edit = QLineEdit()
        self.calibration_method_edit.setPlaceholderText("例如：外部声卡环回实测")
        for column, (text, widget) in enumerate(
            (
                ("配置名称", self.calibration_name_edit),
                ("手动补偿值", self.calibration_compensation_spin),
                ("校准方法 / 来源", self.calibration_method_edit),
            )
        ):
            label = QLabel(text)
            label.setObjectName("fieldLabel")
            calibration_layout.addWidget(label, 3, column)
            calibration_layout.addWidget(widget, 4, column)

        calibration_layout.addWidget(QLabel("备注"), 5, 0)
        self.calibration_notes_edit = QLineEdit()
        self.calibration_notes_edit.setPlaceholderText("记录校准装置、条件或限制（可选）")
        calibration_layout.addWidget(self.calibration_notes_edit, 6, 0, 1, 2)
        self.save_calibration_button = QPushButton("保存为新校准配置")
        self.save_calibration_button.setObjectName("secondaryButton")
        self.save_calibration_button.setCursor(Qt.CursorShape.PointingHandCursor)
        calibration_layout.addWidget(self.save_calibration_button, 6, 2)
        root_layout.addWidget(calibration_card)

        note = QLabel("后端切换将在下次启动后生效。校准值只来自用户输入，不会自动估计或猜测。")
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
        self.save_calibration_button.clicked.connect(self._request_profile_save)
        self.calibration_profile_view_combo.currentIndexChanged.connect(
            self._show_selected_profile_details
        )
        self._reload_calibration_profiles()

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
                minimum_valid_count=self.minimum_valid_count_spin.value(),
                warmup_seconds=self.warmup_spin.value(),
            ),
        )

    def update_config(self, config: AppConfig) -> None:
        self._config = config
        self._reload_calibration_profiles()

    def set_calibration_context(self, context: CalibrationContext | None) -> None:
        self._calibration_context = context
        if context is None:
            self.calibration_context_label.setText("尚未关联当前测试设备，请先刷新设备列表")
            return
        mode_label = {
            "wasapi_shared": "WASAPI 共享",
            "simulation": "离线模拟",
        }.get(context.audio_mode, context.audio_mode)
        self.calibration_context_label.setText(
            f"新配置关联：{context.output_device}（ID {context.output_device_id}） / "
            f"{context.input_device}（ID {context.input_device_id}） · "
            f"{context.sample_rate} Hz · {mode_label}"
        )

    def _request_profile_save(self, _checked: bool = False) -> None:
        if self._calibration_context is None:
            self.show_save_status("请先刷新并选择播放与录音设备")
            return
        try:
            profile = CalibrationManager.create_profile(
                name=self.calibration_name_edit.text(),
                context=self._calibration_context,
                compensation_ms=self.calibration_compensation_spin.value(),
                calibration_method=self.calibration_method_edit.text(),
                notes=self.calibration_notes_edit.text(),
            )
        except ValueError as exc:
            self.show_save_status(f"校准配置无效：{exc}")
            return
        self.calibration_profile_save_requested.emit(profile)

    def _reload_calibration_profiles(self) -> None:
        preferred_id = (
            self.calibration_profile_view_combo.currentData()
            or self._config.calibration.selected_profile_id
        )
        self.calibration_profile_view_combo.clear()
        profiles = self._config.calibration.profiles
        if not profiles:
            self.calibration_profile_view_combo.addItem("尚无校准配置", None)
        else:
            for profile in profiles:
                self.calibration_profile_view_combo.addItem(
                    f"{profile.name} · {profile.compensation_ms:.2f} ms",
                    profile.profile_id,
                )
            preferred_index = self.calibration_profile_view_combo.findData(preferred_id)
            self.calibration_profile_view_combo.setCurrentIndex(max(preferred_index, 0))
        self._show_selected_profile_details()

    def _show_selected_profile_details(self, _index: int = 0) -> None:
        profile = self._config.calibration.profile_by_id(
            self.calibration_profile_view_combo.currentData()
        )
        if profile is None:
            self.calibration_detail_label.setText("尚无校准配置；未选择校准时补偿为 0 ms")
            self.calibration_detail_label.setToolTip("")
            return
        notes = profile.notes or "无备注"
        detail = (
            f"{profile.compensation_ms:.2f} ms · {profile.sample_rate} Hz · {profile.audio_mode}\n"
            f"来源：{profile.calibration_method} · 日期：{profile.calibration_date}\n"
            f"设备：{profile.output_device} / {profile.input_device}\n备注：{notes}"
        )
        self.calibration_detail_label.setText(detail)
        self.calibration_detail_label.setToolTip(detail)

    def set_save_enabled(self, enabled: bool) -> None:
        self.save_button.setEnabled(enabled)
        self.save_calibration_button.setEnabled(enabled)

    def show_save_status(self, message: str) -> None:
        self.save_status_label.setText(message)
