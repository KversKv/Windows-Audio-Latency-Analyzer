import os
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from PySide6.QtCore import QEventLoop, QTimer
from PySide6.QtGui import QIcon
from PySide6.QtWidgets import QApplication

from app.config import AppConfig, AudioConfig, ConfigManager
from audio.audio_backend import AudioBackendError, AudioDevice, SimulatedAudioBackend
from audio.audio_device_manager import AudioDeviceManager
from core.device_service import DeviceService
from core.persistence_service import PersistenceService
from core.test_state_machine import TestState as SessionState
from ui.diagnostics_panel import DiagnosticMode
from ui.main_window import MainWindow
from ui.styles import load_dark_theme


class _DynamicAudioBackend(SimulatedAudioBackend):
    def __init__(self, devices):
        super().__init__(sample_rate=48000)
        self._devices = list(devices)
        self.scan_count = 0
        self.failures_remaining = 0

    def set_devices(self, devices):
        self._devices = list(devices)

    def list_devices(self):
        self.scan_count += 1
        if self.failures_remaining > 0:
            self.failures_remaining -= 1
            raise AudioBackendError("测试设备枚举失败")
        return list(self._devices)


def _device(identifier, name, *, output=True):
    return AudioDevice(
        identifier=identifier,
        name=name,
        host_api="测试 WASAPI",
        max_input_channels=0 if output else 2,
        max_output_channels=2 if output else 0,
        default_sample_rate=48000.0,
        is_simulation=True,
    )


@pytest.fixture
def qt_application():
    application = QApplication.instance() or QApplication([])
    yield application


def _wait_for_signal(signal, timeout_ms=2000):
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


@pytest.fixture
def main_window(qt_application, tmp_path):
    config = AppConfig(audio=AudioConfig(backend="simulation"))
    config_manager = ConfigManager(tmp_path / "config.json")
    backend = SimulatedAudioBackend(config.audio.sample_rate)
    service = DeviceService(AudioDeviceManager(backend))
    window = MainWindow(service, config_manager, config)
    yield window, config_manager
    focus_widget = qt_application.focusWidget()
    if focus_widget is not None:
        focus_widget.clearFocus()
    window.shutdown()
    window.close()
    window.deleteLater()
    qt_application.processEvents()


@pytest.fixture
def dynamic_window(qt_application, tmp_path):
    config = AppConfig(audio=AudioConfig(backend="simulation"))
    config_manager = ConfigManager(tmp_path / "config.json")
    backend = _DynamicAudioBackend(
        [
            _device("out-a", "耳机 A"),
            _device("out-b", "耳机 B"),
            _device("in-a", "麦克风 A", output=False),
        ]
    )
    service = DeviceService(AudioDeviceManager(backend))
    window = MainWindow(service, config_manager, config)
    yield window, backend
    window.shutdown()
    window.close()
    window.deleteLater()
    qt_application.processEvents()


def _wait_initial_devices(window):
    assert _wait_for_signal(window.test_page.refresh_finished) is True
    window.test_page._auto_refresh_timer.stop()


def test_device_refresh_and_page_navigation(main_window):
    window, _ = main_window
    window.show()

    assert _wait_for_signal(window.test_page.refresh_finished) is True
    assert window.test_page.output_combo.count() == 1
    assert window.test_page.input_combo.count() == 1
    assert window.test_page.selected_output_device_id() == "sim-output"
    assert window.test_page.selected_input_device_id() == "sim-input"
    assert window.test_page.simulation_label.isVisible() is True

    window.test_page.refresh_devices()
    assert _wait_for_signal(window.test_page.refresh_finished) is True
    assert window.test_page.refresh_button.isEnabled() is True

    for index in range(window.page_stack.count()):
        window.navigation_buttons[index].click()
        assert window.page_stack.currentIndex() == index
        assert [button.isChecked() for button in window.navigation_buttons] == [
            button_index == index for button_index in range(window.page_stack.count())
        ]


