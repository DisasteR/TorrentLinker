"""Folder explorer shared by Library navigation and its inline folder editor."""

from __future__ import annotations

from collections import Counter
from pathlib import Path

from PySide6.QtCore import QSignalBlocker, Qt
from PySide6.QtWidgets import (
    QComboBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QMessageBox,
    QPlainTextEdit,
    QPushButton,
    QSpinBox,
    QSplitter,
    QTableWidget,
    QTableWidgetItem,
    QTabWidget,
    QVBoxLayout,
    QWidget,
)

from torrentlinker.domain import ContentKind, LibraryFolder
from torrentlinker.gui.library_folder_dialog import grouping, grouping_box
from torrentlinker.gui.settings_dialog import choose_directory, kind_label
from torrentlinker.scan import ScanError


class FolderEditor(QWidget):
    """Edit registered Library Folders without altering downloads or Path Mappings."""

    def __init__(self, folders: list[LibraryFolder], parent: QWidget | None = None):
        super().__init__(parent)
        layout = QVBoxLayout(self)
        note = QLabel(
            self.tr(
                "Register the folders TorrentLinker can read. Add as many as you need. "
                "Download destinations and Torrent Client Path Mappings are configured separately."
            )
        )
        note.setWordWrap(True)
        layout.addWidget(note)
        self.scan_errors: list[ScanError] = []
        self.scanned = False
        self.table = QTableWidget(0, 4)
        self.table.setHorizontalHeaderLabels(
            [
                self.tr("Library Folder"),
                self.tr("Content Kind"),
                self.tr("Scan grouping"),
                self.tr("File access"),
            ]
        )
        self.table.setColumnWidth(0, 430)
        self.table.setColumnWidth(1, 160)
        self.table.setColumnWidth(2, 160)
        self.table.setColumnWidth(3, 250)
        layout.addWidget(self.table, 1)
        self.path = QLineEdit()
        self.path.setPlaceholderText(self.tr("Type a folder path"))
        row = QHBoxLayout()
        row.addWidget(self.path, 1)
        browse = QPushButton(self.tr("Browse…"))
        browse.clicked.connect(self.browse)
        add = QPushButton(self.tr("Add folder"))
        add.clicked.connect(self.add_typed)
        self.path.returnPressed.connect(self.add_typed)
        remove = QPushButton(self.tr("Remove selected"))
        remove.clicked.connect(lambda: self.table.removeRow(self.table.currentRow()))
        for button in (browse, add, remove):
            row.addWidget(button)
        layout.addLayout(row)
        hint = QLabel(
            self.tr(
                "Automatic finds releases, season packs and their episodes at any depth. "
                "Choose a depth only when a folder needs it: depth 1 makes each direct child "
                "one Local Item. Removing a folder here keeps all files on disk."
            )
        )
        hint.setWordWrap(True)
        layout.addWidget(hint)
        self.set_folders(folders)

    def set_folders(self, folders: list[LibraryFolder]) -> None:
        self.table.setRowCount(0)
        for folder in folders:
            self.add_folder(folder)
        self.set_scan_errors(self.scan_errors, self.scanned)

    def add_folder(self, folder: LibraryFolder) -> None:
        if any(str(f.path) == str(folder.path) for f in self.folders()):
            return
        row = self.table.rowCount()
        self.table.insertRow(row)
        cell = QTableWidgetItem(str(folder.path))
        cell.setFlags(cell.flags() & ~Qt.ItemFlag.ItemIsEditable)
        self.table.setItem(row, 0, cell)
        kind = QComboBox()
        for value in ContentKind:
            kind.addItem(kind_label(value), value)
        kind.setCurrentIndex(kind.findData(folder.kind))
        self.table.setCellWidget(row, 1, kind)
        self.table.setCellWidget(row, 2, grouping_box(folder.depth))
        self.set_scan_errors(self.scan_errors, self.scanned)

    def set_scan_errors(self, errors: list[ScanError], scanned: bool = True) -> None:
        self.scan_errors, self.scanned = list(errors), scanned
        for row in range(self.table.rowCount()):
            path = self.table.item(row, 0)
            if path is None:
                continue
            folder = Path(path.text())
            failures = [
                e
                for e in errors
                if e.path == folder or folder in e.path.parents or e.path in folder.parents
            ]
            text = (
                self.tr("Scan incomplete · previous items kept")
                if failures
                else (self.tr("Last scan readable") if scanned else self.tr("Not scanned"))
            )
            cell = QTableWidgetItem(text)
            cell.setFlags(cell.flags() & ~Qt.ItemFlag.ItemIsEditable)
            cell.setToolTip("\n".join(f"{e.path}: {e.message}" for e in failures))
            self.table.setItem(row, 3, cell)

    def folders(self) -> list[LibraryFolder]:
        result = []
        for row in range(self.table.rowCount()):
            cell = self.table.item(row, 0)
            kind, depth = self.table.cellWidget(row, 1), self.table.cellWidget(row, 2)
            assert cell and isinstance(kind, QComboBox) and isinstance(depth, QSpinBox)
            result.append(
                LibraryFolder(Path(cell.text()), grouping(depth), ContentKind(kind.currentData()))
            )
        return result

    def browse(self) -> None:
        if path := choose_directory(self, self.tr("Library Folder"), self.path.text()):
            self.path.setText(path)
            self.add_typed()

    def add_typed(self) -> None:
        value = self.path.text().strip()
        if not value:
            return
        path = Path(value).expanduser().resolve()
        if not path.is_dir():
            QMessageBox.warning(self, self.tr("Library Folder"), self.tr("Folder is unavailable."))
            return
        self.add_folder(LibraryFolder(path, None))  # Automatic until the user picks a depth
        self.path.clear()


