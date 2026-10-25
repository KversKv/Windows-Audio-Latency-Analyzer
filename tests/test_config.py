from app.config import AppConfig, AudioConfig, ConfigManager, MeasurementConfig


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
        measurement=MeasurementConfig(default_test_count=50, warmup_seconds=5.5),
    )

    manager.save(expected)

    assert manager.load() == expected


def test_invalid_config_returns_defaults(tmp_path):
    config_path = tmp_path / "config.json"
    config_path.write_text('{"audio": {"backend": "unsupported"}}', encoding="utf-8")
    manager = ConfigManager(config_path)

    assert manager.load() == AppConfig()
