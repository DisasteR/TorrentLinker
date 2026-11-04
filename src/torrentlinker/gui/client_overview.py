"""Combined torrent management with explicit per-row client identity."""

from __future__ import annotations

from collections import defaultdict
from typing import Any

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QAbstractItemView,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMenu,
    QMessageBox,
    QPushButton,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from torrentlinker.client_profiles import Connections
from torrentlinker.config import Settings
from torrentlinker.credentials import get_secret
from torrentlinker.gui.controls import set_role
from torrentlinker.gui.library_model import human_size
from torrentlinker.gui.tables import NumberItem, style_table
from torrentlinker.gui.tasks import run_in_background
from torrentlinker.seeding import combined_seeding, seeding
from torrentlinker.store import Store
from torrentlinker.torrent_management import (
    manage,
    peer_count_order,
    peer_count_text,
    safe_error,
    status,
)


class ClientOverview(QWidget):
    def __init__(self, settings: Settings, store: Store):
        super().__init__()
        self.settings = settings
        self.store = store
        self.connections = Connections(get_secret)
        self.rows: list[Any] = []
        self.busy = False
        layout = QVBoxLayout(self)
        toolbar = QHBoxLayout()
        self.filter = QLineEdit()
        self.filter.setPlaceholderText(self.tr("Filter torrents or clients…"))
        self.filter.setClearButtonEnabled(True)
        self.filter.textChanged.connect(self.render)
        toolbar.addWidget(self.filter)
        self.refresh_button = QPushButton(self.tr("Refresh all clients"))
        set_role(self.refresh_button, "quiet")
        self.refresh_button.clicked.connect(self.refresh)
        toolbar.addWidget(self.refresh_button)
        layout.addLayout(toolbar)
        self.summary = QLabel()
        self.summary.setWordWrap(True)
        layout.addWidget(self.summary)
        self.table = QTableWidget(0, 8)
        self.table.setHorizontalHeaderLabels(
            [
                self.tr("Torrent"),
                self.tr("Client"),
                self.tr("Status"),
                self.tr("Size"),
                self.tr("Seeders"),
                self.tr("Peers"),
                self.tr("≈ Zen/day"),
                self.tr("≈ Note"),
            ]
        )
        for column in (4, 5):
            header = self.table.horizontalHeaderItem(column)
            if header is not None:
                header.setToolTip(
                    self.tr("Connected (swarm total). ? = unknown. Sorted by total when known.")
                )
        self.table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.table.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection)
        self.table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.table.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.table.customContextMenuRequested.connect(self.menu)
        style_table(self.table)
        self.table.setSortingEnabled(True)
        layout.addWidget(self.table, 1)

    def refresh(self):
        if self.busy:
            return
        self.busy = True
        self.refresh_button.setEnabled(False)
        targets = [
            (p.id, p.name, self.connections.client(self.settings, p.id))
            for p in self.settings.client_profiles
            if p.enabled
        ]

        def work():
            rows, errors, observed = [], [], []
            for identifier, name, client in targets:
                try:
                    torrents = client.torrents(with_files=False)
                    view = seeding(
                        self.settings.zen,
                        torrents,
                        self.settings.indexer.url,
                        catalogue={
                            r.infohash: r for r in self.store.catalogue([t.hash for t in torrents])
                        },
                        categories=self.settings.indexer_categories,
                    )
                    estimates = {row.torrent.hash: row for row in view.rows}
                    observed.extend(torrents)
                    rows.extend(
                        (identifier, name, torrent, estimates.get(torrent.hash))
                        for torrent in torrents
                    )
                except Exception as error:
                    errors.append(name + ": " + safe_error(error))
            combined = combined_seeding(
                self.settings.zen,
                observed,
                self.settings.indexer.url,
                catalogue={
                    r.infohash: r for r in self.store.catalogue([t.hash.lower() for t in observed])
                },
                categories=self.settings.indexer_categories,
            )
            return rows, errors, combined

        def done(result):
            self.rows, errors, combined = result
            self.busy = False
            self.refresh_button.setEnabled(True)
            self.summary.setText(
                self.tr("{0} torrent instances · {1} unique seeds · ≈ {2:.2f} Zen/day").format(
                    len(self.rows), combined.count, combined.day.total
                )
                + ("\n" + "\n".join(errors) if errors else "")
            )
            self.render()

        def failed(error):
            self.busy = False
            self.refresh_button.setEnabled(True)
            self.summary.setText(safe_error(error))

        run_in_background(work, done, failed)

    def render(self):
        text = self.filter.text().casefold()
        rows = [row for row in self.rows if text in (row[1] + " " + row[2].name).casefold()]
        self.table.setSortingEnabled(False)
        self.table.setRowCount(len(rows))
        for row, (identifier, name, torrent, estimate) in enumerate(rows):
            title = QTableWidgetItem(torrent.name)
            title.setData(Qt.ItemDataRole.UserRole, (identifier, torrent.hash))
            self.table.setItem(row, 0, title)
            self.table.setItem(row, 1, QTableWidgetItem(name))
            self.table.setItem(row, 2, QTableWidgetItem(status(torrent)))
            self.table.setItem(
                row, 3, NumberItem(human_size(torrent.total_size), torrent.total_size)
            )
            for column, connected, total in [
                (4, torrent.connected_seeds, torrent.swarm_seeders),
                (5, torrent.connected_peers, torrent.swarm_peers),
            ]:
                self.table.setItem(
                    row,
                    column,
                    NumberItem(
                        peer_count_text(connected, total), peer_count_order(connected, total)
                    ),
                )
            for column, value in [
                (6, estimate.zen if estimate else None),
                (7, estimate.note if estimate else None),
            ]:
                self.table.setItem(
                    row,
                    column,
                    NumberItem(
                        "–" if value is None else f"{value:.2f}",
                        value or 0,
                    ),
                )
        self.table.setSortingEnabled(True)

    def menu(self, point):
        if self.busy:
            return
        menu = QMenu(self)
        for label, action in [
            (self.tr("Start"), "resume"),
            (self.tr("Pause"), "pause"),
            (self.tr("Recheck"), "recheck"),
            (self.tr("Remove torrent…"), "remove"),
            (self.tr("Delete torrent and files…"), "delete"),
        ]:
            menu.addAction(label, lambda checked=False, action=action: self.apply(action))
        menu.exec(self.table.viewport().mapToGlobal(point))

    def apply(self, action):
        selected = [
            self.table.item(i.row(), 0).data(Qt.ItemDataRole.UserRole)
            for i in self.table.selectionModel().selectedRows()
        ]
        if not selected:
            return
        if action == "delete":
            refusing = {
                self.settings.profile(identifier).client.display_name
                for identifier, _ in selected
                if not self.connections.client(self.settings, identifier).can_delete_files
            }
            if refusing:
                QMessageBox.information(
                    self,
                    self.tr("Delete torrent and files"),
                    self.tr(
                        "{0} cannot delete a torrent's files. Remove the torrent instead, which "
                        "keeps its files, then delete them yourself if you no longer need them."
                    ).format(", ".join(sorted(refusing))),
                )
                return
        if action in {"remove", "delete"}:
            names = "\n".join(
                f"{self.settings.profile(identifier).name}: {hash}" for identifier, hash in selected
            )
            warning = (
                self.tr("Delete these torrents AND their files?")
                if action == "delete"
                else self.tr("Remove these torrents, keeping their files?")
            )
            if (
                QMessageBox.question(self, self.tr("Confirm"), warning + "\n" + names)
                != QMessageBox.StandardButton.Yes
            ):
                return
        groups = defaultdict(list)
        for identifier, hash in selected:
            groups[identifier].append(hash)
        targets = [
            (self.connections.client(self.settings, identifier), hashes)
            for identifier, hashes in groups.items()
        ]
        self.busy = True

        def work():
            return [
                result for client, hashes in targets for result in manage(client, hashes, action)
            ]

        def done(results):
            self.busy = False
            errors = [r.detail for r in results if r.outcome == "failed"]
            if errors:
                QMessageBox.warning(self, self.tr("Torrent action"), "\n".join(errors))
            self.refresh()

        def failed(error):
            self.busy = False
            QMessageBox.warning(self, self.tr("Torrent action"), safe_error(error))

        run_in_background(work, done, failed)
