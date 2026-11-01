from __future__ import annotations

from pathlib import Path
from typing import Any

from PySide6.QtCore import (
    QT_TRANSLATE_NOOP,
    QAbstractItemModel,
    QCoreApplication,
    QModelIndex,
    QPersistentModelIndex,
    QSortFilterProxyModel,
    Qt,
)
from PySide6.QtGui import QColor

from torrentlinker.domain import ItemState
from torrentlinker.library import LibraryEntry
from torrentlinker.seeding import Seeding

STATE_LABELS = {
    ItemState.UNKNOWN: QT_TRANSLATE_NOOP("LibraryModel", "Seeding status unknown"),
    ItemState.SEEDED: QT_TRANSLATE_NOOP("LibraryModel", "Seeded"),
    ItemState.PARTIALLY_SEEDED: QT_TRANSLATE_NOOP("LibraryModel", "Partially seeded"),
    ItemState.ORPHAN: QT_TRANSLATE_NOOP("LibraryModel", "Orphan"),
}


def state_label(state: ItemState) -> str:
    return QCoreApplication.translate("LibraryModel", str(STATE_LABELS[state]))


def human_size(n: float) -> str:
    for unit in ("B", "KiB", "MiB", "GiB", "TiB"):
        if n < 1024:
            return f"{n:.0f} {unit}" if unit == "B" else f"{n:.1f} {unit}"
        n /= 1024
    return f"{n:.1f} PiB"


STATE_COLORS = {
    ItemState.SEEDED: QColor("#2e7d32"),
    ItemState.PARTIALLY_SEEDED: QColor("#ef6c00"),
}


class LibraryModel(QAbstractItemModel):
    """Local Items as a tree: packs at the top, their seasons and episodes below.

    `entries` stays the flat list the rest of the app uses; each index's internal id
    is its row in that list."""

    COLUMNS = (
        QT_TRANSLATE_NOOP("LibraryModel", "Name"),
        QT_TRANSLATE_NOOP("LibraryModel", "State"),
        QT_TRANSLATE_NOOP("LibraryModel", "Size"),
        QT_TRANSLATE_NOOP("LibraryModel", "Library Folder"),
        QT_TRANSLATE_NOOP("LibraryModel", "≈ Zen/day"),
    )
    ZEN_COLUMN = 4

    def __init__(self) -> None:
        super().__init__()
        self.entries: list[LibraryEntry] = []
        self._rows: dict[Path, list[int]] = {}  # a lookup per item would be quadratic
        self._parent: list[int | None] = []  # flat row of each entry's pack
        self._children: dict[int | None, list[int]] = {None: []}
        self._position: list[int] = []  # row of each entry under its parent
        self.status: dict[Path, str] = {}
        self.zen: dict[Path, float] = {}  # Zen Estimate of an Orphan's best Match
        self.seeding_zen: dict[str, float] = {}

    def set_entries(self, entries: list[LibraryEntry]) -> None:
        self.beginResetModel()
        self.entries = entries
        self._rows = {}
        for row, e in enumerate(entries):
            self._rows.setdefault(e.item.path, []).append(row)
        self._parent, self._children = [], {None: []}
        self._position = []
        for e in entries:
            packs = self._rows.get(e.item.within, []) if e.item.within else []
            pack = next((r for r in packs if entries[r].folder == e.folder), None)
            siblings = self._children.setdefault(pack, [])
            self._parent.append(pack)
            self._position.append(len(siblings))
            siblings.append(len(self._position) - 1)
        self.endResetModel()

    def flat_index(self, row: int, column: int = 0) -> QModelIndex:
        """The model index of `entries[row]`."""
        return self.createIndex(self._position[row], column, row)

    def pack_of(self, row: int) -> LibraryEntry | None:
        pack = self._parent[row]
        return None if pack is None else self.entries[pack]

    def rows_of(self, path: Path) -> list[int]:
        return self._rows.get(path, [])

    def contains(self, entry: LibraryEntry) -> bool:
        return any(self.entries[row] == entry for row in self.rows_of(entry.item.path))

    def set_status(self, path: Path, text: str | None) -> None:
        """Overlay a transient status (Verifying, Reseeded, Failed…) on a Local Item."""
        if text is None:
            self.status.pop(path, None)
        else:
            self.status[path] = text
        for row in self.rows_of(path):
            i = self.flat_index(row, 1)
            self.dataChanged.emit(i, i)

    def set_zen(self, path: Path, value: float | None) -> None:
        if value is None:
            self.zen.pop(path, None)
        else:
            self.zen[path] = value
        for row in self.rows_of(path):
            i = self.flat_index(row, self.ZEN_COLUMN)
            self.dataChanged.emit(i, i)

    def zen_of(self, entry: LibraryEntry) -> float | None:
        if entry.state is ItemState.ORPHAN:
            return self.zen.get(entry.item.path)
        if entry.torrent_hashes and entry.torrent_hashes <= self.seeding_zen.keys():
            return sum(self.seeding_zen[hash] for hash in sorted(entry.torrent_hashes))
        return None

    def set_seeding(self, view: Seeding | None) -> None:
        self.seeding_zen = (
            {row.torrent.hash.lower(): row.zen or 0.0 for row in view.rows} if view else {}
        )
        for rows in self._children.values():  # one range per pack, not per item
            if rows:
                first = self.flat_index(rows[0], self.ZEN_COLUMN)
                self.dataChanged.emit(first, first.siblingAtRow(len(rows) - 1))

    def index(
        self, row: int, column: int, parent: QModelIndex | QPersistentModelIndex = QModelIndex()
    ) -> QModelIndex:
        rows = self._children.get(parent.internalId() if parent.isValid() else None, [])
        if not 0 <= row < len(rows) or not 0 <= column < len(self.COLUMNS):
            return QModelIndex()
        return self.createIndex(row, column, rows[row])

    def parent(self, index: QModelIndex | QPersistentModelIndex = QModelIndex()) -> QModelIndex:  # type: ignore[override]
        if not index.isValid():
            return QModelIndex()
        pack = self._parent[index.internalId()]
        return QModelIndex() if pack is None else self.flat_index(pack)

    def rowCount(self, parent: QModelIndex | QPersistentModelIndex = QModelIndex()) -> int:
        if parent.isValid() and parent.column() != 0:
            return 0
        return len(self._children.get(parent.internalId() if parent.isValid() else None, []))

    def columnCount(self, parent: QModelIndex | QPersistentModelIndex = QModelIndex()) -> int:
        return len(self.COLUMNS)

    def headerData(self, section: int, orientation: Qt.Orientation, role: int = 0) -> Any:
        if role == Qt.ItemDataRole.DisplayRole and orientation == Qt.Orientation.Horizontal:
            return QCoreApplication.translate("LibraryModel", str(self.COLUMNS[section]))
        return None

    def data(self, index: QModelIndex | QPersistentModelIndex, role: int = 0) -> Any:
        if not index.isValid():
            return None
        e = self.entries[index.internalId()]
        if role == Qt.ItemDataRole.ToolTipRole:
            if index.column() == self.ZEN_COLUMN and e.state is not ItemState.ORPHAN:
                return self.tr(
                    "Current Zen/day estimate for the linked torrents, as shown in Zen. "
                    "A torrent shared by several Local Items appears on each item. "
                    "Paused torrents contribute zero; a blank value means unavailable."
                )
            if index.column() == 0 and e.item.within:
                return self.tr(
                    "{0}\nAlso inside the pack {1}. Seeded on its own only by a torrent of "
                    "its own; select it to search it as a single episode."
                ).format(e.item.path, e.item.within)
            return str(e.item.path) if index.column() == 0 else self.data(index)
        if role == Qt.ItemDataRole.DisplayRole:
            return (
                e.item.path.name,
                state_label(e.state)
                if e.state is ItemState.UNKNOWN
                else self.status.get(e.item.path) or self.state_text(index.internalId()),
                human_size(e.item.size),
                str(e.folder.path),
                f"≈ {zen:.2f}" if (zen := self.zen_of(e)) is not None else "",
            )[index.column()]
        if role == Qt.ItemDataRole.TextAlignmentRole and index.column() == self.ZEN_COLUMN:
            return Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter
        if role == Qt.ItemDataRole.ForegroundRole and index.column() == 1:
            return STATE_COLORS.get(e.state)
        if role == Qt.ItemDataRole.UserRole:
            return e
        return None

    def state_text(self, row: int) -> str:
        e, pack = self.entries[row], self.pack_of(row)
        if (
            e.state is ItemState.ORPHAN
            and pack is not None
            and pack.state in (ItemState.SEEDED, ItemState.PARTIALLY_SEEDED)
        ):
            return self.tr("Orphan · in seeded pack")
        return state_label(e.state)


