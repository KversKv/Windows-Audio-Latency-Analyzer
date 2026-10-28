import importlib
import logging
import os
from typing import Any
from uuid import uuid4

import numpy as np

from audio.audio_backend import (
    AudioBackend,
    AudioBackendError,
    AudioCallback,
    AudioCallbackTime,
    AudioDevice,
    AudioStream,
    AudioStreamInfo,
    AudioStreamStatus,
    SoftwareLoopbackCapability,
)

logger = logging.getLogger(__name__)


def _load_pyaudiowpatch() -> Any:
    return importlib.import_module("pyaudiowpatch")


def detect_software_loopback_capability() -> SoftwareLoopbackCapability:
    if os.name != "nt":
        return SoftwareLoopbackCapability(
            supported=False,
            detail="Software Loop 仅支持 Windows WASAPI，当前系统不可用",
        )
    try:
        api = _load_pyaudiowpatch()
    except (ImportError, OSError) as exc:
        return SoftwareLoopbackCapability(
            supported=False,
            detail=f"PyAudioWPatch 不可用，无法采集 WASAPI 数字环回：{exc}",
        )

    audio = None
    try:
        audio = api.PyAudio()
        audio.get_host_api_info_by_type(api.paWASAPI)
        loopbacks = list(audio.get_loopback_device_info_generator())
    except Exception as exc:
        logger.warning("PyAudioWPatch 数字环回能力探测失败", exc_info=True)
        return SoftwareLoopbackCapability(
            supported=False,
            detail=f"无法访问 WASAPI 数字环回端点：{exc}",
        )
    finally:
        if audio is not None:
            audio.terminate()

    if not loopbacks:
        return SoftwareLoopbackCapability(
            supported=False,
            detail="当前系统没有可用的 WASAPI 数字环回端点",
        )
    return SoftwareLoopbackCapability(
        supported=True,
        detail=f"已检测到 {len(loopbacks)} 个 WASAPI 数字环回端点",
    )


