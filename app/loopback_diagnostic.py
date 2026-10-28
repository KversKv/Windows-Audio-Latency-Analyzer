import argparse
import json
import logging
import math
from dataclasses import asdict, dataclass, is_dataclass, replace
from datetime import datetime, timezone
from enum import Enum
from pathlib import Path
from typing import Any, Sequence

import numpy as np
from PySide6.QtCore import QCoreApplication, QEventLoop, QTimer

from app.config import AppConfig, ConfigManager
from audio.audio_backend import create_audio_backend
from audio.audio_device_manager import AudioDeviceManager
from core.device_service import DeviceService
from core.statistics_engine import SessionStatistics, StatisticsEngine
from core.test_controller import TestController
from core.test_models import MeasurementRecord, TestSessionConfig, TestSessionSnapshot
from core.test_state_machine import TestState

logger = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class DiagnosticRunResult:
    report_directory: Path
    records_path: Path
    summary_path: Path
    terminal_state: str
    expected_count: int
    valid_count: int
    invalid_count: int
    timed_out: bool
    shutdown_completed: bool
    errors: tuple[str, ...]

    @property
    def success(self) -> bool:
        return (
            self.terminal_state == TestState.COMPLETED.name
            and self.valid_count == self.expected_count
            and self.invalid_count == 0
            and not self.timed_out
            and self.shutdown_completed
            and not self.errors
        )


def _build_argument_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Software / Electrical 批量环回延迟诊断")
    parser.add_argument("--mode", required=True, choices=("software", "electrical"))
    parser.add_argument("--output-device", type=int, default=None, help="播放设备 PortAudio 索引")
    parser.add_argument("--input-device", type=int, default=None, help="录音设备 PortAudio 索引")
    parser.add_argument("--sample-rate", type=int, default=None, help="覆盖配置中的采样率")
    parser.add_argument("--blocksize", type=int, default=None, help="覆盖配置中的回调块大小；0 为自动")
    parser.add_argument("--count", type=int, default=None, help="覆盖配置中的测量次数")
    parser.add_argument("--output-dir", type=Path, default=None, help="诊断报告根目录")
    parser.add_argument("--timeout-seconds", type=float, default=120.0, help="会话有界等待秒数")
    return parser


def _make_session_config(config: AppConfig, *, mode: str, count: int | None) -> TestSessionConfig:
    if config.audio.backend != "sounddevice":
        raise ValueError("真实环回诊断需要 sounddevice 后端")
    measurement = config.measurement
    if count is not None:
        if count < measurement.minimum_valid_count:
            raise ValueError("测量次数不能低于配置中的最少有效样本数")
        measurement = replace(measurement, default_test_count=count)
    return TestSessionConfig(
        audio=config.audio,
        measurement=measurement,
        compensation_ms=0.0,
        calibration_profile=None,
        diagnostic_mode=mode,
    )


