import math
from dataclasses import dataclass, field
from datetime import datetime, timezone

import numpy as np

from app.config import AudioConfig, MeasurementConfig
from core.latency_analyzer import LatencyMeasurement
from core.test_state_machine import TestState


@dataclass(frozen=True, slots=True)
class TestSessionConfig:
    audio: AudioConfig
    measurement: MeasurementConfig
    compensation_ms: float = 0.0
    output_device_name: str = ""
    input_device_name: str = ""
    started_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))

    def __post_init__(self) -> None:
        if not math.isfinite(self.compensation_ms):
            raise ValueError("补偿值必须为有限数值")
        if self.started_at.tzinfo is None or self.started_at.utcoffset() is None:
            raise ValueError("会话开始时间必须包含时区")

    @property
    def test_count(self) -> int:
        return self.measurement.default_test_count

    @property
    def is_simulation(self) -> bool:
        return self.audio.backend == "simulation"


@dataclass(frozen=True, slots=True)
class MeasurementRecord:
    sequence: int
    reference: np.ndarray
    captured: np.ndarray
    capture_window_start_sample: int | None
    playback_sample_index: int | None
    measurement: LatencyMeasurement

    def __post_init__(self) -> None:
        if self.sequence < 1:
            raise ValueError("测量序号必须大于 0")
        if self.reference.ndim != 1 or self.captured.ndim != 1:
            raise ValueError("测量波形必须是一维单声道数组")
        self.reference.setflags(write=False)
        self.captured.setflags(write=False)


@dataclass(frozen=True, slots=True)
class TestSessionSnapshot:
    config: TestSessionConfig
    records: tuple[MeasurementRecord, ...]
    terminal_state: TestState
    started_at: datetime
    finished_at: datetime
    is_simulation: bool

    def __post_init__(self) -> None:
        if self.finished_at.tzinfo is None or self.finished_at.utcoffset() is None:
            raise ValueError("会话结束时间必须包含时区")

    @property
    def completed_count(self) -> int:
        return len(self.records)

    @property
    def valid_count(self) -> int:
        return sum(record.measurement.is_valid for record in self.records)

    @property
    def invalid_count(self) -> int:
        return self.completed_count - self.valid_count
