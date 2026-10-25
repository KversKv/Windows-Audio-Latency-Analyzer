import json
import sqlite3
from collections.abc import Iterable
from dataclasses import replace

from data.database import Database
from data.models import CalibrationProfile, TestResult, TestSession


class RepositoryError(RuntimeError):
    pass


class DuplicateSessionError(RepositoryError):
    pass


class SessionNotFoundError(RepositoryError):
    pass


_SESSION_INSERT_COLUMNS = (
    "session_uid",
    "session_name",
    "headphone_name",
    "connection_type",
    "output_device_id_json",
    "output_device",
    "input_device_id_json",
    "input_device",
    "output_sample_rate",
    "input_sample_rate",
    "audio_mode",
    "backend",
    "compensation_ms",
    "test_count",
    "completed_count",
    "valid_count",
    "invalid_count",
    "minimum_valid_count",
    "mean_latency_ms",
    "median_latency_ms",
    "p90_latency_ms",
    "p95_latency_ms",
    "min_latency_ms",
    "max_latency_ms",
    "std_latency_ms",
    "compensated_mean_latency_ms",
    "compensated_median_latency_ms",
    "compensated_p90_latency_ms",
    "compensated_p95_latency_ms",
    "compensated_min_latency_ms",
    "compensated_max_latency_ms",
    "compensated_std_latency_ms",
    "warmup_seconds",
    "background_kind",
    "background_amplitude",
    "terminal_state",
    "is_simulation",
    "created_at",
    "started_at",
    "finished_at",
    "notes",
    "audio_config_json",
    "measurement_config_json",
)

_RESULT_INSERT_COLUMNS = (
    "session_id",
    "sequence",
    "raw_latency_ms",
    "compensated_latency_ms",
    "compensation_ms",
    "correlation_lag_samples",
    "correlation_peak",
    "correlation_ambiguity_ratio",
    "snr_db",
    "signal_level",
    "noise_floor",
    "playback_timestamp_seconds",
    "capture_timestamp_seconds",
    "capture_window_start_sample",
    "playback_sample_index",
    "is_valid",
    "invalid_reason",
    "timestamp",
    "is_simulation",
    "timing_basis",
    "timing_limitations_json",
    "reference_waveform",
    "captured_waveform",
)

_SESSION_SELECT = """
SELECT s.*,
       c.id AS calibration_id,
       c.profile_uid AS calibration_profile_uid,
       c.name AS calibration_name,
       c.output_device_id_json AS calibration_output_device_id_json,
       c.output_device AS calibration_output_device,
       c.input_device_id_json AS calibration_input_device_id_json,
       c.input_device AS calibration_input_device,
       c.sample_rate AS calibration_sample_rate,
       c.audio_mode AS calibration_audio_mode,
       c.compensation_ms AS calibration_compensation_ms,
       c.calibration_method AS calibration_method,
       c.calibration_date AS calibration_date,
       c.notes AS calibration_notes
FROM test_sessions AS s
LEFT JOIN calibration_profiles AS c ON c.session_id = s.id
"""

_SORT_EXPRESSIONS = {
    "created_at_desc": "s.created_at DESC, s.id DESC",
    "created_at_asc": "s.created_at ASC, s.id ASC",
    "mean_latency_asc": "s.mean_latency_ms IS NULL, s.mean_latency_ms ASC, s.id DESC",
    "mean_latency_desc": "s.mean_latency_ms IS NULL, s.mean_latency_ms DESC, s.id DESC",
    "p95_latency_asc": "s.p95_latency_ms IS NULL, s.p95_latency_ms ASC, s.id DESC",
    "p95_latency_desc": "s.p95_latency_ms IS NULL, s.p95_latency_ms DESC, s.id DESC",
}


def _json_value(value: int | str | None) -> str:
    return json.dumps(value, ensure_ascii=False)


def _from_json(value: str) -> int | str | None:
    return json.loads(value)


