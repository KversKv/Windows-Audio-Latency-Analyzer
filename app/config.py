import json
import logging
import math
import os
from dataclasses import asdict, dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)


class ConfigError(RuntimeError):
    pass


@dataclass(frozen=True, slots=True)
class AudioConfig:
    backend: str = "sounddevice"
    sample_rate: int = 48000
    output_device_id: int | str | None = None
    input_device_id: int | str | None = None
    output_channels: int = 1
    input_channels: int = 1
    blocksize: int = 0
    background_kind: str = "band_limited"
    background_amplitude: float = 0.005
    capture_buffer_seconds: float = 8.0

    def __post_init__(self) -> None:
        if self.backend not in {"sounddevice", "simulation"}:
            raise ValueError(f"不支持的音频后端：{self.backend}")
        if not 8000 <= self.sample_rate <= 192000:
            raise ValueError("采样率必须在 8000 到 192000 Hz 之间")
        if self.output_channels not in {1, 2} or self.input_channels not in {1, 2}:
            raise ValueError("播放和录音声道数仅支持 1 或 2")
        if self.blocksize < 0:
            raise ValueError("音频块大小不能为负数")
        if self.background_kind not in {"band_limited", "pink"}:
            raise ValueError(f"不支持的背景音类型：{self.background_kind}")
        if not 0 < self.background_amplitude < 1:
            raise ValueError("背景音幅度必须在 0 到 1 之间")
        if not 1.0 <= self.capture_buffer_seconds <= 60.0:
            raise ValueError("采集缓冲区时长必须在 1 到 60 秒之间")

    @property
    def audio_mode(self) -> str:
        return "simulation" if self.backend == "simulation" else "wasapi_shared"


@dataclass(frozen=True, slots=True)
class MeasurementConfig:
    default_test_count: int = 30
    minimum_valid_count: int = 1
    warmup_seconds: float = 3.0
    stimulus_start_frequency_hz: float = 500.0
    stimulus_end_frequency_hz: float = 8000.0
    stimulus_duration_ms: float = 10.0
    stimulus_window: str = "hann"
    stimulus_amplitude: float = 0.1
    min_snr_db: float = 6.0
    min_correlation: float = 0.35
    min_latency_ms: float = 0.0
    max_latency_ms: float = 200.0
    stimulus_lead_ms: float = 100.0
    capture_tail_ms: float = 30.0
    inter_measurement_delay_ms: float = 150.0
    simulation_delay_ms: float = 25.0
    simulation_snr_db: float = 20.0

    def __post_init__(self) -> None:
        if self.default_test_count < 1:
            raise ValueError("默认测试次数必须大于 0")
        if not 1 <= self.minimum_valid_count <= self.default_test_count:
            raise ValueError("最少有效样本数必须在 1 到测试次数之间")
        if not 1.0 <= self.warmup_seconds <= 15.0:
            raise ValueError("预热时间必须在 1 到 15 秒之间")
        frequencies = (self.stimulus_start_frequency_hz, self.stimulus_end_frequency_hz)
        if not all(math.isfinite(value) for value in frequencies):
            raise ValueError("刺激信号频率必须为有限数值")
        if not 0 < self.stimulus_start_frequency_hz < self.stimulus_end_frequency_hz:
            raise ValueError("刺激信号频率必须大于 0 且递增")
        if not math.isfinite(self.stimulus_duration_ms) or self.stimulus_duration_ms <= 0:
            raise ValueError("刺激信号时长必须为大于 0 的有限数值")
        if self.stimulus_window not in {"hann", "none"}:
            raise ValueError(f"不支持的刺激信号窗函数：{self.stimulus_window}")
        if not math.isfinite(self.stimulus_amplitude) or not 0 < self.stimulus_amplitude <= 1:
            raise ValueError("刺激信号幅度必须在 0 到 1 之间")
        if not math.isfinite(self.min_snr_db):
            raise ValueError("最低 SNR 必须为有限数值")
        if not math.isfinite(self.min_correlation) or not 0 < self.min_correlation <= 1:
            raise ValueError("最低相关系数必须在 0 到 1 之间")
        if not math.isfinite(self.min_latency_ms) or self.min_latency_ms < 0:
            raise ValueError("最小有效延迟不能为负数")
        if not math.isfinite(self.max_latency_ms) or self.max_latency_ms <= self.min_latency_ms:
            raise ValueError("最大有效延迟必须大于最小有效延迟")
        if not math.isfinite(self.stimulus_lead_ms) or not 0 < self.stimulus_lead_ms <= 1000:
            raise ValueError("刺激信号提前量必须在 0 到 1000 ms 之间")
        if not math.isfinite(self.capture_tail_ms) or not 0 < self.capture_tail_ms <= 1000:
            raise ValueError("采集尾长必须在 0 到 1000 ms 之间")
        if (
            not math.isfinite(self.inter_measurement_delay_ms)
            or not 0 <= self.inter_measurement_delay_ms <= 5000
        ):
            raise ValueError("测量间隔必须在 0 到 5000 ms 之间")
        if not math.isfinite(self.simulation_delay_ms) or self.simulation_delay_ms < 0:
            raise ValueError("模拟延迟必须为大于等于 0 的有限数值")
        if not math.isfinite(self.simulation_snr_db):
            raise ValueError("模拟 SNR 必须为有限数值")


