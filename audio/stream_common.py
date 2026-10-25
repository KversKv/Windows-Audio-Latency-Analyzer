import math
import threading
from dataclasses import dataclass

from audio.audio_backend import AudioCallbackTime


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
            )

    @staticmethod
    def _timestamp_or_none(value: float) -> float | None:
        return float(value) if math.isfinite(value) and value > 0 else None