def _insert_sql(table: str, columns: tuple[str, ...]) -> str:
    names = ", ".join(columns)
    placeholders = ", ".join("?" for _ in columns)
    return f"INSERT INTO {table} ({names}) VALUES ({placeholders})"


class SessionRepository:
    def __init__(self, database: Database) -> None:
        database.initialize()
        self.database = database

    def save_session(self, session: TestSession) -> int:
        self._validate_session(session)
        with self.database.connect() as connection:
            try:
                connection.execute("BEGIN IMMEDIATE")
                existing = connection.execute(
                    "SELECT id FROM test_sessions WHERE session_uid = ?",
                    (session.session_uid,),
                ).fetchone()
                if existing is not None:
                    raise DuplicateSessionError("该会话已经保存，未重复写入")
                cursor = connection.execute(
                    _insert_sql("test_sessions", _SESSION_INSERT_COLUMNS),
                    self._session_values(session),
                )
                session_id = cursor.lastrowid
                if session_id is None:
                    raise RepositoryError("数据库未返回会话 ID")
                if session.calibration_profile is not None:
                    self._insert_calibration(connection, session_id, session.calibration_profile)
                connection.executemany(
                    _insert_sql("test_results", _RESULT_INSERT_COLUMNS),
                    [self._result_values(session_id, result) for result in session.results],
                )
                connection.commit()
                return int(session_id)
            except Exception:
                connection.rollback()
                raise

    def list_sessions(
        self,
        *,
        search: str = "",
        connection_type: str = "",
        sort_by: str = "created_at_desc",
    ) -> list[TestSession]:
        if sort_by not in _SORT_EXPRESSIONS:
            raise ValueError(f"不支持的历史排序：{sort_by}")
        conditions: list[str] = []
        parameters: list[object] = []
        if search.strip():
            pattern = self._like_pattern(search.strip())
            conditions.append("(s.headphone_name LIKE ? ESCAPE '\\' OR s.session_name LIKE ? ESCAPE '\\')")
            parameters.extend((pattern, pattern))
        if connection_type:
            conditions.append("s.connection_type = ?")
            parameters.append(connection_type)
        where = f"WHERE {' AND '.join(conditions)}" if conditions else ""
        sql = f"{_SESSION_SELECT} {where} ORDER BY {_SORT_EXPRESSIONS[sort_by]}"
        with self.database.connect() as connection:
            rows = connection.execute(sql, parameters).fetchall()
        return [self._row_to_session(row) for row in rows]

    def get_session(self, session_id: int) -> TestSession:
        with self.database.connect() as connection:
            row = connection.execute(
                f"{_SESSION_SELECT} WHERE s.id = ?",
                (session_id,),
            ).fetchone()
            if row is None:
                raise SessionNotFoundError("历史会话不存在或已删除")
            result_rows = connection.execute(
                "SELECT * FROM test_results WHERE session_id = ? ORDER BY sequence ASC",
                (session_id,),
            ).fetchall()
        session = self._row_to_session(row)
        return replace(
            session,
            results=tuple(self._row_to_result(result) for result in result_rows),
        )

    def update_notes(self, session_id: int, notes: str) -> None:
        with self.database.connect() as connection:
            cursor = connection.execute(
                "UPDATE test_sessions SET notes = ? WHERE id = ?",
                (notes.strip(), session_id),
            )
            if cursor.rowcount != 1:
                raise SessionNotFoundError("历史会话不存在或已删除")
            connection.commit()

    def delete_sessions(self, session_ids: Iterable[int]) -> int:
        ids = tuple(dict.fromkeys(int(session_id) for session_id in session_ids))
        if not ids:
            return 0
        placeholders = ", ".join("?" for _ in ids)
        with self.database.connect() as connection:
            cursor = connection.execute(
                f"DELETE FROM test_sessions WHERE id IN ({placeholders})",
                ids,
            )
            connection.commit()
            return cursor.rowcount

    @staticmethod
    def _validate_session(session: TestSession) -> None:
        if not session.session_uid.strip():
            raise ValueError("会话 UID 不能为空")
        if not session.results:
            raise ValueError("没有单次测量结果，不能保存空会话")
        sequences = [result.sequence for result in session.results]
        if len(set(sequences)) != len(sequences) or any(sequence < 1 for sequence in sequences):
            raise ValueError("单次测量序号必须唯一且大于 0")
        valid_count = sum(result.is_valid for result in session.results)
        if session.completed_count != len(session.results):
            raise ValueError("会话完成次数与单次结果数量不一致")
        if session.valid_count != valid_count or session.invalid_count != len(session.results) - valid_count:
            raise ValueError("会话有效/无效计数与单次结果不一致")

    @staticmethod
    def _like_pattern(value: str) -> str:
        escaped = value.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
        return f"%{escaped}%"

    @staticmethod
    def _session_values(session: TestSession) -> tuple[object, ...]:
        values = {
            "session_uid": session.session_uid,
            "session_name": session.session_name,
            "headphone_name": session.headphone_name,
            "connection_type": session.connection_type,
            "output_device_id_json": _json_value(session.output_device_id),
            "output_device": session.output_device,
            "input_device_id_json": _json_value(session.input_device_id),
            "input_device": session.input_device,
            "output_sample_rate": session.output_sample_rate,
            "input_sample_rate": session.input_sample_rate,
            "audio_mode": session.audio_mode,
            "backend": session.backend,
            "compensation_ms": session.compensation_ms,
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
            "is_simulation": int(session.is_simulation),
            "created_at": session.created_at,
            "started_at": session.started_at,
            "finished_at": session.finished_at,
            "notes": session.notes,
            "audio_config_json": session.audio_config_json,
            "measurement_config_json": session.measurement_config_json,
        }
        return tuple(values[column] for column in _SESSION_INSERT_COLUMNS)

    @staticmethod
    def _result_values(session_id: int, result: TestResult) -> tuple[object, ...]:
        values = {
            "session_id": session_id,
            "sequence": result.sequence,
            "raw_latency_ms": result.raw_latency_ms,
            "compensated_latency_ms": result.compensated_latency_ms,
            "compensation_ms": result.compensation_ms,
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
            "is_valid": int(result.is_valid),
            "invalid_reason": result.invalid_reason,
            "timestamp": result.timestamp,
            "is_simulation": int(result.is_simulation),
            "timing_basis": result.timing_basis,
            "timing_limitations_json": json.dumps(
                list(result.timing_limitations), ensure_ascii=False
            ),
            "reference_waveform": sqlite3.Binary(result.reference_waveform),
            "captured_waveform": sqlite3.Binary(result.captured_waveform),
        }
        return tuple(values[column] for column in _RESULT_INSERT_COLUMNS)

    @staticmethod
    def _insert_calibration(
        connection: sqlite3.Connection,
        session_id: int,
        profile: CalibrationProfile,
    ) -> None:
        connection.execute(
            """
            INSERT INTO calibration_profiles (
                session_id, profile_uid, name, output_device_id_json, output_device,
                input_device_id_json, input_device, sample_rate, audio_mode,
                compensation_ms, calibration_method, calibration_date, notes
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                session_id,
                profile.profile_id,
                profile.name,
                _json_value(profile.output_device_id),
                profile.output_device,
                _json_value(profile.input_device_id),
                profile.input_device,
                profile.sample_rate,
                profile.audio_mode,
                profile.compensation_ms,
                profile.calibration_method,
                profile.calibration_date,
                profile.notes,
            ),
        )

    @staticmethod
    def _row_to_session(row: sqlite3.Row) -> TestSession:
        profile = None
        if row["calibration_id"] is not None:
            profile = CalibrationProfile(
                id=row["calibration_id"],
                session_id=row["id"],
                profile_id=row["calibration_profile_uid"],
                name=row["calibration_name"],
                output_device_id=_from_json(row["calibration_output_device_id_json"]),
                output_device=row["calibration_output_device"],
                input_device_id=_from_json(row["calibration_input_device_id_json"]),
                input_device=row["calibration_input_device"],
                sample_rate=row["calibration_sample_rate"],
                audio_mode=row["calibration_audio_mode"],
                compensation_ms=row["calibration_compensation_ms"],
                calibration_method=row["calibration_method"],
                calibration_date=row["calibration_date"],
                notes=row["calibration_notes"],
            )
        return TestSession(
            id=row["id"],
            session_uid=row["session_uid"],
            session_name=row["session_name"],
            headphone_name=row["headphone_name"],
            connection_type=row["connection_type"],
            output_device_id=_from_json(row["output_device_id_json"]),
            output_device=row["output_device"],
            input_device_id=_from_json(row["input_device_id_json"]),
            input_device=row["input_device"],
            output_sample_rate=row["output_sample_rate"],
            input_sample_rate=row["input_sample_rate"],
            audio_mode=row["audio_mode"],
            backend=row["backend"],
            compensation_ms=row["compensation_ms"],
            test_count=row["test_count"],
            completed_count=row["completed_count"],
            valid_count=row["valid_count"],
            invalid_count=row["invalid_count"],
            minimum_valid_count=row["minimum_valid_count"],
            mean_latency_ms=row["mean_latency_ms"],
            median_latency_ms=row["median_latency_ms"],
            p90_latency_ms=row["p90_latency_ms"],
            p95_latency_ms=row["p95_latency_ms"],
            min_latency_ms=row["min_latency_ms"],
            max_latency_ms=row["max_latency_ms"],
            std_latency_ms=row["std_latency_ms"],
            compensated_mean_latency_ms=row["compensated_mean_latency_ms"],
            compensated_median_latency_ms=row["compensated_median_latency_ms"],
            compensated_p90_latency_ms=row["compensated_p90_latency_ms"],
            compensated_p95_latency_ms=row["compensated_p95_latency_ms"],
            compensated_min_latency_ms=row["compensated_min_latency_ms"],
            compensated_max_latency_ms=row["compensated_max_latency_ms"],
            compensated_std_latency_ms=row["compensated_std_latency_ms"],
            warmup_seconds=row["warmup_seconds"],
            background_kind=row["background_kind"],
            background_amplitude=row["background_amplitude"],
            terminal_state=row["terminal_state"],
            is_simulation=bool(row["is_simulation"]),
            created_at=row["created_at"],
            started_at=row["started_at"],
            finished_at=row["finished_at"],
            notes=row["notes"],
            calibration_profile=profile,
            audio_config_json=row["audio_config_json"],
            measurement_config_json=row["measurement_config_json"],
        )

    @staticmethod
    def _row_to_result(row: sqlite3.Row) -> TestResult:
        return TestResult(
            id=row["id"],
            session_id=row["session_id"],
            sequence=row["sequence"],
            raw_latency_ms=row["raw_latency_ms"],
            compensated_latency_ms=row["compensated_latency_ms"],
            compensation_ms=row["compensation_ms"],
            correlation_lag_samples=row["correlation_lag_samples"],
            correlation_peak=row["correlation_peak"],
            correlation_ambiguity_ratio=row["correlation_ambiguity_ratio"],
            snr_db=row["snr_db"],
            signal_level=row["signal_level"],
            noise_floor=row["noise_floor"],
            playback_timestamp_seconds=row["playback_timestamp_seconds"],
            capture_timestamp_seconds=row["capture_timestamp_seconds"],
            capture_window_start_sample=row["capture_window_start_sample"],
            playback_sample_index=row["playback_sample_index"],
            is_valid=bool(row["is_valid"]),
            invalid_reason=row["invalid_reason"],
            timestamp=row["timestamp"],
            is_simulation=bool(row["is_simulation"]),
            timing_basis=row["timing_basis"],
            timing_limitations=tuple(json.loads(row["timing_limitations_json"])),
            reference_waveform=bytes(row["reference_waveform"]),
            captured_waveform=bytes(row["captured_waveform"]),
        )
