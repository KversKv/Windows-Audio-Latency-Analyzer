import math
from dataclasses import dataclass

from audio.stream_common import StreamTiming


@dataclass(frozen=True, slots=True)
class SampleTimeAnchor:
    sample_index: int
    device_timestamp_seconds: float
    sample_rate: int

    def __post_init__(self) -> None:
        if self.sample_index < 0:
            raise ValueError("时间锚点样本索引不能为负数")
        if not math.isfinite(self.device_timestamp_seconds):
            raise ValueError("时间锚点必须为有限数值")
        if self.sample_rate < 1:
            raise ValueError("时间锚点采样率必须大于 0")


@dataclass(frozen=True, slots=True)
class SynchronizedTiming:
    playback_timestamp_seconds: float
    capture_timestamp_seconds: float
    raw_latency_ms: float
    capture_clock_offset_ms: float | None
    clock_offset_calibrated: bool
    limitations: tuple[str, ...]


class AudioSynchronizer:
    """Map sample indices through DAC/ADC anchors without inventing clock calibration."""

    def __init__(
        self,
        *,
        playback_anchor: SampleTimeAnchor,
        capture_anchor: SampleTimeAnchor,
        capture_clock_offset_seconds: float | None = None,
        playback_drift_ppm: float | None = None,
        capture_drift_ppm: float | None = None,
        timing_basis: str | None = None,
    ) -> None:
        for name, value in (
            ("采集时钟偏移", capture_clock_offset_seconds),
            ("播放时钟漂移", playback_drift_ppm),
            ("采集时钟漂移", capture_drift_ppm),
        ):
            if value is not None and not math.isfinite(value):
                raise ValueError(f"{name}必须为有限数值或未校准状态")
        for name, value in (
            ("播放", playback_drift_ppm),
            ("采集", capture_drift_ppm),
        ):
            if value is not None and 1 + value * 1e-6 <= 0:
                raise ValueError(f"{name}漂移修正导致无效采样率")

        self.playback_anchor = playback_anchor
        self.capture_anchor = capture_anchor
        self.capture_clock_offset_seconds = capture_clock_offset_seconds
        self.playback_drift_ppm = playback_drift_ppm
        self.capture_drift_ppm = capture_drift_ppm
        self.timing_basis = timing_basis

    @classmethod
    def from_stream_timings(
        cls,
        playback_timing: StreamTiming,
        capture_timing: StreamTiming,
        *,
        sample_rate: int | None = None,
        playback_sample_rate: int | None = None,
        capture_sample_rate: int | None = None,
        capture_clock_offset_seconds: float | None = None,
        playback_drift_ppm: float | None = None,
        capture_drift_ppm: float | None = None,
    ) -> "AudioSynchronizer":
        output_rate = sample_rate if playback_sample_rate is None else playback_sample_rate
        input_rate = sample_rate if capture_sample_rate is None else capture_sample_rate
        if output_rate is None or input_rate is None:
            raise ValueError("播放和采集采样率必须明确提供")
        if playback_timing.output_dac_time is None:
            raise ValueError("缺少播放 DAC 时间戳，不能建立播放时间映射")
        if capture_timing.input_adc_time is None:
            raise ValueError("缺少采集 ADC 时间戳，不能建立采集时间映射")
        return cls(
            playback_anchor=SampleTimeAnchor(
                sample_index=playback_timing.sample_index,
                device_timestamp_seconds=playback_timing.output_dac_time,
                sample_rate=output_rate,
            ),
            capture_anchor=SampleTimeAnchor(
                sample_index=capture_timing.sample_index,
                device_timestamp_seconds=capture_timing.input_adc_time,
                sample_rate=input_rate,
            ),
            capture_clock_offset_seconds=capture_clock_offset_seconds,
            playback_drift_ppm=playback_drift_ppm,
            capture_drift_ppm=capture_drift_ppm,
        )

    @property
    def limitations(self) -> tuple[str, ...]:
        limitations = []
        if self.capture_clock_offset_seconds is None:
            limitations.append("capture_clock_offset_uncalibrated")
        if self.playback_drift_ppm is None:
            limitations.append("playback_sample_rate_drift_uncalibrated")
        if self.capture_drift_ppm is None:
            limitations.append("capture_sample_rate_drift_uncalibrated")
        return tuple(limitations)

    def playback_time_at(self, sample_index: float) -> float:
        return self._time_at(
            self.playback_anchor,
            sample_index,
            self.playback_drift_ppm,
        )

    def capture_time_at(self, sample_index: float) -> float:
        timestamp = self._time_at(
            self.capture_anchor,
            sample_index,
            self.capture_drift_ppm,
        )
        if self.capture_clock_offset_seconds is not None:
            timestamp -= self.capture_clock_offset_seconds
        return timestamp

    def synchronize(
        self,
        *,
        playback_sample_index: float,
        capture_sample_index: float,
    ) -> SynchronizedTiming:
        playback_timestamp = self.playback_time_at(playback_sample_index)
        capture_timestamp = self.capture_time_at(capture_sample_index)
        return SynchronizedTiming(
            playback_timestamp_seconds=playback_timestamp,
            capture_timestamp_seconds=capture_timestamp,
            raw_latency_ms=(capture_timestamp - playback_timestamp) * 1000,
            capture_clock_offset_ms=(
                self.capture_clock_offset_seconds * 1000
                if self.capture_clock_offset_seconds is not None
                else None
            ),
            clock_offset_calibrated=self.capture_clock_offset_seconds is not None,
            limitations=self.limitations,
        )

    @staticmethod
    def _time_at(
        anchor: SampleTimeAnchor,
        sample_index: float,
        drift_ppm: float | None,
    ) -> float:
        if not math.isfinite(sample_index) or sample_index < 0:
            raise ValueError("样本索引必须为大于等于 0 的有限数值")
        corrected_drift = drift_ppm if drift_ppm is not None else 0.0
        actual_sample_rate = anchor.sample_rate * (1 + corrected_drift * 1e-6)
        return anchor.device_timestamp_seconds + (
            sample_index - anchor.sample_index
        ) / actual_sample_rate
