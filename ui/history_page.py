from PySide6.QtWidgets import (
    QAbstractItemView,
    QFrame,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QPushButton,
    QTableWidget,
    QVBoxLayout,
    QWidget,
)


class HistoryPage(QWidget):
    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        root_layout = QVBoxLayout(self)
        root_layout.setContentsMargins(30, 26, 30, 24)
        root_layout.setSpacing(16)

        title = QLabel("历史记录")
        title.setObjectName("pageTitle")
        subtitle = QLabel("查看已保存的测试会话、单次测量明细与导出记录")
        subtitle.setObjectName("pageSubtitle")
        root_layout.addWidget(title)
        root_layout.addWidget(subtitle)

        toolbar = QHBoxLayout()
        self.search_input = QLineEdit()
        self.search_input.setPlaceholderText("历史搜索将在数据库模块接入后启用")
        self.search_input.setEnabled(False)
        self.export_button = QPushButton("导出")
        self.export_button.setEnabled(False)
        toolbar.addWidget(self.search_input, 1)
        toolbar.addWidget(self.export_button)
        root_layout.addLayout(toolbar)

        table_card = QFrame()
        table_card.setObjectName("card")
        table_layout = QVBoxLayout(table_card)
        table_layout.setContentsMargins(16, 16, 16, 16)
        self.table = QTableWidget(0, 6)
        self.table.setHorizontalHeaderLabels(["测试日期", "耳机", "连接方式", "平均延迟", "P95", "有效样本"])
        self.table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Stretch)
        self.table.verticalHeader().setVisible(False)
        self.table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        table_layout.addWidget(self.table)
        empty_label = QLabel("暂无历史会话。保存功能将在 P5 阶段接入。")
        empty_label.setObjectName("mutedLabel")
        table_layout.addWidget(empty_label)
        root_layout.addWidget(table_card, 1)
