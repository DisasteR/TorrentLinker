"""Live Torrent Client workspace, with a tracker/category tree and explicit batch actions."""

from __future__ import annotations

from collections import Counter
from collections.abc import Callable
from typing import Any

from PySide6.QtCore import (
    QAbstractTableModel,
    QItemSelectionModel,
    QModelIndex,
    QPersistentModelIndex,
    QPoint,
    QSettings,
    QSignalBlocker,
    QSortFilterProxyModel,
    Qt,
    QTimer,
)
from PySide6.QtGui import QHideEvent, QShowEvent
from PySide6.QtWidgets import (
    QAbstractButton,
    QAbstractItemView,
    QApplication,
    QComboBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QMenu,
    QMessageBox,
    QPlainTextEdit,
    QPushButton,
    QSplitter,
    QStyle,
    QStyledItemDelegate,
    QStyleOptionProgressBar,
    QTableView,
    QTableWidget,
    QTableWidgetItem,
    QTabWidget,
    QToolButton,
    QTreeWidget,
    QTreeWidgetItem,
    QVBoxLayout,
    QWidget,
)

from torrentlinker.domain import Torrent, TorrentFile
from torrentlinker.gui.controls import icon_button
from torrentlinker.gui.library_model import human_size
from torrentlinker.gui.tables import EmptyState, NumberItem, sortable_table, style_table
from torrentlinker.gui.tasks import run_in_background, run_with_progress
from torrentlinker.qbit import Qbit
from torrentlinker.seeding import Seeding
from torrentlinker.torrent_management import (
    ActionResult,
    category_labels,
    manage,
    peer_count_order,
    peer_count_text,
    safe_error,
    status,
    torrent_categories,
    tracker_groups,
    tracker_hosts,
)

SORT = Qt.ItemDataRole.UserRole + 1
FILTER = Qt.ItemDataRole.UserRole + 2


class ProgressBarDelegate(QStyledItemDelegate):
    """Draws the Progress column as a bar, as qBittorrent does."""

    def paint(self, painter: Any, option: Any, index: QModelIndex | QPersistentModelIndex) -> None:
        value = index.data(SORT)
        if not isinstance(value, (int, float)):
            super().paint(painter, option, index)
            return
        bar = QStyleOptionProgressBar()
        bar.rect = option.rect.adjusted(2, 2, -2, -2)
        bar.minimum, bar.maximum = 0, 1000
        bar.progress = int(max(0.0, min(1.0, value)) * 1000)
        bar.text = f"{value * 100:.1f} %"
        bar.textVisible = True
        # Without the horizontal flag Fusion fills the bar from the bottom up.
        bar.state = option.state | QStyle.StateFlag.State_Horizontal
        QApplication.style().drawControl(QStyle.ControlElement.CE_ProgressBar, bar, painter)


