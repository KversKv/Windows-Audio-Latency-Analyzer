import numpy as np


class BackgroundGenerator:
    def __init__(
        self,
        *,
        sample_rate: int,
        channels: int,
        amplitude: float,
        kind: str,
        duration_seconds: float = 2.0,
        low_cut_hz: float = 80.0,
        high_cut_hz: float = 8000.0,
        seed: int = 0,
    ) -> None:
        if sample_rate < 1:
            raise ValueError("背景音采样率必须大于 0")
        if channels < 1:
            raise ValueError("背景音声道数必须大于 0")
        if not 0 <= amplitude < 1:
            raise ValueError("背景音幅度必须在 0（含）到 1（不含）之间")
        if kind not in {"band_limited", "pink"}:
            raise ValueError(f"不支持的背景音类型：{kind}")
        if duration_seconds <= 0:
            raise ValueError("背景音循环时长必须大于 0")
        nyquist = sample_rate / 2
        effective_high_cut = min(high_cut_hz, nyquist)
        if not 0 <= low_cut_hz < effective_high_cut:
            raise ValueError("背景音频段必须位于 0 到奈奎斯特频率之间")

        self.sample_rate = sample_rate
        self.channels = channels
        self.peak_amplitude = float(amplitude)
        self.kind = kind
        frames = max(1, round(sample_rate * duration_seconds))
        self._cursor = 0
        self._samples = self._generate(
            frames=frames,
            low_cut_hz=low_cut_hz,
            high_cut_hz=effective_high_cut,
            seed=seed,
        )

    @property
    def buffer_frames(self) -> int:
        return self._samples.shape[0]

    def render(self, outdata: np.ndarray) -> None:
        if outdata.ndim != 2 or outdata.shape[1] != self.channels:
            raise ValueError("背景音输出缓冲形状不匹配")
        remaining = outdata.shape[0]
        destination_position = 0
        while remaining:
            count = min(remaining, self.buffer_frames - self._cursor)
            outdata[destination_position : destination_position + count] = self._samples[
                self._cursor : self._cursor + count
            ]
            self._cursor = (self._cursor + count) % self.buffer_frames
            destination_position += count
            remaining -= count

    def _generate(
        self,
        *,
        frames: int,
        low_cut_hz: float,
        high_cut_hz: float,
        seed: int,
    ) -> np.ndarray:
        rng = np.random.default_rng(seed)
        spectrum = np.fft.rfft(rng.standard_normal(frames))
        frequencies = np.fft.rfftfreq(frames, d=1 / self.sample_rate)
        if self.kind == "pink":
            nonzero = frequencies > 0
            spectrum[nonzero] /= np.sqrt(frequencies[nonzero])
        spectrum[(frequencies < low_cut_hz) | (frequencies > high_cut_hz)] = 0
        samples = np.fft.irfft(spectrum, n=frames)
        peak = float(np.max(np.abs(samples)))
        if peak > 0:
            samples *= self.peak_amplitude / peak
        samples = samples.astype(np.float32, copy=False)
        tiled = np.repeat(samples[:, np.newaxis], self.channels, axis=1)
        tiled.setflags(write=False)
        return tiled
