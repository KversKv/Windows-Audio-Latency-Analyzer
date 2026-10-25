import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from PySide6.QtCore import QEventLoop, QTimer
from PySide6.QtWidgets import QApplication

from app.config import AppConfig, AudioConfig, ConfigManager
from audio.audio_backend import SimulatedAudioBackend
from audio.audio_device_manager import AudioDeviceManager
from core.device_service import DeviceService
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
