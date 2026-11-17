"""Owner controls for the shared community journal. No network work on the GUI thread."""

from __future__ import annotations

from typing import Any

from PySide6.QtCore import QDateTime, QTimer
from PySide6.QtGui import QWheelEvent
from PySide6.QtWidgets import (
    QAbstractItemView,
    QCheckBox,
    QComboBox,
    QDateTimeEdit,
    QFormLayout,
    QGroupBox,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QPlainTextEdit,
    QPushButton,
    QScrollArea,
    QSpinBox,
    QTableWidget,
    QTableWidgetItem,
    QTabWidget,
    QVBoxLayout,
    QWidget,
)

from torrentlinker.community import EDITABLE, KINDS, message_library
from torrentlinker.profile_workspace import ProfileWorkspace


class QuietSpin(QSpinBox):
    def wheelEvent(self, event: QWheelEvent) -> None:
        event.ignore()


class CommunityView(QWidget):
    def __init__(self, workspace: ProfileWorkspace):
        super().__init__()
        self.workspace = workspace
        self.data: dict[str, Any] = {}
        self.editing: dict[str, Any] | None = None
        self._account = ""
        self._rows = ""
        outer = QVBoxLayout(self)
        self.notice = QLabel()
        self.notice.setWordWrap(True)
        outer.addWidget(self.notice)
        top = QHBoxLayout()
        self.refresh_button = QPushButton(self.tr("Import seeds && channels"))
        self.refresh_button.clicked.connect(lambda: self.update_action("refresh", {"page": 1}))
        top.addWidget(self.refresh_button)
        self.more_button = QPushButton(self.tr("Next seeds page"))
        self.more_button.clicked.connect(
            lambda: self.update_action("refresh", {"page": self.data.get("page", 1) + 1})
        )
        top.addWidget(self.more_button)
        self.pause_button = QPushButton(self.tr("Pause queue"))
        self.pause_button.clicked.connect(self.toggle_queue)
        top.addWidget(self.pause_button)
        outer.addLayout(top)
        self.pages = QTabWidget()
        outer.addWidget(self.pages)
        page = QWidget()
        layout = QVBoxLayout(page)
        self.quotas = QLabel()
        self.quotas.setWordWrap(True)
        layout.addWidget(self.quotas)
        form = self.form = QFormLayout()
        self.kind = QComboBox()
        for key, name in (("comment", "Comment"), ("vote", "Trust vote"), ("chat", "Chat message")):
            self.kind.addItem(self.tr(name), key)
        self.kind.currentIndexChanged.connect(self.kind_changed)
        form.addRow(self.tr("Action"), self.kind)
        self.candidate = QComboBox()
        self.candidate.setMinimumContentsLength(20)
        self.candidate.setSizeAdjustPolicy(
            QComboBox.SizeAdjustPolicy.AdjustToMinimumContentsLengthWithIcon
        )
        self.candidate.activated.connect(self.select_candidate)
        form.addRow(self.tr("Seeded torrent"), self.candidate)
        self.subject = QLineEdit()
        self.subject.setPlaceholderText(self.tr("40-character torrent info hash"))
        form.addRow(self.tr("Torrent hash"), self.subject)
        self.channel = QComboBox()
        form.addRow(self.tr("Chat channel"), self.channel)
        self.vote = QComboBox()
        self.vote.addItem(self.tr("Choose your vote"), "")
        self.vote.addItem(self.tr("Approve"), "approve")
        self.vote.addItem(self.tr("Disapprove"), "disapprove")
        form.addRow(self.tr("Vote"), self.vote)
        self.content = QPlainTextEdit()
        self.content.setMaximumHeight(100)
        self.content.setPlaceholderText(
            self.tr("Message, or reason for disapproval. Maximum 500 characters.")
        )
        form.addRow(self.tr("Text / reason"), self.content)
        self.suggestions = QComboBox()
        self.suggestions.setMinimumContentsLength(30)
        self.suggestions.setSizeAdjustPolicy(
            QComboBox.SizeAdjustPolicy.AdjustToMinimumContentsLengthWithIcon
        )
        self.library = message_library()
        self.suggestions.addItem(self.tr("French message suggestions"), -1)
        for index, entry in enumerate(self.library):
            self.suggestions.addItem(entry["text"].replace("\n", " ")[:100], index)
        self.suggestions.activated.connect(self.choose_suggestion)
        form.addRow(self.tr("Suggestions"), self.suggestions)
        self.due = QDateTimeEdit(QDateTime.currentDateTime())
        self.due.setCalendarPopup(True)
        self.due.setDisplayFormat("yyyy-MM-dd HH:mm")
        form.addRow(self.tr("Send after (local time)"), self.due)
        layout.addLayout(form)
        buttons = QHBoxLayout()
        for text, callback in (
            ("New draft", self.new_draft),
            ("Use saved template", self.use_template),
            ("Save draft", self.save_draft),
        ):
            button = QPushButton(self.tr(text))
            button.clicked.connect(callback)
            buttons.addWidget(button)
        layout.addLayout(buttons)
        self.table = QTableWidget(0, 5)
        self.table.setHorizontalHeaderLabels(
            [
                self.tr(x)
                for x in ("Action", "Destination / text", "State", "Send after", "Reward / result")
            ]
        )
        self.table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.table.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.table.horizontalHeader().setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)
        self.table.setMinimumHeight(160)
        self.table.horizontalHeader().resizeSection(3, 170)
        self.table.horizontalHeader().resizeSection(4, 150)
        self.table.verticalHeader().setDefaultSectionSize(48)
        layout.addWidget(self.table)
        controls = QHBoxLayout()
        for text, operation in (
            ("Edit selected", "edit"),
            ("Queue selected", "queue"),
            ("Pause selected", "pause"),
            ("Move first", "first"),
            ("Cancel selected", "cancel"),
        ):
            button = QPushButton(self.tr(text))
            button.clicked.connect(lambda _=False, action=operation: self.control(action))
            controls.addWidget(button)
        layout.addLayout(controls)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setWidget(page)
        self.pages.addTab(scroll, self.tr("Waitlist"))
        settings_page = QWidget()
        settings_layout = QVBoxLayout(settings_page)
        self.enabled = QCheckBox(
            self.tr("Run queued actions automatically while the companion is connected")
        )
        settings_layout.addWidget(self.enabled)
        self.limits: dict[str, QSpinBox] = {}
        self.intervals: dict[str, QSpinBox] = {}
        self.templates: dict[str, QPlainTextEdit] = {}
        for kind in KINDS:
            title = {"comment": "Comments", "vote": "Votes", "chat": "Chat messages"}[kind]
            box = QGroupBox(self.tr(title))
            fields = QFormLayout(box)
            limit = self.limits[kind] = QuietSpin()
            limit.setRange(0, 5)
            fields.addRow(self.tr("Maximum per day (0 disables)"), limit)
            interval = self.intervals[kind] = QuietSpin()
            interval.setRange(30, 86400)
            fields.addRow(self.tr("Minimum interval (seconds)"), interval)
            if kind != "vote":
                template = self.templates[kind] = QPlainTextEdit()
                template.setMaximumHeight(80)
                fields.addRow(self.tr("Editable template"), template)
            settings_layout.addWidget(box)
        explanation = QLabel(
            self.tr(
                "Limits apply per action. General and class chat share one limit. "
                "Site limits and slow mode also apply. "
                "Local sends are capped over a rolling 24 hours. "
                "A signed-in c411 tab and a running app/server are required. "
                "Editing a queued item returns it to draft."
            )
        )
        explanation.setWordWrap(True)
        settings_layout.addWidget(explanation)
        save_settings = QPushButton(self.tr("Save settings"))
        save_settings.clicked.connect(self.save_settings)
        settings_layout.addWidget(save_settings)
        settings_layout.addStretch()
        settings_scroll = QScrollArea()
        settings_scroll.setWidgetResizable(True)
        settings_scroll.setWidget(settings_page)
        self.pages.addTab(settings_scroll, self.tr("Settings && templates"))
        self.timer = QTimer(self)
        self.timer.setInterval(2000)
        self.timer.timeout.connect(lambda: self.refresh() if self.isVisible() else None)
        self.timer.start()
        self.refresh()
        self.kind_changed()

    def refresh(self) -> None:
        try:
            data = self.workspace.community_state()
        except ValueError as error:
            self.notice.setText(str(error))
            self.data = {}
            self._account = ""
            self._rows = ""
            self.new_draft()
            self.channel.clear()
            self.candidate.clear()
            self.table.setRowCount(0)
            return
        selected = self.selected()
        selected_id = selected["id"] if selected else None
        changed = self._account != data["account_id"]
        self.data = data
        self.notice.setText(
            self.tr("Connected. Queued actions run when due.")
            if data["paired"]
            else self.tr("Connect the updated companion in Settings → Companion.")
        )
        if data["refresh_pending"]:
            self.notice.setText(self.tr("Waiting for the companion to import seeds and channels…"))
        if changed:
            self._account = data["account_id"]
            self.new_draft()
            settings = data["settings"]
            self.enabled.setChecked(settings["enabled"])
            for kind in KINDS:
                self.limits[kind].setValue(settings["limits"][kind])
                self.intervals[kind].setValue(settings["intervals"][kind])
            for kind in self.templates:
                self.templates[kind].setPlainText(settings["templates"][kind])
        self.pause_button.setText(
            self.tr("Pause queue") if data["settings"]["enabled"] else self.tr("Resume queue")
        )
        self.quotas.setText(
            " · ".join(
                f"{self.tr(k.capitalize())}: {q['local_used']}/{q['limit']} "
                f"{self.tr('local')} · {self.tr('site today')}: "
                f"{q['site_used'] if q['site_used'] is not None else '?'}"
                for k, q in data["quotas"].items()
            )
        )
        signature = str((data["items"], data["candidates"], data["channels"]))
        if signature == self._rows:
            self.update_timers()
            return
        self._rows = signature
        for combo, rows, key, label in (
            (self.candidate, data["candidates"], "hash", "title"),
            (self.channel, data["channels"], "id", "name"),
        ):
            current = combo.currentData()
            combo.clear()
            combo.addItem(self.tr("Choose…"), "")
            for row in rows:
                combo.addItem(row[label], row[key])
            combo.setCurrentIndex(max(0, combo.findData(current)))
        self.table.setRowCount(len(data["items"]))
        for row, item in enumerate(data["items"]):
            reward = (
                self.tr("XP pending, 24 h")
                if item["reward"] == "pending_24h" and data["now"] < item.get("reward_due", 0)
                else self.tr("XP unconfirmed")
            )
            values = [
                self.tr(item["kind"].capitalize()),
                item["title"] + "\n" + item["content"],
                self.tr(item["state"].capitalize()),
                QDateTime.fromSecsSinceEpoch(int(item["due"])).toString("yyyy-MM-dd HH:mm"),
                item["note"] or (reward if item["state"] == "posted" else ""),
            ]
            for col, value in enumerate(values):
                cell = QTableWidgetItem(value)
                cell.setToolTip(value)
                self.table.setItem(row, col, cell)
            if item["id"] == selected_id:
                self.table.selectRow(row)
        self.update_timers()

    def update_timers(self) -> None:
        for row, item in enumerate(self.data.get("items", [])):
            if item["state"] != "queued":
                continue
            seconds = max(0, int(item.get("next_at", item["due"]) - self.data["now"]))
            value = (
                self.tr(item["wait_reason"])
                if item.get("wait_reason")
                else (
                    self.tr("In {0} h {1} min {2} s").format(
                        seconds // 3600, seconds % 3600 // 60, seconds % 60
                    )
                    if seconds
                    else self.tr("Due; waiting for companion")
                )
            )
            self.table.setItem(row, 3, QTableWidgetItem(value))

    def update_action(self, operation: str, data: dict[str, Any]) -> bool:
        try:
            self.workspace.community_update(operation, data)
            self.refresh()
            return True
        except ValueError as error:
            self.notice.setText(str(error))
            return False

    def selected(self) -> dict[str, Any] | None:
        row = self.table.currentRow()
        items = self.data.get("items", [])
        return items[row] if 0 <= row < len(items) else None

    def control(self, operation: str) -> None:
        item = self.selected()
        if not item:
            return
        if operation == "edit":
            if item["state"] not in EDITABLE:
                self.notice.setText(
                    self.tr("Already sending or sent. Check c411 for uncertain results.")
                )
                return
            if not self.update_action(
                "control", {"id": item["id"], "revision": item["revision"], "action": "pause"}
            ):
                return
            item = next(i for i in self.data["items"] if i["id"] == item["id"])
            self.editing = dict(item)
            self.kind.setCurrentIndex(self.kind.findData(item["kind"]))
            self.subject.setText(item["subject"])
            self.channel.setCurrentIndex(max(0, self.channel.findData(item["subject"])))
            self.vote.setCurrentIndex(max(0, self.vote.findData(item["vote"])))
            self.content.setPlainText(item["content"])
            self.due.setDateTime(QDateTime.fromSecsSinceEpoch(int(item["due"])))
        else:
            self.update_action(
                "control", {"id": item["id"], "revision": item["revision"], "action": operation}
            )

    def kind_changed(self) -> None:
        kind = self.kind.currentData()
        self.subject.setEnabled(kind != "chat")
        self.candidate.setEnabled(kind != "chat")
        self.channel.setEnabled(kind == "chat")
        self.suggestions.setEnabled(kind == "chat")
        self.vote.setEnabled(kind == "vote")
        for widget in (self.subject, self.candidate):
            self.form.setRowVisible(widget, kind != "chat")
        self.form.setRowVisible(self.channel, kind == "chat")
        self.form.setRowVisible(self.suggestions, kind == "chat")
        self.form.setRowVisible(self.vote, kind == "vote")

    def select_candidate(self) -> None:
        self.subject.setText(self.candidate.currentData() or "")

    def choose_suggestion(self) -> None:
        index = self.suggestions.currentData()
        if index is not None and index >= 0:
            self.content.setPlainText(self.library[index]["text"])

    def use_template(self) -> None:
        self.content.setPlainText(
            self.data.get("settings", {}).get("templates", {}).get(self.kind.currentData(), "")
        )

    def new_draft(self) -> None:
        self.editing = None
        self.subject.clear()
        self.content.clear()
        self.vote.setCurrentIndex(0)
        self.due.setDateTime(QDateTime.currentDateTime())

    def save_draft(self) -> None:
        kind = self.kind.currentData()
        subject = self.channel.currentData() if kind == "chat" else self.subject.text().strip()
        title = (
            self.channel.currentText()
            if kind == "chat"
            else next(
                (c["title"] for c in self.data.get("candidates", []) if c["hash"] == subject),
                subject,
            )
        )
        data = {
            "kind": kind,
            "subject": subject,
            "title": title,
            "content": self.content.toPlainText(),
            "vote": self.vote.currentData(),
            "due": self.due.dateTime().toSecsSinceEpoch(),
        }
        if self.editing:
            data.update(id=self.editing["id"], revision=self.editing["revision"])
        if self.update_action("save", data):
            self.new_draft()

    def save_settings(self) -> None:
        self.update_action(
            "settings",
            {
                "enabled": self.enabled.isChecked(),
                "limits": {k: s.value() for k, s in self.limits.items()},
                "intervals": {k: s.value() for k, s in self.intervals.items()},
                "templates": {k: s.toPlainText() for k, s in self.templates.items()},
            },
        )

    def toggle_queue(self) -> None:
        if self.data:
            settings = {**self.data["settings"], "enabled": not self.data["settings"]["enabled"]}
            if self.update_action("settings", settings):
                self.enabled.setChecked(settings["enabled"])
