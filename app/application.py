import argparse
import logging
import sys
from collections.abc import Sequence
from dataclasses import replace

from PySide6.QtCore import QTimer
from PySide6.QtWidgets import QApplication

from app.config import ConfigManager
from audio.audio_backend import create_audio_backend
from audio.audio_device_manager import AudioDeviceManager
from core.device_service import DeviceService
from log_config import configure_logging
from ui.main_window import MainWindow
from ui.styles import load_dark_theme
from version import __version__

logger = logging.getLogger(__name__)


def _build_argument_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Windows 耳机全链路声学延迟测试工具")
    parser.add_argument("--simulate", action="store_true", help="使用明确标注的离线模拟设备")
    parser.add_argument("--smoke-test", action="store_true", help="启动后自动退出，用于启动验证")
    return parser


def run(argv: Sequence[str] | None = None) -> int:
    args = _build_argument_parser().parse_args(argv)
    config_manager = ConfigManager()
    configure_logging(config_manager.path.parent / "logs")
    config = config_manager.load()
    if args.simulate:
        config = replace(config, audio=replace(config.audio, backend="simulation"))

    application = QApplication([sys.argv[0]])
    application.setApplicationName("Windows Audio Latency Analyzer")
    application.setApplicationDisplayName("Windows 音频延迟分析器")
    application.setApplicationVersion(__version__)
    application.setOrganizationName("LatencyAnalyzer")
    application.setStyle("Fusion")
    application.setStyleSheet(load_dark_theme())

    backend = create_audio_backend(config.audio.backend, config.audio.sample_rate)
    device_manager = AudioDeviceManager(backend)
    device_service = DeviceService(device_manager)
    window = MainWindow(device_service, config_manager, config)
    logger.info("应用启动，音频后端：%s", backend.display_name)

    try:
        window.show()
        if args.smoke_test:
            QTimer.singleShot(400, application.quit)
        exit_code = application.exec()
    finally:
        window.shutdown()
        try:
            config_manager.save(window.current_config())
        except Exception:
            logger.error("退出时保存配置失败", exc_info=True)
    return exit_code
