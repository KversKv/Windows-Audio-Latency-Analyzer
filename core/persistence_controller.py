import logging
from collections.abc import Callable, Iterable
from pathlib import Path
from typing import Any

from PySide6.QtCore import QObject, QThread, Signal, Slot

from core.persistence_service import PersistenceService
from core.test_models import TestSessionSnapshot

logger = logging.getLogger(__name__)


class PersistenceWorker(QObject):
    sessions_loaded = Signal(object)
    session_loaded = Signal(object)
    comparison_session_list_loaded = Signal(object)
    comparison_sessions_loaded = Signal(object)
    session_saved = Signal(object)
    notes_saved = Signal(int)
    sessions_deleted = Signal(int)
    export_completed = Signal(str, int)
    operation_failed = Signal(str, str)
    operation_finished = Signal(str)

    def __init__(self, service: PersistenceService) -> None:
        super().__init__()
        self._service = service

    @Slot(object)
    def save_session(self, snapshot: TestSessionSnapshot) -> None:
        self._execute(
            "save_session",
            lambda: self._service.save_session(snapshot),
            lambda _session_id: self.session_saved.emit(snapshot),
        )

    @Slot(str, str, str)
    def load_sessions(self, search: str, connection_type: str, sort_by: str) -> None:
        self._execute(
            "load_sessions",
            lambda: self._service.list_sessions(
                search=search,
                connection_type=connection_type,
                sort_by=sort_by,
            ),
            self.sessions_loaded.emit,
        )

    @Slot(int)
    def load_session(self, session_id: int) -> None:
        self._execute(
            "load_session",
            lambda: self._service.get_session(session_id),
            self.session_loaded.emit,
        )

    @Slot()
    def load_comparison_session_list(self) -> None:
        self._execute(
            "load_comparison_session_list",
            lambda: self._service.list_sessions(sort_by="created_at_desc"),
            self.comparison_session_list_loaded.emit,
        )

    @Slot(object)
    def load_comparison_sessions(self, session_ids: object) -> None:
        ids = tuple(session_ids)
        self._execute(
            "load_comparison_sessions",
            lambda: self._service.load_comparison_sessions(ids),
            self.comparison_sessions_loaded.emit,
        )

    @Slot(int, str)
    def update_notes(self, session_id: int, notes: str) -> None:
        def save() -> int:
            self._service.update_notes(session_id, notes)
            return session_id

        self._execute("update_notes", save, self.notes_saved.emit)

    @Slot(object)
    def delete_sessions(self, session_ids: object) -> None:
        self._execute(
            "delete_sessions",
            lambda: self._service.delete_sessions(session_ids),
            self.sessions_deleted.emit,
        )

    @Slot(object, object, str)
    def export_sessions(self, session_ids: object, destination: object, file_format: str) -> None:
        ids = tuple(session_ids)

        def export() -> tuple[str, int]:
            path = self._service.export_sessions(ids, Path(destination), file_format)
            return str(path), len(ids)

        self._execute(
            "export_sessions",
            export,
            lambda result: self.export_completed.emit(*result),
        )

    def _execute(
        self,
        operation: str,
        callback: Callable[[], Any],
        succeeded: Callable[[Any], None],
    ) -> None:
        try:
            result = callback()
        except Exception as exc:
            logger.error("持久化操作 %s 失败", operation, exc_info=True)
            self.operation_failed.emit(operation, str(exc) or type(exc).__name__)
        else:
            succeeded(result)
        finally:
            self.operation_finished.emit(operation)


