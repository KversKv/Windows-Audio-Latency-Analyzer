import math
from collections import Counter
from collections.abc import Iterable
from dataclasses import dataclass

import numpy as np

from core.latency_analyzer import LatencyMeasurement


@dataclass(frozen=True, slots=True)
class LatencyStatistics:
    count: int = 0
    mean_latency_ms: float | None = None
    median_latency_ms: float | None = None
    p90_latency_ms: float | None = None
    p95_latency_ms: float | None = None
    min_latency_ms: float | None = None
    max_latency_ms: float | None = None
    std_latency_ms: float | None = None

    @property
    def p50_latency_ms(self) -> float | None:
        return self.median_latency_ms


@dataclass(frozen=True, slots=True)
class SessionStatistics:
    raw: LatencyStatistics
    compensated: LatencyStatistics
    valid_count: int
    invalid_count: int
    minimum_valid_count: int
    invalid_reason_counts: tuple[tuple[str, int], ...]

    @property
    def total_count(self) -> int:
        return self.valid_count + self.invalid_count

    @property
    def meets_minimum_valid(self) -> bool:
        return self.valid_count >= self.minimum_valid_count


class StatisticsEngine:
    @staticmethod
    def calculate(
        measurements: Iterable[LatencyMeasurement],
        *,
        minimum_valid_count: int = 1,
    ) -> SessionStatistics:
        if minimum_valid_count < 1:
            raise ValueError("最少有效样本数必须大于 0")

        valid_measurements: list[LatencyMeasurement] = []
        invalid_count = 0
        invalid_reasons: Counter[str] = Counter()
        for measurement in measurements:
            if measurement.is_valid:
                valid_measurements.append(measurement)
                continue
            invalid_count += 1
            reasons = measurement.invalid_reason or "unknown"
            invalid_reasons.update(reason for reason in reasons.split(";") if reason)

        raw_values = [
            StatisticsEngine._valid_latency(measurement, measurement.raw_latency_ms, "原始")
            for measurement in valid_measurements
        ]
        compensated_values = [
            StatisticsEngine._valid_latency(
                measurement,
                measurement.compensated_latency_ms,
                "补偿后",
            )
            for measurement in valid_measurements
        ]
        return SessionStatistics(
            raw=StatisticsEngine._latency_statistics(raw_values),
            compensated=StatisticsEngine._latency_statistics(compensated_values),
            valid_count=len(valid_measurements),
            invalid_count=invalid_count,
            minimum_valid_count=minimum_valid_count,
            invalid_reason_counts=tuple(sorted(invalid_reasons.items())),
        )

    @staticmethod
    def _valid_latency(
        measurement: LatencyMeasurement,
        value: float | None,
        label: str,
    ) -> float:
        if value is None or not math.isfinite(value):
            raise ValueError(f"有效测量缺少有限{label}延迟：{measurement.timestamp.isoformat()}")
        return value

    @staticmethod
    def _latency_statistics(values: list[float]) -> LatencyStatistics:
        if not values:
            return LatencyStatistics()
        samples = np.asarray(values, dtype=np.float64)
        p50, p90, p95 = np.percentile(samples, (50, 90, 95), method="linear")
        return LatencyStatistics(
            count=samples.size,
            mean_latency_ms=float(np.mean(samples)),
            median_latency_ms=float(p50),
            p90_latency_ms=float(p90),
            p95_latency_ms=float(p95),
            min_latency_ms=float(np.min(samples)),
            max_latency_ms=float(np.max(samples)),
            std_latency_ms=float(np.std(samples, ddof=0)),
        )