def test_test_page_opens_at_top_without_focus_auto_scroll(main_window, qt_application):
    window, _ = main_window
    window.setStyleSheet(load_dark_theme())
    window.resize(1100, 720)
    window.show()
    window.activateWindow()
    assert _wait_for_signal(window.test_page.refresh_finished) is True
    qt_application.processEvents()

    scroll_area = window.test_page.page_scroll_area
    focus_widget = qt_application.focusWidget()
    focus_name = focus_widget.metaObject().className() if focus_widget is not None else "None"
    assert scroll_area.verticalScrollBar().maximum() > 0
    assert scroll_area.verticalScrollBar().value() == 0, (
        f"页面显示后自动滚动到 {scroll_area.verticalScrollBar().value()}，"
        f"焦点控件为 {focus_name}"
    )


def test_device_refresh_preserves_focus_and_scroll_position(main_window, qt_application):
    window, _ = main_window
    window.setStyleSheet(load_dark_theme())
    window.resize(1100, 720)
    window.show()
    window.activateWindow()
    assert _wait_for_signal(window.test_page.refresh_finished) is True
    page = window.test_page
    page._auto_refresh_timer.stop()
    page.output_combo.setFocus()
    qt_application.processEvents()
    assert qt_application.focusWidget() is page.output_combo
    assert page.page_scroll_area.verticalScrollBar().value() == 0

    page.refresh_devices()
    assert _wait_for_signal(page.refresh_finished) is True
    qt_application.processEvents()

    focus_widget = qt_application.focusWidget()
    focus_name = focus_widget.metaObject().className() if focus_widget is not None else "None"
    assert qt_application.focusWidget() is page.output_combo
    assert page.page_scroll_area.verticalScrollBar().value() == 0, (
        f"设备刷新后页面自动滚动，焦点控件为 {focus_name}"
    )


def test_long_pages_scroll_without_compressing_content(main_window, qt_application):
    window, _ = main_window
    window.setStyleSheet(load_dark_theme())
    window.show()
    assert _wait_for_signal(window.test_page.refresh_finished) is True
    window.resize(1100, 720)

    for index, page in ((0, window.test_page), (3, window.settings_page)):
        window.set_current_page(index)
        qt_application.processEvents()
        scroll_area = page.page_scroll_area
        content = scroll_area.widget()
        assert scroll_area.widgetResizable() is True
        assert content.height() >= content.minimumSizeHint().height()
        assert content.height() > scroll_area.viewport().height()
        assert scroll_area.verticalScrollBar().maximum() > 0

        if page is window.test_page:
            fixed_controls = (
                page.state_label,
                page.progress_bar,
                page.session_status_label,
                page.start_button,
                page.stop_button,
                page.clear_button,
                page.save_button,
            )
            scroll_area.verticalScrollBar().setValue(scroll_area.verticalScrollBar().maximum())
            qt_application.processEvents()
            assert page.action_footer.parentWidget() is page
            for control in fixed_controls:
                assert not content.isAncestorOf(control)
                assert control.isVisible()
                assert page.rect().contains(control.mapTo(page, control.rect().center()))
            continue

        scroll_area.ensureWidgetVisible(page.save_button)
        qt_application.processEvents()
        assert scroll_area.verticalScrollBar().value() > 0


def test_dark_theme_label_and_spin_arrow_resources(qt_application):
    theme = load_dark_theme()
    assert "QLabel {\n    background: transparent;\n}" in theme
    assert "{{ASSETS_DIR}}" not in theme

    assets_dir = Path(__file__).resolve().parents[1] / "ui" / "assets"
    for asset_name in ("spin_up.svg", "spin_down.svg"):
        asset_path = assets_dir / asset_name
        assert asset_path.as_posix() in theme
        icon = QIcon(str(asset_path))
        assert not icon.pixmap(12, 12).isNull()
        assert not icon.pixmap(24, 24).isNull()


def test_settings_save_persists_configuration(main_window):
    window, config_manager = main_window
    window.settings_page.sample_rate_spin.setValue(44100)
    window.settings_page.test_count_spin.setValue(50)
    window.settings_page.warmup_spin.setValue(5.5)

    window.settings_page.save_button.click()

    assert _wait_for_signal(window.configuration_saved) is True
    saved_config = config_manager.load()
    assert saved_config.audio.sample_rate == 44100
    assert saved_config.measurement.default_test_count == 50
    assert saved_config.measurement.warmup_seconds == 5.5


