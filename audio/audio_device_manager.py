from dataclasses import dataclass

from audio.audio_backend import AudioBackend, AudioDevice


@dataclass(frozen=True, slots=True)
class DeviceInventory:
    output_devices: tuple[AudioDevice, ...]
    input_devices: tuple[AudioDevice, ...]
    backend_name: str
    is_simulation: bool


class AudioDeviceManager:
    def __init__(self, backend: AudioBackend) -> None:
        self._backend = backend

    def scan(self) -> DeviceInventory:
        devices = tuple(self._backend.list_devices())
        return DeviceInventory(
            output_devices=tuple(device for device in devices if device.supports_output),
            input_devices=tuple(device for device in devices if device.supports_input),
            backend_name=self._backend.display_name,
            is_simulation=self._backend.is_simulation,
        )
