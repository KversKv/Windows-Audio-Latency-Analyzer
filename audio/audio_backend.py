import logging
import os
from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Any

import sounddevice as sd

logger = logging.getLogger(__name__)


class AudioBackendError(RuntimeError):
    pass


@dataclass(frozen=True, slots=True)
class AudioDevice:
    identifier: int | str
    name: str
    host_api: str
    max_input_channels: int
    max_output_channels: int
    default_sample_rate: float
    is_simulation: bool = False

    @property
    def supports_input(self) -> bool:
        return self.max_input_channels > 0

    @property
    def supports_output(self) -> bool:
        return self.max_output_channels > 0


class AudioBackend(ABC):
    display_name = "未知音频后端"
    is_simulation = False

    @abstractmethod
    def list_devices(self) -> list[AudioDevice]:
        raise NotImplementedError


class SoundDeviceBackend(AudioBackend):
    display_name = "sounddevice / PortAudio"

    def list_devices(self) -> list[AudioDevice]:
        try:
            raw_devices = sd.query_devices()
            host_apis = sd.query_hostapis()
        except Exception as exc:
            logger.error("PortAudio 设备枚举失败", exc_info=True)
            raise AudioBackendError(f"无法访问 PortAudio：{exc}") from exc

        wasapi_indices = {
            index
            for index, host_api in enumerate(host_apis)
            if "wasapi" in str(host_api["name"]).lower()
        }
        use_wasapi_only = os.name == "nt" and bool(wasapi_indices)
        devices: list[AudioDevice] = []
        for index, raw_device in enumerate(raw_devices):
            host_api_index = int(raw_device["hostapi"])
            if use_wasapi_only and host_api_index not in wasapi_indices:
                continue
            devices.append(self._to_audio_device(index, raw_device, host_apis))
        return devices

    @staticmethod
    def _to_audio_device(
        index: int,
        raw_device: Any,
        host_apis: Any,
    ) -> AudioDevice:
        host_api_index = int(raw_device["hostapi"])
        host_api_name = str(host_apis[host_api_index]["name"])
        return AudioDevice(
            identifier=index,
            name=str(raw_device["name"]),
            host_api=host_api_name,
            max_input_channels=int(raw_device["max_input_channels"]),
            max_output_channels=int(raw_device["max_output_channels"]),
            default_sample_rate=float(raw_device["default_samplerate"]),
        )


class SimulatedAudioBackend(AudioBackend):
    display_name = "离线模拟后端"
    is_simulation = True

    def __init__(self, sample_rate: int) -> None:
        self._devices = [
            AudioDevice(
                identifier="sim-output",
                name="模拟耳机输出",
                host_api="离线模拟",
                max_input_channels=0,
                max_output_channels=2,
                default_sample_rate=float(sample_rate),
                is_simulation=True,
            ),
            AudioDevice(
                identifier="sim-input",
                name="模拟有线麦克风",
                host_api="离线模拟",
                max_input_channels=2,
                max_output_channels=0,
                default_sample_rate=float(sample_rate),
                is_simulation=True,
            ),
        ]

    def list_devices(self) -> list[AudioDevice]:
        return list(self._devices)


def create_audio_backend(backend_name: str, sample_rate: int) -> AudioBackend:
    if backend_name == "sounddevice":
        return SoundDeviceBackend()
    if backend_name == "simulation":
        return SimulatedAudioBackend(sample_rate)
    raise ValueError(f"不支持的音频后端：{backend_name}")
