import logging
import os
from abc import ABC, abstractmethod
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

import numpy as np
import sounddevice as sd

logger = logging.getLogger(__name__)

_PA_INTERNAL_ERROR = -9986


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


@dataclass(frozen=True, slots=True)
class SoftwareLoopbackCapability:
    supported: bool
    detail: str


@dataclass(slots=True)
class AudioCallbackTime:
    input_buffer_adc_time: float = 0.0
    current_time: float = 0.0
    output_buffer_dac_time: float = 0.0
    clock_domain: str | None = None

    def update(self, raw_time: Any) -> None:
        self.input_buffer_adc_time = float(raw_time.inputBufferAdcTime)
        self.current_time = float(raw_time.currentTime)
        self.output_buffer_dac_time = float(raw_time.outputBufferDacTime)


@dataclass(slots=True)
class AudioStreamStatus:
    input_underflow: bool = False
    input_overflow: bool = False
    output_underflow: bool = False
    output_overflow: bool = False
    priming_output: bool = False

    def update(self, raw_status: Any) -> None:
        self.input_underflow = bool(raw_status.input_underflow)
        self.input_overflow = bool(raw_status.input_overflow)
        self.output_underflow = bool(raw_status.output_underflow)
        self.output_overflow = bool(raw_status.output_overflow)
        self.priming_output = bool(raw_status.priming_output)


AudioCallback = Callable[[np.ndarray, int, AudioCallbackTime, AudioStreamStatus], None]


class AudioStream(ABC):
    @abstractmethod
    def start(self) -> None:
        raise NotImplementedError

    @abstractmethod
    def stop(self) -> None:
        raise NotImplementedError

    @abstractmethod
    def close(self) -> None:
        raise NotImplementedError

    @property
    @abstractmethod
    def is_active(self) -> bool:
        raise NotImplementedError

    @property
    @abstractmethod
    def is_closed(self) -> bool:
        raise NotImplementedError

    @property
    @abstractmethod
    def callback_error(self) -> Exception | None:
        raise NotImplementedError


class AudioBackend(ABC):
    display_name = "未知音频后端"
    is_simulation = False

    @abstractmethod
    def list_devices(self) -> list[AudioDevice]:
        raise NotImplementedError

    def software_loopback_capability(self) -> SoftwareLoopbackCapability:
        return SoftwareLoopbackCapability(
            supported=False,
            detail="当前音频后端未提供真实 WASAPI Loopback Capture",
        )

    @abstractmethod
    def open_output_stream(
        self,
        *,
        device_id: int | str | None,
        sample_rate: int,
        channels: int,
        blocksize: int,
        callback: AudioCallback,
    ) -> AudioStream:
        raise NotImplementedError

    @abstractmethod
    def open_input_stream(
        self,
        *,
        device_id: int | str | None,
        sample_rate: int,
        channels: int,
        blocksize: int,
        callback: AudioCallback,
    ) -> AudioStream:
        raise NotImplementedError


class SoundDeviceStream(AudioStream):
    def __init__(
        self,
        *,
        device_id: int | str | None,
        sample_rate: int,
        channels: int,
        blocksize: int,
        callback: AudioCallback,
        is_output: bool,
    ) -> None:
        self._callback = callback
        self._callback_time = AudioCallbackTime()
        self._callback_status = AudioStreamStatus()
        self._callback_error: Exception | None = None
        self._closed = False
        stream_class = sd.OutputStream if is_output else sd.InputStream
        raw_callback = self._output_callback if is_output else self._input_callback
        self._stream = stream_class(
            device=device_id,
            samplerate=sample_rate,
            channels=channels,
            blocksize=blocksize,
            dtype="float32",
            callback=raw_callback,
        )

    @property
    def is_active(self) -> bool:
        if self._closed:
            return False
        try:
            return bool(self._stream.active)
        except Exception as exc:
            raise AudioBackendError(f"无法读取音频流状态：{exc}") from exc

    @property
    def is_closed(self) -> bool:
        return self._closed

    @property
    def callback_error(self) -> Exception | None:
        return self._callback_error

    def start(self) -> None:
        try:
            self._stream.start()
        except Exception as exc:
            raise AudioBackendError(f"无法启动音频流：{exc}") from exc

    def stop(self) -> None:
        if self._closed:
            return
        try:
            if self._stream.active:
                self._stream.stop()
        except Exception as exc:
            raise AudioBackendError(f"无法停止音频流：{exc}") from exc

    def close(self) -> None:
        if self._closed:
            return
        try:
            self._stream.close()
        except Exception as exc:
            raise AudioBackendError(f"无法关闭音频流：{exc}") from exc
        finally:
            self._closed = True

    def _output_callback(
        self,
        outdata: np.ndarray,
        frames: int,
        raw_time: Any,
        raw_status: Any,
    ) -> None:
        if self._callback_error is not None:
            outdata.fill(0)
            return
        self._callback_time.update(raw_time)
        self._callback_status.update(raw_status)
        try:
            self._callback(outdata, frames, self._callback_time, self._callback_status)
        except Exception as exc:
            self._callback_error = exc
            outdata.fill(0)

    def _input_callback(
        self,
        indata: np.ndarray,
        frames: int,
        raw_time: Any,
        raw_status: Any,
    ) -> None:
        if self._callback_error is not None:
            return
        self._callback_time.update(raw_time)
        self._callback_status.update(raw_status)
        try:
            self._callback(indata, frames, self._callback_time, self._callback_status)
        except Exception as exc:
            self._callback_error = exc


