import sqlite3
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator

SCHEMA_VERSION = 1


class DatabaseError(RuntimeError):
    pass


_SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS test_sessions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    session_uid TEXT NOT NULL UNIQUE,
    session_name TEXT NOT NULL,
    headphone_name TEXT NOT NULL,
    connection_type TEXT NOT NULL,
    output_device_id_json TEXT NOT NULL,
    output_device TEXT NOT NULL,
    input_device_id_json TEXT NOT NULL,
    input_device TEXT NOT NULL,
    output_sample_rate INTEGER NOT NULL,
    input_sample_rate INTEGER NOT NULL,
    audio_mode TEXT NOT NULL,
    backend TEXT NOT NULL,
    compensation_ms REAL NOT NULL,
    test_count INTEGER NOT NULL,
    completed_count INTEGER NOT NULL,
    valid_count INTEGER NOT NULL,
    invalid_count INTEGER NOT NULL,
    minimum_valid_count INTEGER NOT NULL,
    mean_latency_ms REAL,
    median_latency_ms REAL,
    p90_latency_ms REAL,
    p95_latency_ms REAL,
    min_latency_ms REAL,
    max_latency_ms REAL,
    std_latency_ms REAL,
    compensated_mean_latency_ms REAL,
    compensated_median_latency_ms REAL,
    compensated_p90_latency_ms REAL,
    compensated_p95_latency_ms REAL,
    compensated_min_latency_ms REAL,
    compensated_max_latency_ms REAL,
    compensated_std_latency_ms REAL,
    warmup_seconds REAL NOT NULL,
    background_kind TEXT NOT NULL,
    background_amplitude REAL NOT NULL,
    terminal_state TEXT NOT NULL,
    is_simulation INTEGER NOT NULL CHECK (is_simulation IN (0, 1)),
    created_at TEXT NOT NULL,
    started_at TEXT NOT NULL,
    finished_at TEXT NOT NULL,
    notes TEXT NOT NULL DEFAULT '',
    audio_config_json TEXT NOT NULL,
    measurement_config_json TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS test_results (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id INTEGER NOT NULL REFERENCES test_sessions(id) ON DELETE CASCADE,
    sequence INTEGER NOT NULL,
    raw_latency_ms REAL,
    compensated_latency_ms REAL,
    compensation_ms REAL NOT NULL,
    correlation_lag_samples REAL,
    correlation_peak REAL NOT NULL,
    correlation_ambiguity_ratio REAL NOT NULL,
    snr_db REAL,
    signal_level REAL NOT NULL,
    noise_floor REAL NOT NULL,
    playback_timestamp_seconds REAL,
    capture_timestamp_seconds REAL,
    capture_window_start_sample INTEGER,
    playback_sample_index INTEGER,
    is_valid INTEGER NOT NULL CHECK (is_valid IN (0, 1)),
    invalid_reason TEXT,
    timestamp TEXT NOT NULL,
    is_simulation INTEGER NOT NULL CHECK (is_simulation IN (0, 1)),
    timing_basis TEXT NOT NULL,
    timing_limitations_json TEXT NOT NULL,
    reference_waveform BLOB NOT NULL,
    captured_waveform BLOB NOT NULL,
    UNIQUE (session_id, sequence)
);

CREATE TABLE IF NOT EXISTS calibration_profiles (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id INTEGER NOT NULL UNIQUE REFERENCES test_sessions(id) ON DELETE CASCADE,
    profile_uid TEXT NOT NULL,
    name TEXT NOT NULL,
    output_device_id_json TEXT NOT NULL,
    output_device TEXT NOT NULL,
    input_device_id_json TEXT NOT NULL,
    input_device TEXT NOT NULL,
    sample_rate INTEGER NOT NULL,
    audio_mode TEXT NOT NULL,
    compensation_ms REAL NOT NULL,
    calibration_method TEXT NOT NULL,
    calibration_date TEXT NOT NULL,
    notes TEXT NOT NULL DEFAULT ''
);

CREATE INDEX IF NOT EXISTS idx_test_sessions_created_at
    ON test_sessions(created_at DESC, id DESC);
CREATE INDEX IF NOT EXISTS idx_test_sessions_headphone
    ON test_sessions(headphone_name);
CREATE INDEX IF NOT EXISTS idx_test_sessions_connection
    ON test_sessions(connection_type);
CREATE INDEX IF NOT EXISTS idx_test_results_session
    ON test_results(session_id, sequence);
"""


class Database:
    def __init__(self, path: str | Path, *, timeout_seconds: float = 2.0) -> None:
        if timeout_seconds <= 0:
            raise ValueError("数据库连接超时必须大于 0 秒")
        self.path = Path(path)
        self.timeout_seconds = timeout_seconds

    @contextmanager
    def connect(self) -> Iterator[sqlite3.Connection]:
        connection = sqlite3.connect(self.path, timeout=self.timeout_seconds)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys = ON")
        connection.execute(f"PRAGMA busy_timeout = {int(self.timeout_seconds * 1000)}")
        try:
            yield connection
        finally:
            connection.close()

    def initialize(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.connect() as connection:
            connection.execute("PRAGMA journal_mode = WAL")
            version = int(connection.execute("PRAGMA user_version").fetchone()[0])
            if version > SCHEMA_VERSION:
                raise DatabaseError(
                    f"数据库 schema 版本 {version} 高于当前支持的 {SCHEMA_VERSION}"
                )
            if version == SCHEMA_VERSION:
                return
            if version != 0:
                raise DatabaseError(f"缺少从 schema 版本 {version} 开始的迁移")
            try:
                connection.executescript(
                    "BEGIN IMMEDIATE;\n"
                    + _SCHEMA_SQL
                    + f"\nPRAGMA user_version = {SCHEMA_VERSION};\nCOMMIT;"
                )
            except sqlite3.Error as exc:
                connection.rollback()
                raise DatabaseError("初始化数据库 schema 失败") from exc

    def schema_version(self) -> int:
        with self.connect() as connection:
            return int(connection.execute("PRAGMA user_version").fetchone()[0])