def test_calibration_profile_save_and_selection(main_window):
    window, config_manager = main_window
    window.show()
    assert _wait_for_signal(window.test_page.refresh_finished) is True
    page = window.test_page
    settings = window.settings_page
    assert "模拟耳机输出" in settings.calibration_context_label.text()
    assert "模拟有线麦克风" in settings.calibration_context_label.text()

    settings.calibration_name_edit.setText("模拟环回校准")
    settings.calibration_compensation_spin.setValue(3.25)
    settings.calibration_method_edit.setText("确定性测试输入")
    settings.calibration_notes_edit.setText("仅验证配置追溯")
    settings.save_calibration_button.click()

    assert _wait_for_signal(window.configuration_saved) is True
    saved_config = config_manager.load()
    assert len(saved_config.calibration.profiles) == 1
    profile = saved_config.calibration.profiles[0]
    assert saved_config.calibration.selected_profile_id == profile.profile_id
    assert profile.name == "模拟环回校准"
    assert profile.output_device_id == "sim-output"
    assert profile.output_device == "模拟耳机输出"
    assert profile.input_device_id == "sim-input"
    assert profile.input_device == "模拟有线麦克风"
    assert profile.sample_rate == 48000
    assert profile.audio_mode == "simulation"
    assert profile.compensation_ms == 3.25
    assert profile.calibration_method == "确定性测试输入"
    assert profile.notes == "仅验证配置追溯"
    assert page.selected_calibration_profile() == profile
    assert page.compensation_spin.value() == 3.25
    assert page.compensation_spin.isEnabled() is False
    assert profile.calibration_method in page.calibration_source_label.text()
    assert profile.calibration_date in page.calibration_source_label.text()
    assert profile.notes in page.calibration_source_label.text()
    assert profile.calibration_method in settings.calibration_detail_label.text()
    session_config = page.session_config()
    assert session_config.compensation_ms == 3.25
    assert session_config.calibration_profile == profile

    page.calibration_profile_combo.setCurrentIndex(0)
    assert page.selected_calibration_profile() is None
    assert page.compensation_spin.isEnabled() is True
    assert page.compensation_spin.value() == 0.0
    assert "未补偿" in page.compensation_status_label.text()
    assert window.current_config().calibration.selected_profile_id is None


def test_simulated_measurement_page_workflow(main_window):
    window, config_manager = main_window
    window.show()
    assert _wait_for_signal(window.test_page.refresh_finished) is True
    page = window.test_page
    page.test_count_spin.setValue(2)
    page.compensation_spin.setValue(2.0)

    assert page.start_button.isEnabled() is True
    page.start_button.click()

    assert _wait_for_signal(page.controller.finished, 5000) is True
    assert page.controller.state == SessionState.COMPLETED
    assert page.progress_bar.value() == page.progress_bar.maximum() == 2
    statistics = page.controller.statistics
    assert statistics.valid_count == 2
    assert statistics.invalid_count == 0
    assert page.valid_count_label.text() == "2"
    assert page.invalid_count_label.text() == "0"
    for labels, values, current_latency in (
        (
            page.metric_value_labels,
            statistics.raw,
            page.controller.records[-1].measurement.raw_latency_ms,
        ),
        (
            page.compensated_metric_value_labels,
            statistics.compensated,
            page.controller.records[-1].measurement.compensated_latency_ms,
        ),
    ):
        assert labels[0].text() == f"{current_latency:.2f} ms"
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
            assert label.text() == f"{value:.2f} ms"
    assert page.reference_curve.xData.size > 0
    assert page.response_curve.xData.size > 0
    assert page.peak_line.isVisible() is True
    assert all(record.measurement.is_simulation for record in page.controller.records)
    assert "手动补偿" in page.compensation_status_label.text()
    assert "模拟结果" in page.compensation_status_label.text()
    assert "统计仅使用有效样本" in page.quality_summary_label.text()

    save_requests = []
    page.session_save_requested.connect(save_requests.append)
    assert page.save_button.isEnabled() is True
    page.save_button.click()
    assert len(save_requests) == 1
    assert save_requests[0].completed_count == 2
    assert page.controller.save_pending is True
    assert page.clear_button.isEnabled() is False
    assert _wait_for_signal(page.controller.session_saved) is True
    assert page.controller.state == SessionState.SAVED
    assert page.controller.save_pending is False
    assert page.session_status_label.text() == "会话已保存"

    database_path = config_manager.path.with_name("latency_analyzer.db")
    reopened_service = PersistenceService(database_path)
    sessions = reopened_service.list_sessions()
    assert len(sessions) == 1
    stored = reopened_service.get_session(sessions[0].id)
    assert stored.session_uid == save_requests[0].session_id
    assert stored.completed_count == stored.valid_count == 2
    assert stored.invalid_count == 0
    assert stored.is_simulation is True
    assert len(stored.results) == 2
    assert reopened_service.recalculate_statistics(stored).raw.mean_latency_ms == pytest.approx(
        statistics.raw.mean_latency_ms
    )

    page.clear_button.click()
    assert page.controller.state == SessionState.IDLE
    assert page.controller.records == ()
    assert all(label.text() == "—" for label in page.metric_value_labels)
    assert all(label.text() == "—" for label in page.compensated_metric_value_labels)
    assert page.valid_count_label.text() == "0"
    assert page.invalid_count_label.text() == "0"
    assert "尚无样本" in page.quality_summary_label.text()
    assert page.reference_curve.xData is None or page.reference_curve.xData.size == 0
    assert page.response_curve.xData is None or page.response_curve.xData.size == 0
    assert page.start_button.isEnabled() is True


