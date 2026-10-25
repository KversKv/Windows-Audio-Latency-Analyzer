import threading

import numpy as np


class AudioRingBuffer:
    def __init__(self, capacity_frames: int, channels: int) -> None:
        if capacity_frames < 1:
            raise ValueError("环形缓冲区容量必须大于 0")
        if channels < 1:
            raise ValueError("环形缓冲区声道数必须大于 0")
        self.capacity_frames = capacity_frames
        self.channels = channels
        self._buffer = np.zeros((capacity_frames, channels), dtype=np.float32)
        self._lock = threading.Lock()
        self._read_position = 0
        self._write_position = 0
        self._available = 0
        self._total_frames_received = 0
        self._total_frames_written = 0
        self._overwritten_frames = 0
        self._skipped_frames = 0
        self._callback_dropped_frames = 0

    @property
    def available(self) -> int:
        return self._available

    @property
    def total_frames_received(self) -> int:
        return self._total_frames_received

    @property
    def total_frames_written(self) -> int:
        return self._total_frames_written

    @property
    def overwritten_frames(self) -> int:
        return self._overwritten_frames

    @property
    def skipped_frames(self) -> int:
        return self._skipped_frames

    @property
    def callback_dropped_frames(self) -> int:
        return self._callback_dropped_frames

    @property
    def dropped_frames(self) -> int:
        return self._overwritten_frames + self._skipped_frames + self._callback_dropped_frames

    def write(self, samples: np.ndarray) -> int:
        if samples.ndim != 2 or samples.shape[1] != self.channels:
            raise ValueError("写入环形缓冲区的样本形状不匹配")
        if samples.dtype != np.float32:
            raise ValueError("环形缓冲区仅接受 float32 样本")
        original_frames = samples.shape[0]
        if original_frames == 0:
            return 0
        if not self._lock.acquire(blocking=False):
            self._total_frames_received += original_frames
            self._callback_dropped_frames += original_frames
            return 0

        try:
            self._total_frames_received += original_frames
            frames = original_frames
            if frames > self.capacity_frames:
                skipped = frames - self.capacity_frames
                self._skipped_frames += skipped
                self._overwritten_frames += self._available
                self._available = 0
                self._read_position = self._write_position
                samples = samples[skipped:]
                frames = self.capacity_frames
            else:
                overwrite = max(0, self._available + frames - self.capacity_frames)
                if overwrite:
                    self._read_position = (self._read_position + overwrite) % self.capacity_frames
                    self._available -= overwrite
                    self._overwritten_frames += overwrite

            remaining = frames
            source_position = 0
            while remaining:
                count = min(remaining, self.capacity_frames - self._write_position)
                self._buffer[self._write_position : self._write_position + count] = samples[
                    source_position : source_position + count
                ]
                self._write_position = (self._write_position + count) % self.capacity_frames
                source_position += count
                remaining -= count
            self._available += frames
            self._total_frames_written += frames
            return frames
        finally:
            self._lock.release()

    def read(self, frames: int) -> np.ndarray:
        if frames < 0:
            raise ValueError("读取帧数不能为负数")
        with self._lock:
            count = min(frames, self._available)
            result = np.empty((count, self.channels), dtype=np.float32)
            remaining = count
            destination_position = 0
            while remaining:
                chunk = min(remaining, self.capacity_frames - self._read_position)
                result[destination_position : destination_position + chunk] = self._buffer[
                    self._read_position : self._read_position + chunk
                ]
                self._read_position = (self._read_position + chunk) % self.capacity_frames
                destination_position += chunk
                remaining -= chunk
            self._available -= count
            return result

    def read_all(self) -> np.ndarray:
        return self.read(self.available)

    def clear(self) -> None:
        with self._lock:
            self._read_position = 0
            self._write_position = 0
            self._available = 0
