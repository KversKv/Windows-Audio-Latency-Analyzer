import csv
import math
from collections.abc import Iterable
from pathlib import Path
from typing import Any

from data.models import TestResult, TestSession


class ExportError(RuntimeError):
    pass


class ExportDependencyError(ExportError):
    pass


def _session_row(session: TestSession) -> dict[str, Any]:
    profile = session.calibration_profile
    return {
        "session_id": session.id,
        "session_uid": session.session_uid,
        "session_name": session.session_name,
        "headphone_name": session.headphone_name,
        "connection_type": session.connection_type,
        "output_device_id": session.output_device_id,
        "output_device": session.output_device,
        "input_device_id": session.input_device_id,
        "input_device": session.input_device,
        "output_sample_rate": session.output_sample_rate,
        "input_sample_rate": session.input_sample_rate,
        "audio_mode": session.audio_mode,
        "backend": session.backend,
        "session_compensation_ms": session.compensation_ms,
        "calibration_profile_id": profile.profile_id if profile else "",
        "calibration_profile_name": profile.name if profile else "",
        "calibration_method": profile.calibration_method if profile else "",
        "calibration_date": profile.calibration_date if profile else "",
        "calibration_notes": profile.notes if profile else "",
        "test_count": session.test_count,
        "completed_count": session.completed_count,
        "valid_count": session.valid_count,
        "invalid_count": session.invalid_count,
        "minimum_valid_count": session.minimum_valid_count,
        "mean_latency_ms": session.mean_latency_ms,
        "median_latency_ms": session.median_latency_ms,
        "p90_latency_ms": session.p90_latency_ms,
        "p95_latency_ms": session.p95_latency_ms,
        "min_latency_ms": session.min_latency_ms,
        "max_latency_ms": session.max_latency_ms,
        "std_latency_ms": session.std_latency_ms,
        "compensated_mean_latency_ms": session.compensated_mean_latency_ms,
        "compensated_median_latency_ms": session.compensated_median_latency_ms,
        "compensated_p90_latency_ms": session.compensated_p90_latency_ms,
        "compensated_p95_latency_ms": session.compensated_p95_latency_ms,
        "compensated_min_latency_ms": session.compensated_min_latency_ms,
        "compensated_max_latency_ms": session.compensated_max_latency_ms,
        "compensated_std_latency_ms": session.compensated_std_latency_ms,
        "warmup_seconds": session.warmup_seconds,
        "background_kind": session.background_kind,
        "background_amplitude": session.background_amplitude,
        "terminal_state": session.terminal_state,
        "is_simulation": session.is_simulation,
        "created_at": session.created_at,
        "started_at": session.started_at,
        "finished_at": session.finished_at,
        "notes": session.notes,
    }


def _result_row(session: TestSession, result: TestResult) -> dict[str, Any]:
    return {
        "session_uid": session.session_uid,
        "result_id": result.id,
        "result_session_id": result.session_id,
        "sequence": result.sequence,
        "raw_latency_ms": result.raw_latency_ms,
        "compensated_latency_ms": result.compensated_latency_ms,
        "result_compensation_ms": result.compensation_ms,
        "correlation_lag_samples": result.correlation_lag_samples,
        "correlation_peak": result.correlation_peak,
        "correlation_ambiguity_ratio": result.correlation_ambiguity_ratio,
        "snr_db": result.snr_db,
        "signal_level": result.signal_level,
        "noise_floor": result.noise_floor,
        "playback_timestamp_seconds": result.playback_timestamp_seconds,
        "capture_timestamp_seconds": result.capture_timestamp_seconds,
        "capture_window_start_sample": result.capture_window_start_sample,
        "playback_sample_index": result.playback_sample_index,
        "is_valid": result.is_valid,
        "invalid_reason": result.invalid_reason,
        "timestamp": result.timestamp,
        "result_is_simulation": result.is_simulation,
        "timing_basis": result.timing_basis,
        "timing_limitations": ";".join(result.timing_limitations),
        "reference_sample_count": len(result.reference_waveform) // 4,
        "captured_sample_count": len(result.captured_waveform) // 4,
    }


def _csv_value(value: Any) -> Any:
    if isinstance(value, float) and not math.isfinite(value):
        return str(value)
    if value is None:
        return ""
    return value


def _excel_value(value: Any) -> Any:
    if isinstance(value, float) and not math.isfinite(value):
        return str(value)
    if isinstance(value, tuple):
        return ";".join(str(item) for item in value)
    return value


def _prepare_destination(destination: str | Path, suffix: str) -> Path:
    path = Path(destination)
    if path.suffix.lower() != suffix:
        path = path.with_suffix(suffix)
    path.parent.mkdir(parents=True, exist_ok=True)
    return path


def export_csv(sessions: Iterable[TestSession], destination: str | Path) -> Path:
    session_list = list(sessions)
    if not session_list:
        raise ValueError("至少选择一个会话才能导出")
    path = _prepare_destination(destination, ".csv")
    temporary_path = path.with_name(path.name + ".tmp")
    session_fields = list(_session_row(session_list[0]))
    result_fields = list(_result_row(session_list[0], session_list[0].results[0]))
    fieldnames = [*session_fields, *result_fields]
    try:
        with temporary_path.open("w", encoding="utf-8-sig", newline="") as stream:
            writer = csv.DictWriter(stream, fieldnames=fieldnames)
            writer.writeheader()
            for session in session_list:
                session_row = _session_row(session)
                for result in session.results:
                    writer.writerow(
                        {
                            key: _csv_value(value)
                            for key, value in {**session_row, **_result_row(session, result)}.items()
                        }
                    )
        temporary_path.replace(path)
    except OSError as exc:
        temporary_path.unlink(missing_ok=True)
        raise ExportError(f"CSV 导出失败：{path}") from exc
    return path


def export_excel(sessions: Iterable[TestSession], destination: str | Path) -> Path:
    session_list = list(sessions)
    if not session_list:
        raise ValueError("至少选择一个会话才能导出")
    try:
        from openpyxl import Workbook
    except ImportError as exc:
        raise ExportDependencyError("Excel 导出需要 openpyxl 依赖") from exc

    path = _prepare_destination(destination, ".xlsx")
    temporary_path = path.with_name(path.name + ".tmp")
    session_fields = list(_session_row(session_list[0]))
    result_fields = list(_result_row(session_list[0], session_list[0].results[0]))
    try:
        workbook = Workbook()
        session_sheet = workbook.active
        session_sheet.title = "会话"
        result_sheet = workbook.create_sheet("单次测量")
        session_sheet.append(session_fields)
        result_sheet.append(result_fields)
        for session in session_list:
            session_sheet.append(
                [_excel_value(value) for value in _session_row(session).values()]
            )
            for result in session.results:
                result_sheet.append(
                    [_excel_value(value) for value in _result_row(session, result).values()]
                )
        session_sheet.freeze_panes = "A2"
        result_sheet.freeze_panes = "A2"
        session_sheet.auto_filter.ref = session_sheet.dimensions
        result_sheet.auto_filter.ref = result_sheet.dimensions
        workbook.save(temporary_path)
        temporary_path.replace(path)
    except OSError as exc:
        temporary_path.unlink(missing_ok=True)
        raise ExportError(f"Excel 导出失败：{path}") from exc
    return path