def test_invalid_measurement_reason_is_visible(main_window):
    window, _ = main_window
    window.show()
    assert _wait_for_signal(window.test_page.refresh_finished) is True
    page = window.test_page
    page.test_count_spin.setValue(1)
    page.simulation_delay_spin.setValue(250.0)

    page.start_button.click()

    assert _wait_for_signal(page.controller.finished, 5000) is True
    assert "第 1 次无效" in page.measurement_status_label.text()
    assert "延迟超出有效范围" in page.measurement_status_label.text()
    assert "原始记录已保留" in page.measurement_status_label.text()
    assert page.controller.snapshot.invalid_count == 1
    assert page.valid_count_label.text() == "0"
    assert page.invalid_count_label.text() == "1"
    assert all(label.text() == "—" for label in page.metric_value_labels[1:])
    assert all(label.text() == "—" for label in page.compensated_metric_value_labels[1:])
    assert "延迟超出有效范围 × 1" in page.quality_summary_label.text()
    assert "有效样本不足最低要求的 1 次" in page.quality_summary_label.text()
    assert "有效样本不足最低要求的 1 次" in page.session_status_label.text()


def test_manual_button_and_auto_refresh_apply_device_changes(dynamic_window):
    window, backend = dynamic_window
    _wait_initial_devices(window)
    page = window.test_page

    backend.set_devices(
        [
            _device("out-a", "耳机 A"),
            _device("out-b", "耳机 B"),
            _device("out-c", "新插入耳机"),
            _device("in-a", "麦克风 A", output=False),
        ]
    )
    page.refresh_button.click()
    assert _wait_for_signal(page.refresh_finished) is True
    page._auto_refresh_timer.stop()

    assert page.output_combo.count() == 3
    assert page.selected_output_device_id() == "out-a"
    assert "新插入耳机" in page.output_combo.itemText(2)

    backend.set_devices(
        [
            _device("out-a", "耳机 A"),
            _device("out-b", "耳机 B"),
            _device("out-c", "新插入耳机"),
            _device("in-a", "麦克风 A", output=False),
            _device("in-b", "新插入麦克风", output=False),
        ]
    )
    page._auto_refresh_devices()
    assert _wait_for_signal(page.refresh_finished) is True
    page._auto_refresh_timer.stop()

    assert page.input_combo.count() == 2
    assert page.selected_input_device_id() == "in-a"
    assert "新插入麦克风" in page.input_combo.itemText(1)


