import math
from datetime import datetime

from PySide6.QtCore import QItemSelection, QItemSelectionModel, Qt, QTimer
from PySide6.QtWidgets import (
    QAbstractItemView,
    QComboBox,
    QFileDialog,
    QFrame,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPlainTextEdit,
    QPushButton,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from core.persistence_controller import PersistenceController

_SESSION_ID_ROLE = Qt.ItemDataRole.UserRole
_CONNECTION_LABELS = {
    "unknown": "未知",
    "wired": "有线",
    "usb": "USB",
    "2.4ghz": "2.4 GHz",
    "bluetooth": "蓝牙",
    "other": "其他",
}


class HistoryPage(QWidget):
    def __init__(
        self,
        persistence_controller: PersistenceController,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self._controller = persistence_controller
        self._sessions = []
        self._detail_session = None
        self._pending: dict[str, int] = {}
        self._search_timer = QTimer(self)
        self._search_timer.setSingleShot(True)
        self._search_timer.setInterval(250)
        self._search_timer.timeout.connect(self.refresh)
        self._build_ui()
        self._connect_controller()

    def _build_ui(self) -> None:
        root_layout = QVBoxLayout(self)
        root_layout.setContentsMargins(30, 26, 30, 24)
        root_layout.setSpacing(12)

        title = QLabel("历史记录")
        title.setObjectName("pageTitle")
        subtitle = QLabel("查看已保存的测试会话、单次测量明细与导出记录")
        subtitle.setObjectName("pageSubtitle")
        root_layout.addWidget(title)
        root_layout.addWidget(subtitle)

        toolbar = QHBoxLayout()
        self.search_input = QLineEdit()
        self.search_input.setPlaceholderText("搜索耳机名称或会话名称")
        self.search_input.setClearButtonEnabled(True)
        self.connection_filter = QComboBox()
        self.connection_filter.addItem("全部连接方式", "")
        for value in ("wired", "usb", "2.4ghz", "bluetooth", "other", "unknown"):
            self.connection_filter.addItem(_CONNECTION_LABELS[value], value)
        self.sort_combo = QComboBox()
        for text, value in (
            ("测试日期：最新", "created_at_desc"),
            ("测试日期：最早", "created_at_asc"),
            ("平均原始延迟：低到高", "mean_latency_asc"),
            ("平均原始延迟：高到低", "mean_latency_desc"),
            ("P95 原始延迟：低到高", "p95_latency_asc"),
            ("P95 原始延迟：高到低", "p95_latency_desc"),
        ):
            self.sort_combo.addItem(text, value)
        self.refresh_button = QPushButton("刷新")
        self.refresh_button.setObjectName("secondaryButton")
        toolbar.addWidget(self.search_input, 1)
        toolbar.addWidget(self.connection_filter)
        toolbar.addWidget(self.sort_combo)
        toolbar.addWidget(self.refresh_button)
        root_layout.addLayout(toolbar)

        table_card = QFrame()
        table_card.setObjectName("card")
        table_layout = QVBoxLayout(table_card)
        table_layout.setContentsMargins(16, 16, 16, 12)
        self.table = QTableWidget(0, 8)
        self.table.setHorizontalHeaderLabels(
            ["测试日期", "耳机", "连接方式", "来源", "平均原始", "平均补偿后", "P95 原始", "有效 / 总数"]
        )
        self.table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Stretch)
        self.table.verticalHeader().setVisible(False)
        self.table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.table.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection)
        self.table.setAlternatingRowColors(True)
        table_layout.addWidget(self.table)
        self.empty_label = QLabel("暂无历史会话。完成测试后点击“保存会话”。")
        self.empty_label.setObjectName("mutedLabel")
        table_layout.addWidget(self.empty_label)
        root_layout.addWidget(table_card, 1)

        detail_card = QFrame()
        detail_card.setObjectName("card")
        detail_layout = QVBoxLayout(detail_card)
        detail_layout.setContentsMargins(16, 14, 16, 14)
        detail_layout.setSpacing(8)
        detail_header = QHBoxLayout()
        detail_title = QLabel("会话详情")
        detail_title.setObjectName("sectionTitle")
        detail_header.addWidget(detail_title)
        detail_header.addStretch()
        self.save_notes_button = QPushButton("保存备注")
        self.delete_button = QPushButton("删除所选")
        self.export_csv_button = QPushButton("导出 CSV")
        self.export_excel_button = QPushButton("导出 Excel")
        for button in (
            self.save_notes_button,
            self.delete_button,
            self.export_csv_button,
            self.export_excel_button,
        ):
            detail_header.addWidget(button)
        detail_layout.addLayout(detail_header)
        self.detail_label = QLabel("选择一条会话查看完整测试条件和单次测量明细。")
        self.detail_label.setObjectName("mutedLabel")
        self.detail_label.setWordWrap(True)
        detail_layout.addWidget(self.detail_label)
        self.results_table = QTableWidget(0, 8)
        self.results_table.setHorizontalHeaderLabels(
            ["序号", "原始延迟", "补偿后", "SNR", "相关系数", "有效", "无效原因", "测量时间"]
        )
        self.results_table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Stretch)
        self.results_table.verticalHeader().setVisible(False)
        self.results_table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.results_table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.results_table.setMaximumHeight(170)
        detail_layout.addWidget(self.results_table)
        notes_layout = QHBoxLayout()
        notes_layout.addWidget(QLabel("备注"))
        self.notes_edit = QPlainTextEdit()
        self.notes_edit.setPlaceholderText("记录测试环境、耳机状态或其他说明")
        self.notes_edit.setMaximumHeight(60)
        notes_layout.addWidget(self.notes_edit, 1)
        detail_layout.addLayout(notes_layout)
        root_layout.addWidget(detail_card, 1)

        self.status_label = QLabel("正在等待历史查询")
        self.status_label.setObjectName("mutedLabel")
        self.status_label.setWordWrap(True)
        root_layout.addWidget(self.status_label)
        self.search_input.textChanged.connect(self._schedule_refresh)
        self.connection_filter.currentIndexChanged.connect(self.refresh)
        self.sort_combo.currentIndexChanged.connect(self.refresh)
        self.refresh_button.clicked.connect(self.refresh)
        self.table.itemSelectionChanged.connect(self._on_selection_changed)
        self.save_notes_button.clicked.connect(self._save_notes)
        self.delete_button.clicked.connect(self._delete_selected)
        self.export_csv_button.clicked.connect(lambda: self._export_selected("csv"))
        self.export_excel_button.clicked.connect(lambda: self._export_selected("xlsx"))
        self._update_buttons()

    def _connect_controller(self) -> None:
        self._controller.sessions_loaded.connect(self._on_sessions_loaded)
        self._controller.session_loaded.connect(self._on_session_loaded)
        self._controller.notes_saved.connect(self._on_notes_saved)
        self._controller.sessions_deleted.connect(self._on_sessions_deleted)
        self._controller.export_completed.connect(self._on_export_completed)
        self._controller.operation_started.connect(self._on_operation_started)
        self._controller.operation_finished.connect(self._on_operation_finished)
        self._controller.operation_failed.connect(self._on_operation_failed)

    def refresh(self) -> None:
        self._controller.load_sessions(
            search=self.search_input.text(),
            connection_type=self.connection_filter.currentData(),
            sort_by=self.sort_combo.currentData(),
        )

    def selected_session_ids(self) -> list[int]:
        rows = self.table.selectionModel().selectedRows()
        return [
            int(self.table.item(index.row(), 0).data(_SESSION_ID_ROLE))
            for index in rows
            if self.table.item(index.row(), 0) is not None
        ]

    def _schedule_refresh(self) -> None:
        self._search_timer.start()

    def _on_sessions_loaded(self, sessions: list) -> None:
        selected_ids = set(self.selected_session_ids())
        self._sessions = list(sessions)
        selection_model = self.table.selectionModel()
        signals_blocked = selection_model.blockSignals(True)
        try:
            self.table.clearSelection()
            self.table.setRowCount(len(self._sessions))
            restored_selection = QItemSelection()
            for row, session in enumerate(self._sessions):
                values = (
                    self._format_date(session.created_at),
                    session.headphone_name or "未命名耳机",
                    _CONNECTION_LABELS.get(session.connection_type, session.connection_type),
                    "模拟" if session.is_simulation else "真实",
                    self._format_ms(session.mean_latency_ms),
                    self._format_ms(session.compensated_mean_latency_ms),
                    self._format_ms(session.p95_latency_ms),
                    f"{session.valid_count} / {session.completed_count}",
                )
                for column, value in enumerate(values):
                    item = QTableWidgetItem(value)
                    if column == 0:
                        item.setData(_SESSION_ID_ROLE, session.id)
                    self.table.setItem(row, column, item)
                if session.id in selected_ids:
                    restored_selection.select(
                        self.table.model().index(row, 0),
                        self.table.model().index(row, self.table.columnCount() - 1),
                    )
            selection_model.select(
                restored_selection,
                QItemSelectionModel.SelectionFlag.ClearAndSelect
                | QItemSelectionModel.SelectionFlag.Rows,
            )
        finally:
            selection_model.blockSignals(signals_blocked)
        self.empty_label.setVisible(not self._sessions)
        self.status_label.setText(f"已加载 {len(self._sessions)} 条会话")
        self._on_selection_changed()

    def _on_selection_changed(self) -> None:
        ids = self.selected_session_ids()
        self._update_buttons()
        if len(ids) == 1:
            if self._detail_session is None or self._detail_session.id != ids[0]:
                self.detail_label.setText("正在加载会话详情…")
                self._controller.load_session(ids[0])
        else:
            self._clear_details()
            if len(ids) > 1:
                self.detail_label.setText(f"已选择 {len(ids)} 条会话，可导出或删除；详情仅支持单条查看。")

    def _on_session_loaded(self, session) -> None:
        if self.selected_session_ids() != [session.id]:
            return
        self._detail_session = session
        profile = session.calibration_profile
        profile_text = (
            f"校准：{profile.name} · {profile.calibration_method} · {profile.calibration_date} · "
            f"补偿 {profile.compensation_ms:.2f} ms"
            if profile is not None
            else f"未选择校准配置；手动补偿 {session.compensation_ms:.2f} ms"
        )
        simulation_text = "模拟结果（非真实声学测量）" if session.is_simulation else "真实音频链路"
        self.detail_label.setText(
            f"{session.session_name} · {simulation_text}\n"
            f"设备：{session.output_device} → {session.input_device} · "
            f"采样率 {session.output_sample_rate} / {session.input_sample_rate} Hz · "
            f"模式 {session.audio_mode} · 连接 {_CONNECTION_LABELS.get(session.connection_type, session.connection_type)}\n"
            f"{profile_text} · 预热 {session.warmup_seconds:g} s · "
            f"背景 {session.background_kind} / {session.background_amplitude:g}\n"
            f"样本：{session.completed_count} 次（有效 {session.valid_count}，无效 {session.invalid_count}）· "
            f"原始平均 {self._format_ms(session.mean_latency_ms)} · "
            f"补偿后平均 {self._format_ms(session.compensated_mean_latency_ms)} · "
            f"状态 {session.terminal_state}"
        )
        self.notes_edit.setPlainText(session.notes)
        self._populate_results(session.results)
        self._update_buttons()

    def _populate_results(self, results) -> None:
        self.results_table.setRowCount(len(results))
        for row, result in enumerate(results):
            values = (
                str(result.sequence),
                self._format_ms(result.raw_latency_ms),
                self._format_ms(result.compensated_latency_ms),
                self._format_snr(result.snr_db),
                f"{result.correlation_peak:.3f}",
                "是" if result.is_valid else "否",
                result.invalid_reason or "—",
                self._format_date(result.timestamp),
            )
            for column, value in enumerate(values):
                self.results_table.setItem(row, column, QTableWidgetItem(value))

    def _clear_details(self) -> None:
        self._detail_session = None
        self.detail_label.setText("选择一条会话查看完整测试条件和单次测量明细。")
        self.notes_edit.clear()
        self.results_table.setRowCount(0)
        self._update_buttons()

    def _save_notes(self) -> None:
        if self._detail_session is not None:
            self._controller.update_notes(
                self._detail_session.id,
                self.notes_edit.toPlainText(),
            )

    def _on_notes_saved(self, session_id: int) -> None:
        self.status_label.setText("备注已保存")
        if self._detail_session is not None and self._detail_session.id == session_id:
            self._controller.load_session(session_id)

    def _delete_selected(self) -> None:
        ids = self.selected_session_ids()
        if not ids:
            return
        confirmation = QMessageBox(self)
        confirmation.setWindowTitle("确认删除历史会话")
        confirmation.setText(f"确定删除选中的 {len(ids)} 条会话及其全部单次测量吗？此操作不可撤销。")
        confirmation.setStandardButtons(
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No
        )
        confirmation.button(QMessageBox.StandardButton.Yes).setAutoDefault(False)
        confirmation.button(QMessageBox.StandardButton.No).setAutoDefault(True)
        confirmation.setDefaultButton(QMessageBox.StandardButton.No)
        confirmation.setEscapeButton(QMessageBox.StandardButton.No)
        if confirmation.exec() != QMessageBox.StandardButton.Yes:
            return
        self._controller.delete_sessions(ids)

    def _on_sessions_deleted(self, count: int) -> None:
        self.status_label.setText(f"已删除 {count} 条会话及其单次结果")
        self._clear_details()
        self.refresh()

    def _export_selected(self, file_format: str) -> None:
        ids = self.selected_session_ids()
        if not ids:
            return
        extension = "CSV 文件 (*.csv)" if file_format == "csv" else "Excel 文件 (*.xlsx)"
        suffix = "csv" if file_format == "csv" else "xlsx"
        path, _selected_filter = QFileDialog.getSaveFileName(
            self,
            "导出历史测量",
            f"latency_sessions.{suffix}",
            extension,
        )
        if path:
            self._controller.export_sessions(ids, path, file_format)

    def _on_export_completed(self, path: str, count: int) -> None:
        self.status_label.setText(f"已导出 {count} 条会话：{path}")

    def _on_operation_started(self, operation: str) -> None:
        self._pending[operation] = self._pending.get(operation, 0) + 1
        labels = {
            "load_sessions": "正在查询历史会话…",
            "load_session": "正在加载会话详情…",
            "update_notes": "正在保存备注…",
            "delete_sessions": "正在删除会话…",
            "export_sessions": "正在导出所选会话…",
        }
        if operation in labels:
            self.status_label.setText(labels[operation])
        self._update_buttons()

    def _on_operation_finished(self, operation: str) -> None:
        self._pending[operation] = max(0, self._pending.get(operation, 0) - 1)
        self._update_buttons()

    def _on_operation_failed(self, operation: str, message: str) -> None:
        self.status_label.setText(f"操作失败（{operation}）：{message}")
        self._update_buttons()

    def _update_buttons(self) -> None:
        ids = self.selected_session_ids()
        single_detail = (
            len(ids) == 1
            and self._detail_session is not None
            and self._detail_session.id == ids[0]
        )
        self.save_notes_button.setEnabled(single_detail and not self._pending.get("update_notes"))
        self.delete_button.setEnabled(bool(ids) and not self._pending.get("delete_sessions"))
        export_busy = bool(self._pending.get("export_sessions"))
        self.export_csv_button.setEnabled(bool(ids) and not export_busy)
        self.export_excel_button.setEnabled(bool(ids) and not export_busy)
        self.refresh_button.setEnabled(not self._pending.get("load_sessions"))

    @staticmethod
    def _format_ms(value: float | None) -> str:
        if value is None:
            return "—"
        if math.isinf(value):
            return "∞" if value > 0 else "-∞"
        return f"{value:.2f} ms"

    @staticmethod
    def _format_snr(value: float | None) -> str:
        if value is None:
            return "—"
        if math.isinf(value):
            return "∞" if value > 0 else "-∞"
        return f"{value:.1f} dB"

    @staticmethod
    def _format_date(value: str) -> str:
        try:
            parsed = datetime.fromisoformat(value)
        except (TypeError, ValueError):
            return str(value)
        return parsed.astimezone().strftime("%Y-%m-%d %H:%M:%S")
