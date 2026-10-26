import logging
from dataclasses import dataclass

from audio.audio_backend import (
    AudioBackend,
    AudioDevice,
    SoftwareLoopbackCapability,
)

logger = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class DeviceInventory:
    output_devices: tuple[AudioDevice, ...]
    input_devices: tuple[AudioDevice, ...]
    backend_name: str
    is_simulation: bool
    software_loopback_capability: SoftwareLoopbackCapability


class AudioDeviceManager:
    def __init__(self, backend: AudioBackend) -> None:
        self._backend = backend

    @property
    def backend(self) -> AudioBackend:
        return self._backend

    def scan(self) -> DeviceInventory:
        devices = tuple(self._backend.list_devices())
        try:
            loopback_capability = self._backend.software_loopback_capability()
        except Exception as exc:
            logger.warning("Software Loop 能力检测失败", exc_info=True)
            loopback_capability = SoftwareLoopbackCapability(
                supported=False,
                detail=f"Software Loop 能力检测失败：{exc}",
            )
        return DeviceInventory(
            output_devices=tuple(device for device in devices if device.supports_output),
            input_devices=tuple(device for device in devices if device.supports_input),
            backend_name=self._backend.display_name,
            is_simulation=self._backend.is_simulation,
            software_loopback_capability=loopback_capability,
        )
