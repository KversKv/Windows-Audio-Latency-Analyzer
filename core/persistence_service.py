import json
from dataclasses import asdict, dataclass
from datetime import datetime
from pathlib import Path
from typing import Iterable

import numpy as np

from core.latency_analyzer import LatencyMeasurement
from core.statistics_engine import SessionStatistics, StatisticsEngine
from core.test_models import MeasurementRecord, TestSessionSnapshot
from data.database import Database
from data.exporter import export_csv, export_excel
from data.models import CalibrationProfile as StoredCalibrationProfile
from data.models import TestResult as StoredTestResult
from data.models import TestSession as StoredTestSession
from data.repository import SessionRepository


@dataclass(frozen=True, slots=True)
class SessionComparison:
    session: StoredTestSession
    statistics: SessionStatistics


class PersistenceService:
    def __init__(self, database_path: str | Path) -> None:
        self.database = Database(database_path)

    def save_session(self, snapshot: TestSessionSnapshot) -> int:
        return self._repository().save_session(self._to_stored_session(snapshot))

    def list_sessions(
        self,
        *,
        search: str = "",
        connection_type: str = "",
        sort_by: str = "created_at_desc",
    ) -> list[StoredTestSession]:
        return self._repository().list_sessions(
            search=search,
            connection_type=connection_type,
            sort_by=sort_by,
        )

    def get_session(self, session_id: int) -> StoredTestSession:
        return self._repository().get_session(session_id)

    def load_comparison_sessions(
        self,
        session_ids: Iterable[int],
    ) -> tuple[SessionComparison, ...]:
        repository = self._repository()
        comparisons = []
        for session_id in session_ids:
            session = repository.get_session(session_id)
            comparisons.append(
                SessionComparison(
                    session=session,
                    statistics=self.recalculate_statistics(session),
                )
            )
        return tuple(comparisons)

    def update_notes(self, session_id: int, notes: str) -> None:
        self._repository().update_notes(session_id, notes)

    def delete_sessions(self, session_ids: Iterable[int]) -> int:
        return self._repository().delete_sessions(session_ids)

    def export_sessions(
        self,
        session_ids: Iterable[int],
        destination: str | Path,
        file_format: str,
    ) -> Path:
        repository = self._repository()
        sessions = [repository.get_session(session_id) for session_id in session_ids]
        if file_format == "csv":
            return export_csv(sessions, destination)
        if file_format == "xlsx":
            return export_excel(sessions, destination)
        raise ValueError(f"不支持的导出格式：{file_format}")

    def recalculate_statistics(self, session: StoredTestSession) -> SessionStatistics:
        return StatisticsEngine.calculate(
            (self._to_measurement(result) for result in session.results),
            minimum_valid_count=session.minimum_valid_count,
        )

    def _repository(self) -> SessionRepository:
        return SessionRepository(self.database)

    @staticmethod
    def _to_stored_session(snapshot: TestSessionSnapshot) -> StoredTestSession:
        config = snapshot.config
        statistics = snapshot.statistics
        raw = statistics.raw
        compensated = statistics.compensated
        headphone_name = config.headphone_name.strip()
        session_name = config.session_name.strip()
        if not session_name:
            session_name = (
                f"{headphone_name or config.output_device_name.strip() or '未命名会话'}"
                f" · {config.started_at.isoformat()}"
            )
        profile = config.calibration_profile
        stored_profile = (
            StoredCalibrationProfile(
                profile_id=profile.profile_id,
                name=profile.name,
                output_device_id=profile.output_device_id,
                output_device=profile.output_device,
                input_device_id=profile.input_device_id,
                input_device=profile.input_device,
                sample_rate=profile.sample_rate,
                audio_mode=profile.audio_mode,
                compensation_ms=profile.compensation_ms,
                calibration_method=profile.calibration_method,
                calibration_date=profile.calibration_date,
                notes=profile.notes,
            )
            if profile is not None
            else None
        )
        return StoredTestSession(
            session_uid=snapshot.session_id,
            session_name=session_name,
            headphone_name=headphone_name,
            connection_type=config.connection_type,
            output_device_id=config.audio.output_device_id,
            output_device=config.output_device_name,
            input_device_id=config.audio.input_device_id,
            input_device=config.input_device_name,
            output_sample_rate=config.audio.sample_rate,
            input_sample_rate=config.audio.sample_rate,
            audio_mode=config.audio.audio_mode,
            backend=config.audio.backend,
            compensation_ms=config.compensation_ms,
            test_count=config.test_count,
            completed_count=snapshot.completed_count,
            valid_count=snapshot.valid_count,
            invalid_count=snapshot.invalid_count,
            minimum_valid_count=config.measurement.minimum_valid_count,
            mean_latency_ms=raw.mean_latency_ms,
            median_latency_ms=raw.median_latency_ms,
            p90_latency_ms=raw.p90_latency_ms,
            p95_latency_ms=raw.p95_latency_ms,
            min_latency_ms=raw.min_latency_ms,
            max_latency_ms=raw.max_latency_ms,
            std_latency_ms=raw.std_latency_ms,
            compensated_mean_latency_ms=compensated.mean_latency_ms,
            compensated_median_latency_ms=compensated.median_latency_ms,
            compensated_p90_latency_ms=compensated.p90_latency_ms,
            compensated_p95_latency_ms=compensated.p95_latency_ms,
            compensated_min_latency_ms=compensated.min_latency_ms,
            compensated_max_latency_ms=compensated.max_latency_ms,
            compensated_std_latency_ms=compensated.std_latency_ms,
            warmup_seconds=config.measurement.warmup_seconds,
            background_kind=config.audio.background_kind,
            background_amplitude=config.audio.background_amplitude,
            terminal_state=snapshot.terminal_state.name,
            is_simulation=snapshot.is_simulation,
            created_at=snapshot.started_at.isoformat(),
            started_at=snapshot.started_at.isoformat(),
            finished_at=snapshot.finished_at.isoformat(),
            notes=config.notes,
            calibration_profile=stored_profile,
            audio_config_json=json.dumps(asdict(config.audio), ensure_ascii=False, sort_keys=True),
            measurement_config_json=json.dumps(
                asdict(config.measurement), ensure_ascii=False, sort_keys=True
            ),
            results=tuple(PersistenceService._to_stored_result(record) for record in snapshot.records),
        )

    @staticmethod
    def _to_stored_result(record: MeasurementRecord) -> StoredTestResult:
        measurement = record.measurement
        return StoredTestResult(
            sequence=record.sequence,
            raw_latency_ms=measurement.raw_latency_ms,
            compensated_latency_ms=measurement.compensated_latency_ms,
            compensation_ms=measurement.compensation_ms,
            correlation_lag_samples=measurement.correlation_lag_samples,
            correlation_peak=measurement.correlation_peak,
            correlation_ambiguity_ratio=measurement.correlation_ambiguity_ratio,
            snr_db=measurement.snr_db,
            signal_level=measurement.signal_amplitude,
            noise_floor=measurement.noise_floor,
            playback_timestamp_seconds=measurement.playback_timestamp_seconds,
            capture_timestamp_seconds=measurement.capture_timestamp_seconds,
            capture_window_start_sample=record.capture_window_start_sample,
            playback_sample_index=record.playback_sample_index,
            is_valid=measurement.is_valid,
            invalid_reason=measurement.invalid_reason,
            timestamp=measurement.timestamp.isoformat(),
            is_simulation=measurement.is_simulation,
            timing_basis=measurement.timing_basis,
            timing_limitations=measurement.timing_limitations,
            reference_waveform=np.ascontiguousarray(record.reference, dtype="<f4").tobytes(),
            captured_waveform=np.ascontiguousarray(record.captured, dtype="<f4").tobytes(),
        )

    @staticmethod
    def _to_measurement(result: StoredTestResult) -> LatencyMeasurement:
        return LatencyMeasurement(
            raw_latency_ms=result.raw_latency_ms,
            compensated_latency_ms=result.compensated_latency_ms,
            compensation_ms=result.compensation_ms,
            correlation_lag_samples=result.correlation_lag_samples,
            correlation_peak=result.correlation_peak,
            correlation_ambiguity_ratio=result.correlation_ambiguity_ratio,
            signal_amplitude=result.signal_level,
            noise_floor=result.noise_floor,
            snr_db=result.snr_db,
            is_valid=result.is_valid,
            invalid_reason=result.invalid_reason,
            timestamp=datetime.fromisoformat(result.timestamp),
            playback_timestamp_seconds=result.playback_timestamp_seconds,
            capture_timestamp_seconds=result.capture_timestamp_seconds,
            is_simulation=result.is_simulation,
            timing_basis=result.timing_basis,
            timing_limitations=result.timing_limitations,
        )
