import math

import numpy as np
from scipy.signal import chirp


class ChirpGenerator:
    def __init__(
        self,
        *,
        sample_rate: int,
        start_frequency_hz: float,
        end_frequency_hz: float,
        duration_ms: float,
        amplitude: float,
        window: str,
    ) -> None:
        if sample_rate < 1:
            raise ValueError("Chirp 采样率必须大于 0")
        if not math.isfinite(start_frequency_hz) or not math.isfinite(end_frequency_hz):
            raise ValueError("Chirp 频率必须为有限数值")
        if not 0 < start_frequency_hz < end_frequency_hz < sample_rate / 2:
            raise ValueError("Chirp 频率必须递增且结束频率低于奈奎斯特频率")
        if not math.isfinite(duration_ms) or duration_ms <= 0:
            raise ValueError("Chirp 时长必须为大于 0 的有限数值")
        if not math.isfinite(amplitude) or not 0 < amplitude <= 1:
            raise ValueError("Chirp 幅度必须在 0 到 1 之间")
        if window not in {"hann", "none"}:
            raise ValueError(f"不支持的 Chirp 窗函数：{window}")

        self.sample_rate = sample_rate
        self.start_frequency_hz = start_frequency_hz
        self.end_frequency_hz = end_frequency_hz
        self.duration_ms = duration_ms
        self.amplitude = amplitude
        self.window = window
        self.frame_count = max(1, round(sample_rate * duration_ms / 1000))
        if window == "hann" and self.frame_count < 3:
            raise ValueError("Hann 窗 Chirp 至少需要 3 个样本")
        self._samples = self._generate()
        self._samples.setflags(write=False)

    @property
    def samples(self) -> np.ndarray:
        return self._samples

    def generate(self) -> np.ndarray:
        return self._samples

    def _generate(self) -> np.ndarray:
        duration_seconds = self.frame_count / self.sample_rate
        time_axis = np.arange(self.frame_count, dtype=np.float64) / self.sample_rate
        waveform = chirp(
            time_axis,
            f0=self.start_frequency_hz,
            f1=self.end_frequency_hz,
            t1=duration_seconds,
            method="linear",
            phi=-90,
        )
        if self.window == "hann":
            waveform *= np.hanning(self.frame_count)
        peak = float(np.max(np.abs(waveform)))
        if peak == 0:
            raise ValueError("Chirp 波形为空，请检查时长和窗函数")
        waveform *= self.amplitude / peak
        return waveform.astype(np.float32)