def test_unchanged_auto_refresh_is_silent_and_single_flight(dynamic_window):
    window, backend = dynamic_window
    _wait_initial_devices(window)
    page = window.test_page
    refreshed = []
    selection_changes = []
    model_events = []
    page.devices_refreshed.connect(refreshed.append)
    page.device_selection_changed.connect(selection_changes.append)
    model = page.output_combo.model()
    model.rowsInserted.connect(lambda *args: model_events.append(args))
    model.rowsRemoved.connect(lambda *args: model_events.append(args))
    previous_scan_count = backend.scan_count

    page._auto_refresh_devices()
    page._auto_refresh_devices()
    assert _wait_for_signal(page.refresh_finished) is True
    page._auto_refresh_timer.stop()

    assert backend.scan_count == previous_scan_count + 1
    assert refreshed == []
    assert selection_changes == []
    assert model_events == []

    page.refresh_button.click()
    assert _wait_for_signal(page.refresh_finished) is True
    assert len(refreshed) == 1
    assert selection_changes == []
    assert model_events == []

    assert page._auto_refresh_timer.isActive() is True
    window.shutdown()
    assert page._auto_refresh_timer.isActive() is False


def test_auto_refresh_tracks_device_when_portaudio_id_changes(dynamic_window):
    window, backend = dynamic_window
    _wait_initial_devices(window)
    page = window.test_page
    backend.set_devices(
        [
            _device("out-a", "耳机 B"),
            _device("out-a-reindexed", "耳机 A"),
            _device("in-a", "麦克风 A", output=False),
        ]
    )

    page._auto_refresh_devices()
    assert _wait_for_signal(page.refresh_finished) is True
    page._auto_refresh_timer.stop()

    assert page.selected_output_device_id() == "out-a-reindexed"
    assert "耳机 A" in page.output_combo.currentText()
    assert window.current_config().audio.output_device_id == "out-a-reindexed"


def test_selected_device_removal_and_unique_reinsertion(dynamic_window):
    window, backend = dynamic_window
    _wait_initial_devices(window)
    page = window.test_page
    backend.set_devices(
        [
            _device("out-b", "耳机 B"),
            _device("in-a", "麦克风 A", output=False),
        ]
    )

    page._auto_refresh_devices()
    assert _wait_for_signal(page.refresh_finished) is True
    page._auto_refresh_timer.stop()

    assert page.selected_output_device_id() is None
    assert "原设备不可用" in page.output_combo.currentText()
    assert page.output_combo.isEnabled() is True
    assert page.start_button.isEnabled() is False
    assert window.current_config().audio.output_device_id == "out-a"

    backend.set_devices(
        [
            _device("out-b", "耳机 B"),
            _device("out-a-reconnected", "耳机 A"),
            _device("in-a", "麦克风 A", output=False),
        ]
    )
    page._auto_refresh_devices()
    assert _wait_for_signal(page.refresh_finished) is True
    page._auto_refresh_timer.stop()

    assert page.selected_output_device_id() == "out-a-reconnected"
    assert page.start_button.isEnabled() is True
    assert window.current_config().audio.output_device_id == "out-a-reconnected"


def test_auto_refresh_failure_backoff_and_recovery(dynamic_window):
    window, backend = dynamic_window
    _wait_initial_devices(window)
    page = window.test_page
    refresh_errors = []
    refreshed = []
    page.refresh_failed.connect(refresh_errors.append)
    page.devices_refreshed.connect(refreshed.append)
    backend.failures_remaining = 6

    for expected_delay in (2000, 4000, 8000, 16000, 30000, 30000):
        page._auto_refresh_devices()
        assert _wait_for_signal(page.refresh_finished) is True
        assert page._auto_refresh_timer.interval() == expected_delay
        page._auto_refresh_timer.stop()

    assert len(refresh_errors) == 1
    assert refreshed == []
    assert page.output_combo.count() == 2

    page._auto_refresh_devices()
    assert _wait_for_signal(page.refresh_finished) is True
    page._auto_refresh_timer.stop()

    assert page._auto_refresh_timer.interval() == 2000
    assert len(refreshed) == 1
    assert "设备刷新失败" not in page.device_status_label.text()


def test_auto_refresh_defers_while_controller_is_active(dynamic_window, monkeypatch):
    window, backend = dynamic_window
    _wait_initial_devices(window)
    page = window.test_page
    initial_output_count = page.output_combo.count()
    initial_scan_count = backend.scan_count
    backend.set_devices(
        [
            _device("out-a", "耳机 A"),
            _device("out-b", "耳机 B"),
            _device("out-c", "测量期间插入的耳机"),
            _device("in-a", "麦克风 A", output=False),
        ]
    )

    with monkeypatch.context() as context:
        context.setattr(
            type(page.controller),
            "is_running",
            property(lambda _controller: True),
        )
        page._auto_refresh_devices()
        assert backend.scan_count == initial_scan_count
        assert page.output_combo.count() == initial_output_count
        assert page.selected_output_device_id() == "out-a"

    page._on_controller_finished()
    assert _wait_for_signal(page.refresh_finished) is True
    page._auto_refresh_timer.stop()

    assert backend.scan_count == initial_scan_count + 1
    assert page.output_combo.count() == initial_output_count + 1
    assert page.selected_output_device_id() == "out-a"


