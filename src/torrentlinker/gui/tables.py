"""Shared table presentation and numeric ordering for the desktop workflows."""

from PySide6.QtCore import QEvent, QModelIndex, QObject, QPersistentModelIndex, Qt
from PySide6.QtGui import QBrush, QColor, QPalette
from PySide6.QtWidgets import (
    QAbstractItemView,
    QHeaderView,
    QLabel,
    QStyledItemDelegate,
    QStyleOptionViewItem,
    QTableView,
    QTableWidget,
    QTableWidgetItem,
    QTreeView,
)
from shiboken6 import isValid


class NumberItem(QTableWidgetItem):
    """Display a formatted quantity while ordering by its underlying number."""

    def __init__(self, text: str, value: float) -> None:
        super().__init__(text)
        self.setData(Qt.ItemDataRole.UserRole, value)
        self.setTextAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)

    def __lt__(self, other: QTableWidgetItem) -> bool:
        mine, theirs = self.data(Qt.ItemDataRole.UserRole), other.data(Qt.ItemDataRole.UserRole)
        if isinstance(mine, int | float) and isinstance(theirs, int | float):
            return mine < theirs
        return super().__lt__(other)


class StatusDelegate(QStyledItemDelegate):
    """Keep status text readable when the system switches to a dark palette."""

    def initStyleOption(
        self, option: QStyleOptionViewItem, index: QModelIndex | QPersistentModelIndex
    ) -> None:
        super().initStyleOption(option, index)
        color = index.data(Qt.ItemDataRole.ForegroundRole)
        if isinstance(color, QBrush):
            color = color.color()
        if isinstance(color, QColor) and option.palette.base().color().lightness() < 128:
            readable = {
                "#2e7d32": "#8dd4a5",
                "#ef6c00": "#edbf73",
                "#c62828": "#ff9c9c",
            }.get(color.name())
            if readable:
                option.palette.setColor(QPalette.ColorRole.Text, QColor(readable))


class TreeTable(QTreeView):
    """A tree styled and driven like the app's tables: packs expand to their episodes."""

    def selectRow(self, row: int) -> None:  # noqa: N802 - mirrors QTableView
        self.setCurrentIndex(self.model().index(row, 0))

    def horizontalHeader(self) -> QHeaderView:  # noqa: N802 - mirrors QTableView
        return self.header()


def style_tree(tree: TreeTable) -> None:
    tree.setItemDelegate(StatusDelegate(tree))
    tree.setAlternatingRowColors(True)
    tree.setWordWrap(False)
    tree.setUniformRowHeights(True)
    tree.setAllColumnsShowFocus(True)
    header = tree.header()
    header.setSectionResizeMode(QHeaderView.ResizeMode.Interactive)
    header.setStretchLastSection(False)
    header.setDefaultSectionSize(130)
    header.setMinimumSectionSize(40)
    if header.count():
        header.resizeSection(0, 380)
    header.setDefaultAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)


def style_table(table: QTableView) -> None:
    table.setItemDelegate(StatusDelegate(table))
    table.setAlternatingRowColors(True)
    table.setShowGrid(False)
    table.setWordWrap(False)
    table.verticalHeader().hide()
    table.verticalHeader().setDefaultSectionSize(34)
    header = table.horizontalHeader()
    header.setSectionResizeMode(QHeaderView.ResizeMode.Interactive)
    header.setStretchLastSection(False)
    header.setDefaultSectionSize(130)
    header.setMinimumSectionSize(40)
    if header.count():
        header.resizeSection(0, 280)
    table.horizontalHeader().setDefaultAlignment(
        Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter
    )
    table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
    table.setVerticalScrollMode(QAbstractItemView.ScrollMode.ScrollPerPixel)
    table.setHorizontalScrollMode(QAbstractItemView.ScrollMode.ScrollPerPixel)


def sortable_table(table: QTableWidget) -> None:
    """Keep the workflow's initial order until the user clicks a column header."""
    style_table(table)
    table.horizontalHeader().setSortIndicator(-1, Qt.SortOrder.AscendingOrder)
    table.setSortingEnabled(True)


def filter_rows(table: QTableWidget, text: str) -> None:
    query = text.strip().casefold()
    for row in range(table.rowCount()):
        item = table.item(row, 0)
        table.setRowHidden(row, bool(query and item and query not in item.text().casefold()))


class EmptyState(QObject):
    """An accessible hint inside an empty table, without consuming space in filled lists."""

    def __init__(self, table: QAbstractItemView, text: str) -> None:
        super().__init__(table)
        self.table = table
        self.label = QLabel(text, table.viewport())
        self.label.setProperty("role", "muted")
        self.label.setWordWrap(True)
        self.label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.label.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
        table.viewport().installEventFilter(self)
        model = table.model()
        if model is not None:
            model.rowsInserted.connect(self.update)
            model.rowsRemoved.connect(self.update)
            model.modelReset.connect(self.update)
            model.layoutChanged.connect(self.update)
        self.update()

    def update(self) -> None:
        if not isValid(self.table) or not isValid(self.label):
            return
        self.label.setGeometry(self.table.viewport().rect().adjusted(20, 12, -20, -12))
        model = self.table.model()
        self.label.setVisible(model is not None and model.rowCount() == 0)

    def eventFilter(self, watched: QObject, event: QEvent) -> bool:
        if event.type() in (QEvent.Type.Resize, QEvent.Type.Show):
            self.update()
        return super().eventFilter(watched, event)
