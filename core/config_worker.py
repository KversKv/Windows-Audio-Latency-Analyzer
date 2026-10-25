import logging
from collections.abc import Callable

from PySide6.QtCore import QObject, Signal, Slot

logger = logging.getLogger(__name__)


class ConfigSaveWorker(QObject):
    succeeded = Signal()
    failed = Signal(str)
    finished = Signal()

    def __init__(self, save_callback: Callable[[], None]) -> None:
        super().__init__()
        self._save_callback = save_callback

    @Slot()
    def run(self) -> None:
        try:
            self._save_callback()
        except Exception as exc:
            logger.error("配置保存工作线程失败", exc_info=True)
            self.failed.emit(str(exc) or type(exc).__name__)
        else:
            self.succeeded.emit()
        finally:
            self.finished.emit()