def test_software_session_config_does_not_require_input_device(main_window):
    window, _ = main_window
    _wait_initial_devices(window)
    page = window.test_page
    page.input_combo.setCurrentIndex(-1)

    config = page.session_config(diagnostic_mode="software")

    assert config.audio.output_device_id == "sim-output"
    assert config.audio.input_device_id is None
    assert config.output_device_name == "模拟耳机输出"
    assert config.input_device_name == ""
    assert config.diagnostic_mode == "software"
    assert config.compensation_ms == 0.0
    assert config.calibration_profile is None
    with pytest.raises(ValueError, match="播放和录音设备"):
        page.session_config()
    with pytest.raises(ValueError, match="播放和录音设备"):
        page.session_config(diagnostic_mode="electrical")


def test_simulation_disables_all_real_diagnostic_modes(main_window):
    window, _ = main_window
    window.show()
    assert _wait_for_signal(window.test_page.refresh_finished) is True
    page = window.test_page
    page._auto_refresh_timer.stop()
    panel = page.diagnostics_panel
    save_requests = []
    page.session_save_requested.connect(save_requests.append)

    for mode in DiagnosticMode:
        panel.mode_combo.setCurrentIndex(panel.mode_combo.findData(mode))
        assert panel.start_button.isEnabled() is False
        assert "离线模拟后端不提供真实环回诊断" in panel.support_label.text()
        assert panel.start(page.session_config()) is False
        assert panel.record_for(mode) is None
        assert panel.config_for(mode) is None
        assert panel.latency_value_label.text() == "—"
        assert "不会生成替代测量结果" in panel.error_label.text()

    assert page.controller.records == ()
    assert page.controller.snapshot is None
    assert save_requests == []

    panel.set_external_busy(True)
    assert panel.start(page.session_config()) is False
    assert "普通测量或设备刷新" in panel.error_label.text()
    panel.set_external_busy(False)
    window.shutdown()
    assert panel.start_button.isEnabled() is False
    assert page.refresh_button.isEnabled() is False
    assert page._auto_refresh_timer.isActive() is False


def test_diagnostics_defer_refresh_and_block_normal_measurement(
    dynamic_window,
    monkeypatch,
):
    window, backend = dynamic_window
    _wait_initial_devices(window)
    page = window.test_page
    panel = page.diagnostics_panel
    initial_output_count = page.output_combo.count()
    initial_scan_count = backend.scan_count
    backend.set_devices(
        [
            _device("out-a", "耳机 A"),
            _device("out-b", "耳机 B"),
            _device("out-c", "诊断期间插入的耳机"),
            _device("in-a", "麦克风 A", output=False),
        ]
    )

    with monkeypatch.context() as context:
        context.setattr(
            type(panel),
            "is_running",
            property(lambda _panel: True),
        )
        page._update_controls()
        assert page.start_button.isEnabled() is False
        assert page.refresh_button.isEnabled() is False
        assert page.output_combo.isEnabled() is False
        assert page.input_combo.isEnabled() is False
        assert panel.stop_button.isEnabled() is True

        page.start_test()
        page.refresh_devices()
        page._auto_refresh_devices()

        assert page.controller.is_running is False
        assert page.controller.records == ()
        assert backend.scan_count == initial_scan_count
        assert page.output_combo.count() == initial_output_count
        assert page.selected_output_device_id() == "out-a"

    page._on_diagnostics_finished()
    assert _wait_for_signal(page.refresh_finished) is True
    page._auto_refresh_timer.stop()

    assert backend.scan_count == initial_scan_count + 1
    assert page.output_combo.count() == initial_output_count + 1
    assert page.selected_output_device_id() == "out-a"
    assert page.start_button.isEnabled() is True
