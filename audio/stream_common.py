import math
import threading
from dataclasses import dataclass

from audio.audio_backend import AudioCallbackTime, AudioStreamInfo


class AudioStreamError(RuntimeError):
    pass


@dataclass(frozen=True, slots=True)
class StreamTiming:
    sample_index: int
    frames: int
    callback_count: int
    sample_count: int
    current_time: float | None
    input_adc_time: float | None
    output_dac_time: float | None
    clock_domain: str | None = None
    min_frames: int | None = None
    max_frames: int | None = None


@dataclass(frozen=True, slots=True)
class StreamDiagnostics:
    direction: str
    requested_device_id: int | str | None
    requested_sample_rate: int
    requested_channels: int
    requested_blocksize: int
    requested_dtype: str
    requested_share_mode: str
    device_id: int | str | None
    device_name: str
    host_api: str
    native_channels: int | None
    stream_sample_rate: float | None
    stream_blocksize: int | None
    stream_latency_seconds: float | None
    input_latency_seconds: float | None
    output_latency_seconds: float | None
    endpoint_default_sample_rate: float | None
    windows_endpoint_mix_format: str
    hardware_buffer: str
    apo_state: str
    open_count: int
    start_count: int
    callback_count: int
    latest_callback_frames: int
    min_callback_frames: int | None
    max_callback_frames: int | None
    underflow_count: int
    overflow_count: int
    dropped_frames: int
    latest_timing: StreamTiming

    @classmethod
    def build(
        cls,
        *,
        direction: str,
        requested_device_id: int | str | None,
        requested_sample_rate: int,
        requested_channels: int,
        requested_blocksize: int,
        stream_info: AudioStreamInfo,
        open_count: int,
        start_count: int,
        timing: StreamTiming,
        underflow_count: int = 0,
        overflow_count: int = 0,
        dropped_frames: int = 0,
    ) -> "StreamDiagnostics":
        return cls(
            direction=direction,
            requested_device_id=requested_device_id,
            requested_sample_rate=requested_sample_rate,
            requested_channels=requested_channels,
            requested_blocksize=requested_blocksize,
            requested_dtype="float32",
            requested_share_mode="shared",
            device_id=stream_info.device_id,
            device_name=stream_info.device_name,
            host_api=stream_info.host_api,
            native_channels=stream_info.native_channels,
            stream_sample_rate=stream_info.sample_rate,
            stream_blocksize=stream_info.blocksize,
            stream_latency_seconds=stream_info.latency_seconds,
            input_latency_seconds=stream_info.input_latency_seconds,
            output_latency_seconds=stream_info.output_latency_seconds,
            endpoint_default_sample_rate=stream_info.endpoint_default_sample_rate,
            windows_endpoint_mix_format="unknown",
            hardware_buffer="unknown",
            apo_state="unknown",
            open_count=open_count,
            start_count=start_count,
            callback_count=timing.callback_count,
            latest_callback_frames=timing.frames,
            min_callback_frames=timing.min_frames,
            max_callback_frames=timing.max_frames,
            underflow_count=underflow_count,
            overflow_count=overflow_count,
            dropped_frames=dropped_frames,
            latest_timing=timing,
        )


class StreamTelemetry:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._sample_index = 0
        self._frames = 0
        self._callback_count = 0
        self._sample_count = 0
        self._current_time: float | None = None
        self._input_adc_time: float | None = None
        self._output_dac_time: float | None = None
        self._clock_domain: str | None = None
        self._min_frames: int | None = None
        self._max_frames: int | None = None

    def record(
        self,
        *,
        sample_index: int,
        frames: int,
        callback_count: int,
        sample_count: int,
        time_info: AudioCallbackTime,
    ) -> None:
        if not self._lock.acquire(blocking=False):
            return
        try:
            self._sample_index = sample_index
            self._frames = frames
            self._callback_count = callback_count
            self._sample_count = sample_count
            self._current_time = self._timestamp_or_none(time_info.current_time)
            self._input_adc_time = self._timestamp_or_none(time_info.input_buffer_adc_time)
            self._output_dac_time = self._timestamp_or_none(time_info.output_buffer_dac_time)
            self._clock_domain = time_info.clock_domain or None
            self._min_frames = frames if self._min_frames is None else min(self._min_frames, frames)
            self._max_frames = frames if self._max_frames is None else max(self._max_frames, frames)
        finally:
            self._lock.release()

    def snapshot(self) -> StreamTiming:
        with self._lock:
            return StreamTiming(
                sample_index=self._sample_index,
                frames=self._frames,
                callback_count=self._callback_count,
                sample_count=self._sample_count,
                current_time=self._current_time,
                input_adc_time=self._input_adc_time,
                output_dac_time=self._output_dac_time,
                clock_domain=self._clock_domain,
                min_frames=self._min_frames,
                max_frames=self._max_frames,
            )

    @staticmethod
    def _timestamp_or_none(value: float) -> float | None:
        return float(value) if math.isfinite(value) and value > 0 else None
