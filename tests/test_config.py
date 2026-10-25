from app.config import (
    AppConfig,
    AudioConfig,
    CalibrationConfig,
    CalibrationProfile,
    ConfigManager,
    MeasurementConfig,
)


def test_missing_config_returns_defaults(tmp_path):
    manager = ConfigManager(tmp_path / "config.json")

    assert manager.load() == AppConfig()


def test_config_round_trip(tmp_path):
    manager = ConfigManager(tmp_path / "config.json")
    expected = AppConfig(
        audio=AudioConfig(
            backend="simulation",
            sample_rate=96000,
            output_device_id="sim-output",
            input_device_id="sim-input",
            output_channels=2,
            input_channels=1,
            blocksize=256,
            background_kind="pink",
            background_amplitude=0.002,
            capture_buffer_seconds=12.0,
        ),
        measurement=MeasurementConfig(
            default_test_count=50,
            minimum_valid_count=10,
            warmup_seconds=5.5,
            stimulus_start_frequency_hz=600,
            stimulus_end_frequency_hz=7000,
            stimulus_duration_ms=12,
            stimulus_window="none",
            stimulus_amplitude=0.08,
            min_snr_db=8,
            min_correlation=0.4,
            min_latency_ms=1,
            max_latency_ms=150,
        ),
        calibration=CalibrationConfig(
            profiles=(
                CalibrationProfile(
                    profile_id="profile-1",
                    name="测试校准",
                    output_device_id="sim-output",
                    output_device="模拟耳机输出",
                    input_device_id="sim-input",
                    input_device="模拟有线麦克风",
                    sample_rate=96000,
                    audio_mode="simulation",
                    compensation_ms=1.25,
                    calibration_method="已知合成延迟",
                    calibration_date="2026-10-25T12:00:00+00:00",
                    notes="仅用于配置往返验证",
                ),
            ),
            selected_profile_id="profile-1",
            manual_compensation_ms=0.5,
        ),
    )

    manager.save(expected)

    assert manager.load() == expected


def test_invalid_config_returns_defaults(tmp_path):
    config_path = tmp_path / "config.json"
    config_path.write_text('{"audio": {"backend": "unsupported"}}', encoding="utf-8")
    manager = ConfigManager(config_path)

    assert manager.load() == AppConfig()
