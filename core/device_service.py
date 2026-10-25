import logging

from PySide6.QtCore import QObject, Signal, Slot

from audio.audio_backend import AudioDevice
from audio.audio_device_manager import AudioDeviceManager, DeviceInventory

logger = logging.getLogger(__name__)

__all__ = [
    "AudioDevice",
    "DeviceInventory",
    "DeviceEnumerationWorker",
    "DeviceService",
]


class DeviceService:
    def __init__(self, device_manager: AudioDeviceManager) -> None:
        self._device_manager = device_manager

    def scan(self) -> DeviceInventory:
        return self._device_manager.scan()


class DeviceEnumerationWorker(QObject):
    succeeded = Signal(object)
    failed = Signal(str)
    finished = Signal()

    def __init__(self, service: DeviceService) -> None:
        super().__init__()
        self._service = service

    @Slot()
    def run(self) -> None:
        try:
            inventory = self._service.scan()
        except Exception as exc:
            logger.error("音频设备枚举工作线程失败", exc_info=True)
            self.failed.emit(str(exc) or type(exc).__name__)
        else:
            self.succeeded.emit(inventory)
        finally:
            self.finished.emit()
