import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from PySide6.QtCore import QEventLoop, QTimer
from PySide6.QtWidgets import QApplication

from app.config import AppConfig, AudioConfig, ConfigManager
from audio.audio_backend import SimulatedAudioBackend
from audio.audio_device_manager import AudioDeviceManager
from core.device_service import DeviceService
from core.persistence_service import PersistenceService
from core.test_state_machine import TestState as SessionState
from ui.main_window import MainWindow


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
    window.shutdown()
    window.close()
    window.deleteLater()
    qt_application.processEvents()


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
        assert window.navigation_buttons[index].isChecked() is True


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
