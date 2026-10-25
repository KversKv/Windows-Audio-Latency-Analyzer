import logging
from dataclasses import replace

from PySide6.QtCore import Qt, QThread, QTimer, Signal
from PySide6.QtGui import QCloseEvent
from PySide6.QtWidgets import (
    QFrame,
    QHBoxLayout,
    QLabel,
    QMainWindow,
    QPushButton,
    QStackedWidget,
    QStatusBar,
    QVBoxLayout,
    QWidget,
)

from app.config import AppConfig, ConfigManager
from core.config_worker import ConfigSaveWorker
from core.device_service import DeviceInventory, DeviceService
from ui.comparison_page import ComparisonPage
from ui.history_page import HistoryPage
from ui.settings_page import SettingsPage
from ui.test_page import TestPage
from version import __version__

logger = logging.getLogger(__name__)


class MainWindow(QMainWindow):
    configuration_saved = Signal(object)

    def __init__(
        self,
        device_service: DeviceService,
        config_manager: ConfigManager,
        config: AppConfig,
    ) -> None:
        super().__init__()
        self._config_manager = config_manager
        self._config = config
        self._pending_config: AppConfig | None = None
        self._config_save_thread: QThread | None = None
        self._config_save_worker: ConfigSaveWorker | None = None
        self._shutting_down = False

        self.setWindowTitle("Windows 音频延迟分析器")
        self.setMinimumSize(1100, 720)
        self.resize(1360, 860)
        self._build_ui(device_service, config)
        self.setStatusBar(QStatusBar())
        self.backend_status_label = QLabel("音频后端初始化中")
        self.test_state_status_label = QLabel("测试：空闲")
        self.sample_rate_status_label = QLabel("输出 / 采集：—")
        self.test_progress_status_label = QLabel("进度：—")
        self.statusBar().addPermanentWidget(self.test_state_status_label)
        self.statusBar().addPermanentWidget(self.sample_rate_status_label)
        self.statusBar().addPermanentWidget(self.test_progress_status_label)
        self.statusBar().addPermanentWidget(self.backend_status_label)
        self.statusBar().showMessage("就绪")

        self.test_page.devices_refreshed.connect(self._on_devices_refreshed)
        self.test_page.refresh_failed.connect(self._on_device_error)
        self.test_page.device_selection_changed.connect(self._on_device_selection_changed)
        self.test_page.state_changed.connect(self._on_test_state_changed)
        self.test_page.progress_changed.connect(self._on_test_progress_changed)
        self.test_page.audio_parameters_changed.connect(self._on_audio_parameters_changed)
        self.test_page.status_message.connect(self._on_test_status)
        self.test_page.session_save_requested.connect(self._on_session_save_requested)
        self.configuration_saved.connect(self.test_page.update_config)
        self.settings_page.save_requested.connect(self._save_settings)
        QTimer.singleShot(0, self.test_page.refresh_devices)

    def _build_ui(self, device_service: DeviceService, config: AppConfig) -> None:
        central_widget = QWidget()
        central_layout = QHBoxLayout(central_widget)
        central_layout.setContentsMargins(0, 0, 0, 0)
        central_layout.setSpacing(0)
        self.setCentralWidget(central_widget)

        sidebar = QFrame()
        sidebar.setObjectName("sidebar")
        sidebar.setFixedWidth(220)
        sidebar_layout = QVBoxLayout(sidebar)
        sidebar_layout.setContentsMargins(18, 28, 18, 20)
        sidebar_layout.setSpacing(8)

        product_label = QLabel("AUDIO LATENCY")
        product_label.setObjectName("productTitle")
        product_subtitle = QLabel("Windows 声学测量工具")
        product_subtitle.setObjectName("productSubtitle")
        sidebar_layout.addWidget(product_label)
        sidebar_layout.addWidget(product_subtitle)
        sidebar_layout.addSpacing(28)

        self.navigation_buttons: list[QPushButton] = []
        for index, name in enumerate(("测试", "历史记录", "对比分析", "设置")):
            button = QPushButton(name)
            button.setObjectName("navButton")
            button.setCheckable(True)
            button.setCursor(Qt.CursorShape.PointingHandCursor)
            button.clicked.connect(lambda checked=False, page_index=index: self.set_current_page(page_index))
            sidebar_layout.addWidget(button)
            self.navigation_buttons.append(button)
        sidebar_layout.addStretch()

        scope_label = QLabel("端到端声学延迟\n连续音频流测量")
        scope_label.setObjectName("sidebarNote")
        version_label = QLabel(f"版本 {__version__}")
        version_label.setObjectName("sidebarNote")
        sidebar_layout.addWidget(scope_label)
        sidebar_layout.addSpacing(8)
        sidebar_layout.addWidget(version_label)
        central_layout.addWidget(sidebar)

        self.page_stack = QStackedWidget()
        self.test_page = TestPage(device_service, config)
        self.history_page = HistoryPage()
        self.comparison_page = ComparisonPage()
        self.settings_page = SettingsPage(config)
        for page in (self.test_page, self.history_page, self.comparison_page, self.settings_page):
            self.page_stack.addWidget(page)
        central_layout.addWidget(self.page_stack, 1)
        self.set_current_page(0)

    def set_current_page(self, index: int) -> None:
        if not 0 <= index < self.page_stack.count():
            raise IndexError(f"无效页面索引：{index}")
        self.page_stack.setCurrentIndex(index)
        self.navigation_buttons[index].setChecked(True)

    def current_config(self) -> AppConfig:
        return self._pending_config or self._config

    def _on_devices_refreshed(self, inventory: DeviceInventory) -> None:
        backend_text = "离线模拟（非真实测量）" if inventory.is_simulation else inventory.backend_name
        self.backend_status_label.setText(backend_text)
        self.statusBar().showMessage("设备列表已更新", 4000)

    def _on_device_error(self, message: str) -> None:
        self.backend_status_label.setText("设备枚举失败")
        self.statusBar().showMessage(f"设备刷新失败：{message}", 8000)

    def _on_test_state_changed(self, _state: object) -> None:
        self.test_state_status_label.setText(f"测试：{self.test_page.state_label.text()}")

    def _on_test_progress_changed(self, completed: int, total: int) -> None:
        progress_text = f"{completed} / {total}" if total else "—"
        self.test_progress_status_label.setText(f"进度：{progress_text}")

    def _on_audio_parameters_changed(self, output_rate: int, input_rate: int) -> None:
        self.sample_rate_status_label.setText(f"输出 / 采集：{output_rate} / {input_rate} Hz")

    def _on_test_status(self, message: str) -> None:
        self.statusBar().showMessage(message, 6000)

    def _on_session_save_requested(self, _snapshot: object) -> None:
        self.statusBar().showMessage(
            "会话快照已准备；SQLite 持久化尚未接入，当前未写入数据库",
            8000,
        )

    def _on_device_selection_changed(self) -> None:
        self._config = replace(
            self._config,
            audio=replace(
                self._config.audio,
                output_device_id=self.test_page.selected_output_device_id(),
                input_device_id=self.test_page.selected_input_device_id(),
            ),
        )

    def _save_settings(self) -> None:
        if self._shutting_down:
            return
        if self._config_save_thread is not None and self._config_save_thread.isRunning():
            return
        new_config = self.settings_page.apply_to(self._config)
        self._pending_config = new_config
        self.settings_page.set_save_enabled(False)
        self.settings_page.show_save_status("正在保存…")

        thread = QThread(self)
        worker = ConfigSaveWorker(lambda: self._config_manager.save(new_config))
        worker.moveToThread(thread)
        thread.started.connect(worker.run)
        worker.succeeded.connect(self._on_configuration_saved)
        worker.failed.connect(self._on_configuration_save_failed)
        worker.finished.connect(thread.quit)
        worker.finished.connect(worker.deleteLater)
        thread.finished.connect(self._on_configuration_thread_finished)
        thread.finished.connect(thread.deleteLater)
        self._config_save_thread = thread
        self._config_save_worker = worker
        thread.start()

    def _on_configuration_saved(self) -> None:
        if self._pending_config is None:
            return
        previous_backend = self._config.audio.backend
        self._config = self._pending_config
        self._pending_config = None
        message = "设置已保存"
        if previous_backend != self._config.audio.backend:
            message += "；后端切换将在重启后生效"
        self.settings_page.show_save_status(message)
        self.statusBar().showMessage(message, 5000)
        self.configuration_saved.emit(self._config)

    def _on_configuration_save_failed(self, message: str) -> None:
        self._pending_config = None
        error_text = f"设置保存失败：{message}"
        self.settings_page.show_save_status(error_text)
        self.statusBar().showMessage(error_text, 8000)

    def _on_configuration_thread_finished(self) -> None:
        if self.sender() is self._config_save_thread:
            self._config_save_thread = None
            self._config_save_worker = None
            self.settings_page.set_save_enabled(not self._shutting_down)

    def shutdown(self) -> None:
        if self._shutting_down:
            return
        self._shutting_down = True
        self.test_page.shutdown()
        thread = self._config_save_thread
        if thread is not None and thread.isRunning():
            thread.quit()
            if not thread.wait(2000):
                logger.warning("配置保存线程未在 2 秒内结束")

    def closeEvent(self, event: QCloseEvent) -> None:
        self.shutdown()
        event.accept()
