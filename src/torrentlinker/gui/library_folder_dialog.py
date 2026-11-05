"""Configure a Library Folder without leaving the Library."""

from pathlib import Path

from PySide6.QtCore import QCoreApplication
from PySide6.QtWidgets import (
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFormLayout,
    QLabel,
    QSpinBox,
    QWidget,
)
from shiboken6 import isValid

from torrentlinker.domain import ContentKind, LibraryFolder
from torrentlinker.gui.settings_dialog import kind_label, offered_kinds
from torrentlinker.gui.tasks import run_in_background
from torrentlinker.item_detection import DetectedItem, detect_items

AUTOMATIC = -1  # the spin box value shown as "Automatic"


def grouping_box(depth: int | None) -> QSpinBox:
    """Scan grouping: Automatic, or a fixed depth for layouts detection gets wrong."""
    box = QSpinBox()
    box.setRange(AUTOMATIC, 10)
    box.setSpecialValueText(QCoreApplication.translate("LibraryFolderDialog", "Automatic"))
    box.setPrefix(QCoreApplication.translate("LibraryFolderDialog", "Depth "))
    box.setValue(AUTOMATIC if depth is None else depth)
    return box


def grouping(box: QSpinBox) -> int | None:
    return None if box.value() == AUTOMATIC else box.value()


class LibraryFolderDialog(QDialog):
    def __init__(self, path: Path, parent: QWidget | None = None, *, adult: bool = False) -> None:
        super().__init__(parent)
        self.path = path
        self.setWindowTitle(self.tr("Add Library Folder"))
        layout = QFormLayout(self)
        name = QLabel(str(path))
        name.setWordWrap(True)
        layout.addRow(self.tr("Folder"), name)
        self.kind = QComboBox()
        for kind in offered_kinds(adult):
            self.kind.addItem(kind_label(kind), kind)
        self.kind.setCurrentIndex(self.kind.findData(LibraryFolder(path).kind))
        layout.addRow(self.tr("Content Kind"), self.kind)
        self.scan_depth = grouping_box(None)
        layout.addRow(self.tr("Scan grouping"), self.scan_depth)
        self.preview = QLabel()
        self.preview.setWordWrap(True)
        layout.addRow(self.preview)
        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Save | QDialogButtonBox.StandardButton.Cancel
        )
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addRow(buttons)
        self._preview_run = 0
        self.kind.currentIndexChanged.connect(self.refresh_preview)
        self.scan_depth.valueChanged.connect(self.refresh_preview)
        self.refresh_preview()

    def folder(self) -> LibraryFolder:
        return LibraryFolder(
            self.path, grouping(self.scan_depth), ContentKind(self.kind.currentData())
        )

    def refresh_preview(self) -> None:
        self._preview_run += 1
        if grouping(self.scan_depth) is not None:
            self.preview.setText(
                self.tr(
                    "Depth {0} treats every entry {0} levels down as one Local Item; files "
                    "met earlier are Local Items too."
                ).format(self.scan_depth.value())
            )
            return
        self.preview.setText(self.tr("Looking for Local Items…"))
        run, path, kind = self._preview_run, self.path, ContentKind(self.kind.currentData())

        def shown(items: list[DetectedItem]) -> None:
            # The dialog may be gone, or the choice changed, before the folder was read.
            if isValid(self.preview) and run == self._preview_run:
                self.preview.setText(self.summary(items))

        def failed(error: Exception) -> None:
            if isValid(self.preview) and run == self._preview_run:
                self.preview.setText(self.tr("Could not read the folder: {0}").format(error))

        run_in_background(lambda: detect_items(path, kind), shown, failed)

    def summary(self, items: list[DetectedItem]) -> str:
        packs = sum(item.within is None for item in items)
        if not packs:
            return self.tr("Automatic finds no Local Item here. Check the Content Kind.")
        text = self.tr("Automatic finds {0} Local Items").format(packs)
        if episodes := len(items) - packs:
            text += self.tr(", plus {0} episodes inside packs").format(episodes)
        return (
            text
            + ". "
            + self.tr(
                "Folders holding a release stay whole; genre and collection folders are "
                "opened. Seeded torrents and the Catalogue refine this when scanning."
            )
        )
