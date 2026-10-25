import math
import queue
import threading
from collections import deque
from dataclasses import dataclass

import numpy as np

from audio.audio_backend import (
    AudioBackend,
    AudioBackendError,
    AudioCallbackTime,
    AudioStream,
    AudioStreamStatus,
)
from audio.background_generator import BackgroundGenerator
from audio.stream_common import AudioStreamError, StreamTelemetry, StreamTiming


@dataclass(slots=True)
class ScheduledStimulus:
    sequence: int
    samples: np.ndarray
    start_sample: int
    position: int = 0
    playback_time_seconds: float | None = None
    state: str = "scheduled"

    @property
    def frame_count(self) -> int:
        return self.samples.shape[0]

    @property
    def end_sample(self) -> int:
        return self.start_sample + self.frame_count


class PlaybackEngine:
    def __init__(
        self,
        backend: AudioBackend,
        *,
        device_id: int | str | None,
        sample_rate: int,
        channels: int,
        blocksize: int,
        background: BackgroundGenerator,
        warmup_seconds: float,
    ) -> None:
        if sample_rate < 1 or channels < 1 or blocksize < 0:
            raise ValueError("播放流采样率、声道数或块大小无效")
        if not 1.0 <= warmup_seconds <= 15.0:
            raise ValueError("预热时间必须在 1 到 15 秒之间")
        if background.sample_rate != sample_rate or background.channels != channels:
            raise ValueError("背景音与播放流参数不匹配")
        self._backend = backend
        self._device_id = device_id
        self._sample_rate = sample_rate
        self._channels = channels
        self._blocksize = blocksize
        self._background = background
        self._warmup_frames = round(sample_rate * warmup_seconds)
        self._stream: AudioStream | None = None
        self._running = False
        self._closed = False
        self._frames_rendered = 0
        self._callback_count = 0
        self._open_count = 0
        self._start_count = 0
        self._underflow_count = 0
        self._sequence = 0
        self._completed_stimulus_count = 0
        self._next_free_sample = 0
        self._scheduler_lock = threading.Lock()
        self._schedule_queue: queue.SimpleQueue[ScheduledStimulus] = queue.SimpleQueue()
        self._pending: deque[ScheduledStimulus] = deque()
        self._telemetry = StreamTelemetry()
        self._last_error: str | None = None
        self._callback_exception: Exception | None = None

    @property
    def sample_rate(self) -> int:
        return self._sample_rate

    @property
    def frames_rendered(self) -> int:
        return self._frames_rendered

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
    def underflow_count(self) -> int:
        return self._underflow_count

    @property
    def completed_stimulus_count(self) -> int:
        return self._completed_stimulus_count

    @property
    def is_running(self) -> bool:
        return self._running

    @property
    def is_warmed_up(self) -> bool:
        return self._running and self._frames_rendered >= self._warmup_frames

    @property
    def warmup_remaining_seconds(self) -> float:
        return max(0, self._warmup_frames - self._frames_rendered) / self._sample_rate

    @property
    def latest_timing(self) -> StreamTiming:
        return self._telemetry.snapshot()

    def open(self) -> None:
        if self._closed:
            raise AudioStreamError("播放引擎已关闭，不能重新打开")
        if self._stream is not None:
            raise AudioStreamError("播放流已经打开")
        self._stream = self._backend.open_output_stream(
            device_id=self._device_id,
            sample_rate=self._sample_rate,
            channels=self._channels,
            blocksize=self._blocksize,
            callback=self._output_callback,
        )
        self._open_count += 1

    def start(self) -> None:
        if self._closed or self._stream is None:
            raise AudioStreamError("播放流尚未打开或已经关闭")
        if self._running:
            raise AudioStreamError("播放流已经启动")
        self._running = True
        try:
            self._stream.start()
        except Exception:
            self._running = False
            raise
        self._start_count += 1

    def schedule_stimulus(
        self,
        samples: np.ndarray,
        *,
        start_sample: int | None = None,
        lead_frames: int = 0,
    ) -> ScheduledStimulus:
        self.check_health()
        if not self._running:
            raise AudioStreamError("播放流未运行，不能调度刺激信号")
        if not self.is_warmed_up:
            raise AudioStreamError("连续播放流尚未完成预热")
        if lead_frames < 0:
            raise ValueError("刺激信号提前帧数不能为负数")
        stimulus = self._prepare_samples(samples)

        with self._scheduler_lock:
            current_sample = self._frames_rendered
            if start_sample is None:
                start_sample = max(current_sample + lead_frames, self._next_free_sample)
            if start_sample < current_sample:
                raise ValueError("刺激信号起始样本索引早于当前播放位置")
            if start_sample < self._next_free_sample:
                raise ValueError("刺激信号时段与已调度信号重叠")
            self._sequence += 1
            scheduled = ScheduledStimulus(
                sequence=self._sequence,
                samples=stimulus,
                start_sample=start_sample,
            )
            self._next_free_sample = scheduled.end_sample
            self._schedule_queue.put(scheduled)
            return scheduled

    def check_health(self) -> None:
        if self._callback_exception is not None:
            raise AudioStreamError("播放回调处理失败") from self._callback_exception
        if self._last_error is not None:
            raise AudioStreamError(self._last_error)
        if self._closed:
            raise AudioStreamError("播放流已关闭")
        if self._stream is None:
            raise AudioStreamError("播放流尚未打开")
        callback_error = self._stream.callback_error
        if callback_error is not None:
            raise AudioStreamError("PortAudio 播放回调失败") from callback_error
        if self._running and not self._stream.is_active:
            raise AudioStreamError("播放流已意外停止，可能设备已断开或被关闭")

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
        self._pending.clear()
        while not self._schedule_queue.empty():
            self._schedule_queue.get_nowait()
        if error is not None:
            raise error

    def _prepare_samples(self, samples: np.ndarray) -> np.ndarray:
        stimulus = np.asarray(samples, dtype=np.float32)
        if stimulus.ndim == 1:
            stimulus = stimulus[:, np.newaxis]
        if stimulus.ndim != 2 or stimulus.shape[0] == 0 or stimulus.shape[1] != self._channels:
            raise ValueError("刺激信号必须为非空且声道数匹配的二维数组")
        if not np.isfinite(stimulus).all():
            raise ValueError("刺激信号包含非有限数值")
        peak = float(np.max(np.abs(stimulus)))
        if peak + self._background.peak_amplitude > 1.0:
            raise ValueError("刺激信号与背景音叠加可能削波")
        stimulus = np.array(stimulus, dtype=np.float32, copy=True, order="C")
        stimulus.setflags(write=False)
        return stimulus

    def _output_callback(
        self,
        outdata: np.ndarray,
        frames: int,
        time_info: AudioCallbackTime,
        status: AudioStreamStatus,
    ) -> None:
        if self._closed:
            outdata.fill(0)
            return
        block_start = self._frames_rendered
        block_end = block_start + frames
        self._callback_count += 1
        if status.output_underflow:
            self._underflow_count += 1
            self._last_error = self._last_error or "检测到输出欠载，本次连续流已不可靠"
        self._telemetry.record(
            sample_index=block_start,
            frames=frames,
            callback_count=self._callback_count,
            sample_count=block_end,
            time_info=time_info,
        )
        try:
            self._background.render(outdata)
            while not self._schedule_queue.empty():
                try:
                    self._pending.append(self._schedule_queue.get_nowait())
                except queue.Empty:
                    break
            self._render_stimuli(outdata, block_start, block_end, time_info)
        except Exception as exc:
            self._callback_exception = exc
            outdata.fill(0)
        finally:
            self._frames_rendered = block_end

    def _render_stimuli(
        self,
        outdata: np.ndarray,
        block_start: int,
        block_end: int,
        time_info: AudioCallbackTime,
    ) -> None:
        frames = outdata.shape[0]
        while self._pending:
            scheduled = self._pending[0]
            next_sample = scheduled.start_sample + scheduled.position
            if next_sample >= block_end:
                break
            if next_sample < block_start:
                scheduled.state = "missed"
                self._pending.popleft()
                self._last_error = self._last_error or f"刺激信号 {scheduled.sequence} 错过调度位置"
                continue

            offset = next_sample - block_start
            count = min(scheduled.frame_count - scheduled.position, frames - offset)
            if scheduled.position == 0:
                dac_time = time_info.output_buffer_dac_time
                if math.isfinite(dac_time) and dac_time > 0:
                    scheduled.playback_time_seconds = dac_time + offset / self._sample_rate
                scheduled.state = "rendering"
            outdata[offset : offset + count] += scheduled.samples[
                scheduled.position : scheduled.position + count
            ]
            scheduled.position += count
            if scheduled.position == scheduled.frame_count:
                scheduled.state = "completed"
                self._completed_stimulus_count += 1
                self._pending.popleft()
            else:
                break
