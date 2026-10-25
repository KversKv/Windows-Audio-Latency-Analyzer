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
        self._read_position = 0
        self._write_position = 0
        self._oldest_position = 0
        self._total_frames_received = 0
        self._total_frames_written = 0
        self._overwritten_frames = 0
        self._skipped_frames = 0

    @property
    def available(self) -> int:
        read_position = max(self._read_position, self._oldest_position)
        return min(
            self.capacity_frames,
            max(0, self._write_position - read_position),
        )

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
    def dropped_frames(self) -> int:
        return self._overwritten_frames + self._skipped_frames

    def write(self, samples: np.ndarray) -> int:
        if samples.ndim != 2 or samples.shape[1] != self.channels:
            raise ValueError("写入环形缓冲区的样本形状不匹配")
        if samples.dtype != np.float32:
            raise ValueError("环形缓冲区仅接受 float32 样本")
        original_frames = samples.shape[0]
        if original_frames == 0:
            return 0

        self._total_frames_received += original_frames
        write_position = self._write_position
        read_position = max(self._read_position, self._oldest_position)
        available = max(0, write_position - read_position)
        frames = original_frames
        if frames > self.capacity_frames:
            skipped = frames - self.capacity_frames
            self._skipped_frames += skipped
            overwritten = available
            samples = samples[skipped:]
            frames = self.capacity_frames
        else:
            overwritten = max(0, available + frames - self.capacity_frames)

        if overwritten:
            self._overwritten_frames += overwritten
            # 先发布覆盖边界；读者在拷贝后发现边界变化时会放弃该次读取。
            self._oldest_position = write_position + frames - self.capacity_frames

        remaining = frames
        source_position = 0
        buffer_position = write_position % self.capacity_frames
        while remaining:
            count = min(remaining, self.capacity_frames - buffer_position)
            self._buffer[buffer_position : buffer_position + count] = samples[
                source_position : source_position + count
            ]
            buffer_position = (buffer_position + count) % self.capacity_frames
            source_position += count
            remaining -= count
        self._total_frames_written += frames
        self._write_position = write_position + frames
        return frames

    def read(self, frames: int) -> np.ndarray:
        if frames < 0:
            raise ValueError("读取帧数不能为负数")
        for _ in range(3):
            oldest_position = self._oldest_position
            write_position = self._write_position
            read_position = max(self._read_position, oldest_position)
            count = min(frames, max(0, write_position - read_position))
            if count == 0:
                self._read_position = read_position
                return np.empty((0, self.channels), dtype=np.float32)

            result = np.empty((count, self.channels), dtype=np.float32)
            remaining = count
            destination_position = 0
            buffer_position = read_position % self.capacity_frames
            while remaining:
                chunk = min(remaining, self.capacity_frames - buffer_position)
                result[destination_position : destination_position + chunk] = self._buffer[
                    buffer_position : buffer_position + chunk
                ]
                buffer_position = (buffer_position + chunk) % self.capacity_frames
                destination_position += chunk
                remaining -= chunk

            if self._oldest_position != oldest_position:
                continue
            self._read_position = read_position + count
            return result
        return np.empty((0, self.channels), dtype=np.float32)

    def read_all(self) -> np.ndarray:
        return self.read(self.available)

    def clear(self) -> None:
        self._read_position = self._write_position