@dataclass(frozen=True, slots=True)
class CalibrationContext:
    output_device_id: int | str | None
    output_device: str
    input_device_id: int | str | None
    input_device: str
    sample_rate: int
    audio_mode: str

    def __post_init__(self) -> None:
        if not self.output_device.strip() or not self.input_device.strip():
            raise ValueError("校准必须关联明确的播放和录音设备")
        if not 8000 <= self.sample_rate <= 192000:
            raise ValueError("校准采样率必须在 8000 到 192000 Hz 之间")
        if not self.audio_mode.strip():
            raise ValueError("校准必须关联音频模式")


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

    def __post_init__(self) -> None:
        CalibrationContext(
            output_device_id=self.output_device_id,
            output_device=self.output_device,
            input_device_id=self.input_device_id,
            input_device=self.input_device,
            sample_rate=self.sample_rate,
            audio_mode=self.audio_mode,
        )
        if not self.profile_id.strip() or not self.name.strip():
            raise ValueError("校准配置 ID 和名称不能为空")
        if not math.isfinite(self.compensation_ms):
            raise ValueError("补偿值必须为有限数值")
        if not self.calibration_method.strip():
            raise ValueError("校准方法不能为空")
        try:
            calibration_date = datetime.fromisoformat(self.calibration_date)
        except (TypeError, ValueError) as exc:
            raise ValueError("校准日期必须是 ISO 8601 格式") from exc
        if calibration_date.tzinfo is None or calibration_date.utcoffset() is None:
            raise ValueError("校准日期必须包含时区")

    def matches(self, context: CalibrationContext) -> bool:
        return (
            self.output_device_id == context.output_device_id
            and self.output_device == context.output_device
            and self.input_device_id == context.input_device_id
            and self.input_device == context.input_device
            and self.sample_rate == context.sample_rate
            and self.audio_mode == context.audio_mode
        )

    def require_match(self, context: CalibrationContext) -> None:
        if not self.matches(context):
            raise ValueError("校准配置与当前设备、采样率或音频模式不匹配")


@dataclass(frozen=True, slots=True)
class CalibrationConfig:
    profiles: tuple[CalibrationProfile, ...] = ()
    selected_profile_id: str | None = None
    manual_compensation_ms: float = 0.0

    def __post_init__(self) -> None:
        if not math.isfinite(self.manual_compensation_ms):
            raise ValueError("手动补偿值必须为有限数值")
        profile_ids = [profile.profile_id for profile in self.profiles]
        if len(profile_ids) != len(set(profile_ids)):
            raise ValueError("校准配置 ID 不能重复")
        if self.selected_profile_id is not None and self.selected_profile_id not in profile_ids:
            raise ValueError("所选校准配置不存在")

    def profile_by_id(self, profile_id: str | None) -> CalibrationProfile | None:
        if profile_id is None:
            return None
        return next((profile for profile in self.profiles if profile.profile_id == profile_id), None)

    @property
    def selected_profile(self) -> CalibrationProfile | None:
        return self.profile_by_id(self.selected_profile_id)


@dataclass(frozen=True, slots=True)
class UiConfig:
    theme: str = "dark"

    def __post_init__(self) -> None:
        if self.theme != "dark":
            raise ValueError("当前仅支持深色主题")


@dataclass(frozen=True, slots=True)
class AppConfig:
    format_version: int = 1
    audio: AudioConfig = field(default_factory=AudioConfig)
    measurement: MeasurementConfig = field(default_factory=MeasurementConfig)
    calibration: CalibrationConfig = field(default_factory=CalibrationConfig)
    ui: UiConfig = field(default_factory=UiConfig)

    def __post_init__(self) -> None:
        if self.format_version != 1:
            raise ValueError(f"不支持的配置格式版本：{self.format_version}")


def default_config_path() -> Path:
    override_directory = os.environ.get("LATENCY_ANALYZER_CONFIG_DIR")
    if override_directory:
        return Path(override_directory) / "config.json"
    app_data = os.environ.get("APPDATA")
    base_directory = Path(app_data) if app_data else Path.home() / ".config"
    return base_directory / "WindowsAudioLatencyAnalyzer" / "config.json"


class ConfigManager:
    def __init__(self, path: Path | None = None) -> None:
        self.path = path or default_config_path()

    def load(self) -> AppConfig:
        if not self.path.exists():
            return AppConfig()
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
            if not isinstance(data, dict):
                raise ValueError("配置文件根节点必须是对象")
            return AppConfig(
                format_version=data.get("format_version", 1),
                audio=AudioConfig(**self._section(data, "audio")),
                measurement=MeasurementConfig(**self._section(data, "measurement")),
                calibration=self._calibration(data),
                ui=UiConfig(**self._section(data, "ui")),
            )
        except (OSError, TypeError, ValueError):
            logger.error("读取配置失败，将使用默认配置：%s", self.path, exc_info=True)
            return AppConfig()

    def save(self, config: AppConfig) -> None:
        temporary_path = self.path.with_suffix(f"{self.path.suffix}.tmp")
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            payload = json.dumps(asdict(config), ensure_ascii=False, indent=2) + "\n"
            temporary_path.write_text(payload, encoding="utf-8")
            temporary_path.replace(self.path)
        except OSError as exc:
            raise ConfigError(f"无法保存配置：{self.path}") from exc
        logger.info("配置已保存：%s", self.path)

    @classmethod
    def _calibration(cls, data: dict[str, Any]) -> CalibrationConfig:
        section = cls._section(data, "calibration")
        profiles = section.get("profiles", [])
        if not isinstance(profiles, list) or not all(isinstance(item, dict) for item in profiles):
            raise ValueError("校准配置列表必须是对象数组")
        return CalibrationConfig(
            profiles=tuple(CalibrationProfile(**item) for item in profiles),
            selected_profile_id=section.get("selected_profile_id"),
            manual_compensation_ms=section.get("manual_compensation_ms", 0.0),
        )

    @staticmethod
    def _section(data: dict[str, Any], name: str) -> dict[str, Any]:
        section = data.get(name, {})
        if not isinstance(section, dict):
            raise ValueError(f"配置节 {name} 必须是对象")
        return section