def _json_value(value: Any) -> Any:
    if is_dataclass(value) and not isinstance(value, type):
        return _json_value(asdict(value))
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, Enum):
        return value.name
    if isinstance(value, Path):
        return str(value)
    if isinstance(value, np.generic):
        return _json_value(value.item())
    if isinstance(value, float):
        return value if math.isfinite(value) else None
    if isinstance(value, dict):
        return {str(key): _json_value(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_json_value(item) for item in value]
    return value


def _json_text(value: Any) -> str:
    return json.dumps(_json_value(value), ensure_ascii=False, allow_nan=False)


def _latency_statistics_payload(statistics: SessionStatistics) -> dict[str, Any]:
    def values(raw: Any) -> dict[str, Any]:
        return {
            "count": raw.count,
            "mean_ms": raw.mean_latency_ms,
            "p50_ms": raw.p50_latency_ms,
            "p90_ms": raw.p90_latency_ms,
            "p95_ms": raw.p95_latency_ms,
            "min_ms": raw.min_latency_ms,
            "max_ms": raw.max_latency_ms,
            "std_ms": raw.std_latency_ms,
        }

    return {
        "valid_count": statistics.valid_count,
        "invalid_count": statistics.invalid_count,
        "invalid_reason_counts": statistics.invalid_reason_counts,
        "raw": values(statistics.raw),
        "compensated": values(statistics.compensated),
    }


def _write_invalid_waveform(directory: Path, record: MeasurementRecord) -> str:
    filename = f"invalid_{record.sequence:04d}.npz"
    np.savez(
        directory / filename,
        reference=record.reference,
        captured=record.captured,
        capture_window_start_sample=np.array(
            np.nan if record.capture_window_start_sample is None else record.capture_window_start_sample
        ),
        playback_sample_index=np.array(
            np.nan if record.playback_sample_index is None else record.playback_sample_index
        ),
        invalid_reason=np.array(record.measurement.invalid_reason or ""),
    )
    return filename


def write_diagnostic_report(
    directory: Path,
    *,
    config: TestSessionConfig,
    snapshot: TestSessionSnapshot | None,
    records: Sequence[MeasurementRecord],
    terminal_state: str,
    errors: Sequence[str],
    timed_out: bool,
    shutdown_completed: bool,
    finalized: bool = True,
) -> tuple[Path, Path]:
    directory.mkdir(parents=True, exist_ok=True)
    records_path = directory / "records.jsonl"
    summary_path = directory / "summary.json"
    with records_path.open("w", encoding="utf-8", newline="\n") as stream:
        for record in records:
            invalid_waveform = (
                _write_invalid_waveform(directory, record)
                if not record.measurement.is_valid
                else None
            )
            payload = {
                "sequence": record.sequence,
                "capture_window_start_sample": record.capture_window_start_sample,
                "playback_sample_index": record.playback_sample_index,
                "measurement": record.measurement,
                "invalid_waveform": invalid_waveform,
            }
            stream.write(_json_text(payload) + "\n")

    statistics = StatisticsEngine.calculate(
        (record.measurement for record in records),
        minimum_valid_count=config.measurement.minimum_valid_count,
    )
    summary = {
        "schema_version": 1,
        "created_at": datetime.now(timezone.utc),
        "report_finalized": finalized,
        "session_id": config.session_id,
        "mode": config.diagnostic_mode,
        "measurement_boundary": (
            "wasapi_render_to_matching_wasapi_loopback_timestamp_difference"
            if config.diagnostic_mode == "software"
            else "selected_playback_to_selected_capture_timestamp_difference"
        ),
        "config": config,
        "terminal_state": terminal_state,
        "started_at": snapshot.started_at if snapshot is not None else config.started_at,
        "finished_at": snapshot.finished_at if snapshot is not None else None,
        "audio_diagnostics": snapshot.audio_diagnostics if snapshot is not None else None,
        "statistics": _latency_statistics_payload(statistics),
        "timed_out": timed_out,
        "shutdown_completed": shutdown_completed,
        "errors": list(errors),
        "calibration": {
            "compensation_ms": 0.0,
            "capture_clock_offset": "uncalibrated",
            "sample_rate_drift": "uncalibrated",
        },
    }
    summary_path.write_text(_json_text(summary) + "\n", encoding="utf-8")
    return records_path, summary_path


def run_diagnostic(
    config: AppConfig,
    *,
    mode: str,
    count: int | None = None,
    output_root: Path | None = None,
    timeout_seconds: float = 120.0,
) -> DiagnosticRunResult:
    if not math.isfinite(timeout_seconds) or not 0 < timeout_seconds <= 3600:
        raise ValueError("诊断超时必须在 0 到 3600 秒之间")
    session_config = _make_session_config(config, mode=mode, count=count)
    root = output_root or ConfigManager().path.parent / "logs" / "diagnostics"
    directory_name = (
        f"{datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S%fZ')}-{session_config.session_id}"
    )
    report_directory = root / directory_name
    report_directory.mkdir(parents=True, exist_ok=False)

    application = QCoreApplication.instance() or QCoreApplication([])
    backend = create_audio_backend(session_config.audio.backend, session_config.audio.sample_rate)
    controller = TestController(DeviceService(AudioDeviceManager(backend)))
    errors: list[str] = []
    snapshots: list[TestSessionSnapshot] = []
    progress_records: list[MeasurementRecord] = []
    event_loop = QEventLoop()
    timer = QTimer()
    timer.setSingleShot(True)
    timed_out = False
    shutdown_completed = False

    def persist_progress(record: MeasurementRecord) -> None:
        progress_records.append(record)
        logger.info(
            "诊断测量进度：%d/%d | 有效=%s",
            record.sequence,
            session_config.test_count,
            record.measurement.is_valid,
        )
        write_diagnostic_report(
            report_directory,
            config=session_config,
            snapshot=controller.snapshot,
            records=progress_records,
            terminal_state=controller.state.name,
            errors=errors,
            timed_out=timed_out,
            shutdown_completed=shutdown_completed,
            finalized=False,
        )

    controller.error_occurred.connect(errors.append)
    controller.measurement_ready.connect(persist_progress)
    controller.session_finished.connect(snapshots.append)
    controller.finished.connect(event_loop.quit)
    timer.timeout.connect(event_loop.quit)
    try:
        controller.start(session_config)
    except Exception as exc:
        errors.append(f"启动诊断会话失败：{exc}")
        logger.error("启动环回诊断会话失败", exc_info=True)
    else:
        timer.start(max(1, round(timeout_seconds * 1000)))
        event_loop.exec()
        timed_out = not timer.isActive()
        timer.stop()
        if timed_out:
            errors.append(f"诊断会话超过 {timeout_seconds:g} 秒，已请求停止")
            controller.request_stop()

    shutdown_timeout_ms = min(5000, max(1000, round(timeout_seconds * 1000)))
    shutdown_completed = controller.shutdown(timeout_ms=shutdown_timeout_ms)
    if not shutdown_completed:
        errors.append(f"诊断线程未在 {shutdown_timeout_ms} ms 内完成收尾")
    application.processEvents()

    snapshot = snapshots[-1] if snapshots else controller.snapshot
    records = snapshot.records if snapshot is not None else controller.records
    terminal_state = (
        snapshot.terminal_state.name if snapshot is not None else controller.state.name
    )
    records_path, summary_path = write_diagnostic_report(
        report_directory,
        config=session_config,
        snapshot=snapshot,
        records=records,
        terminal_state=terminal_state,
        errors=errors,
        timed_out=timed_out,
        shutdown_completed=shutdown_completed,
    )
    valid_count = sum(record.measurement.is_valid for record in records)
    return DiagnosticRunResult(
        report_directory=report_directory,
        records_path=records_path,
        summary_path=summary_path,
        terminal_state=terminal_state,
        expected_count=session_config.test_count,
        valid_count=valid_count,
        invalid_count=len(records) - valid_count,
        timed_out=timed_out,
        shutdown_completed=shutdown_completed,
        errors=tuple(errors),
    )


def _apply_audio_overrides(config: AppConfig, args: argparse.Namespace) -> AppConfig:
    audio = config.audio
    if args.output_device is not None:
        audio = replace(audio, output_device_id=args.output_device)
    if args.input_device is not None:
        audio = replace(audio, input_device_id=args.input_device)
    if args.sample_rate is not None:
        audio = replace(audio, sample_rate=args.sample_rate)
    if args.blocksize is not None:
        audio = replace(audio, blocksize=args.blocksize)
    return replace(config, audio=audio)


def main(argv: Sequence[str] | None = None) -> int:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s | %(message)s", force=True)
    args = _build_argument_parser().parse_args(argv)
    try:
        config = _apply_audio_overrides(ConfigManager().load(), args)
        result = run_diagnostic(
            config,
            mode=args.mode,
            count=args.count,
            output_root=args.output_dir,
            timeout_seconds=args.timeout_seconds,
        )
    except Exception:
        logger.error("环回批量诊断失败", exc_info=True)
        return 1

    logger.info(
        "诊断报告：%s | 状态=%s | 有效/无效=%d/%d",
        result.report_directory,
        result.terminal_state,
        result.valid_count,
        result.invalid_count,
    )
    if not result.success:
        for message in result.errors:
            logger.error("%s", message)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