class TorrentModel(QAbstractTableModel):
    def __init__(self) -> None:
        super().__init__()
        self.rows: list[Torrent] = []
        self.categories: dict[str, str] = {}
        self.estimates: dict[str, tuple[float | None, float | None]] = {}
        self.headers = [
            self.tr("Torrent"),
            self.tr("Status"),
            self.tr("Progress"),
            self.tr("Size"),
            self.tr("Download/s"),
            self.tr("Upload/s"),
            self.tr("Ratio"),
            self.tr("ETA"),
            self.tr("Category"),
            self.tr("Trackers"),
            self.tr("≈ Zen/day"),
            self.tr("≈ Note"),
            self.tr("Seeders"),
            self.tr("Peers"),
        ]
        self.states = {
            "downloading": self.tr("Downloading"),
            "seeding": self.tr("Seeding"),
            "paused": self.tr("Paused"),
            "checking": self.tr("Checking"),
            "error": self.tr("Error"),
            "other": self.tr("Other"),
        }

    def rowCount(self, parent: QModelIndex | QPersistentModelIndex = QModelIndex()) -> int:
        return 0 if parent.isValid() else len(self.rows)

    def columnCount(self, parent: QModelIndex | QPersistentModelIndex = QModelIndex()) -> int:
        return len(self.headers)

    def headerData(self, section: int, orientation: Qt.Orientation, role: int = 0) -> Any:
        if orientation == Qt.Orientation.Horizontal and role == Qt.ItemDataRole.ToolTipRole:
            if section == 12:
                return self.tr(
                    "Connected seeders (swarm total). ? = unknown. Sorted by total when known."
                )
            if section == 13:
                return self.tr(
                    "Connected peers (swarm total). ? = unknown. Sorted by total when known."
                )
        if orientation == Qt.Orientation.Horizontal and role == Qt.ItemDataRole.DisplayRole:
            return self.headers[section]
        return None

    def data(
        self, index: QModelIndex | QPersistentModelIndex, role: int = Qt.ItemDataRole.DisplayRole
    ) -> Any:
        if not index.isValid():
            return None
        t, col = self.rows[index.row()], index.column()
        if role == Qt.ItemDataRole.UserRole:
            return t.hash
        if role == Qt.ItemDataRole.ToolTipRole:
            if col == 12 and t.swarm_seeders >= 0:
                return self.tr("Connected seeders: {0}\nTracker-reported seeders: {1}").format(
                    t.connected_seeds, t.swarm_seeders
                )
            if col in (12, 13):
                return self.headerData(col, Qt.Orientation.Horizontal, role)
            return t.name if col == 0 else t.state if col == 1 else None
        if role not in (Qt.ItemDataRole.DisplayRole, SORT):
            return None
        if col == 0:
            return t.name
        if col == 1:
            return self.states[status(t)]
        if col in (12, 13):
            connected, total = (
                (t.connected_seeds, t.swarm_seeders)
                if col == 12
                else (t.connected_peers, t.swarm_peers)
            )
            return (
                peer_count_order(connected, total)
                if role == SORT
                else peer_count_text(connected, total)
            )
        zen, note = self.estimates.get(t.hash, (None, None))
        values = (
            t.name,
            self.states[status(t)],
            t.progress,
            t.total_size,
            t.download_rate,
            t.upload_rate,
            t.ratio,
            t.eta,
            self.categories.get(t.hash, t.category),
            ", ".join(h for h in tracker_hosts(t) if h) if col == 9 else "",
            zen,
            note,
            t.connected_seeds,
            t.connected_peers,
        )
        value = values[col]
        if role == SORT:
            return value if value is not None else -1.0
        if col == 2:
            return f"{t.progress:.1%}"
        if col in (3, 4, 5):
            return human_size(int(value))  # type: ignore[arg-type]
        if col in (6, 10, 11):
            return f"{value:.2f}" if isinstance(value, int | float) and value >= 0 else "–"
        if col == 7:
            if t.eta < 0 or t.eta >= 8_640_000 or t.progress >= 1:
                return "–"
            return self.tr("{0} h {1} min").format(t.eta // 3600, (t.eta % 3600) // 60)
        return value

    def update(self, rows: list[Torrent]) -> None:
        # Keep indexes and selections stable across the normal polling path.
        incoming = {t.hash: t for t in rows}
        if len(incoming) == len(self.rows) and all(t.hash in incoming for t in self.rows):
            changed: dict[int, list[int]] = {}
            for row, old in enumerate(self.rows):
                new = incoming[old.hash]
                if old == new:
                    continue
                before = (
                    old.name,
                    (old.state, old.tags, status(old)),
                    old.progress,
                    old.total_size,
                    old.download_rate,
                    old.upload_rate,
                    old.ratio,
                    old.eta,
                    old.category,
                    (old.tracker, old.trackers),
                    None,
                    None,
                    (old.connected_seeds, old.swarm_seeders),
                    (old.connected_peers, old.swarm_peers),
                )
                after = (
                    new.name,
                    (new.state, new.tags, status(new)),
                    new.progress,
                    new.total_size,
                    new.download_rate,
                    new.upload_rate,
                    new.ratio,
                    new.eta,
                    new.category,
                    (new.tracker, new.trackers),
                    None,
                    None,
                    (new.connected_seeds, new.swarm_seeders),
                    (new.connected_peers, new.swarm_peers),
                )
                for col, (a, b) in enumerate(zip(before, after, strict=True)):
                    if a != b:
                        changed.setdefault(col, []).append(row)
            self.rows = [incoming[t.hash] for t in self.rows]
            for col, indexes in changed.items():
                roles = [Qt.ItemDataRole.DisplayRole, Qt.ItemDataRole.ToolTipRole, SORT]
                if col in (0, 1):
                    roles.append(FILTER)
                self.dataChanged.emit(
                    self.index(indexes[0], col), self.index(indexes[-1], col), roles
                )
        else:
            self.beginResetModel()
            self.rows = list(incoming.values())
            self.endResetModel()


class TorrentFilter(QSortFilterProxyModel):
    def __init__(self, model: TorrentModel) -> None:
        super().__init__()
        self.setSourceModel(model)
        self.source = model
        self.query = ""
        self.state = ""
        self.tag = ""
        self.path: tuple[str, ...] = ()
        self.paths: dict[str, tuple[tuple[str, ...], ...]] = {}
        self.setSortRole(SORT)
        # Filtering depends on names, status and tags, not every changing rate.
        self.setFilterRole(FILTER)
        self.setFilterKeyColumn(-1)
        self.setSortCaseSensitivity(Qt.CaseSensitivity.CaseInsensitive)
        self.setDynamicSortFilter(False)
        model.dataChanged.connect(self._source_changed)

    def _source_changed(self, top, bottom, roles):
        # Qt's default dynamic filter revisits every changed row even when a
        # rate/ratio update cannot affect our name/status/tag/path predicates.
        if not roles or FILTER in roles:
            if hasattr(self, "endFilterChange"):
                self.beginFilterChange()
                self.endFilterChange(QSortFilterProxyModel.Direction.Rows)
            else:
                self.invalidateRowsFilter()
        if (not roles or SORT in roles) and top.column() <= self.sortColumn() <= bottom.column():
            self.sort(self.sortColumn(), self.sortOrder())

    def filterAcceptsRow(self, row: int, parent: QModelIndex | QPersistentModelIndex) -> bool:
        t = self.source.rows[row]
        if self.query and self.query not in t.name.casefold():
            return False
        if self.state and status(t) != self.state:
            return False
        if self.tag and self.tag not in t.tags:
            return False
        return not self.path or any(
            p[: len(self.path)] == self.path for p in self.paths.get(t.hash, ())
        )


class TorrentsTab(QWidget):
    def __init__(self, client: Callable[[], Qbit]) -> None:
        super().__init__()
        self.client = client
        self._loading = False
        self._acting = False
        self._generation = 0
        self._detail_generation = 0
        self._detail_hash = ""
        self._file_hash = ""
        self._known_categories: dict[str, tuple[str, ...]] = {}
        self._category_by_hash: dict[str, tuple[str, ...]] = {}
        self._tree_counts: dict[tuple[str, ...], int] = {}
        self._focus_hash = ""
        self.model = TorrentModel()
        self.proxy = TorrentFilter(self.model)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        bar = QHBoxLayout()
        bar.setSpacing(2)
        self.buttons: list[QAbstractButton] = []
        self.context_menu = QMenu(self)
        for glyph, text, action in [
            ("▶", self.tr("Start"), "resume"),
            ("⏸", self.tr("Pause"), "pause"),
            ("⟳", self.tr("Recheck"), "recheck"),
            ("✕", self.tr("Remove…"), "remove"),
        ]:
            button = icon_button(glyph, text)
            button.clicked.connect(lambda _=False, a=action: self.perform(a))
            self.buttons.append(button)
            bar.addWidget(button)
            if action == "remove":
                self.context_menu.addSeparator()
            menu_action = self.context_menu.addAction(text)
            menu_action.triggered.connect(lambda _=False, a=action: self.perform(a))
        delete_action = self.context_menu.addAction(self.tr("Delete torrent and files…"))
        delete_action.triggered.connect(lambda: self.perform("delete"))
        self.refresh_button = icon_button("↻", self.tr("Refresh"))
        self.refresh_button.clicked.connect(self.refresh)
        bar.addWidget(self.refresh_button)
        bar.addStretch()
        self.search = QLineEdit()
        self.search.setPlaceholderText(self.tr("Find a torrent…"))
        self.search.setClearButtonEnabled(True)
        self.search.setMaximumWidth(300)
        self.search.textChanged.connect(self._filter)
        bar.addWidget(self.search)
        self.columns = icon_button("⚙", self.tr("Columns"))
        columns = QMenu(self.columns)
        self.columns.setMenu(columns)
        self.columns.setPopupMode(QToolButton.ToolButtonPopupMode.InstantPopup)
        bar.addWidget(self.columns)
        layout.addLayout(bar)
        # The state and tag filters live in the sidebar, as in qBittorrent; the combo boxes
        # keep the choice and stay out of sight.
        self.states = QComboBox(self)
        self.states.addItem(self.tr("All states"), "")
        for key, label in self.model.states.items():
            self.states.addItem(label, key)
        self.states.currentIndexChanged.connect(self._filter)
        self.tags = QComboBox(self)
        self.tags.addItem(self.tr("All tags"), "")
        self.tags.currentIndexChanged.connect(self._filter)
        self.states.hide()
        self.tags.hide()
        split = QSplitter()
        side = QWidget()
        side_layout = QVBoxLayout(side)
        side_layout.setContentsMargins(0, 0, 0, 0)
        side_layout.setSpacing(4)
        self.status_list = self._side_list(side_layout, self.tr("STATUS"), self.states)
        self.tag_list = self._side_list(side_layout, self.tr("TAGS"), self.tags)
        heading = QLabel(self.tr("TRACKERS"))
        heading.setProperty("role", "muted")
        side_layout.addWidget(heading)
        self.tree = QTreeWidget()
        self.tree.setHeaderHidden(True)
        self.tree.setMinimumWidth(140)
        self.tree.setUniformRowHeights(True)
        self.tree.setAccessibleName(self.tr("Trackers and categories"))
        self.tree.currentItemChanged.connect(self._filter)
        tree_action = columns.addAction(self.tr("Tracker tree"))
        tree_action.setCheckable(True)
        tree_action.setChecked(True)
        tree_action.toggled.connect(self.tree.setVisible)
        side_layout.addWidget(self.tree, 1)
        split.addWidget(side)
        right = QSplitter(Qt.Orientation.Vertical)
        self.table = QTableView()
        self.table.setModel(self.proxy)
        style_table(self.table)
        self.table.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection)
        self.table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.table.setItemDelegateForColumn(2, ProgressBarDelegate(self.table))
        self.table.verticalHeader().setDefaultSectionSize(26)  # dense, like qBittorrent's list
        self.table.setSortingEnabled(True)
        self.table.sortByColumn(0, Qt.SortOrder.AscendingOrder)
        self.table.selectionModel().selectionChanged.connect(self._selection)
        self.table.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.table.customContextMenuRequested.connect(self._context_menu)
        for i, width in enumerate(
            [330, 115, 95, 100, 100, 100, 75, 115, 180, 160, 100, 85, 85, 85]
        ):
            self.table.setColumnWidth(i, width)
            if i in (10, 11):
                self.table.setColumnHidden(i, True)
        # Keep existing logical column IDs so older saved widths and visibility survive.
        settings = QSettings()
        saved = settings.value("torrents/header")
        if saved is not None:
            self.table.horizontalHeader().restoreState(saved)
        self._legacy_header = saved is not None and not settings.value(
            "torrents/connection_columns", False, type=bool
        )
        if not settings.value("torrents/connection_columns", False, type=bool):
            header = self.table.horizontalHeader()
            for offset, column in enumerate((12, 13)):
                header.moveSection(header.visualIndex(column), header.visualIndex(5) + 1 + offset)
                self.table.setColumnHidden(column, False)
                self.table.setColumnWidth(column, 85)
            settings.setValue("torrents/header", header.saveState())
            settings.setValue("torrents/connection_columns", True)
        for i, name in enumerate(self.model.headers):
            column_action = columns.addAction(name)
            column_action.setCheckable(True)
            column_action.setChecked(not self.table.isColumnHidden(i))
            column_action.setEnabled(i != 0)
            column_action.toggled.connect(lambda checked, c=i: self._column(c, checked))
        self.empty = EmptyState(self.table, self.tr("No torrents match these filters."))
        right.addWidget(self.table)
        self.details = QTabWidget()
        self.overview = QPlainTextEdit()
        self.overview.setReadOnly(True)
        self.details.addTab(self.overview, self.tr("Information"))
        file_page = QWidget()
        file_layout = QVBoxLayout(file_page)
        file_layout.setContentsMargins(0, 0, 0, 0)
        self.files = QTableWidget(0, 3)
        self.files.setHorizontalHeaderLabels(
            [self.tr("File"), self.tr("Size"), self.tr("Download")]
        )
        sortable_table(self.files)
        self.files.setColumnWidth(0, 500)
        self.files.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection)
        self.files.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        file_layout.addWidget(self.files)
        file_buttons = QHBoxLayout()
        for text, action in [
            (self.tr("Download"), "want_files"),
            (self.tr("Skip"), "skip_files"),
        ]:
            file_button = QPushButton(text)
            file_button.clicked.connect(lambda _=False, a=action: self.perform(a))
            self.buttons.append(file_button)
            file_buttons.addWidget(file_button)
        file_buttons.addStretch()
        file_layout.addLayout(file_buttons)
        self.details.addTab(file_page, self.tr("Files"))
        self.activity = QPlainTextEdit()
        self.activity.setReadOnly(True)
        self.activity.setMaximumBlockCount(1000)
        self.details.addTab(self.activity, self.tr("Activity"))
        self.details.currentChanged.connect(self._load_files)
        right.addWidget(self.details)
        right.setSizes([500, 180])
        split.addWidget(right)
        split.setStretchFactor(1, 1)
        split.setSizes([230, 1000])
        layout.addWidget(split, 1)
        self.connection = QLabel(self.tr("Open this tab to connect to your Torrent Client."))
        self.connection.setTextFormat(Qt.TextFormat.PlainText)
        self.connection.setWordWrap(True)
        layout.addWidget(self.connection)
        self.timer = QTimer(self)
        self.timer.setInterval(5000)
        self.timer.timeout.connect(self.refresh)
        self._selection()

    def _side_list(self, layout: QVBoxLayout, title: str, combo: QComboBox) -> QListWidget:
        """A sidebar section whose rows mirror a (hidden) combo box and set its choice."""
        heading = QLabel(title)
        heading.setProperty("role", "muted")
        layout.addWidget(heading)
        listing = QListWidget()
        listing.setFrameShape(QListWidget.Shape.NoFrame)
        listing.setUniformItemSizes(True)
        listing.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        listing.setAccessibleName(title)
        listing.currentRowChanged.connect(lambda row, c=combo: row >= 0 and c.setCurrentIndex(row))
        combo.currentIndexChanged.connect(lambda _i, c=combo, w=listing: self._mirror(c, w))
        layout.addWidget(listing)
        return listing

    def _mirror(self, combo: QComboBox, listing: QListWidget) -> None:
        """Show the combo's rows, with their counts, in the sidebar list."""
        with QSignalBlocker(listing):
            listing.clear()
            for i in range(combo.count()):
                listing.addItem(combo.itemText(i))
            listing.setCurrentRow(combo.currentIndex())
        listing.setFixedHeight(
            min(8, listing.count()) * listing.sizeHintForRow(0) + 6 if listing.count() else 0
        )

    def _column(self, column: int, visible: bool) -> None:
        self.table.setColumnHidden(column, not visible)
        QSettings().setValue("torrents/header", self.table.horizontalHeader().saveState())

    def showEvent(self, event: QShowEvent) -> None:
        super().showEvent(event)
        self.timer.start()
        self.refresh()

    def hideEvent(self, event: QHideEvent) -> None:
        self.timer.stop()
        QSettings().setValue("torrents/header", self.table.horizontalHeader().saveState())
        super().hideEvent(event)

    def reset_connection(self) -> None:
        self._generation += 1
        self._loading = False
        self._detail_generation += 1
        self._detail_hash = self._file_hash = ""
        self.model.update([])
        self._tree_counts = {}
        self.tree.clear()
        self.overview.clear()
        self.files.setRowCount(0)
        if self.isVisible():
            self.refresh()

    def set_seeding(self, view: Seeding) -> None:
        self._known_categories = category_labels(view.categories)
        self._category_by_hash = {}
        from torrentlinker.category_tags import category_path

        for row in view.rows:
            path = category_path(row.category, view.categories)
            if path and all(cid in view.categories for cid in path):
                self._category_by_hash[row.torrent.hash] = tuple(
                    view.categories[cid].name for cid in path
                )
        self.model.estimates = {r.torrent.hash: (r.zen, r.note) for r in view.rows}
        if self.model.rows:
            self._group()
            self.model.dataChanged.emit(
                self.model.index(0, 8),
                self.model.index(len(self.model.rows) - 1, 11),
                [Qt.ItemDataRole.DisplayRole, SORT],
            )

    def refresh(self) -> None:
        if self._loading or self._acting or not self.isVisible():
            return
        self._loading = True
        generation = self._generation
        self.refresh_button.setEnabled(False)
        if not self.model.rows:
            self.connection.setText(self.tr("Connecting to the Torrent Client…"))

        def done(rows: list[Torrent]) -> None:
            if generation != self._generation:
                return
            self._loading = False
            self.refresh_button.setEnabled(True)
            selected = {t.hash for t in self.selected()}
            scroll = self.table.verticalScrollBar().value()
            self.model.update(rows)
            self._group()
            if self._legacy_header and rows:
                # Qt must reconcile restored 12-column headers with the populated
                # 14-column proxy once before their next state can be saved.
                self.proxy.invalidate()
                self._legacy_header = False
            state_counts = Counter(status(t) for t in rows)
            for i in range(1, self.states.count()):
                key = self.states.itemData(i)
                n = state_counts[key]
                self.states.setItemText(i, f"{self.model.states[key]} ({n})")
            current_tag = self.tags.currentData()
            tag_counts = Counter(tag for t in rows for tag in t.tags)
            available = sorted(tag_counts)
            if available != [self.tags.itemData(i) for i in range(1, self.tags.count())]:
                with QSignalBlocker(self.tags):
                    self.tags.clear()
                    self.tags.addItem(self.tr("All tags"), "")
                    for tag in available:
                        self.tags.addItem(tag, tag)
                    self.tags.setCurrentIndex(max(0, self.tags.findData(current_tag)))
                self._filter()
            for i in range(1, self.tags.count()):
                tag = self.tags.itemData(i)
                self.tags.setItemText(i, f"{tag} ({tag_counts[tag]})")
            self._mirror(self.states, self.status_list)
            self._mirror(self.tags, self.tag_list)
            self._restore_selection(selected)
            self.table.verticalScrollBar().setValue(scroll)
            self.connection.setText(
                self.tr("Connected · {0} torrents · refresh every 5 seconds").format(len(rows))
            )
            self.timer.setInterval(5000)
            self._selection()
            if self._focus_hash:
                self._restore_selection({self._focus_hash})
                current = self.table.selectionModel().selectedRows()
                if current:
                    self.table.scrollTo(current[0])
                    self._focus_hash = ""

        def failed(error: Exception) -> None:
            if generation != self._generation:
                return
            self._loading = False
            self.refresh_button.setEnabled(True)
            self.connection.setText(
                self.tr("Connection failed; displayed data may be stale. {0}").format(
                    safe_error(error)
                )
            )
            self.timer.setInterval(30000)

        run_in_background(lambda: self.client().torrents(with_files=False), done, failed)

    def _group(self) -> None:
        groups: dict[tuple[str, ...], set[str]] = {(): set()}
        parents = {
            path[:i] for path in self._known_categories.values() for i in range(1, len(path))
        }
        paths = {}
        for t in self.model.rows:
            categories = torrent_categories(t, self._known_categories)
            if categories == ((),) and t.hash in self._category_by_hash:
                categories = (self._category_by_hash[t.hash],)
            leaves = tuple(
                (host, *(cat + ("",) if cat in parents else cat or ("",)))
                for host in tracker_groups(t)
                for cat in categories
            )
            paths[t.hash] = leaves
            self.model.categories[t.hash] = "; ".join(" / ".join(c) for c in categories)
            for leaf in leaves:
                for size in range(len(leaf) + 1):
                    groups.setdefault(leaf[:size], set()).add(t.hash)
        counts = {key: len(hashes) for key, hashes in groups.items()}
        if counts != self._tree_counts:
            expanded = set()

            def visit(item: QTreeWidgetItem | None) -> None:
                if item is None:
                    return
                if item.isExpanded():
                    expanded.add(item.data(0, Qt.ItemDataRole.UserRole))
                for i in range(item.childCount()):
                    visit(item.child(i))

            if self.tree.topLevelItemCount():
                visit(self.tree.topLevelItem(0))
            current = self.proxy.path
            with QSignalBlocker(self.tree):
                self.tree.clear()
                nodes = {}
                for path in sorted(counts, key=lambda p: (len(p), p)):
                    name = self.tr("All torrents") if not path else path[-1]
                    if path and not name:
                        name = (
                            self.tr("No tracker")
                            if len(path) == 1
                            else self.tr("Other / unspecified")
                        )
                    item = QTreeWidgetItem([f"{name} ({counts[path]})"])
                    item.setData(0, Qt.ItemDataRole.UserRole, path)
                    nodes[path] = item
                    if path:
                        nodes[path[:-1]].addChild(item)
                    else:
                        self.tree.addTopLevelItem(item)
                    item.setExpanded(not path or path in expanded)
                self.tree.setCurrentItem(nodes.get(current, nodes[()]))
            self._tree_counts = counts
        self._filter(paths=paths)

    def _filter(self, *_: Any, paths: dict[str, tuple[tuple[str, ...], ...]] | None = None) -> None:
        query = self.search.text().strip().casefold()
        state = self.states.currentData() or ""
        tag = self.tags.currentData() or ""
        item = self.tree.currentItem()
        path = item.data(0, Qt.ItemDataRole.UserRole) if item else ()
        proxy = self.proxy
        paths = proxy.paths if paths is None else paths
        changed = (query, state, tag, path) != (proxy.query, proxy.state, proxy.tag, proxy.path)
        changed = changed or bool(path and paths != proxy.paths)
        modern = hasattr(proxy, "endFilterChange")
        if changed and modern:
            proxy.beginFilterChange()
        proxy.query, proxy.state, proxy.tag, proxy.path = query, state, tag, path
        proxy.paths = paths
        if changed:
            if modern:
                proxy.endFilterChange(QSortFilterProxyModel.Direction.Rows)
            else:  # Qt 6.6–6.9, still supported by the desktop dependency.
                proxy.invalidateRowsFilter()

    def selected(self) -> list[Torrent]:
        return [
            self.model.rows[self.proxy.mapToSource(i).row()]
            for i in self.table.selectionModel().selectedRows()
        ]

    def _context_menu(self, position: QPoint) -> None:
        index = self.table.indexAt(position)
        if not index.isValid():
            return
        if not self.table.selectionModel().isRowSelected(index.row(), QModelIndex()):
            self.table.selectionModel().setCurrentIndex(
                index,
                QItemSelectionModel.SelectionFlag.ClearAndSelect
                | QItemSelectionModel.SelectionFlag.Rows,
            )
        self._selection()
        self.context_menu.popup(self.table.viewport().mapToGlobal(position))

    def _restore_selection(self, hashes: set[str]) -> None:
        from PySide6.QtCore import QItemSelection

        selection = QItemSelection()
        for row, t in enumerate(self.model.rows):
            if t.hash in hashes:
                index = self.proxy.mapFromSource(self.model.index(row, 0))
                if index.isValid():
                    selection.select(index, index)
        self.table.selectionModel().select(
            selection,
            QItemSelectionModel.SelectionFlag.ClearAndSelect
            | QItemSelectionModel.SelectionFlag.Rows,
        )

    def focus_torrent(self, hash: str = "") -> None:
        self._focus_hash = hash
        self.search.clear()
        self.states.setCurrentIndex(0)
        self.tags.setCurrentIndex(0)
        if (root := self.tree.topLevelItem(0)) is not None:
            self.tree.setCurrentItem(root)
        self.refresh()

    def _selection(self, *_: Any) -> None:
        selected = self.selected()
        for button in self.buttons:
            button.setEnabled(bool(selected) and not self._acting)
        for action in self.context_menu.actions():
            if not action.isSeparator():
                action.setEnabled(bool(selected) and not self._acting)
        if len(selected) != 1:
            self._detail_hash = self._file_hash = ""
            self._detail_generation += 1
            self.files.setRowCount(0)
            self.overview.setPlainText(self.tr("{0} torrents selected.").format(len(selected)))
            return
        t = selected[0]
        hosts = ", ".join(h for h in tracker_hosts(t) if h) or self.tr("No tracker")
        self.overview.setPlainText(
            "\n".join(
                [
                    t.name,
                    self.tr("Status: {0}").format(self.model.states[status(t)]),
                    self.tr("Download Folder: {0}").format(t.save_path),
                    self.tr("Trackers: {0}").format(hosts),
                    self.tr("Tags: {0}").format(", ".join(sorted(t.tags))),
                    self.tr("Hash: {0}").format(t.hash),
                ]
            )
        )
        if t.hash != self._detail_hash:
            self._detail_hash = t.hash
            self._file_hash = ""
            self._detail_generation += 1
            self.files.setRowCount(0)
            self._load_files()

    def _load_files(self, *_: Any) -> None:
        if (
            self.details.currentIndex() != 1
            or not self._detail_hash
            or self._file_hash == self._detail_hash
        ):
            return
        hash, generation = self._detail_hash, self._detail_generation
        self._file_hash = hash

        def done(files: list[TorrentFile]) -> None:
            if generation != self._detail_generation or hash != self._detail_hash:
                return
            self.files.setSortingEnabled(False)
            self.files.setRowCount(len(files))
            for index, file in enumerate(files):
                item = QTableWidgetItem(file.name)
                item.setData(Qt.ItemDataRole.UserRole, file.index)
                self.files.setItem(index, 0, item)
                self.files.setItem(index, 1, NumberItem(human_size(file.size), file.size))
                self.files.setItem(
                    index,
                    2,
                    QTableWidgetItem(self.tr("Yes") if file.wanted else self.tr("Skipped")),
                )
            self.files.setSortingEnabled(True)

        def failed(error: Exception) -> None:
            if generation == self._detail_generation:
                self._file_hash = ""
                self.connection.setText(
                    self.tr("Could not load files: {0}").format(safe_error(error))
                )

        run_in_background(lambda: self.client().file_listing(hash), done, failed)

    def _refuse_delete(self, client: str) -> None:
        QMessageBox.information(
            self,
            self.tr("Delete torrent and files"),
            self.tr(
                "{0} cannot delete a torrent's files. Remove the torrent instead, which keeps "
                "its files, then delete them yourself if you no longer need them."
            ).format(client or self.tr("Your torrent client")),
        )

    def _confirm_delete(self, selected: list[Torrent]) -> bool:
        box = QMessageBox(self)
        box.setIcon(QMessageBox.Icon.Warning)
        box.setWindowTitle(self.tr("Delete torrent and files"))
        box.setTextFormat(Qt.TextFormat.PlainText)
        box.setText(
            self.tr("Delete “{0}” and its downloaded files?").format(selected[0].name)
            if len(selected) == 1
            else self.tr("Delete {0} selected torrents and their downloaded files?").format(
                len(selected)
            )
        )
        box.setInformativeText(
            self.tr(
                "The Torrent Client will delete the files on its disk, including on a remote "
                "server or seedbox. Files shared with other torrents may also be lost. "
                "This cannot be undone in TorrentLinker."
            )
        )
        box.setDetailedText("\n\n".join(f"{t.name}\n{t.save_path}" for t in selected))
        box.setStandardButtons(QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.Cancel)
        box.button(QMessageBox.StandardButton.Yes).setText(self.tr("Delete torrent and files"))
        box.setDefaultButton(QMessageBox.StandardButton.Cancel)
        box.setEscapeButton(QMessageBox.StandardButton.Cancel)
        return box.exec() == QMessageBox.StandardButton.Yes

    def perform(self, action: str) -> None:
        selected = self.selected()
        if not selected or self._acting:
            return
        if action == "delete" and not getattr(self.client(), "can_delete_files", True):
            self._refuse_delete(getattr(self.client(), "client_name", ""))
            return
        if action == "delete" and not self._confirm_delete(selected):
            return
        indexes = []
        if action.endswith("_files"):
            if len(selected) != 1 or self._file_hash != selected[0].hash:
                return
            indexes = [
                item.data(Qt.ItemDataRole.UserRole)
                for i in self.files.selectionModel().selectedRows()
                if (item := self.files.item(i.row(), 0)) is not None
            ]
            if not indexes:
                return
        if action == "remove":
            answer = QMessageBox.question(
                self,
                self.tr("Remove torrents"),
                self.tr(
                    "Remove {0} selected torrents from the Torrent Client? Files will be kept."
                ).format(len(selected)),
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                QMessageBox.StandardButton.No,
            )
            if answer != QMessageBox.StandardButton.Yes:
                return
        try:
            client = self.client()
        except Exception as error:
            self.connection.setText(safe_error(error))
            return
        self._acting = True
        self._selection()
        self.connection.setText(self.tr("Sending commands to the Torrent Client…"))
        self.details.setCurrentWidget(self.activity)

        def progress(result: ActionResult) -> None:
            message = {
                "sent": self.tr("Command sent"),
                "verifying": self.tr("Skipped: Reseed verification in progress"),
                "missing": self.tr("Torrent no longer exists"),
                "failed": self.tr("Failed"),
            }[result.outcome]
            self.activity.appendPlainText(f"{result.name}: {message} {result.detail}")

        def done(results: list[ActionResult]) -> None:
            self._file_hash = ""
            self._acting = False
            self._selection()
            self.refresh()

        def failed(error: Exception) -> None:
            self._acting = False
            self._selection()
            self.connection.setText(safe_error(error))

        run_with_progress(
            lambda report: manage(
                client, [t.hash for t in selected], action, report, file_indexes=indexes
            ),
            progress,
            done,
            failed,
        )
