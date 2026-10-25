import json
import math
from datetime import datetime

import numpy as np
import pyqtgraph as pg
from PySide6.QtCore import QItemSelection, QItemSelectionModel, Qt
from PySide6.QtGui import QBrush, QColor
from PySide6.QtWidgets import (
    QAbstractItemView,
    QComboBox,
    QFrame,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QPushButton,
    QScrollArea,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from core.persistence_controller import PersistenceController
from core.persistence_service import SessionComparison

_SESSION_ID_ROLE = Qt.ItemDataRole.UserRole
_CONNECTION_LABELS = {
    "unknown": "未知",
    "wired": "有线",
    "usb": "USB",
    "2.4ghz": "2.4 GHz",
    "bluetooth": "蓝牙",
    "other": "其他",
}
_SESSION_COLORS = ("#45b8c7", "#d7a64a", "#8ba8e8", "#b184d6", "#68b984", "#e58b68")
_DIFFERENCE_BRUSH = QBrush(QColor("#2b2518"))


class ComparisonPage(QWidget):
    def __init__(
        self,
        persistence_controller: PersistenceController,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self._controller = persistence_controller
        self._sessions = []
        self._comparisons: tuple[SessionComparison, ...] = ()
        self._pending: dict[str, int] = {}
        self.statistics_bars: dict[str, pg.BarGraphItem] = {}
        self.trend_curves: dict[int, pg.PlotDataItem] = {}
        self.invalid_trend_items: list[pg.PlotDataItem] = []
        self.distribution_bars: dict[int, pg.BarGraphItem] = {}
        self.distribution_bin_edges = np.asarray([], dtype=np.float64)
        self._build_ui()
        self._connect_controller()

    def _build_ui(self) -> None:
        root_layout = QVBoxLayout(self)
        root_layout.setContentsMargins(30, 26, 30, 24)
        root_layout.setSpacing(12)

        title = QLabel("对比分析")
        title.setObjectName("pageTitle")
        subtitle = QLabel("由已保存的单次记录重算统计，对比延迟水平、波动、趋势与测试条件")
        subtitle.setObjectName("pageSubtitle")
        root_layout.addWidget(title)
        root_layout.addWidget(subtitle)

        selection_card = QFrame()
        selection_card.setObjectName("card")
        selection_layout = QVBoxLayout(selection_card)
        selection_layout.setContentsMargins(16, 14, 16, 12)
        selection_layout.setSpacing(8)
        selection_header = QHBoxLayout()
        selection_title = QLabel("历史会话（可多选）")
        selection_title.setObjectName("sectionTitle")
        selection_header.addWidget(selection_title)
        selection_header.addStretch()
        selection_header.addWidget(QLabel("统计口径"))
        self.metric_combo = QComboBox()
        self.metric_combo.addItem("原始延迟", "raw")
        self.metric_combo.addItem("补偿后延迟", "compensated")
        self.refresh_button = QPushButton("刷新")
        self.refresh_button.setObjectName("secondaryButton")
        self.compare_button = QPushButton("生成对比")
        self.compare_button.setObjectName("primaryButton")
        selection_header.addWidget(self.metric_combo)
        selection_header.addWidget(self.refresh_button)
        selection_header.addWidget(self.compare_button)
        selection_layout.addLayout(selection_header)

        self.session_table = QTableWidget(0, 6)
        self.session_table.setHorizontalHeaderLabels(
            ["测试日期", "会话 / 耳机", "连接", "来源", "有效 / 总数", "平均原始"]
        )
        self.session_table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Stretch)
        self.session_table.verticalHeader().setVisible(False)
        self.session_table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.session_table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.session_table.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection)
        self.session_table.setAlternatingRowColors(True)
        self.session_table.setMinimumHeight(120)
        self.session_table.setMaximumHeight(190)
        selection_layout.addWidget(self.session_table)
        self.sessions_empty_label = QLabel("暂无历史会话。完成测试并保存后，可在这里进行多会话对比。")
        self.sessions_empty_label.setObjectName("mutedLabel")
        self.sessions_empty_label.setWordWrap(True)
        self.sessions_empty_label.hide()
        selection_layout.addWidget(self.sessions_empty_label)
        self.status_label = QLabel("正在等待会话查询")
        self.status_label.setObjectName("mutedLabel")
        self.status_label.setWordWrap(True)
        selection_layout.addWidget(self.status_label)
        root_layout.addWidget(selection_card)

        self.analysis_empty_label = QLabel("请选择至少两条历史会话，然后点击“生成对比”。")
        self.analysis_empty_label.setObjectName("mutedLabel")
        self.analysis_empty_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.analysis_empty_label.setWordWrap(True)
        root_layout.addWidget(self.analysis_empty_label, 1)

        self.analysis_scroll = QScrollArea()
        self.analysis_scroll.setWidgetResizable(True)
        self.analysis_scroll.setFrameShape(QFrame.Shape.NoFrame)
        analysis_content = QWidget()
        self.analysis_scroll.setWidget(analysis_content)
        analysis_layout = QVBoxLayout(analysis_content)
        analysis_layout.setContentsMargins(0, 0, 0, 0)
        analysis_layout.setSpacing(10)

        self.analysis_summary_label = QLabel()
        self.analysis_summary_label.setObjectName("mutedLabel")
        self.analysis_summary_label.setWordWrap(True)
        analysis_layout.addWidget(self.analysis_summary_label)
        self.simulation_banner = QLabel("包含模拟结果（非真实声学测量），不得将其解释为真实设备的物理延迟。")
        self.simulation_banner.setObjectName("simulationBanner")
        self.simulation_banner.setWordWrap(True)
        analysis_layout.addWidget(self.simulation_banner)

        self.statistics_plot = self._make_plot("平均 / 中位数 / P95", "会话", "延迟", "ms")
        self.statistics_plot.addLegend(offset=(12, 12))
        analysis_layout.addWidget(self.statistics_plot)
        self.statistics_empty_label = self._make_muted_label("没有可绘制的有效样本统计。")
        analysis_layout.addWidget(self.statistics_empty_label)
        self.statistics_table = self._make_table(
            ["编号", "会话", "有效 / 总数", "无效", "平均", "中位数（P50）", "P95", "标准差"]
        )
        self.statistics_table.setMaximumHeight(210)
        analysis_layout.addWidget(self.statistics_table)
        self.precision_label = self._make_muted_label(
            "图表与表格均由单次有效记录重算；显示两位小数仅为可读性，不代表测量精度。"
        )
        analysis_layout.addWidget(self.precision_label)

        self.trend_plot = self._make_plot("单次样本趋势", "原始单次序号", "延迟", "ms")
        self.trend_plot.addLegend(offset=(12, 12))
        analysis_layout.addWidget(self.trend_plot)
        self.trend_empty_label = self._make_muted_label("没有可绘制的有效样本趋势。")
        analysis_layout.addWidget(self.trend_empty_label)
        self.trend_note_label = self._make_muted_label(
            "折线仅连接有效样本；红叉表示具有限延迟值的无效记录，不参与统计；横轴保留原始序号。"
        )
        analysis_layout.addWidget(self.trend_note_label)

        self.distribution_plot = self._make_plot("有效样本延迟分布", "延迟", "有效样本数", "次")
        self.distribution_plot.addLegend(offset=(12, 12))
        analysis_layout.addWidget(self.distribution_plot)
        self.distribution_empty_label = self._make_muted_label("没有可绘制分布的有效样本。")
        analysis_layout.addWidget(self.distribution_empty_label)
        self.distribution_note_label = self._make_muted_label("")
        analysis_layout.addWidget(self.distribution_note_label)

        conditions_title = QLabel("测试条件与设备信息")
        conditions_title.setObjectName("sectionTitle")
        analysis_layout.addWidget(conditions_title)
        self.condition_difference_label = self._make_muted_label("")
        analysis_layout.addWidget(self.condition_difference_label)
        self.conditions_table = self._make_table([])
        self.conditions_table.setMaximumHeight(390)
        analysis_layout.addWidget(self.conditions_table)
        analysis_layout.addStretch()
        root_layout.addWidget(self.analysis_scroll, 1)
        self.analysis_scroll.hide()

        self.metric_combo.currentIndexChanged.connect(self._on_metric_changed)
        self.refresh_button.clicked.connect(self.refresh)
        self.compare_button.clicked.connect(self.compare_selected)
        self.session_table.itemSelectionChanged.connect(self._update_controls)
        self._update_controls()

    @staticmethod
    def _make_muted_label(text: str) -> QLabel:
        label = QLabel(text)
        label.setObjectName("mutedLabel")
        label.setWordWrap(True)
        return label

    @staticmethod
    def _make_table(headers: list[str]) -> QTableWidget:
        table = QTableWidget(0, len(headers))
        table.setHorizontalHeaderLabels(headers)
        table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Stretch)
        table.verticalHeader().setVisible(False)
        table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        table.setAlternatingRowColors(True)
        table.setHorizontalScrollMode(QAbstractItemView.ScrollMode.ScrollPerPixel)
        return table

    @staticmethod
    def _make_plot(title: str, bottom: str, left: str, units: str) -> pg.PlotWidget:
        plot = pg.PlotWidget(background="#141922")
        plot.setTitle(title, color="#96a2b3", size="11pt")
        plot.showGrid(x=True, y=True, alpha=0.18)
        plot.setLabel("bottom", bottom)
        plot.setLabel("left", left, units=units)
        plot.setMinimumHeight(230)
        plot.getPlotItem().getAxis("bottom").setPen("#5f6a79")
        plot.getPlotItem().getAxis("left").setPen("#5f6a79")
        return plot

    def _connect_controller(self) -> None:
        self._controller.comparison_session_list_loaded.connect(self._on_sessions_loaded)
        self._controller.comparison_sessions_loaded.connect(self._on_comparisons_loaded)
        self._controller.operation_started.connect(self._on_operation_started)
        self._controller.operation_finished.connect(self._on_operation_finished)
        self._controller.operation_failed.connect(self._on_operation_failed)

    def refresh(self) -> None:
        self._controller.load_comparison_session_list()

    def selected_session_ids(self) -> list[int]:
        rows = self.session_table.selectionModel().selectedRows()
        return [
            int(self.session_table.item(index.row(), 0).data(_SESSION_ID_ROLE))
            for index in rows
            if self.session_table.item(index.row(), 0) is not None
        ]

    def compare_selected(self) -> None:
        ids = self.selected_session_ids()
        if len(ids) < 2:
            self.status_label.setText("请至少选择两条会话再生成对比。")
            return
        self._controller.load_comparison_sessions(ids)

    def _on_sessions_loaded(self, sessions: list) -> None:
        selected_ids = set(self.selected_session_ids())
        self._sessions = list(sessions)
        selection_model = self.session_table.selectionModel()
        signals_blocked = selection_model.blockSignals(True)
        try:
            self.session_table.clearSelection()
            self.session_table.setRowCount(len(self._sessions))
            restored_selection = QItemSelection()
            for row, session in enumerate(self._sessions):
                session_label = session.session_name
                if session.headphone_name and session.headphone_name not in session_label:
                    session_label = f"{session_label} / {session.headphone_name}"
                values = (
                    self._format_date(session.created_at),
                    session_label,
                    _CONNECTION_LABELS.get(session.connection_type, session.connection_type),
                    "模拟" if session.is_simulation else "真实",
                    f"{session.valid_count} / {session.completed_count}",
                    self._format_ms(session.mean_latency_ms),
                )
                for column, value in enumerate(values):
                    item = QTableWidgetItem(value)
                    if column == 0:
                        item.setData(_SESSION_ID_ROLE, session.id)
                    self.session_table.setItem(row, column, item)
                if session.id in selected_ids:
                    restored_selection.select(
                        self.session_table.model().index(row, 0),
                        self.session_table.model().index(row, self.session_table.columnCount() - 1),
                    )
            selection_model.select(
                restored_selection,
                QItemSelectionModel.SelectionFlag.ClearAndSelect
                | QItemSelectionModel.SelectionFlag.Rows,
            )
        finally:
            selection_model.blockSignals(signals_blocked)
        self.sessions_empty_label.setText("暂无历史会话。完成测试并保存后，可在这里进行多会话对比。")
        self.sessions_empty_label.setVisible(not self._sessions)
        self.status_label.setText(f"已加载 {len(self._sessions)} 条会话；请选择至少两条生成对比。")
        available_ids = {session.id for session in self._sessions}
        if self._comparisons and any(
            comparison.session.id not in available_ids for comparison in self._comparisons
        ):
            self._clear_analysis("部分会话已不可用，请重新选择并生成对比。")
        self._update_controls()

    def _on_comparisons_loaded(self, comparisons: tuple) -> None:
        self._comparisons = tuple(comparisons)
        if not self._comparisons:
            self._clear_analysis("所选会话未返回可对比数据，请刷新后重试。")
            return
        self._render_analysis()
        self.analysis_empty_label.hide()
        self.analysis_scroll.show()
        self.status_label.setText(
            f"已生成 {len(self._comparisons)} 条会话的对比；统计来自数据库单次明细重算。"
        )
        self._update_controls()

    def _on_metric_changed(self) -> None:
        if self._comparisons:
            self._render_analysis()

    def _render_analysis(self) -> None:
        metric_name = "原始延迟" if self._metric_key() == "raw" else "补偿后延迟"
        self.analysis_summary_label.setText(
            f"当前口径：{metric_name}。统计和分布仅使用有效样本；无效原始记录仍保留在数据库和历史详情中。"
            "补偿值来自各会话保存时的快照，请结合下方校准来源与测试条件差异解读。"
        )
        self.simulation_banner.setVisible(
            any(comparison.session.is_simulation for comparison in self._comparisons)
        )
        self._render_statistics(metric_name)
        self._render_trend(metric_name)
        self._render_distribution(metric_name)
        self._render_conditions()

    def _render_statistics(self, metric_name: str) -> None:
        self._reset_plot(self.statistics_plot)
        self.statistics_plot.setTitle(
            f"平均 / 中位数 / P95 · {metric_name}", color="#96a2b3", size="11pt"
        )
        self.statistics_bars = {}
        statistics = [self._statistics(comparison) for comparison in self._comparisons]
        specifications = (
            ("mean", "平均值", "mean_latency_ms", "#45b8c7"),
            ("median", "中位数（P50）", "median_latency_ms", "#d7a64a"),
            ("p95", "P95", "p95_latency_ms", "#8ba8e8"),
        )
        all_values = []
        for metric_index, (key, label, attribute, color) in enumerate(specifications):
            positions = []
            heights = []
            for session_index, values in enumerate(statistics):
                value = getattr(values, attribute)
                if value is None:
                    continue
                positions.append(session_index + (metric_index - 1) * 0.24)
                heights.append(value)
                all_values.append(value)
            if not heights:
                continue
            bar = pg.BarGraphItem(
                x=np.asarray(positions, dtype=np.float64),
                height=np.asarray(heights, dtype=np.float64),
                width=0.20,
                brush=pg.mkBrush(color),
                pen=None,
                name=label,
            )
            self.statistics_plot.addItem(bar)
            self.statistics_bars[key] = bar

        count = len(self._comparisons)
        ticks = [[(index, f"S{index + 1}") for index in range(count)]]
        self.statistics_plot.getPlotItem().getAxis("bottom").setTicks(ticks)
        self.statistics_plot.setXRange(-0.6, count - 0.4, padding=0)
        self.statistics_plot.setYRange(*self._include_zero_range(all_values), padding=0)
        self.statistics_empty_label.setVisible(not all_values)

        self.statistics_table.setRowCount(count)
        for row, comparison in enumerate(self._comparisons):
            values = self._statistics(comparison)
            cells = (
                f"S{row + 1}",
                comparison.session.session_name,
                f"{comparison.statistics.valid_count} / {comparison.statistics.total_count}",
                str(comparison.statistics.invalid_count),
                self._format_ms(values.mean_latency_ms),
                self._format_ms(values.median_latency_ms),
                self._format_ms(values.p95_latency_ms),
                self._format_ms(values.std_latency_ms),
            )
            for column, value in enumerate(cells):
                self.statistics_table.setItem(row, column, QTableWidgetItem(value))

    def _render_trend(self, metric_name: str) -> None:
        self._reset_plot(self.trend_plot)
        self.trend_plot.setTitle(f"单次样本趋势 · {metric_name}", color="#96a2b3", size="11pt")
        self.trend_curves = {}
        self.invalid_trend_items = []
        all_values = []
        all_sequences = []
        valid_total = 0
        invalid_total = 0
        invalid_legend_added = False
        for index, comparison in enumerate(self._comparisons):
            color = _SESSION_COLORS[index % len(_SESSION_COLORS)]
            valid_points = [
                (result.sequence, self._result_latency(result))
                for result in comparison.session.results
                if result.is_valid
            ]
            if valid_points:
                x = np.asarray([point[0] for point in valid_points], dtype=np.float64)
                y = np.asarray([point[1] for point in valid_points], dtype=np.float64)
                curve = self.trend_plot.plot(
                    x,
                    y,
                    pen=pg.mkPen(color, width=2),
                    symbol="o",
                    symbolSize=6,
                    symbolBrush=pg.mkBrush(color),
                    symbolPen=pg.mkPen(color),
                    name=f"S{index + 1}",
                )
                self.trend_curves[comparison.session.id] = curve
                all_values.extend(y.tolist())
                all_sequences.extend(x.tolist())
                valid_total += len(y)

            invalid_points = []
            for result in comparison.session.results:
                if result.is_valid:
                    continue
                invalid_total += 1
                value = self._result_latency(result)
                if value is not None and math.isfinite(value):
                    invalid_points.append((result.sequence, value))
            if invalid_points:
                scatter = pg.PlotDataItem(
                    x=np.asarray([point[0] for point in invalid_points], dtype=np.float64),
                    y=np.asarray([point[1] for point in invalid_points], dtype=np.float64),
                    pen=None,
                    symbol="x",
                    symbolSize=10,
                    symbolPen=pg.mkPen("#e66f75", width=1.6),
                    name=None if invalid_legend_added else "无效样本",
                )
                self.trend_plot.addItem(scatter)
                self.invalid_trend_items.append(scatter)
                invalid_legend_added = True
                all_values.extend(point[1] for point in invalid_points)
                all_sequences.extend(point[0] for point in invalid_points)

        self.trend_empty_label.setVisible(valid_total == 0)
        self.trend_note_label.setText(
            f"有效样本 {valid_total} 个，无效记录 {invalid_total} 条。折线仅连接有效样本；"
            "红叉表示具有限延迟值的无效记录，不参与统计；横轴保留原始序号。"
        )
        self._set_point_range(self.trend_plot, all_sequences, all_values)

    def _render_distribution(self, metric_name: str) -> None:
        self._reset_plot(self.distribution_plot)
        self.distribution_plot.setTitle(
            f"有效样本延迟分布 · {metric_name}", color="#96a2b3", size="11pt"
        )
        self.distribution_plot.setLabel("bottom", metric_name, units="ms")
        self.distribution_bars = {}
        values_by_session = [self._valid_values(comparison) for comparison in self._comparisons]
        nonempty_values = [values for values in values_by_session if values.size]
        self.distribution_empty_label.setVisible(not nonempty_values)
        if not nonempty_values:
            self.distribution_bin_edges = np.asarray([], dtype=np.float64)
            self.distribution_note_label.setText("无有效样本，未绘制分布；空值不会按 0 ms 计入。")
            self.distribution_plot.setXRange(0, 1, padding=0)
            self.distribution_plot.setYRange(0, 1, padding=0)
            return

        all_values = np.concatenate(nonempty_values)
        bin_count = min(20, max(1, int(np.ceil(np.sqrt(all_values.size)))))
        edges = np.histogram_bin_edges(all_values, bins=bin_count)
        self.distribution_bin_edges = edges
        centers = (edges[:-1] + edges[1:]) / 2
        minimum_bin_width = float(np.min(np.diff(edges)))
        group_width = minimum_bin_width * 0.8
        bar_width = group_width / len(self._comparisons)
        maximum_count = 0
        for index, (comparison, values) in enumerate(
            zip(self._comparisons, values_by_session, strict=True)
        ):
            if not values.size:
                continue
            counts, _ = np.histogram(values, bins=edges)
            offset = (index - (len(self._comparisons) - 1) / 2) * bar_width
            color = pg.mkColor(_SESSION_COLORS[index % len(_SESSION_COLORS)])
            color.setAlpha(170)
            bar = pg.BarGraphItem(
                x=centers + offset,
                height=counts.astype(np.float64),
                width=bar_width,
                brush=pg.mkBrush(color),
                pen=None,
                name=f"S{index + 1}",
            )
            self.distribution_plot.addItem(bar)
            self.distribution_bars[comparison.session.id] = bar
            maximum_count = max(maximum_count, int(np.max(counts)))
        self.distribution_plot.setXRange(float(edges[0]), float(edges[-1]), padding=0.02)
        self.distribution_plot.setYRange(0, max(1, maximum_count + 1), padding=0)
        self.distribution_note_label.setText(
            f"所有会话共用 {len(edges) - 1} 个分组，边界 {edges[0]:.2f}–{edges[-1]:.2f} ms；"
            "柱高为有效样本数，无效记录不进入分布。"
        )

    def _render_conditions(self) -> None:
        measurement_configs = [self._config_dict(c.session.measurement_config_json) for c in self._comparisons]
        audio_configs = [self._config_dict(c.session.audio_config_json) for c in self._comparisons]
        rows = self._condition_rows(measurement_configs, audio_configs)
        self.conditions_table.setColumnCount(len(self._comparisons) + 1)
        self.conditions_table.setHorizontalHeaderLabels(
            ["条件"] + [f"S{index + 1}" for index in range(len(self._comparisons))]
        )
        self.conditions_table.setRowCount(len(rows))
        differences = []
        for row, (label, values, compare_difference) in enumerate(rows):
            differs = compare_difference and len(set(values)) > 1
            if differs:
                differences.append(label)
            label_item = QTableWidgetItem(label)
            if differs:
                label_item.setBackground(_DIFFERENCE_BRUSH)
            self.conditions_table.setItem(row, 0, label_item)
            for column, value in enumerate(values, start=1):
                item = QTableWidgetItem(value)
                item.setToolTip(value)
                if differs:
                    item.setBackground(_DIFFERENCE_BRUSH)
                self.conditions_table.setItem(row, column, item)
        self.conditions_table.horizontalHeader().setSectionResizeMode(
            0, QHeaderView.ResizeMode.ResizeToContents
        )
        if differences:
            self.condition_difference_label.setText("测试条件差异（已高亮）：" + "、".join(differences))
        else:
            self.condition_difference_label.setText("所选会话的设备与测量条件一致；会话名称和时间不参与差异判断。")

    def _condition_rows(self, measurements: list[dict], audio_configs: list[dict]) -> list[tuple]:
        sessions = [comparison.session for comparison in self._comparisons]
        rows = [
            ("会话名称", [session.session_name for session in sessions], False),
            ("耳机名称", [session.headphone_name or "—" for session in sessions], True),
            (
                "连接方式",
                [_CONNECTION_LABELS.get(s.connection_type, s.connection_type) for s in sessions],
                True,
            ),
            ("输出设备", [self._device_text(s.output_device, s.output_device_id) for s in sessions], True),
            ("输入设备", [self._device_text(s.input_device, s.input_device_id) for s in sessions], True),
            (
                "采样率（输出 / 输入）",
                [f"{s.output_sample_rate} / {s.input_sample_rate} Hz" for s in sessions],
                True,
            ),
            ("音频模式 / 后端", [f"{s.audio_mode or '—'} / {s.backend or '—'}" for s in sessions], True),
            (
                "声道 / 采集缓冲",
                [
                    f"输出 {a.get('output_channels', '—')} 声道；输入 {a.get('input_channels', '—')} 声道；"
                    f"缓冲 {a.get('capture_buffer_seconds', '—')} s"
                    for a in audio_configs
                ],
                True,
            ),
            (
                "Chirp 激励",
                [
                    f"{m.get('stimulus_start_frequency_hz', '—')}–{m.get('stimulus_end_frequency_hz', '—')} Hz；"
                    f"{m.get('stimulus_duration_ms', '—')} ms；{m.get('stimulus_window', '—')}；"
                    f"幅度 {m.get('stimulus_amplitude', '—')}"
                    for m in measurements
                ],
                True,
            ),
            (
                "有效判定阈值",
                [
                    f"延迟 {m.get('min_latency_ms', '—')}–{m.get('max_latency_ms', '—')} ms；"
                    f"SNR ≥ {m.get('min_snr_db', '—')} dB；相关 ≥ {m.get('min_correlation', '—')}"
                    for m in measurements
                ],
                True,
            ),
            (
                "采集与测量间隔",
                [
                    f"提前 {m.get('stimulus_lead_ms', '—')} ms；尾长 {m.get('capture_tail_ms', '—')} ms；"
                    f"间隔 {m.get('inter_measurement_delay_ms', '—')} ms"
                    for m in measurements
                ],
                True,
            ),
            ("补偿", [self._compensation_text(session) for session in sessions], True),
            ("校准来源", [self._calibration_text(session) for session in sessions], True),
            ("预热", [f"{s.warmup_seconds:g} s" for s in sessions], True),
            (
                "背景音",
                [f"{s.background_kind or '—'}；幅度 {s.background_amplitude:g}" for s in sessions],
                True,
            ),
            ("计划 / 完成次数", [f"{s.test_count} / {s.completed_count}" for s in sessions], True),
            ("最少有效样本要求", [str(s.minimum_valid_count) for s in sessions], True),
            ("结束状态", [s.terminal_state for s in sessions], True),
            (
                "数据来源",
                ["模拟（非真实声学测量）" if s.is_simulation else "真实音频链路" for s in sessions],
                True,
            ),
            (
                "模拟参数",
                [
                    f"延迟 {m.get('simulation_delay_ms', '—')} ms；SNR {m.get('simulation_snr_db', '—')} dB"
                    if s.is_simulation
                    else "—"
                    for s, m in zip(sessions, measurements, strict=True)
                ],
                True,
            ),
            ("开始时间", [self._format_date(s.started_at) for s in sessions], False),
        ]
        return rows

    def _on_operation_started(self, operation: str) -> None:
        if operation not in {"load_comparison_session_list", "load_comparison_sessions"}:
            return
        self._pending[operation] = self._pending.get(operation, 0) + 1
        if operation == "load_comparison_session_list":
            self.status_label.setText("正在加载历史会话…")
        else:
            self.status_label.setText("正在读取单次明细并重算对比统计…")
        self._update_controls()

    def _on_operation_finished(self, operation: str) -> None:
        if operation not in {"load_comparison_session_list", "load_comparison_sessions"}:
            return
        self._pending[operation] = max(0, self._pending.get(operation, 0) - 1)
        self._update_controls()

    def _on_operation_failed(self, operation: str, message: str) -> None:
        if operation == "load_comparison_session_list":
            self.status_label.setText(f"会话列表加载失败：{message}")
            self.sessions_empty_label.setText("会话列表加载失败，请点击“刷新”重试。")
            self.sessions_empty_label.show()
        elif operation == "load_comparison_sessions":
            self.status_label.setText(f"对比数据加载失败：{message}")
            self._clear_analysis(f"对比数据加载失败：{message}\n请检查会话是否仍存在，然后重试。")
        self._update_controls()

    def _update_controls(self) -> None:
        list_busy = bool(self._pending.get("load_comparison_session_list"))
        comparison_busy = bool(self._pending.get("load_comparison_sessions"))
        self.refresh_button.setEnabled(not list_busy)
        self.compare_button.setEnabled(len(self.selected_session_ids()) >= 2 and not comparison_busy)
        self.metric_combo.setEnabled(bool(self._comparisons) and not comparison_busy)

    def _clear_analysis(self, message: str) -> None:
        self._comparisons = ()
        self.statistics_bars = {}
        self.trend_curves = {}
        self.invalid_trend_items = []
        self.distribution_bars = {}
        self.distribution_bin_edges = np.asarray([], dtype=np.float64)
        self.statistics_table.setRowCount(0)
        self.conditions_table.setRowCount(0)
        for plot in (self.statistics_plot, self.trend_plot, self.distribution_plot):
            self._reset_plot(plot)
        self.analysis_scroll.hide()
        self.analysis_empty_label.setText(message)
        self.analysis_empty_label.show()
        self._update_controls()

    def _metric_key(self) -> str:
        return str(self.metric_combo.currentData())

    def _statistics(self, comparison: SessionComparison):
        return comparison.statistics.raw if self._metric_key() == "raw" else comparison.statistics.compensated

    def _result_latency(self, result) -> float | None:
        return result.raw_latency_ms if self._metric_key() == "raw" else result.compensated_latency_ms

    def _valid_values(self, comparison: SessionComparison) -> np.ndarray:
        return np.asarray(
            [
                self._result_latency(result)
                for result in comparison.session.results
                if result.is_valid
            ],
            dtype=np.float64,
        )

    @staticmethod
    def _reset_plot(plot: pg.PlotWidget) -> None:
        legend = plot.getPlotItem().legend
        if legend is not None:
            legend.clear()
        plot.clear()

    @staticmethod
    def _include_zero_range(values: list[float]) -> tuple[float, float]:
        if not values:
            return 0.0, 1.0
        lower = min(0.0, min(values))
        upper = max(0.0, max(values))
        span = upper - lower
        if span == 0:
            return lower - 1.0, upper + 1.0
        return lower - (span * 0.08 if lower < 0 else 0.0), upper + span * 0.08

    @staticmethod
    def _set_point_range(plot: pg.PlotWidget, sequences: list[float], values: list[float]) -> None:
        if not sequences or not values:
            plot.setXRange(0, 1, padding=0)
            plot.setYRange(0, 1, padding=0)
            return
        x_min, x_max = min(sequences), max(sequences)
        y_min, y_max = min(values), max(values)
        x_padding = max(0.5, (x_max - x_min) * 0.05)
        y_padding = max(1.0, (y_max - y_min) * 0.10)
        plot.setXRange(x_min - x_padding, x_max + x_padding, padding=0)
        plot.setYRange(y_min - y_padding, y_max + y_padding, padding=0)

    @staticmethod
    def _device_text(name: str, device_id: int | str | None) -> str:
        return f"{name or '—'}（ID：{device_id if device_id is not None else '—'}）"

    @staticmethod
    def _compensation_text(session) -> str:
        if session.calibration_profile is not None:
            return f"{session.compensation_ms:.2f} ms（校准配置）"
        if session.compensation_ms == 0:
            return "0.00 ms（未补偿）"
        return f"{session.compensation_ms:.2f} ms（手动）"

    @staticmethod
    def _calibration_text(session) -> str:
        profile = session.calibration_profile
        if profile is None:
            return "无校准配置" if session.compensation_ms == 0 else "手动补偿，无校准配置"
        return f"{profile.name}；{profile.calibration_method}；{profile.calibration_date}"

    @staticmethod
    def _config_dict(raw: str) -> dict:
        try:
            value = json.loads(raw or "{}")
        except (TypeError, json.JSONDecodeError):
            return {}
        return value if isinstance(value, dict) else {}

    @staticmethod
    def _format_ms(value: float | None) -> str:
        return "—" if value is None else f"{value:.2f} ms"

    @staticmethod
    def _format_date(value: str) -> str:
        try:
            parsed = datetime.fromisoformat(value)
        except (TypeError, ValueError):
            return str(value)
        return parsed.astimezone().strftime("%Y-%m-%d %H:%M:%S")
