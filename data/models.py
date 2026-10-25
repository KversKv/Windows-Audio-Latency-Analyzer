from dataclasses import dataclass, field


@dataclass(frozen=True, slots=True)
class CalibrationProfile:
    profile_id: str
    name: str
    output_device_id: int | str | None
    output_device: str
    input_device_id: int | str | None
    input_device: str
    sample_rate: int
    audio_mode: str
    compensation_ms: float
    calibration_method: str
    calibration_date: str
    notes: str = ""
    id: int | None = None
    session_id: int | None = None


@dataclass(frozen=True, slots=True)
class TestResult:
    sequence: int
    raw_latency_ms: float | None
    compensated_latency_ms: float | None
    correlation_peak: float
    snr_db: float | None
    signal_level: float
    is_valid: bool
    invalid_reason: str | None
    timestamp: str
    compensation_ms: float = 0.0
    correlation_lag_samples: float | None = None
    correlation_ambiguity_ratio: float = 0.0
    noise_floor: float = 0.0
    playback_timestamp_seconds: float | None = None
    capture_timestamp_seconds: float | None = None
    capture_window_start_sample: int | None = None
    playback_sample_index: int | None = None
    is_simulation: bool = False
    timing_basis: str = ""
    timing_limitations: tuple[str, ...] = ()
    reference_waveform: bytes = b""
    captured_waveform: bytes = b""
    id: int | None = None
    session_id: int | None = None


@dataclass(frozen=True, slots=True)
class TestSession:
    session_uid: str
    session_name: str
    headphone_name: str
    connection_type: str
    output_device: str
    input_device: str
    output_sample_rate: int
    input_sample_rate: int
    audio_mode: str
    compensation_ms: float
    test_count: int
    valid_count: int
    invalid_count: int
    mean_latency_ms: float | None
    median_latency_ms: float | None
    p90_latency_ms: float | None
    p95_latency_ms: float | None
    min_latency_ms: float | None
    max_latency_ms: float | None
    std_latency_ms: float | None
    compensated_mean_latency_ms: float | None
    compensated_median_latency_ms: float | None
    compensated_p90_latency_ms: float | None
    compensated_p95_latency_ms: float | None
    compensated_min_latency_ms: float | None
    compensated_max_latency_ms: float | None
    compensated_std_latency_ms: float | None
    created_at: str
    started_at: str
    finished_at: str
    terminal_state: str
    is_simulation: bool
    id: int | None = None
    output_device_id: int | str | None = None
    input_device_id: int | str | None = None
    completed_count: int = 0
    minimum_valid_count: int = 1
    backend: str = ""
    warmup_seconds: float = 0.0
    background_kind: str = ""
    background_amplitude: float = 0.0
    notes: str = ""
    calibration_profile: CalibrationProfile | None = None
    audio_config_json: str = "{}"
    measurement_config_json: str = "{}"
    results: tuple[TestResult, ...] = field(default_factory=tuple)
