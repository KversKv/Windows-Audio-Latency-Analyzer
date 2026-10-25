from PySide6.QtCore import Qt
from PySide6.QtWidgets import QFrame, QLabel, QVBoxLayout, QWidget


class ComparisonPage(QWidget):
    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        root_layout = QVBoxLayout(self)
        root_layout.setContentsMargins(30, 26, 30, 24)
        root_layout.setSpacing(16)

        title = QLabel("对比分析")
        title.setObjectName("pageTitle")
        subtitle = QLabel("对比多个测试会话的延迟水平、波动与测量质量")
        subtitle.setObjectName("pageSubtitle")
        root_layout.addWidget(title)
        root_layout.addWidget(subtitle)

        placeholder = QFrame()
        placeholder.setObjectName("placeholderPanel")
        placeholder_layout = QVBoxLayout(placeholder)
        placeholder_layout.setContentsMargins(28, 28, 28, 28)
        heading = QLabel("多会话对比")
        heading.setObjectName("sectionTitle")
        heading.setAlignment(Qt.AlignmentFlag.AlignCenter)
        description = QLabel(
            "选择已保存会话后，将在这里显示平均值、中位数、P95、延迟分布和单次样本趋势。\n"
            "图表功能将在 P6 阶段接入。"
        )
        description.setObjectName("mutedLabel")
        description.setAlignment(Qt.AlignmentFlag.AlignCenter)
        description.setWordWrap(True)
        placeholder_layout.addStretch()
        placeholder_layout.addWidget(heading)
        placeholder_layout.addWidget(description)
        placeholder_layout.addStretch()
        root_layout.addWidget(placeholder, 1)