class PyAudioWPatchStream(AudioStream):
    def __init__(
        self,
        backend: "PyAudioWPatchBackend",
        raw_stream: Any,
        *,
        callback: AudioCallback,
        channels: int,
        blocksize: int,
        is_output: bool,
        endpoint: dict[str, Any],
        native_input_channels: int | None = None,
    ) -> None:
        self._backend = backend
        self._api = backend._api
        self._stream = raw_stream
        self._callback = callback
        self._endpoint = endpoint
        self._channels = channels
        self._is_output = is_output
        self._native_input_channels = native_input_channels
        self._callback_time = AudioCallbackTime(clock_domain=backend.clock_domain)
        self._callback_status = AudioStreamStatus()
        self._callback_error: Exception | None = None
        self._closed = False
        buffer_frames = max(blocksize, 0)
        self._output_buffer = (
            np.empty((buffer_frames, channels), dtype=np.float32)
            if is_output
            else None
        )
        self._input_map_buffer = (
            np.empty((buffer_frames, channels), dtype=np.float32)
            if not is_output and native_input_channels != channels
            else None
        )

    @property
    def is_active(self) -> bool:
        if self._closed:
            return False
        try:
            return bool(self._stream.is_active())
        except Exception as exc:
            raise AudioBackendError(f"无法读取数字环回流状态：{exc}") from exc

    @property
    def is_closed(self) -> bool:
        return self._closed

    @property
    def callback_error(self) -> Exception | None:
        return self._callback_error

    @property
    def stream_info(self) -> AudioStreamInfo:
        try:
            default_sample_rate = self._endpoint.get("defaultSampleRate")
            return AudioStreamInfo(
                device_id=int(self._endpoint["index"]),
                device_name=str(self._endpoint["name"]),
                host_api="Windows WASAPI",
                native_channels=(
                    self._channels if self._is_output else self._native_input_channels
                ),
                input_latency_seconds=(
                    None if self._is_output else float(self._stream.get_input_latency())
                ),
                output_latency_seconds=(
                    float(self._stream.get_output_latency()) if self._is_output else None
                ),
                endpoint_default_sample_rate=(
                    float(default_sample_rate) if default_sample_rate is not None else None
                ),
            )
        except Exception as exc:
            raise AudioBackendError(f"无法读取数字环回流信息：{exc}") from exc

    def start(self) -> None:
        try:
            self._stream.start_stream()
        except Exception as exc:
            raise AudioBackendError(f"无法启动数字环回流：{exc}") from exc

    def stop(self) -> None:
        if self._closed:
            return
        try:
            if self._stream.is_active():
                self._stream.stop_stream()
        except Exception as exc:
            raise AudioBackendError(f"无法停止数字环回流：{exc}") from exc

    def close(self) -> None:
        if self._closed:
            return
        try:
            self._stream.close()
        except Exception as exc:
            raise AudioBackendError(f"无法关闭数字环回流：{exc}") from exc
        finally:
            self._closed = True
            self._backend._release_stream(self)

    def _output_callback(
        self,
        _in_data: bytes | None,
        frames: int,
        raw_time: dict[str, float],
        raw_status: int,
    ) -> tuple[bytes, int]:
        if self._callback_error is not None or self._closed:
            return bytes(frames * self._channels * 4), self._api.paAbort
        try:
            self._update_callback_metadata(raw_time, raw_status)
            if self._output_buffer is None or self._output_buffer.shape[0] < frames:
                self._output_buffer = np.empty((frames, self._channels), dtype=np.float32)
            outdata = self._output_buffer[:frames]
            self._callback(outdata, frames, self._callback_time, self._callback_status)
            return outdata.tobytes(), self._api.paContinue
        except Exception as exc:
            self._callback_error = exc
            return bytes(frames * self._channels * 4), self._api.paAbort

    def _input_callback(
        self,
        in_data: bytes,
        frames: int,
        raw_time: dict[str, float],
        raw_status: int,
    ) -> tuple[None, int]:
        if self._callback_error is not None or self._closed:
            return None, self._api.paAbort
        try:
            self._update_callback_metadata(raw_time, raw_status)
            native_channels = self._native_input_channels
            if native_channels is None:
                raise AudioBackendError("数字环回流缺少原生声道配置")
            native_samples = np.frombuffer(in_data, dtype=np.float32).reshape(
                frames,
                native_channels,
            )
            samples = self._map_input_channels(native_samples, frames)
            self._callback(samples, frames, self._callback_time, self._callback_status)
            return None, self._api.paContinue
        except Exception as exc:
            self._callback_error = exc
            return None, self._api.paAbort

    def _map_input_channels(self, samples: np.ndarray, frames: int) -> np.ndarray:
        native_channels = samples.shape[1]
        if native_channels == self._channels:
            return samples
        if native_channels > self._channels:
            return samples[:, : self._channels]
        if self._input_map_buffer is None or self._input_map_buffer.shape[0] < frames:
            self._input_map_buffer = np.empty((frames, self._channels), dtype=np.float32)
        mapped = self._input_map_buffer[:frames]
        for channel in range(self._channels):
            mapped[:, channel] = samples[:, min(channel, native_channels - 1)]
        return mapped

    def _update_callback_metadata(
        self,
        raw_time: dict[str, float],
        raw_status: int,
    ) -> None:
        self._callback_time.input_buffer_adc_time = float(raw_time["input_buffer_adc_time"])
        self._callback_time.current_time = float(raw_time["current_time"])
        self._callback_time.output_buffer_dac_time = float(raw_time["output_buffer_dac_time"])
        flags = int(raw_status)
        self._callback_status.input_underflow = bool(flags & self._api.paInputUnderflow)
        self._callback_status.input_overflow = bool(flags & self._api.paInputOverflow)
        self._callback_status.output_underflow = bool(flags & self._api.paOutputUnderflow)
        self._callback_status.output_overflow = bool(flags & self._api.paOutputOverflow)
        self._callback_status.priming_output = bool(flags & self._api.paPrimingOutput)


