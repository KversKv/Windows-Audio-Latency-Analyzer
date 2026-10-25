from dataclasses import dataclass

import numpy as np

from audio.audio_backend import (
    AudioBackend,
    AudioBackendError,
    AudioCallbackTime,
    AudioStream,
    AudioStreamStatus,
)
from audio.ring_buffer import AudioRingBuffer
from audio.stream_common import AudioStreamError, StreamTelemetry, StreamTiming


@dataclass(frozen=True, slots=True)
class CapturedAudioBlock:
    samples: np.ndarray
    start_sample: int | None
    dropped_frames: int

    @property
    def end_sample(self) -> int | None:
        if self.start_sample is None:
            return None
        return self.start_sample + self.samples.shape[0]


class CaptureEngine:
    def __init__(
        self,
        backend: AudioBackend,
        *,
        device_id: int | str | None,
        sample_rate: int,
        channels: int,
        blocksize: int,
        capacity_frames: int,
    ) -> None:
        if sample_rate < 1 or channels < 1 or blocksize < 0:
            raise ValueError("录音流采样率、声道数或块大小无效")
        self._backend = backend
        self._device_id = device_id
        self._sample_rate = sample_rate
        self._channels = channels
        self._blocksize = blocksize
        self._ring_buffer = AudioRingBuffer(capacity_frames, channels)
        self._stream: AudioStream | None = None
        self._running = False
        self._closed = False
        self._frames_captured = 0
        self._frames_read = 0
        self._read_position_valid = True
        self._callback_count = 0
        self._open_count = 0
        self._start_count = 0
        self._overflow_count = 0
        self._telemetry = StreamTelemetry()
        self._last_error: str | None = None
        self._callback_exception: Exception | None = None

    @property
    def frames_captured(self) -> int:
        return self._frames_captured

    @property
    def callback_count(self) -> int:
        return self._callback_count

    @property
    def open_count(self) -> int:
        return self._open_count

    @property
    def start_count(self) -> int:
        return self._start_count

    @property
    def overflow_count(self) -> int:
        return self._overflow_count

    @property
    def available(self) -> int:
        return self._ring_buffer.available

    @property
    def dropped_frames(self) -> int:
        return self._ring_buffer.dropped_frames

    @property
    def is_running(self) -> bool:
        return self._running

    @property
    def latest_timing(self) -> StreamTiming:
        return self._telemetry.snapshot()

    def open(self) -> None:
        if self._closed:
            raise AudioStreamError("采集引擎已关闭，不能重新打开")
        if self._stream is not None:
            raise AudioStreamError("录音流已经打开")
        self._stream = self._backend.open_input_stream(
            device_id=self._device_id,
            sample_rate=self._sample_rate,
            channels=self._channels,
            blocksize=self._blocksize,
            callback=self._input_callback,
        )
        self._open_count += 1

    def start(self) -> None:
        if self._closed or self._stream is None:
            raise AudioStreamError("录音流尚未打开或已经关闭")
        if self._running:
            raise AudioStreamError("录音流已经启动")
        self._running = True
        try:
            self._stream.start()
        except Exception:
            self._running = False
            raise
        self._start_count += 1

    def read(self, frames: int) -> np.ndarray:
        return self.read_with_metadata(frames).samples

    def read_all(self) -> np.ndarray:
        return self.read_all_with_metadata().samples

    def read_with_metadata(self, frames: int) -> CapturedAudioBlock:
        samples = self._ring_buffer.read(frames)
        dropped_frames = self._ring_buffer.dropped_frames
        if dropped_frames:
            self._read_position_valid = False
        start_sample = self._frames_read if self._read_position_valid else None
        self._frames_read += samples.shape[0]
        return CapturedAudioBlock(
            samples=samples,
            start_sample=start_sample,
            dropped_frames=dropped_frames,
        )

    def read_all_with_metadata(self) -> CapturedAudioBlock:
        return self.read_with_metadata(self.available)

    def check_health(self) -> None:
        if self._callback_exception is not None:
            raise AudioStreamError("采集回调处理失败") from self._callback_exception
        if self._last_error is not None:
            raise AudioStreamError(self._last_error)
        if self._ring_buffer.dropped_frames:
            raise AudioStreamError(f"采集环形缓冲区已丢失 {self._ring_buffer.dropped_frames} 帧")
        if self._closed:
            raise AudioStreamError("录音流已关闭")
        if self._stream is None:
            raise AudioStreamError("录音流尚未打开")
        callback_error = self._stream.callback_error
        if callback_error is not None:
            raise AudioStreamError("PortAudio 采集回调失败") from callback_error
        if self._running and not self._stream.is_active:
            raise AudioStreamError("录音流已意外停止，可能设备已断开或被关闭")

    def stop(self) -> None:
        self._running = False
        if self._stream is not None and not self._stream.is_closed:
            self._stream.stop()

    def close(self) -> None:
        if self._closed:
            return
        self._running = False
        error: AudioBackendError | None = None
        if self._stream is not None and not self._stream.is_closed:
            try:
                self._stream.stop()
            except AudioBackendError as exc:
                error = exc
            try:
                self._stream.close()
            except AudioBackendError as exc:
                error = error or exc
        self._closed = True
        if error is not None:
            raise error

    def _input_callback(
        self,
        indata: np.ndarray,
        frames: int,
        time_info: AudioCallbackTime,
        status: AudioStreamStatus,
    ) -> None:
        if self._closed:
            return
        block_start = self._frames_captured
        block_end = block_start + frames
        self._callback_count += 1
        if status.input_overflow:
            self._overflow_count += 1
            self._last_error = self._last_error or "检测到输入溢出，本次采集已不完整"
        self._telemetry.record(
            sample_index=block_start,
            frames=frames,
            callback_count=self._callback_count,
            sample_count=block_end,
            time_info=time_info,
        )
        try:
            self._ring_buffer.write(indata)
        except Exception as exc:
            self._callback_exception = exc
        finally:
            self._frames_captured = block_end
