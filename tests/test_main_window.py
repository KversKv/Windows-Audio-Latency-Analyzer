import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from PySide6.QtCore import QEventLoop, QTimer
from PySide6.QtWidgets import QApplication

from app.config import AppConfig, AudioConfig, ConfigManager
from audio.audio_backend import SimulatedAudioBackend
from audio.audio_device_manager import AudioDeviceManager
from core.device_service import DeviceService
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


def test_simulated_measurement_page_workflow(main_window):
    window, _ = main_window
    window.show()
    assert _wait_for_signal(window.test_page.refresh_finished) is True
    page = window.test_page
    page.test_count_spin.setValue(2)

    assert page.start_button.isEnabled() is True
    page.start_button.click()

    assert _wait_for_signal(page.controller.finished, 5000) is True
    assert page.controller.state == SessionState.COMPLETED
    assert page.progress_bar.value() == page.progress_bar.maximum() == 2
    assert page.metric_value_labels[0].text() != "—"
    assert page.metric_value_labels[7].text() == "2"
    assert page.reference_curve.xData.size > 0
    assert page.response_curve.xData.size > 0
    assert page.peak_line.isVisible() is True
    assert all(record.measurement.is_simulation for record in page.controller.records)
    assert "模拟结果" in page.compensation_status_label.text()

    save_requests = []
    page.session_save_requested.connect(save_requests.append)
    assert page.save_button.isEnabled() is True
    page.save_button.click()
    assert len(save_requests) == 1
    assert save_requests[0].completed_count == 2
    assert "未写入数据库" in page.session_status_label.text()

    page.clear_button.click()
    assert page.controller.state == SessionState.IDLE
    assert page.controller.records == ()
    assert page.metric_value_labels[0].text() == "—"
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
