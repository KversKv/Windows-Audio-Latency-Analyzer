import json
import logging
import os
from dataclasses import asdict, dataclass, field
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

    def __post_init__(self) -> None:
        if self.backend not in {"sounddevice", "simulation"}:
            raise ValueError(f"不支持的音频后端：{self.backend}")
        if not 8000 <= self.sample_rate <= 192000:
            raise ValueError("采样率必须在 8000 到 192000 Hz 之间")


@dataclass(frozen=True, slots=True)
class MeasurementConfig:
    default_test_count: int = 30
    warmup_seconds: float = 3.0

    def __post_init__(self) -> None:
        if self.default_test_count < 1:
            raise ValueError("默认测试次数必须大于 0")
        if not 1.0 <= self.warmup_seconds <= 15.0:
            raise ValueError("预热时间必须在 1 到 15 秒之间")


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

    @staticmethod
    def _section(data: dict[str, Any], name: str) -> dict[str, Any]:
        section = data.get(name, {})
        if not isinstance(section, dict):
            raise ValueError(f"配置节 {name} 必须是对象")
        return section
