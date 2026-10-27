import math
from dataclasses import dataclass
from datetime import datetime, timezone

import numpy as np
from scipy.signal import correlate

from audio.audio_synchronizer import AudioSynchronizer


@dataclass(frozen=True, slots=True)
class LatencyMeasurement:
    raw_latency_ms: float | None
    compensated_latency_ms: float | None
    compensation_ms: float
    correlation_lag_samples: float | None
    correlation_peak: float
    correlation_ambiguity_ratio: float
    signal_amplitude: float
    noise_floor: float
    snr_db: float | None
    is_valid: bool
    invalid_reason: str | None
    timestamp: datetime
    playback_timestamp_seconds: float | None
    capture_timestamp_seconds: float | None
    is_simulation: bool
    timing_basis: str
    timing_limitations: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class _Detection:
    lag_samples: float | None = None
    peak_index: int | None = None
    correlation_peak: float = 0.0
    ambiguity_ratio: float = 0.0
    signal_amplitude: float = 0.0
    noise_floor: float = 0.0
    snr_db: float | None = None
    failure_reason: str | None = None


class LatencyAnalyzer:
    def __init__(
        self,
        *,
        min_snr_db: float,
        min_correlation: float,
        min_latency_ms: float,
        max_latency_ms: float,
        max_ambiguity_ratio: float = 0.9,
    ) -> None:
        if not math.isfinite(min_snr_db):
            raise ValueError("最低 SNR 必须为有限数值")
        if not math.isfinite(min_correlation) or not 0 < min_correlation <= 1:
            raise ValueError("最低相关系数必须在 0 到 1 之间")
        if not math.isfinite(min_latency_ms) or min_latency_ms < 0:
            raise ValueError("最小有效延迟不能为负数")
        if not math.isfinite(max_latency_ms) or max_latency_ms <= min_latency_ms:
            raise ValueError("最大有效延迟必须大于最小有效延迟")
        if not math.isfinite(max_ambiguity_ratio) or not 0 < max_ambiguity_ratio <= 1:
            raise ValueError("相关歧义阈值必须在 0 到 1 之间")
        self.min_snr_db = min_snr_db
        self.min_correlation = min_correlation
        self.min_latency_ms = min_latency_ms
        self.max_latency_ms = max_latency_ms
        self.max_ambiguity_ratio = max_ambiguity_ratio

    def analyze(
        self,
        reference: np.ndarray,
        captured: np.ndarray,
        *,
        sample_rate: int,
        compensation_ms: float = 0.0,
        is_simulation: bool = False,
        synchronizer: AudioSynchronizer | None = None,
        playback_sample_index: int | None = None,
        capture_window_start_sample: int | None = None,
        measurement_timestamp: datetime | None = None,
    ) -> LatencyMeasurement:
        if sample_rate < 1:
            raise ValueError("分析采样率必须大于 0")
        if not math.isfinite(compensation_ms):
            raise ValueError("补偿值必须为有限数值")
        timestamp = measurement_timestamp or datetime.now(timezone.utc)
        if timestamp.tzinfo is None or timestamp.utcoffset() is None:
            raise ValueError("测量时间必须包含时区")
        if synchronizer is None:
            if playback_sample_index is not None or capture_window_start_sample is not None:
                raise ValueError("样本时间映射必须同时提供同步器")
        else:
            if playback_sample_index is None or capture_window_start_sample is None:
                raise ValueError("同步分析必须提供播放和采集窗口起始样本索引")

        reference_samples = self._validate_reference(reference)
        capture_samples = np.asarray(captured, dtype=np.float64)
        if capture_samples.ndim != 1:
            raise ValueError("分析采集波形必须是一维单声道数组")
        detection = self._detect(reference_samples, capture_samples)

        raw_latency_ms: float | None = None
        playback_timestamp_seconds: float | None = None
        capture_timestamp_seconds: float | None = None
        if synchronizer is not None:
            playback_timestamp_seconds = synchronizer.playback_time_at(playback_sample_index)
            timing_basis = synchronizer.timing_basis or (
                "synthetic_dac_adc_timestamps"
                if is_simulation
                else "dac_adc_stream_timestamps"
            )
            timing_limitations = synchronizer.limitations
            if is_simulation:
                timing_limitations = (*timing_limitations, "synthetic_time_axis")
        elif is_simulation:
            timing_basis = "synthetic_sample_index"
            timing_limitations = ("synthetic_time_axis",)
        else:
            timing_basis = "relative_sample_index"
            timing_limitations = ("relative_sample_time_only",)

        reasons: list[str] = []
        if detection.failure_reason is not None:
            reasons.append(detection.failure_reason)
        elif detection.lag_samples is not None and detection.peak_index is not None:
            no_signal = detection.correlation_peak <= 1e-6 or (
                detection.signal_amplitude <= 1e-12
                and detection.correlation_peak < self.min_correlation
            )
            if no_signal:
                reasons.append("no_signal")
            else:
                if synchronizer is not None:
                    synchronized = synchronizer.synchronize(
                        playback_sample_index=playback_sample_index,
                        capture_sample_index=capture_window_start_sample + detection.lag_samples,
                    )
                    raw_latency_ms = synchronized.raw_latency_ms
                    capture_timestamp_seconds = synchronized.capture_timestamp_seconds
                else:
                    raw_latency_ms = detection.lag_samples * 1000 / sample_rate

                if detection.snr_db is None or detection.snr_db < self.min_snr_db:
                    reasons.append("low_snr")
                if detection.correlation_peak < self.min_correlation:
                    reasons.append("low_correlation")
                if detection.ambiguity_ratio > self.max_ambiguity_ratio:
                    reasons.append("ambiguous_correlation")
                if not self.min_latency_ms <= raw_latency_ms <= self.max_latency_ms:
                    reasons.append("latency_out_of_range")

        compensated_latency_ms = (
            raw_latency_ms - compensation_ms if raw_latency_ms is not None else None
        )
        return LatencyMeasurement(
            raw_latency_ms=raw_latency_ms,
            compensated_latency_ms=compensated_latency_ms,
            compensation_ms=compensation_ms,
            correlation_lag_samples=detection.lag_samples,
            correlation_peak=detection.correlation_peak,
            correlation_ambiguity_ratio=detection.ambiguity_ratio,
            signal_amplitude=detection.signal_amplitude,
            noise_floor=detection.noise_floor,
            snr_db=detection.snr_db,
            is_valid=not reasons,
            invalid_reason=";".join(reasons) if reasons else None,
            timestamp=timestamp,
            playback_timestamp_seconds=playback_timestamp_seconds,
            capture_timestamp_seconds=capture_timestamp_seconds,
            is_simulation=is_simulation,
            timing_basis=timing_basis,
            timing_limitations=timing_limitations,
        )

    def _detect(self, reference: np.ndarray, captured: np.ndarray) -> _Detection:
        if not np.isfinite(captured).all():
            return _Detection(failure_reason="non_finite_capture")
        if captured.size == 0 or not np.any(captured):
            return _Detection(failure_reason="no_signal")
        if captured.size < reference.size:
            return _Detection(failure_reason="capture_too_short")

        centered_reference = reference - np.mean(reference)
        reference_norm = float(np.linalg.norm(centered_reference))
        raw_correlation = correlate(captured, centered_reference, mode="valid", method="fft")
        cumulative = np.concatenate(([0.0], np.cumsum(captured)))
        cumulative_squares = np.concatenate(([0.0], np.cumsum(captured * captured)))
        window_length = reference.size
        local_sum = cumulative[window_length:] - cumulative[:-window_length]
        local_square_sum = (
            cumulative_squares[window_length:] - cumulative_squares[:-window_length]
        )
        local_energy = np.maximum(local_square_sum - local_sum * local_sum / window_length, 0)
        denominator = reference_norm * np.sqrt(local_energy)
        normalized = np.zeros_like(raw_correlation)
        np.divide(raw_correlation, denominator, out=normalized, where=denominator > 0)
        normalized = np.clip(normalized, -1, 1)

        magnitudes = np.abs(normalized)
        peak_index = int(np.argmax(magnitudes))
        correlation_peak = float(magnitudes[peak_index])
        lag_samples = float(peak_index) + self._interpolate_peak(magnitudes, peak_index)
        ambiguity_ratio = self._ambiguity_ratio(
            centered_reference,
            magnitudes,
            peak_index,
            correlation_peak,
        )
        signal_amplitude, noise_floor, snr_db = self._estimate_levels(
            captured,
            peak_index,
            window_length,
        )
        return _Detection(
            lag_samples=lag_samples,
            peak_index=peak_index,
            correlation_peak=correlation_peak,
            ambiguity_ratio=ambiguity_ratio,
            signal_amplitude=signal_amplitude,
            noise_floor=noise_floor,
            snr_db=snr_db,
        )

    @staticmethod
    def _validate_reference(reference: np.ndarray) -> np.ndarray:
        samples = np.asarray(reference, dtype=np.float64)
        if samples.ndim != 1 or samples.size < 3:
            raise ValueError("参考波形必须为至少含 3 个样本的一维数组")
        if not np.isfinite(samples).all():
            raise ValueError("参考波形包含非有限数值")
        centered = samples - np.mean(samples)
        if not np.any(centered):
            raise ValueError("参考波形必须包含非零变化")
        return samples

    @staticmethod
    def _interpolate_peak(magnitudes: np.ndarray, peak_index: int) -> float:
        if peak_index <= 0 or peak_index >= magnitudes.size - 1:
            return 0.0
        left = float(magnitudes[peak_index - 1])
        center = float(magnitudes[peak_index])
        right = float(magnitudes[peak_index + 1])
        denominator = left - 2 * center + right
        if denominator >= 0 or abs(denominator) <= 1e-15:
            return 0.0
        offset = 0.5 * (left - right) / denominator
        return offset if math.isfinite(offset) and abs(offset) <= 0.5 else 0.0

    @staticmethod
    def _ambiguity_ratio(
        centered_reference: np.ndarray,
        magnitudes: np.ndarray,
        peak_index: int,
        correlation_peak: float,
    ) -> float:
        if correlation_peak <= 1e-12:
            return 0.0
        autocorrelation = correlate(
            centered_reference,
            centered_reference,
            mode="full",
            method="fft",
        )[centered_reference.size - 1 :]
        normalized_autocorrelation = np.abs(autocorrelation / autocorrelation[0])
        below_half = np.flatnonzero(normalized_autocorrelation[1:] < 0.5)
        exclusion_radius = (
            int(below_half[0]) + 1 if below_half.size else centered_reference.size - 1
        )
        exclusion_radius = max(1, exclusion_radius)
        mask = np.ones(magnitudes.size, dtype=bool)
        mask[
            max(0, peak_index - exclusion_radius) : min(
                magnitudes.size,
                peak_index + exclusion_radius + 1,
            )
        ] = False
        if not np.any(mask):
            return 0.0
        secondary_peak = float(np.max(magnitudes[mask]))
        return secondary_peak / correlation_peak

    @staticmethod
    def _estimate_levels(
        captured: np.ndarray,
        peak_index: int,
        reference_length: int,
    ) -> tuple[float, float, float | None]:
        response = captured[peak_index : peak_index + reference_length]
        guard = reference_length // 4
        noise_mask = np.ones(captured.size, dtype=bool)
        noise_mask[
            max(0, peak_index - guard) : min(
                captured.size,
                peak_index + reference_length + guard,
            )
        ] = False
        noise = captured[noise_mask]
        if noise.size < max(8, reference_length // 8):
            noise_mask[:] = True
            noise_mask[peak_index : peak_index + reference_length] = False
            noise = captured[noise_mask]

        centered_response = response - np.mean(response)
        response_power = float(np.mean(centered_response * centered_response))
        if noise.size:
            centered_noise = noise - np.mean(noise)
            noise_power = float(np.mean(centered_noise * centered_noise))
        else:
            noise_power = 0.0
        signal_power = max(response_power - noise_power, 0.0)
        signal_amplitude = math.sqrt(signal_power)
        noise_floor = math.sqrt(noise_power)
        if signal_power <= 0:
            snr_db = None
        elif noise_power <= 0:
            snr_db = math.inf
        else:
            snr_db = 10 * math.log10(signal_power / noise_power)
        return signal_amplitude, noise_floor, snr_db