class LibraryFilter(QSortFilterProxyModel):
    """Filter the Library by state and by Library Folder; sorts sizes numerically."""

    def __init__(self) -> None:
        super().__init__()
        self.state: ItemState | None = None
        self.folder: Path | None = None
        self.query = ""
        self.setSortCaseSensitivity(Qt.CaseSensitivity.CaseInsensitive)
        self.setRecursiveFilteringEnabled(True)  # a matching episode keeps its pack shown

    def set_filters(self, state: ItemState | None, folder: Path | None, query: str = "") -> None:
        if hasattr(self, "beginFilterChange"):  # Qt >= 6.9
            self.beginFilterChange()
            self.state, self.folder, self.query = state, folder, query.strip().casefold()
            self.endFilterChange()
        else:
            self.state, self.folder, self.query = state, folder, query.strip().casefold()
            self.invalidateFilter()

    def filterAcceptsRow(
        self, source_row: int, source_parent: QModelIndex | QPersistentModelIndex
    ) -> bool:
        model = self.sourceModel()
        assert isinstance(model, LibraryModel)
        e = model.entries[model.index(source_row, 0, source_parent).internalId()]
        return (
            (self.state is None or e.state is self.state)
            and (self.folder is None or e.folder.path == self.folder)
            and (not self.query or self.query in e.item.path.name.casefold())
        )

    def lessThan(
        self,
        left: QModelIndex | QPersistentModelIndex,
        right: QModelIndex | QPersistentModelIndex,
    ) -> bool:
        model = self.sourceModel()
        assert isinstance(model, LibraryModel)
        a_entry, b_entry = model.entries[left.internalId()], model.entries[right.internalId()]
        if left.column() == 2:
            return a_entry.item.size < b_entry.item.size
        if left.column() == LibraryModel.ZEN_COLUMN:
            a = model.zen_of(a_entry)
            b = model.zen_of(b_entry)
            return (-1.0 if a is None else a) < (-1.0 if b is None else b)
        return super().lessThan(left, right)
