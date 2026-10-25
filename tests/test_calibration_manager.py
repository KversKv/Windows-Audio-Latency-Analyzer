from dataclasses import replace
from datetime import datetime, timezone

import pytest

from app.config import CalibrationConfig, CalibrationContext
from core.calibration_manager import CalibrationManager


_CONTEXT = CalibrationContext(
    output_device_id=1,
    output_device="测试播放设备",
    input_device_id=2,
    input_device="测试录音设备",
    sample_rate=48000,
    audio_mode="wasapi_shared",
)
_DATE = datetime(2026, 10, 25, 12, 0, tzinfo=timezone.utc)


def _profile():
    return CalibrationManager.create_profile(
        name="有线环回校准",
        context=_CONTEXT,
        compensation_ms=1.75,
        calibration_method="外部环回实测",
        calibration_date=_DATE,
        notes="独立声卡环回",
        profile_id="profile-1",
    )


def test_profile_creation_preserves_explicit_traceable_information():
    profile = _profile()

    assert profile.profile_id == "profile-1"
    assert profile.name == "有线环回校准"
    assert profile.output_device_id == 1
    assert profile.output_device == "测试播放设备"
    assert profile.input_device_id == 2
    assert profile.input_device == "测试录音设备"
    assert profile.sample_rate == 48000
    assert profile.audio_mode == "wasapi_shared"
    assert profile.compensation_ms == 1.75
    assert profile.calibration_method == "外部环回实测"
    assert profile.calibration_date == _DATE.isoformat()
    assert profile.notes == "独立声卡环回"
    assert profile.matches(_CONTEXT)


def test_manual_default_and_selected_profile_are_distinguishable():
    manual = CalibrationConfig(manual_compensation_ms=0.5)

    assert CalibrationManager.resolve_compensation(manual, _CONTEXT) == (0.5, None)

    with_profile = CalibrationManager.add_profile(manual, _profile())
    compensation, selected = CalibrationManager.resolve_compensation(with_profile, _CONTEXT)

    assert compensation == 1.75
    assert selected == _profile()
    assert with_profile.manual_compensation_ms == 0.5

    uncompensated = CalibrationManager.use_manual_compensation(with_profile, 0.0)
    assert CalibrationManager.resolve_compensation(uncompensated, _CONTEXT) == (0.0, None)


def test_profile_selection_requires_existing_id():
    config = CalibrationConfig(profiles=(_profile(),))

    assert CalibrationManager.select_profile(config, "profile-1").selected_profile == _profile()
    assert CalibrationManager.select_profile(config, None).selected_profile is None
    with pytest.raises(ValueError, match="所选校准配置不存在"):
        CalibrationManager.select_profile(config, "missing")


def test_mismatched_context_never_falls_back_to_invented_compensation():
    config = CalibrationConfig(profiles=(_profile(),), selected_profile_id="profile-1")

    for mismatched in (
        replace(_CONTEXT, output_device_id=99),
        replace(_CONTEXT, input_device="另一支麦克风"),
        replace(_CONTEXT, sample_rate=96000),
        replace(_CONTEXT, audio_mode="wasapi_exclusive"),
    ):
        with pytest.raises(ValueError, match="校准配置与当前设备、采样率或音频模式不匹配"):
            CalibrationManager.resolve_compensation(config, mismatched)


def test_calibration_rejects_untraceable_or_non_finite_values():
    with pytest.raises(ValueError, match="补偿值必须为有限数值"):
        CalibrationManager.create_profile(
            name="无效校准",
            context=_CONTEXT,
            compensation_ms=float("nan"),
            calibration_method="外部环回实测",
        )
    with pytest.raises(ValueError, match="校准方法不能为空"):
        CalibrationManager.create_profile(
            name="无效校准",
            context=_CONTEXT,
            compensation_ms=0.0,
            calibration_method="  ",
        )
    with pytest.raises(ValueError, match="校准日期必须包含时区"):
        CalibrationManager.create_profile(
            name="无效校准",
            context=_CONTEXT,
            compensation_ms=0.0,
            calibration_method="外部环回实测",
            calibration_date=datetime(2026, 10, 25, 12, 0),
        )
