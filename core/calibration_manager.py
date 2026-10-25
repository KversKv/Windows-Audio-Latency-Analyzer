from dataclasses import replace
from datetime import datetime, timezone
from uuid import uuid4

from app.config import CalibrationConfig, CalibrationContext, CalibrationProfile


class CalibrationManager:
    @staticmethod
    def create_profile(
        *,
        name: str,
        context: CalibrationContext,
        compensation_ms: float,
        calibration_method: str,
        notes: str = "",
        calibration_date: datetime | None = None,
        profile_id: str | None = None,
    ) -> CalibrationProfile:
        date = calibration_date or datetime.now(timezone.utc)
        if date.tzinfo is None or date.utcoffset() is None:
            raise ValueError("校准日期必须包含时区")
        return CalibrationProfile(
            profile_id=profile_id if profile_id is not None else uuid4().hex,
            name=name.strip(),
            output_device_id=context.output_device_id,
            output_device=context.output_device.strip(),
            input_device_id=context.input_device_id,
            input_device=context.input_device.strip(),
            sample_rate=context.sample_rate,
            audio_mode=context.audio_mode,
            compensation_ms=compensation_ms,
            calibration_method=calibration_method.strip(),
            calibration_date=date.isoformat(),
            notes=notes.strip(),
        )

    @staticmethod
    def add_profile(
        config: CalibrationConfig,
        profile: CalibrationProfile,
        *,
        select: bool = True,
    ) -> CalibrationConfig:
        if config.profile_by_id(profile.profile_id) is not None:
            raise ValueError("校准配置 ID 已存在")
        return replace(
            config,
            profiles=(*config.profiles, profile),
            selected_profile_id=profile.profile_id if select else config.selected_profile_id,
        )

    @staticmethod
    def select_profile(
        config: CalibrationConfig,
        profile_id: str | None,
    ) -> CalibrationConfig:
        if profile_id is not None and config.profile_by_id(profile_id) is None:
            raise ValueError("所选校准配置不存在")
        return replace(config, selected_profile_id=profile_id)

    @staticmethod
    def use_manual_compensation(
        config: CalibrationConfig,
        compensation_ms: float,
    ) -> CalibrationConfig:
        return replace(
            config,
            selected_profile_id=None,
            manual_compensation_ms=compensation_ms,
        )

    @staticmethod
    def resolve_compensation(
        config: CalibrationConfig,
        context: CalibrationContext,
    ) -> tuple[float, CalibrationProfile | None]:
        profile = config.selected_profile
        if profile is None:
            return config.manual_compensation_ms, None
        profile.require_match(context)
        return profile.compensation_ms, profile
