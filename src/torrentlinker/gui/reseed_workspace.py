"""Native column board backed by the same workflow as the Web UI."""

from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QApplication,
    QCheckBox,
    QDialog,
    QDialogButtonBox,
    QFileDialog,
    QFrame,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMessageBox,
    QProgressBar,
    QPushButton,
    QScrollArea,
    QVBoxLayout,
    QWidget,
)

from torrentlinker.agent.desktop import DesktopSetup
from torrentlinker.gui.tasks import run_with_progress
from torrentlinker.reseed_workspace import ReseedWorkspace
from torrentlinker.web.jobs import JobStop
from torrentlinker.web.library import Library


class ReseedBoard(QWidget):
    def __init__(self, window):
        super().__init__()
        self.owner = window
        self.rows: list[dict] = []
        self.selected: set[str] = set()
        self.stop = JobStop()
        self.running = False
        self.active_path = ""
        self.limits: dict[str, int] = {}
        layout = QVBoxLayout(self)
        heading = QHBoxLayout()
        title = QLabel(self.tr("Reseed"))
        title.setStyleSheet("font-size: 23px; font-weight: 600")
        heading.addWidget(title, 1)
        self.run_all = QPushButton(self.tr("Run all steps"))
        self.run_all.setProperty("role", "primary")
        self.run_all.setObjectName("reseedRunAll")
        self.run_all.clicked.connect(lambda: self.confirm(self.visible_rows(), "all"))
        scan = QPushButton(self.tr("Scan folders"))
        scan.clicked.connect(lambda: window.scan())
        refresh = QPushButton(self.tr("Refresh"))
        refresh.clicked.connect(lambda: self.start([], "refresh"))
        self.navigation_actions = [refresh, scan]
        heading.addWidget(refresh)
        heading.addWidget(scan)
        heading.addWidget(self.run_all)
        layout.addLayout(heading)
        note = QLabel(
            self.tr(
                "Expected names, without renaming originals. A persistent symlink folder "
                "reproduces the torrent layout. Keep it while seeding."
            )
        )
        note.setWordWrap(True)
        note.setStyleSheet("padding: 14px; border-left: 3px solid #55bcb9;")
        layout.addWidget(note)
        layout.addWidget(
            QLabel(self.tr("Link folder (outside Library Folders, accessible to the client)"))
        )
        root_row = QHBoxLayout()
        self.root = QLineEdit()
        self.root.setObjectName("reseedLinkRoot")
        self.root.setPlaceholderText("/media/reseed-links")
        root_row.addWidget(self.root, 1)
        self.browse = QPushButton(self.tr("Browse…"))
        self.browse.clicked.connect(self.choose_root)
        root_row.addWidget(self.browse)
        layout.addLayout(root_row)
        toolbar = QHBoxLayout()
        self.query = QLineEdit()
        self.query.setPlaceholderText(self.tr("Filter items…"))
        self.query.textChanged.connect(self.render)
        toolbar.addWidget(self.query, 1)
        self.count = QLabel()
        toolbar.addWidget(self.count)
        select = QPushButton(self.tr("Select visible"))
        select.clicked.connect(self.select_visible)
        toolbar.addWidget(select)
        self.run_selected = QPushButton(self.tr("Run selected"))
        self.run_selected.clicked.connect(
            lambda: self.confirm([r for r in self.rows if r["path"] in self.selected], "all")
        )
        toolbar.addWidget(self.run_selected)
        layout.addLayout(toolbar)
        self.progress_panel = QWidget()
        progress_layout = QVBoxLayout(self.progress_panel)
        self.status = QLabel()
        self.status.setWordWrap(True)
        progress_layout.addWidget(self.status)
        progress_row = QHBoxLayout()
        self.meter = QProgressBar()
        progress_row.addWidget(self.meter, 1)
        self.pause = QPushButton(self.tr("Pause"))
        self.pause.clicked.connect(self.toggle_pause)
        self.cancel = QPushButton(self.tr("Stop"))
        self.cancel.clicked.connect(self.stop_run)
        progress_row.addWidget(self.pause)
        progress_row.addWidget(self.cancel)
        progress_layout.addLayout(progress_row)
        hint = QLabel(
            self.tr(
                "Pause stops between operations. A client hash check may finish while paused. "
                "Completed steps are kept."
            )
        )
        hint.setWordWrap(True)
        progress_layout.addWidget(hint)
        self.progress_panel.hide()
        layout.addWidget(self.progress_panel)
        self.scroll = QScrollArea()
        self.scroll.setWidgetResizable(True)
        self.scroll.setMinimumHeight(380)
        layout.addWidget(self.scroll, 1)
        window.library_model.modelReset.connect(self.refresh)
        app = QApplication.instance()
        if app:
            app.aboutToQuit.connect(self.stop_run)
        self.refresh()

    def service(self):
        setup = DesktopSetup(
            self.owner.settings,
            self.owner.settings_path,
            self.owner.store.for_client(self.owner.settings.active_client_id),
        )
        # The local owner chooses this folder directly, as in the download-folder picker.
        if self.root.text().strip():
            from pathlib import Path

            setup._folder_candidate = Path(self.root.text().strip())
        library = Library(setup)
        library.entries = list(self.owner.library_model.entries)
        return ReseedWorkspace(library)

    def refresh(self):
        if not self.running:
            self.rows = self.service().rows()
            if not self.root.text():
                from pathlib import Path

                previous = next(
                    (r.get("destination") for r in self.rows if r.get("destination")), None
                )
                if previous:
                    self.root.setText(str(Path(previous).parent))
            self.render()

    def choose_root(self):
        chosen = QFileDialog.getExistingDirectory(self, self.tr("Link folder"), self.root.text())
        if chosen:
            self.root.setText(chosen)

    def visible_rows(self):
        return [r for r in self.rows if self.query.text().casefold() in r["name"].casefold()]

    def stages(self):
        return [
            ("find", self.tr("To find"), "#63a4d0", self.tr("Search for compatible torrents")),
            (
                "candidates",
                self.tr("Candidates found"),
                "#55bcb9",
                self.tr("Compare files before proceeding"),
            ),
            (
                "links",
                self.tr("Links to prepare"),
                "#5cc9a5",
                self.tr("Expected names, originals preserved"),
            ),
            (
                "verify",
                self.tr("Verification"),
                "#72bf80",
                self.tr("Check the bytes before seeding"),
            ),
            (
                "incomplete",
                self.tr("Needs attention"),
                "#d5a449",
                self.tr("Review missing files and archives"),
            ),
            ("seeding", self.tr("Seeding"), "#60bd67", self.tr("Keep the links while seeding")),
        ]

    def render(self):
        offset = self.scroll.horizontalScrollBar().value()
        body = QWidget()
        columns = QHBoxLayout(body)
        rows = self.visible_rows()
        for stage, name, color, hint in self.stages():
            lane = QFrame()
            lane.setObjectName(f"reseedLane{stage}")
            lane.setMinimumWidth(170)
            lane.setStyleSheet(
                f"QFrame#reseedLane{stage} {{border: 1px solid {color}; "
                f"border-top: 3px solid {color}; border-radius: 10px;}}"
            )
            stack = QVBoxLayout(lane)
            entries = [r for r in rows if r["stage"] == stage]
            title = QLabel(f"{name} · {len(entries)}")
            title.setStyleSheet("font-weight: 600; padding: 5px")
            stack.addWidget(title)
            subtitle = QLabel(hint)
            subtitle.setWordWrap(True)
            stack.addWidget(subtitle)
            limit = self.limits.get(stage, 50)
            for row in entries[:limit]:
                card = QFrame()
                card.setObjectName("reseedCard")
                active = self.running and row["path"] == self.active_path
                card.setStyleSheet(
                    f"QFrame#reseedCard {{border: {2 if active else 1}px solid {color}; "
                    "border-radius: 8px;}"
                )
                box = QVBoxLayout(card)
                check = QCheckBox()
                check.setAccessibleName(row["name"])
                check.setToolTip(row["path"])
                check.setChecked(row["path"] in self.selected)
                check.setEnabled(not self.running and stage != "seeding")
                check.toggled.connect(lambda enabled, path=row["path"]: self.select(path, enabled))
                heading = QHBoxLayout()
                heading.addWidget(check, 0, Qt.AlignmentFlag.AlignTop)
                name = QLabel(row["name"])
                name.setTextFormat(Qt.TextFormat.PlainText)
                name.setWordWrap(True)
                heading.addWidget(name, 1)
                box.addLayout(heading)
                if row.get("status") == "verified":
                    box.addWidget(QLabel(self.tr("Verified")))
                if row.get("error"):
                    error = QLabel(row["error"])
                    error.setWordWrap(True)
                    error.setMaximumWidth(240)
                    box.addWidget(error)
                inspect = QPushButton(self.tr("Inspect"))
                inspect.clicked.connect(lambda _=False, value=row: self.inspect(value))
                box.addWidget(inspect)
                if active:
                    busy = QProgressBar()
                    busy.setRange(0, 0)
                    busy.setFixedHeight(5)
                    box.addWidget(busy)
                stack.addWidget(card)
            if len(entries) > limit:
                more = QPushButton(self.tr("Show more"))
                more.clicked.connect(lambda _=False, key=stage: self.show_more(key))
                stack.addWidget(more)
            if not entries:
                stack.addWidget(QLabel(self.tr("Nothing here yet")))
            stack.addStretch()
            columns.addWidget(lane)
        self.scroll.setWidget(body)
        self.scroll.horizontalScrollBar().setValue(offset)
        self.run_all.setEnabled(
            not self.running and any(r["stage"] not in {"seeding", "incomplete"} for r in rows)
        )
        for control in self.navigation_actions:
            control.setEnabled(not self.running)
        self.root.setEnabled(not self.running)
        self.browse.setEnabled(not self.running)
        self.selection_changed()

    def show_more(self, stage):
        self.limits[stage] = self.limits.get(stage, 50) + 50
        self.render()

    def select(self, path, enabled):
        self.selected.add(path) if enabled else self.selected.discard(path)
        self.selection_changed()

    def selection_changed(self):
        self.count.setText(self.tr("{0} selected").format(len(self.selected)))
        self.run_selected.setEnabled(not self.running and bool(self.selected))

    def select_visible(self):
        if not self.running:
            self.selected.update(
                r["path"]
                for r in self.visible_rows()
                if r["stage"] not in {"seeding", "incomplete"}
            )
            self.render()

    def action_label(self, action):
        return {
            "all": self.tr("Run all steps"),
            "find": self.tr("Find candidates"),
            "compare": self.tr("Compare"),
            "prepare": self.tr("Prepare links"),
            "verify": self.tr("Verify"),
            "seed": self.tr("Start seeding"),
        }[action]

    def inspect(self, row):
        dialog = QDialog(self)
        dialog.setWindowTitle(row["name"])
        dialog.resize(700, 550)
        layout = QVBoxLayout(dialog)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        body = QWidget()
        details = QVBoxLayout(body)

        def text(value):
            label = QLabel(value)
            label.setTextFormat(Qt.TextFormat.PlainText)
            label.setWordWrap(True)
            label.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
            details.addWidget(label)

        text(row["path"])
        if row.get("destination"):
            text(self.tr("Persistent link folder: ") + row["destination"])
        for f in row.get("files", []):
            text(f"{f['source']}\n↳ {f['name']}\n→ {f['target']}")
        for candidate in row.get("candidates", []):
            text(candidate["title"] + " · " + candidate["match"])
            for f in candidate.get("files", []):
                text(f"{f['local'] or self.tr('Missing')}\n↳ {f['torrent']}")
            if (
                row["stage"] == "candidates"
                and candidate["match"] == "full"
                and not candidate["archive"]
            ):
                choose = QPushButton(self.tr("Use this Candidate"))
                choose.setEnabled(not self.running)
                choose.clicked.connect(
                    lambda _=False, key=candidate["key"]: (
                        dialog.accept(),
                        self.start([row["path"]], "compare", key),
                    )
                )
                details.addWidget(choose)
        if row.get("error"):
            text(row["error"])
        if row["stage"] == "incomplete":
            text(
                self.tr(
                    "Open this Local Item in the Local Items tab "
                    "to review missing files or archives."
                )
            )
        scroll.setWidget(body)
        layout.addWidget(scroll)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Close)
        buttons.rejected.connect(dialog.reject)
        action = {
            "find": "find",
            "candidates": "compare",
            "links": "prepare",
            "verify": "seed" if row.get("status") == "verified" else "verify",
        }.get(row["stage"])
        if action:
            go = buttons.addButton(
                self.action_label(action), QDialogButtonBox.ButtonRole.ActionRole
            )
            go.setEnabled(not self.running)
            go.clicked.connect(lambda: (dialog.accept(), self.confirm([row], action)))
        if row.get("error") and row["stage"] != "seeding":
            retry = buttons.addButton(
                self.tr("Compare again"), QDialogButtonBox.ButtonRole.ActionRole
            )
            retry.setEnabled(not self.running)
            retry.clicked.connect(lambda: (dialog.accept(), self.start([row["path"]], "compare")))
        layout.addWidget(buttons)
        dialog.exec()

    def confirm(self, rows, action):
        paths = [r["path"] for r in rows if r["stage"] not in {"seeding", "incomplete"}]
        if not paths:
            return
        if action in {"all", "prepare"} and not self.root.text().strip():
            self.root.setFocus()
            QMessageBox.information(
                self,
                self.tr("Link folder"),
                self.tr("Choose an existing link folder outside the Library."),
            )
            return
        message = self.tr(
            "{0} Local Item(s). Originals keep their names. Keep the link folder while seeding."
        ).format(len(paths))
        if action == "all":
            message += "\n\n" + self.tr(
                "Find → Compare → Prepare links → Verify → Seed. "
                "Incomplete or ambiguous matches stop for review."
            )
        if (
            QMessageBox.question(
                self, self.action_label(action), message + "\n\n" + self.root.text()
            )
            == QMessageBox.StandardButton.Yes
        ):
            self.start(paths, action)

    def start(self, paths, action, key=""):
        if self.running:
            return
        service, root = self.service(), self.root.text().strip()
        if action != "refresh":
            service.setup._indexer = self.owner.indexer()
        self.stop = JobStop()
        self.running = True
        self.progress_panel.show()
        self.pause.setEnabled(True)
        self.cancel.setEnabled(True)
        self.pause.setText(self.tr("Pause"))
        self.render()
        run_with_progress(
            lambda report: service.run(paths, action, root, self.stop, report, key),
            self.progress,
            self.finished,
            self.failed,
        )

    def progress(self, value):
        phase = value.get("phase", "")
        label = {
            "local-hash": self.tr("Checking original files"),
            "client-hash": self.tr("Client verification"),
            "item-complete": self.tr("Item processed"),
        }.get(phase)
        if label is None:
            label = self.action_label(phase)
        self.status.setText(
            f"{label} · {value.get('done', 0)}/{value.get('total', 0)}\n{value.get('path', '')}"
        )
        fraction = value.get("fraction")
        if fraction is not None:
            self.meter.setRange(0, 100)
            self.meter.setValue(round(fraction * 100))
        else:
            self.meter.setRange(0, max(1, value.get("total", 1)))
            self.meter.setValue(value.get("done", 0))
        changed = self.active_path != value.get("path", "") or "rows" in value
        self.active_path = value.get("path", "")
        if "rows" in value:
            self.rows = value["rows"]
        if changed:
            self.render()

    def toggle_pause(self):
        if self.stop.paused.is_set():
            self.stop.paused.clear()
            self.pause.setText(self.tr("Pause"))
        else:
            self.stop.paused.set()
            self.pause.setText(self.tr("Resume"))
            self.status.setText(self.tr("Pause requested. Waiting for the current operation."))

    def stop_run(self):
        self.stop.set()

    def finished(self, result):
        self.running = False
        self.pause.setEnabled(False)
        self.cancel.setEnabled(False)
        self.rows = result["rows"]
        self.selected.clear()
        self.status.setText(
            self.tr("Run stopped. Completed steps are kept.")
            if result.get("cancelled")
            else self.tr("Finished · {0} seeded · {1} error(s)").format(
                result["seeded"], len(result["errors"])
            )
        )
        self.render()

    def failed(self, error):
        self.running = False
        self.pause.setEnabled(False)
        self.cancel.setEnabled(False)
        self.status.setText(str(error))
        self.refresh()
