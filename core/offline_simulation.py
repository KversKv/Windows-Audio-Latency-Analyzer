import math
from dataclasses import dataclass

import numpy as np
from scipy.fft import next_fast_len

from audio.stimulus_generator import ChirpGenerator
from core.latency_analyzer import LatencyAnalyzer, LatencyMeasurement


@dataclass(frozen=True, slots=True)
class OfflineSimulationResult:
    reference: np.ndarray
    captured: np.ndarray
    true_delay_ms: float
    measurement: LatencyMeasurement

    @property
    def is_simulation(self) -> bool:
        return True


class OfflineLatencySimulator:
    def __init__(
        self,
        *,
        generator: ChirpGenerator,
        analyzer: LatencyAnalyzer,
        seed: int = 0,
    ) -> None:
        self.generator = generator
        self.analyzer = analyzer
        self.seed = seed

    def simulate(
        self,
        delay_ms: float,
        *,
        snr_db: float | None = 20.0,
        gain: float = 1.0,
        tail_ms: float = 20.0,
        compensation_ms: float = 0.0,
    ) -> OfflineSimulationResult:
        if not math.isfinite(delay_ms) or delay_ms < 0:
            raise ValueError("模拟延迟必须为大于等于 0 的有限数值")
        if snr_db is not None and not math.isfinite(snr_db):
            raise ValueError("模拟 SNR 必须为有限数值或 None")
        if not math.isfinite(gain):
            raise ValueError("模拟增益必须为有限数值")
        if not math.isfinite(tail_ms) or tail_ms <= 0:
            raise ValueError("模拟尾长必须为大于 0 的有限数值")

        reference = self.generator.samples
        delay_samples = delay_ms * self.generator.sample_rate / 1000
        tail_frames = max(1, round(tail_ms * self.generator.sample_rate / 1000))
        capture_frames = math.ceil(delay_samples) + reference.size + tail_frames
        delayed = self._fractional_delay(reference, delay_samples, capture_frames)

        rng = np.random.default_rng(self.seed)
        if snr_db is None or gain == 0:
            noise_amplitude = 0.0
        else:
            centered_reference = reference.astype(np.float64) - np.mean(reference)
            signal_rms = abs(gain) * float(np.sqrt(np.mean(centered_reference**2)))
            noise_amplitude = signal_rms / (10 ** (snr_db / 20))
        noise = rng.standard_normal(capture_frames) * noise_amplitude
        captured = (gain * delayed + noise).astype(np.float32)
        captured.setflags(write=False)
        measurement = self.analyzer.analyze(
            reference,
            captured,
            sample_rate=self.generator.sample_rate,
            compensation_ms=compensation_ms,
            is_simulation=True,
        )
        return OfflineSimulationResult(
            reference=reference,
            captured=captured,
            true_delay_ms=delay_ms,
            measurement=measurement,
        )

    @staticmethod
    def _fractional_delay(
        samples: np.ndarray,
        delay_samples: float,
        output_frames: int,
    ) -> np.ndarray:
        fft_size = next_fast_len(output_frames + samples.size)
        frequencies = np.fft.rfftfreq(fft_size)
        spectrum = np.fft.rfft(samples.astype(np.float64), n=fft_size)
        shifted_spectrum = spectrum * np.exp(-2j * np.pi * frequencies * delay_samples)
        return np.fft.irfft(shifted_spectrum, n=fft_size)[:output_frames]
