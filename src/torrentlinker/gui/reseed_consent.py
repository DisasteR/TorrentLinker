"""Ask on the GUI thread before a worker renames local files for µTorrent or rTorrent."""

from __future__ import annotations

import threading
from collections.abc import Callable
from dataclasses import dataclass, field

from PySide6.QtCore import QObject, Qt, QThread, Signal, Slot
from PySide6.QtWidgets import (
    QApplication,
    QDialog,
    QDialogButtonBox,
    QHeaderView,
    QLabel,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from torrentlinker.disk_rename import DiskRenamePlan


class DiskRenameDialog(QDialog):
    def __init__(
        self, plan: DiskRenamePlan, parent: QWidget | None = None, client: str = ""
    ) -> None:
        super().__init__(parent)
        client = client or self.tr("Your torrent client")
        self.setWindowTitle(self.tr("{0}: rename files on disk?").format(client))
        self.resize(800, 450)
        layout = QVBoxLayout(self)
        warning = QLabel(
            self.tr(
                "{0} cannot rename files inside a torrent. "
                "To seed this torrent, TorrentLinker must rename the files on disk listed below. "
                "This can break other torrents, readers or shortcuts using the old paths.\n\n"
                "File contents stay unchanged. Existing destinations are never overwritten. "
                "The files are checked before renaming and again in {0} before seeding. "
                "If setup or verification fails, TorrentLinker restores the old names when "
                "the files are unchanged and those names are still available."
            ).format(client)
        )
        warning.setWordWrap(True)
        layout.addWidget(warning)
        title = QLabel(plan.title)
        title.setTextFormat(Qt.TextFormat.PlainText)
        title.setWordWrap(True)
        layout.addWidget(title)
        self.files = QTableWidget(len(plan.moves), 2)
        self.files.setHorizontalHeaderLabels([self.tr("Current path"), self.tr("New path on disk")])
        self.files.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self.files.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Stretch)
        for row, move in enumerate(plan.moves):
            for col, path in enumerate([move.source, move.target]):
                cell = QTableWidgetItem(str(path))
                cell.setToolTip(str(path))
                self.files.setItem(row, col, cell)
        layout.addWidget(self.files, 1)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Cancel)
        self.rename_button = buttons.addButton(
            self.tr("Rename files and reseed"), QDialogButtonBox.ButtonRole.AcceptRole
        )
        self.rename_button.setAutoDefault(False)
        self.cancel_button = buttons.button(QDialogButtonBox.StandardButton.Cancel)
        self.cancel_button.setDefault(True)
        self.cancel_button.setFocus()
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)


@dataclass
class _Request:
    plan: DiskRenamePlan
    done: threading.Event = field(default_factory=threading.Event)
    approved: bool = False


class ReseedConsent(QObject):
    requested = Signal(object)

    def __init__(self, parent: QWidget, client: Callable[[], str] = lambda: "") -> None:
        super().__init__(parent)
        self.window = parent
        self.client = client  # the Torrent Client's name, for the dialog
        self.closed = threading.Event()
        self.dialog: DiskRenameDialog | None = None
        self.requested.connect(self._show, Qt.ConnectionType.QueuedConnection)
        app = QApplication.instance()
        if app is not None:
            app.aboutToQuit.connect(self.cancel)

    def approve(self, plan: DiskRenamePlan) -> bool:
        if self.closed.is_set():
            return False
        request = _Request(plan)
        if QThread.currentThread() == self.thread():
            self._show(request)
        else:
            self.requested.emit(request)
            while not request.done.wait(0.1):
                if self.closed.is_set():
                    return False
        return request.approved and not self.closed.is_set()

    @Slot(object)
    def _show(self, request: _Request) -> None:
        try:
            if self.closed.is_set():
                return
            self.dialog = DiskRenameDialog(request.plan, self.window, self.client())
            request.approved = self.dialog.exec() == QDialog.DialogCode.Accepted
        finally:
            if self.dialog is not None:
                self.dialog.deleteLater()
                self.dialog = None
            request.done.set()

    def cancel(self) -> None:
        self.closed.set()
        if self.dialog is not None:
            self.dialog.reject()