class PyAudioWPatchBackend(AudioBackend):
    display_name = "PyAudioWPatch / WASAPI 数字环回"

    def __init__(self, output_device: AudioDevice) -> None:
        if output_device.is_simulation or "wasapi" not in output_device.host_api.lower():
            raise AudioBackendError("Software Loop 仅支持真实 Windows WASAPI 播放设备")
        self._output_device = output_device
        self._api = _load_pyaudiowpatch()
        try:
            self._audio = self._api.PyAudio()
        except Exception as exc:
            raise AudioBackendError(f"无法初始化 PyAudioWPatch：{exc}") from exc
        self._clock_domain = f"pyaudiowpatch:{uuid4().hex}"
        self._streams: set[PyAudioWPatchStream] = set()
        self._output_endpoint: dict[str, Any] | None = None
        self._closed = False

    @property
    def clock_domain(self) -> str:
        return self._clock_domain

    def list_devices(self) -> list[AudioDevice]:
        return [self._output_device]

    def software_loopback_capability(self) -> SoftwareLoopbackCapability:
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
        self._ensure_open()
        if device_id != self._output_device.identifier:
            raise AudioBackendError("数字环回后端只能打开会话绑定的播放设备")
        try:
            endpoint = self._find_output_endpoint()
            raw_stream = self._audio.open(
                format=self._api.paFloat32,
                channels=channels,
                rate=sample_rate,
                output=True,
                output_device_index=endpoint["index"],
                frames_per_buffer=blocksize,
                start=False,
                stream_callback=self._output_callback_proxy,
            )
        except Exception as exc:
            logger.error("打开 WASAPI 数字环回播放流失败", exc_info=True)
            raise AudioBackendError(f"无法打开所选播放设备的 WASAPI 输出：{exc}") from exc
        self._output_endpoint = endpoint
        stream = PyAudioWPatchStream(
            self,
            raw_stream,
            callback=callback,
            channels=channels,
            blocksize=blocksize,
            is_output=True,
            endpoint=endpoint,
        )
        self._streams.add(stream)
        return stream

    def open_input_stream(
        self,
        *,
        device_id: int | str | None,
        sample_rate: int,
        channels: int,
        blocksize: int,
        callback: AudioCallback,
    ) -> AudioStream:
        self._ensure_open()
        if device_id is not None:
            raise AudioBackendError("Software Loop 禁止选择麦克风或其他普通输入设备")
        if self._output_endpoint is None:
            raise AudioBackendError("必须先打开绑定的播放流，再打开其数字环回采集流")
        try:
            endpoint = self._find_loopback_endpoint(self._output_endpoint)
            native_channels = int(endpoint["maxInputChannels"])
            raw_stream = self._audio.open(
                format=self._api.paFloat32,
                channels=native_channels,
                rate=sample_rate,
                input=True,
                input_device_index=endpoint["index"],
                frames_per_buffer=blocksize,
                start=False,
                stream_callback=self._input_callback_proxy,
            )
        except Exception as exc:
            logger.error("打开 WASAPI 数字环回采集流失败", exc_info=True)
            raise AudioBackendError(f"无法打开所选播放设备的数字环回：{exc}") from exc
        stream = PyAudioWPatchStream(
            self,
            raw_stream,
            callback=callback,
            channels=channels,
            blocksize=blocksize,
            is_output=False,
            endpoint=endpoint,
            native_input_channels=native_channels,
        )
        self._streams.add(stream)
        return stream

    def close(self) -> None:
        if self._closed:
            return
        errors: list[Exception] = []
        for stream in list(self._streams):
            try:
                stream.close()
            except Exception as exc:
                errors.append(exc)
        try:
            self._audio.terminate()
        except Exception as exc:
            errors.append(exc)
        finally:
            self._closed = True
        if errors:
            raise AudioBackendError(f"释放 PyAudioWPatch 资源失败：{errors[0]}") from errors[0]

    def _release_stream(self, stream: PyAudioWPatchStream) -> None:
        self._streams.discard(stream)

    def _ensure_open(self) -> None:
        if self._closed:
            raise AudioBackendError("PyAudioWPatch 后端已经关闭")

    def _find_output_endpoint(self) -> dict[str, Any]:
        candidates = [
            device
            for device in self._audio.get_device_info_generator_by_host_api(
                host_api_type=self._api.paWASAPI
            )
            if not device["isLoopbackDevice"]
            and int(device["maxOutputChannels"]) > 0
            and device["name"] == self._output_device.name
        ]
        if len(candidates) != 1:
            raise AudioBackendError(
                f"所选播放设备无法唯一匹配 WASAPI 端点：{self._output_device.name}"
            )
        return candidates[0]

    def _find_loopback_endpoint(self, output_endpoint: dict[str, Any]) -> dict[str, Any]:
        expected_name = f"{output_endpoint['name']} [Loopback]"
        candidates = [
            device
            for device in self._audio.get_loopback_device_info_generator()
            if device["name"] == expected_name and int(device["maxInputChannels"]) > 0
        ]
        if len(candidates) != 1:
            raise AudioBackendError(
                f"所选播放设备无法唯一匹配其 WASAPI [Loopback] 端点：{expected_name}"
            )
        return candidates[0]

    def _output_callback_proxy(
        self,
        in_data: bytes | None,
        frames: int,
        raw_time: dict[str, float],
        raw_status: int,
    ) -> tuple[bytes, int]:
        stream = self._stream_for_callback(raw_time, is_output=True)
        return stream._output_callback(in_data, frames, raw_time, raw_status)

    def _input_callback_proxy(
        self,
        in_data: bytes,
        frames: int,
        raw_time: dict[str, float],
        raw_status: int,
    ) -> tuple[None, int]:
        stream = self._stream_for_callback(raw_time, is_output=False)
        return stream._input_callback(in_data, frames, raw_time, raw_status)

    def _stream_for_callback(
        self,
        raw_time: dict[str, float],
        *,
        is_output: bool,
    ) -> PyAudioWPatchStream:
        for stream in self._streams:
            if stream._is_output == is_output:
                return stream
        raise AudioBackendError(f"找不到数字环回{'播放' if is_output else '采集'}回调对应的流")