class LibraryWorkspace(QWidget):
    def __init__(self, window, items: QWidget, toolbar: QWidget, matches: QWidget):
        super().__init__()
        self.owner = window
        self.scan_errors: list[ScanError] = []
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        heading = QLabel(self.tr("Explore your folders. Review Candidates. Verify before seeding."))
        heading.setProperty("role", "muted")
        layout.addWidget(heading)
        self.sections = QTabWidget()
        self.sections.setObjectName("librarySections")
        layout.addWidget(self.sections)
        explorer = QWidget()
        body = QHBoxLayout(explorer)
        body.setContentsMargins(0, 10, 0, 0)
        rail = QWidget()
        rail.setMaximumWidth(240)
        rail.setMinimumWidth(180)
        rail_layout = QVBoxLayout(rail)
        rail_layout.setContentsMargins(0, 0, 8, 0)
        rail_layout.addWidget(QLabel(self.tr("LIBRARY FOLDERS")))
        self.rail = QListWidget()
        self.rail.setStyleSheet(
            "QListWidget {border: 0;} "
            "QListWidget::item {padding: 12px 8px; margin: 3px 0; border-radius: 7px;}"
        )
        self.rail.setAccessibleName(self.tr("Library Folders"))
        self.rail.currentRowChanged.connect(self.select_folder)
        rail_layout.addWidget(self.rail)
        add = QPushButton(self.tr("Manage folders"))
        add.clicked.connect(lambda: self.sections.setCurrentIndex(1))
        rail_layout.addWidget(add)
        body.addWidget(rail)
        content = QWidget()
        stack = QVBoxLayout(content)
        stack.setContentsMargins(0, 0, 0, 0)
        stack.addWidget(toolbar)
        combine = QPushButton(self.tr("Combine selected Local Items"))
        combine.setObjectName("combineLibraryItems")
        combine.clicked.connect(self.combine_selected)
        self.combine = combine
        stack.addWidget(combine, 0, Qt.AlignmentFlag.AlignLeft)
        stack.addWidget(matches)
        self.split = QSplitter(Qt.Orientation.Vertical)
        self.split.setChildrenCollapsible(False)
        self.split.addWidget(items)
        self.split.addWidget(window.review)
        self.split.setSizes([340, 350])
        stack.addWidget(self.split, 1)
        body.addWidget(content, 1)
        self.sections.addTab(explorer, self.tr("Local Items"))
        self.editor = FolderEditor(window.settings.library_folders)
        save = QPushButton(self.tr("Save folders"))
        save.setProperty("role", "primary")
        save.clicked.connect(self.save_folders)
        editor_layout = self.editor.layout()
        assert editor_layout is not None
        editor_layout.addWidget(save)
        self.sections.addTab(self.editor, self.tr("Folders"))
        self.activity = QPlainTextEdit()
        self.activity.setReadOnly(True)
        self.activity.setMaximumBlockCount(300)
        self.activity.setPlaceholderText(
            self.tr("Scan, search and Verification progress appears here.")
        )
        self.sections.addTab(self.activity, self.tr("Activity"))
        self.items = items
        self.toolbar, self.matches = toolbar, matches
        window.review.expand_button.clicked.connect(self.expand_review)
        window.folder_filter.currentIndexChanged.connect(self.sync_rail)
        window.library_model.modelReset.connect(self.refresh)
        window.statusBar().messageChanged.connect(self.record_activity)
        self.refresh()

    def record_activity(self, message: str) -> None:
        if message:
            self.activity.appendPlainText(message)

    def expand_review(self) -> None:
        expanded = not self.items.isHidden()
        for widget in (self.items, self.toolbar, self.matches, self.combine):
            widget.setVisible(not expanded)
        self.owner.review.expand_button.setText(
            self.tr("Back to Local Items") if expanded else self.tr("Expand review")
        )

    def combine_selected(self):
        from torrentlinker.compatible import combine_items
        from torrentlinker.domain import ItemState, TorrentClient
        from torrentlinker.library import LibraryEntry
        from torrentlinker.scan import scan_item

        entries = self.owner.selected_entries()
        try:
            if self.owner.settings.client is not TorrentClient.QBITTORRENT:
                raise ValueError("Combining folders currently requires qBittorrent.")
            if len({e.folder.kind for e in entries}) != 1:
                raise ValueError("Select Local Items with the same Content Kind.")
            item = combine_items(
                [scan_item(e.item.path) for e in entries], self.owner.settings.path_mappings
            )
            self.owner.review.set_entry(LibraryEntry(entries[0].folder, item, ItemState.ORPHAN))
        except (ValueError, OSError) as error:
            QMessageBox.warning(self, self.tr("Combine Local Items"), str(error))

    def select_folder(self, row: int) -> None:
        if row >= 0:
            self.owner.folder_filter.setCurrentIndex(row)

    def sync_rail(self) -> None:
        with QSignalBlocker(self.rail):
            self.rail.setCurrentRow(self.owner.folder_filter.currentIndex())

    def refresh(self) -> None:
        counts = Counter(e.folder.path for e in self.owner.library_model.entries)
        with QSignalBlocker(self.rail):
            self.rail.clear()
            self.rail.addItem(self.tr("All folders · {0}").format(sum(counts.values())))
            for folder in self.owner.settings.library_folders:
                item = QListWidgetItem(f"{folder.path.name or folder.path} · {counts[folder.path]}")
                failures = [
                    e
                    for e in self.scan_errors
                    if e.path == folder.path
                    or folder.path in e.path.parents
                    or e.path in folder.path.parents
                ]
                if failures:
                    item.setText(item.text() + " · " + self.tr("Scan incomplete"))
                item.setToolTip(str(folder.path) + "\n" + "\n".join(e.message for e in failures))
                self.rail.addItem(item)
        self.sync_rail()

    def set_scan_errors(self, errors: list[ScanError]) -> None:
        self.scan_errors = list(errors)
        self.editor.set_scan_errors(errors)
        self.refresh()

    def save_folders(self) -> None:
        if self.owner.save_library_folders(self.editor.folders()):
            self.sections.setCurrentIndex(0)