class PersistenceController(QObject):
    sessions_loaded = Signal(object)
    session_loaded = Signal(object)
    comparison_session_list_loaded = Signal(object)
    comparison_sessions_loaded = Signal(object)
    session_saved = Signal(object)
    notes_saved = Signal(int)
    sessions_deleted = Signal(int)
    export_completed = Signal(str, int)
    operation_started = Signal(str)
    operation_finished = Signal(str)
    operation_failed = Signal(str, str)
    save_failed = Signal(str)

    _save_requested = Signal(object)
    _load_sessions_requested = Signal(str, str, str)
    _load_session_requested = Signal(int)
    _load_comparison_list_requested = Signal()
    _load_comparison_requested = Signal(object)
    _update_notes_requested = Signal(int, str)
    _delete_requested = Signal(object)
    _export_requested = Signal(object, object, str)

    def __init__(self, service: PersistenceService, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self._shutting_down = False
        self._thread = QThread(self)
        self._worker = PersistenceWorker(service)
        self._worker.moveToThread(self._thread)
        self._save_requested.connect(self._worker.save_session)
        self._load_sessions_requested.connect(self._worker.load_sessions)
        self._load_session_requested.connect(self._worker.load_session)
        self._load_comparison_list_requested.connect(self._worker.load_comparison_session_list)
        self._load_comparison_requested.connect(self._worker.load_comparison_sessions)
        self._update_notes_requested.connect(self._worker.update_notes)
        self._delete_requested.connect(self._worker.delete_sessions)
        self._export_requested.connect(self._worker.export_sessions)
        self._worker.sessions_loaded.connect(self.sessions_loaded)
        self._worker.session_loaded.connect(self.session_loaded)
        self._worker.comparison_session_list_loaded.connect(self.comparison_session_list_loaded)
        self._worker.comparison_sessions_loaded.connect(self.comparison_sessions_loaded)
        self._worker.session_saved.connect(self.session_saved)
        self._worker.notes_saved.connect(self.notes_saved)
        self._worker.sessions_deleted.connect(self.sessions_deleted)
        self._worker.export_completed.connect(self.export_completed)
        self._worker.operation_finished.connect(self.operation_finished)
        self._worker.operation_failed.connect(self._on_operation_failed)
        self._thread.finished.connect(self._worker.deleteLater)
        self._thread.finished.connect(self._thread.deleteLater)
        self._thread.start()

    def save_session(self, snapshot: TestSessionSnapshot) -> None:
        if self._accept("save_session"):
            self._save_requested.emit(snapshot)

    def load_sessions(
        self,
        search: str = "",
        connection_type: str = "",
        sort_by: str = "created_at_desc",
    ) -> None:
        if self._accept("load_sessions"):
            self._load_sessions_requested.emit(search, connection_type, sort_by)

    def load_session(self, session_id: int) -> None:
        if self._accept("load_session"):
            self._load_session_requested.emit(session_id)

    def load_comparison_session_list(self) -> None:
        if self._accept("load_comparison_session_list"):
            self._load_comparison_list_requested.emit()

    def load_comparison_sessions(self, session_ids: Iterable[int]) -> None:
        if self._accept("load_comparison_sessions"):
            self._load_comparison_requested.emit(tuple(session_ids))

    def update_notes(self, session_id: int, notes: str) -> None:
        if self._accept("update_notes"):
            self._update_notes_requested.emit(session_id, notes)

    def delete_sessions(self, session_ids: Iterable[int]) -> None:
        if self._accept("delete_sessions"):
            self._delete_requested.emit(tuple(session_ids))

    def export_sessions(
        self,
        session_ids: Iterable[int],
        destination: str | Path,
        file_format: str,
    ) -> None:
        if self._accept("export_sessions"):
            self._export_requested.emit(tuple(session_ids), str(destination), file_format)

    def shutdown(self, timeout_ms: int = 3000) -> bool:
        if self._shutting_down:
            return True
        self._shutting_down = True
        if self._thread.isRunning():
            self._thread.quit()
            if not self._thread.wait(timeout_ms):
                logger.warning("持久化线程未在 %d ms 内结束", timeout_ms)
                return False
        return True

    def _accept(self, operation: str) -> bool:
        if self._shutting_down:
            return False
        self.operation_started.emit(operation)
        return True

    @Slot(str, str)
    def _on_operation_failed(self, operation: str, message: str) -> None:
        if operation == "save_session":
            self.save_failed.emit(message)
        else:
            self.operation_failed.emit(operation, message)