class SoundDeviceBackend(AudioBackend):
    display_name = "sounddevice / PortAudio"

    def list_devices(self) -> list[AudioDevice]:
        try:
            device_list_refreshed = sd._initialized <= 0
            if device_list_refreshed:
                sd._initialize()
            host_apis = sd.query_hostapis()
            wasapi_indices = {
                index
                for index, host_api in enumerate(host_apis)
                if "wasapi" in str(host_api["name"]).lower()
            }
            if os.name == "nt" and wasapi_indices and not device_list_refreshed:
                update_result = sd._lib.PaWasapi_UpdateDeviceList()
                if update_result == _PA_INTERNAL_ERROR:
                    # 默认 PortAudio 构建禁用动态 WASAPI 设备表，只能在空闲时重建。
                    if sd._initialized > 0:
                        sd._terminate()
                    sd._initialize()
                else:
                    sd._check(update_result, "无法更新 WASAPI 设备列表")
            raw_devices = sd.query_devices()
        except Exception as exc:
            logger.error("PortAudio 设备枚举失败", exc_info=True)
            raise AudioBackendError(f"无法访问 PortAudio：{exc}") from exc

        use_wasapi_only = os.name == "nt" and bool(wasapi_indices)
        devices: list[AudioDevice] = []
        for index, raw_device in enumerate(raw_devices):
            host_api_index = int(raw_device["hostapi"])
            if use_wasapi_only and host_api_index not in wasapi_indices:
                continue
            devices.append(self._to_audio_device(index, raw_device, host_apis))
        return devices

    def software_loopback_capability(self) -> SoftwareLoopbackCapability:
        from audio.pyaudiowpatch_backend import detect_software_loopback_capability

        return detect_software_loopback_capability()

    def open_output_stream(
        self,
        *,
        device_id: int | str | None,
        sample_rate: int,
        channels: int,
        blocksize: int,
        callback: AudioCallback,
    ) -> AudioStream:
        try:
            return SoundDeviceStream(
                device_id=device_id,
                sample_rate=sample_rate,
                channels=channels,
                blocksize=blocksize,
                callback=callback,
                is_output=True,
            )
        except Exception as exc:
            logger.error("打开 PortAudio 播放流失败", exc_info=True)
            raise AudioBackendError(f"无法打开播放设备 {device_id!r}：{exc}") from exc

    def open_input_stream(
        self,
        *,
        device_id: int | str | None,
        sample_rate: int,
        channels: int,
        blocksize: int,
        callback: AudioCallback,
    ) -> AudioStream:
        try:
            return SoundDeviceStream(
                device_id=device_id,
                sample_rate=sample_rate,
                channels=channels,
                blocksize=blocksize,
                callback=callback,
                is_output=False,
            )
        except Exception as exc:
            logger.error("打开 PortAudio 录音流失败", exc_info=True)
            raise AudioBackendError(f"无法打开录音设备 {device_id!r}：{exc}") from exc

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

    def software_loopback_capability(self) -> SoftwareLoopbackCapability:
        return SoftwareLoopbackCapability(
            supported=False,
            detail="离线模拟后端不提供真实 WASAPI Loopback Capture",
        )

    def open_output_stream(
        self,
        *,
        device_id: int | str | None,
        sample_rate: int,
        channels: int,
        blocksize: int,
        callback: AudioCallback,
    ) -> AudioStream:
        raise AudioBackendError("离线模拟后端仅用于设备枚举，实时流诊断请使用 sounddevice 后端")

    def open_input_stream(
        self,
        *,
        device_id: int | str | None,
        sample_rate: int,
        channels: int,
        blocksize: int,
        callback: AudioCallback,
    ) -> AudioStream:
        raise AudioBackendError("离线模拟后端仅用于设备枚举，实时流诊断请使用 sounddevice 后端")


def create_audio_backend(backend_name: str, sample_rate: int) -> AudioBackend:
    if backend_name == "sounddevice":
        return SoundDeviceBackend()
    if backend_name == "simulation":
        return SimulatedAudioBackend(sample_rate)
    raise ValueError(f"不支持的音频后端：{backend_name}")
