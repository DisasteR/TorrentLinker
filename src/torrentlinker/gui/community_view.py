"""Owner controls for the shared community journal. No network work on the GUI thread."""

from __future__ import annotations

from typing import Any

from PySide6.QtCore import QDate, QDateTime, Qt, QTime, QTimer, QTimeZone
from PySide6.QtGui import QWheelEvent
from PySide6.QtWidgets import (
    QAbstractItemView,
    QCalendarWidget,
    QCheckBox,
    QComboBox,
    QDateEdit,
    QDateTimeEdit,
    QDialog,
    QFormLayout,
    QGroupBox,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QListWidget,
    QPlainTextEdit,
    QPushButton,
    QScrollArea,
    QSpinBox,
    QSplitter,
    QTableWidget,
    QTableWidgetItem,
    QTabWidget,
    QTimeEdit,
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
        self._visible_items: list[dict[str, Any]] = []
        outer = QVBoxLayout(self)
        self.notice = QLabel()
        self.notice.setWordWrap(True)
        outer.addWidget(self.notice)
        top = QHBoxLayout()
        self.refresh_button = QPushButton(self.tr("Refresh library"))
        self.refresh_button.clicked.connect(lambda: self.update_action("refresh", {"page": 1}))
        top.addWidget(self.refresh_button)
        self.pause_button = QPushButton(self.tr("Pause queue"))
        self.pause_button.clicked.connect(self.toggle_queue)
        top.addWidget(self.pause_button)
        outer.addLayout(top)
        self.pages = QTabWidget()
        outer.addWidget(self.pages)
        self.build_library()
        page = QWidget()
        layout = QVBoxLayout(page)
        self.quotas = QLabel()
        self.quotas.setWordWrap(True)
        outer.insertWidget(1, self.quotas)
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
        auto_vote_hint = QLabel(
            self.tr(
                "When automatic votes are enabled, saved votes are scheduled "
                "using your choice and date."
            )
        )
        auto_vote_hint.setWordWrap(True)
        layout.addWidget(auto_vote_hint)
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
        queue_page = QWidget()
        queue_layout = QVBoxLayout(queue_page)
        queue_layout.addWidget(self.table)
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
        queue_layout.addLayout(controls)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setWidget(page)
        layout.addStretch()
        self.pages.addTab(scroll, self.tr("Chat / draft"))
        self.pages.addTab(queue_page, self.tr("Waitlist"))
        self.build_calendar()
        self.build_history()
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
        self.auto_enabled = QCheckBox(self.tr("Plan automatically"))
        settings_layout.addWidget(self.auto_enabled)
        self.auto_kinds = {}
        for key, label in (
            ("comment", "Automatic comments from my template"),
            ("vote", "Schedule my prepared votes"),
            ("chat", "Automatic chat from my selected messages"),
        ):
            checkbox = self.auto_kinds[key] = QCheckBox(self.tr(label))
            settings_layout.addWidget(checkbox)
        auto_form = QFormLayout()
        self.auto_start = QTimeEdit(QTime(9, 0))
        self.auto_end = QTimeEdit(QTime(19, 0))
        self.auto_timezone = QLineEdit(bytes(QTimeZone.systemTimeZoneId().data()).decode())
        auto_form.addRow(self.tr("Publication starts"), self.auto_start)
        auto_form.addRow(self.tr("Publication ends"), self.auto_end)
        auto_form.addRow(self.tr("Timezone"), self.auto_timezone)
        self.auto_general = QCheckBox(self.tr("General channel"))
        self.auto_class = QCheckBox(self.tr("Class channel"))
        auto_form.addRow(self.auto_general)
        auto_form.addRow(self.auto_class)
        self.auto_messages = QPlainTextEdit()
        self.auto_messages.setPlaceholderText(
            self.tr("One selected message per line. At least five words each.")
        )
        auto_form.addRow(self.tr("Selected chat messages"), self.auto_messages)
        add_suggestion = QPushButton(self.tr("Add current suggestion to automatic chat"))
        add_suggestion.clicked.connect(self.add_auto_suggestion)
        auto_form.addRow(add_suggestion)
        settings_layout.addLayout(auto_form)
        auto_help = QLabel(
            self.tr(
                "Plans up to seven days ahead. Comments use {titre}. Votes keep "
                "your prepared choice. Disabling an action cancels only its "
                "automatic slots. Keep the companion connected; calendar times are estimates."
            )
        )
        auto_help.setWordWrap(True)
        settings_layout.addWidget(auto_help)
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
            self.clear_account_views()
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
            self.clear_account_views()
            self._account = data["account_id"]
            self.new_draft()
            settings = data["settings"]
            self.enabled.setChecked(settings["enabled"])
            for kind in KINDS:
                self.limits[kind].setValue(settings["limits"][kind])
                self.intervals[kind].setValue(settings["intervals"][kind])
            for kind in self.templates:
                self.templates[kind].setPlainText(settings["templates"][kind])
            config = settings["automation"]
            self.auto_enabled.setChecked(config["enabled"])
            for kind, checkbox in self.auto_kinds.items():
                checkbox.setChecked(config["kinds"][kind])
            self.auto_start.setTime(QTime.fromString(config["start"], "HH:mm"))
            self.auto_end.setTime(QTime.fromString(config["end"], "HH:mm"))
            self.auto_timezone.setText(config["timezone"])
            self.auto_general.setChecked("general" in config["channels"])
            self.auto_class.setChecked("class" in config["channels"])
            self.auto_messages.setPlainText("\n".join(config["messages"]))
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
        signature = str(
            (
                [{k: v for k, v in i.items() if k != "next_at"} for i in data["items"]],
                data["candidates"],
                data["channels"],
                data["settings"],
            )
        )
        if signature == self._rows:
            self.update_timers()
            return
        self._rows = signature
        self.render_library()
        self.render_calendar()
        self.render_history()
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
        self._visible_items = [
            i for i in data["items"] if i["state"] not in ("posted", "cancelled")
        ]
        self.table.setRowCount(len(self._visible_items))
        for row, item in enumerate(self._visible_items):
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
        for row, item in enumerate(self._visible_items):
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
        items = self._visible_items
        return items[row] if 0 <= row < len(items) else None

    def control(self, operation: str, item: dict[str, Any] | None = None) -> None:
        item = item or self.selected()
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
            self.pages.setCurrentIndex(1)
            self.content.setFocus()
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
            self.pages.setCurrentIndex(2)

    def save_settings(self) -> None:
        self.update_action(
            "settings",
            {
                "enabled": self.enabled.isChecked(),
                "limits": {k: s.value() for k, s in self.limits.items()},
                "intervals": {k: s.value() for k, s in self.intervals.items()},
                "templates": {k: s.toPlainText() for k, s in self.templates.items()},
                "automation": {
                    "enabled": self.auto_enabled.isChecked(),
                    "kinds": {k: c.isChecked() for k, c in self.auto_kinds.items()},
                    "start": self.auto_start.time().toString("HH:mm"),
                    "end": self.auto_end.time().toString("HH:mm"),
                    "timezone": self.auto_timezone.text().strip(),
                    "channels": [
                        k
                        for k, c in (("general", self.auto_general), ("class", self.auto_class))
                        if c.isChecked()
                    ],
                    "messages": [
                        m.strip()
                        for m in self.auto_messages.toPlainText().splitlines()
                        if m.strip()
                    ],
                },
            },
        )

    def toggle_queue(self) -> None:
        if self.data:
            settings = {**self.data["settings"], "enabled": not self.data["settings"]["enabled"]}
            if self.update_action("settings", settings):
                self.enabled.setChecked(settings["enabled"])

    def build_library(self) -> None:
        page = QWidget()
        layout = QVBoxLayout(page)
        title = QLabel(self.tr("Your torrents, ready to contribute"))
        title.setObjectName("pageHeading")
        layout.addWidget(title)
        hint = QLabel(
            self.tr(
                "Seeds load automatically through the companion. Choose a "
                "torrent to see its actions."
            )
        )
        hint.setWordWrap(True)
        layout.addWidget(hint)
        self.library_search = QLineEdit()
        self.library_search.setPlaceholderText(self.tr("Search torrents…"))
        self.library_search.textChanged.connect(self.render_library)
        layout.addWidget(self.library_search)
        self.library_split = QSplitter(Qt.Orientation.Horizontal)
        self.library_rows = QListWidget()
        self.library_rows.setSpacing(6)
        self.library_rows.setStyleSheet("QListWidget::item { padding: 14px; border-radius: 7px; }")
        self.library_rows.currentRowChanged.connect(self.library_selected)
        self.library_split.addWidget(self.library_rows)
        detail = QWidget()
        detail_layout = QVBoxLayout(detail)
        self.library_detail = QLabel()
        self.library_detail.setWordWrap(True)
        detail_layout.addWidget(self.library_detail)
        detail_layout.addStretch()
        buttons = QVBoxLayout()
        for kind, label in (
            ("comment", "Write a comment"),
            ("vote", "Prepare a vote"),
            ("chat", "Write in chat"),
        ):
            button = QPushButton(self.tr(label))
            button.clicked.connect(lambda _=False, k=kind: self.prepare_candidate(k))
            buttons.addWidget(button)
        detail_layout.addLayout(buttons)
        self.library_split.addWidget(detail)
        self.library_split.setStretchFactor(0, 3)
        self.library_split.setStretchFactor(1, 2)
        layout.addWidget(self.library_split)
        self._library_candidates: list[dict[str, Any]] = []
        self._library_signature = ""
        self.pages.addTab(page, self.tr("Torrents"))

    def render_library(self) -> None:
        search = self.library_search.text().casefold()
        rows = [
            c
            for c in self.data.get("candidates", [])
            if search in (c["title"] + c["hash"]).casefold()
        ]
        signature = str(
            (rows, [(i["id"], i["state"], i["revision"]) for i in self.data.get("items", [])])
        )
        if signature == self._library_signature:
            return
        self._library_signature = signature
        selected = self.library_rows.currentRow()
        selected_hash = (
            self._library_candidates[selected]["hash"]
            if 0 <= selected < len(self._library_candidates)
            else None
        )
        activity: dict[str, list[dict[str, Any]]] = {}
        for item in self.data.get("items", []):
            activity.setdefault(item["subject"], []).append(item)
        self._library_candidates = rows
        scroll = self.library_rows.verticalScrollBar().value()
        self.library_rows.clear()
        for candidate in rows:
            actions = [
                self.tr(i["kind"].capitalize()) + ": " + self.tr(i["state"].capitalize())
                for i in activity.get(candidate["hash"], [])
            ]
            self.library_rows.addItem(
                candidate["title"] + "\n" + (" · ".join(actions) or self.tr("No local action yet"))
            )
        if rows:
            self.library_rows.setCurrentRow(
                next((n for n, c in enumerate(rows) if c["hash"] == selected_hash), 0)
            )
        else:
            self.library_detail.setText(
                self.tr(
                    "No matching torrents. Discovery continues while the companion is connected."
                )
            )

        self.library_rows.verticalScrollBar().setValue(scroll)

    def library_selected(self) -> None:
        row = self.library_rows.currentRow()
        if 0 <= row < len(self._library_candidates):
            candidate = self._library_candidates[row]
            items = [i for i in self.data.get("items", []) if i["subject"] == candidate["hash"]]
            self.library_detail.setText(
                candidate["title"]
                + "\n"
                + candidate["hash"]
                + "\n"
                + "\n".join(i["content"] for i in items)
            )

    def prepare_candidate(self, kind: str) -> None:
        row = self.library_rows.currentRow()
        self.new_draft()
        self.kind.setCurrentIndex(self.kind.findData(kind))
        if kind != "chat" and 0 <= row < len(self._library_candidates):
            candidate = self._library_candidates[row]
            self.subject.setText(candidate["hash"])
            self.candidate.setCurrentIndex(self.candidate.findData(candidate["hash"]))
        self.pages.setCurrentIndex(1)
        self.content.setFocus()

    def build_calendar(self) -> None:
        page = QWidget()
        layout = QVBoxLayout(page)
        self.calendar = QCalendarWidget()
        self.calendar.selectionChanged.connect(self.render_calendar)
        self.calendar.currentPageChanged.connect(self.calendar_page_changed)
        layout.addWidget(self.calendar)
        self.calendar_scale = QComboBox()
        for label, key in (("Selected day", "day"), ("Week", "week"), ("Month", "month")):
            self.calendar_scale.addItem(self.tr(label), key)
        self.calendar_scale.currentIndexChanged.connect(self.render_calendar)
        layout.addWidget(self.calendar_scale)
        self.calendar_rows = QListWidget()
        self.calendar_rows.itemActivated.connect(lambda _: self.calendar_detail())
        layout.addWidget(self.calendar_rows)
        inspect = QPushButton(self.tr("Inspect selected operation"))
        inspect.clicked.connect(self.calendar_detail)
        layout.addWidget(inspect)
        layout.addWidget(
            QLabel(
                self.tr(
                    "Estimated local times. Site quotas and companion "
                    "availability can delay sending."
                )
            )
        )
        self._calendar_items: list[dict[str, Any]] = []
        self.pages.addTab(page, self.tr("Calendar"))

    def calendar_page_changed(self, year: int, month: int) -> None:
        selected = self.calendar.selectedDate()
        if selected.year() != year or selected.month() != month:
            self.calendar.setSelectedDate(QDate(year, month, 1))

    def render_calendar(self) -> None:
        from PySide6.QtGui import QColor, QTextCharFormat

        self.calendar.setDateTextFormat(QDate(), QTextCharFormat())
        scheduled = [
            i for i in self.data.get("items", []) if i["state"] in ("queued", "paused", "sending")
        ]
        for item in scheduled:
            fmt = QTextCharFormat()
            fmt.setBackground(QColor("#24575b"))
            fmt.setForeground(QColor("#ffffff"))
            self.calendar.setDateTextFormat(
                QDateTime.fromSecsSinceEpoch(int(item.get("next_at", item["due"]))).date(), fmt
            )
        start = self.calendar.selectedDate()
        scale = self.calendar_scale.currentData()
        if scale == "week":
            start = start.addDays(1 - start.dayOfWeek())
        elif scale == "month":
            start = QDate(start.year(), start.month(), 1)
        end = (
            start.addDays(7)
            if scale == "week"
            else start.addMonths(1)
            if scale == "month"
            else start.addDays(1)
        )
        self._calendar_items = sorted(
            [
                i
                for i in scheduled
                if start
                <= QDateTime.fromSecsSinceEpoch(int(i.get("next_at", i["due"]))).date()
                < end
            ],
            key=lambda i: i.get("next_at", i["due"]),
        )
        selected_id = (
            self.calendar_rows.currentItem().data(256) if self.calendar_rows.currentItem() else None
        )
        scroll = self.calendar_rows.verticalScrollBar().value()
        self.calendar_rows.clear()
        for item in self._calendar_items:
            when = QDateTime.fromSecsSinceEpoch(int(item.get("next_at", item["due"]))).toString(
                "dd MMM HH:mm"
            )
            self.calendar_rows.addItem(
                f"{when}  {self.tr(item['kind'].capitalize())} · {item['title']}\n"
                f"{self.tr(item['source'].capitalize())} · {self.tr(item['state'].capitalize())}"
            )

            entry = self.calendar_rows.item(self.calendar_rows.count() - 1)
            entry.setData(256, item["id"])
            if item["id"] == selected_id:
                self.calendar_rows.setCurrentItem(entry)
        self.calendar_rows.verticalScrollBar().setValue(scroll)

    def calendar_detail(self) -> None:
        index = self.calendar_rows.currentRow()
        if 0 <= index < len(self._calendar_items):
            self.show_operation(self._calendar_items[index])

    def build_history(self) -> None:
        page = QWidget()
        layout = QVBoxLayout(page)
        self.history_search = QLineEdit()
        self.history_search.setPlaceholderText(self.tr("Search titles, messages or results…"))
        self.history_search.textChanged.connect(self.render_history)
        layout.addWidget(self.history_search)
        filters = QHBoxLayout()
        self.history_kind = QComboBox()
        self.history_kind.addItem(self.tr("All actions"), "")
        for kind in KINDS:
            self.history_kind.addItem(self.tr(kind.capitalize()), kind)
        self.history_kind.currentIndexChanged.connect(self.render_history)
        filters.addWidget(self.history_kind)
        self.history_period = QComboBox()
        for label, days in (
            ("All time", 0),
            ("Today", 1),
            ("7 days", 7),
            ("30 days", 30),
            ("Custom dates", -1),
        ):
            self.history_period.addItem(self.tr(label), days)
        self.history_period.currentIndexChanged.connect(self.render_history)
        filters.addWidget(self.history_period)
        self.history_from = QDateEdit(QDate.currentDate().addMonths(-1))
        self.history_to = QDateEdit(QDate.currentDate())
        for field in (self.history_from, self.history_to):
            field.setCalendarPopup(True)
            field.dateChanged.connect(self.render_history)
            filters.addWidget(field)
        layout.addLayout(filters)
        self.history_timeline = QCalendarWidget()
        self.history_timeline.setMaximumHeight(230)
        self.history_timeline.clicked.connect(self.history_day)
        layout.addWidget(self.history_timeline)
        self.history_rows = QListWidget()
        self.history_rows.itemActivated.connect(lambda _: self.history_detail())
        layout.addWidget(self.history_rows)
        inspect = QPushButton(self.tr("View operation history"))
        inspect.clicked.connect(self.history_detail)
        layout.addWidget(inspect)
        self._history_items: list[dict[str, Any]] = []
        self.pages.addTab(page, self.tr("History"))

    def history_day(self, day: QDate) -> None:
        self.history_from.setDate(day)
        self.history_to.setDate(day)
        self.history_period.setCurrentIndex(self.history_period.findData(-1))
        self.render_history()

    def render_history(self) -> None:
        from PySide6.QtGui import QColor, QTextCharFormat

        if not hasattr(self, "history_rows"):
            return
        days = self.history_period.currentData()
        for field in (self.history_from, self.history_to):
            field.setEnabled(days == -1)
        query = self.history_search.text().casefold()
        kind = self.history_kind.currentData()
        self._history_items = []
        selected_id = (
            self.history_rows.currentItem().data(256) if self.history_rows.currentItem() else None
        )
        scroll = self.history_rows.verticalScrollBar().value()
        self.history_rows.clear()
        self.history_timeline.setDateTextFormat(QDate(), QTextCharFormat())
        for item in sorted(
            self.data.get("items", []), key=lambda i: i.get("updated", i["created"]), reverse=True
        ):
            if kind and item["kind"] != kind:
                continue
            if query not in (item["title"] + item["content"] + item["note"]).casefold():
                continue
            day = QDateTime.fromSecsSinceEpoch(int(item.get("updated", item["created"]))).date()
            fmt = QTextCharFormat()
            fmt.setBackground(QColor("#24575b"))
            self.history_timeline.setDateTextFormat(day, fmt)
            if days == -1 and not self.history_from.date() <= day <= self.history_to.date():
                continue
            if days > 0 and day < QDate.currentDate().addDays(1 - days):
                continue
            self._history_items.append(item)
            self.history_rows.addItem(
                f"{day.toString('dd MMM yyyy')} · {self.tr(item['kind'].capitalize())} · "
                f"{self.tr(item['state'].capitalize())}\n{item['title']}\n{item['content']}"
            )

            entry = self.history_rows.item(self.history_rows.count() - 1)
            entry.setData(256, item["id"])
            if item["id"] == selected_id:
                self.history_rows.setCurrentItem(entry)
        self.history_rows.verticalScrollBar().setValue(scroll)

    def history_detail(self) -> None:
        index = self.history_rows.currentRow()
        if 0 <= index < len(self._history_items):
            self.show_operation(self._history_items[index])

    def show_operation(self, item: dict[str, Any]) -> None:
        dialog = QDialog(self)
        dialog.setWindowTitle(item["title"])
        dialog.resize(620, 520)
        layout = QVBoxLayout(dialog)
        content = QPlainTextEdit()
        content.setReadOnly(True)
        lines = [
            item["title"],
            item["subject"],
            item["content"],
            self.tr(item["state"].capitalize()),
            item["note"],
        ]
        if item["kind"] == "vote":
            lines.append(self.tr(item["vote"].capitalize()))
        for event in item.get("events", []):
            when = QDateTime.fromSecsSinceEpoch(int(event["time"])).toString("yyyy-MM-dd HH:mm:ss")
            lines.extend(
                [
                    f"{when} · {self.tr(event['state'].capitalize())} · {event['note']}",
                    event["content"] + " " + self.tr(event.get("vote", "").capitalize()),
                ]
            )
        content.setPlainText("\n\n".join(lines))
        layout.addWidget(content)
        if item["state"] in EDITABLE:
            for label, operation in (
                ("Edit / reschedule", "edit"),
                ("Queue", "queue"),
                ("Pause", "pause"),
                ("Cancel", "cancel"),
            ):
                button = QPushButton(self.tr(label))

                def act(_=False, action=operation):
                    dialog.accept()
                    self.pages.setCurrentIndex(1)
                    self.control(action, item)

                button.clicked.connect(act)
                layout.addWidget(button)
        dialog.exec()

    def add_auto_suggestion(self) -> None:
        index = self.suggestions.currentData()
        if index is not None and index >= 0:
            message = " ".join(self.library[index]["text"].split())
            self.auto_messages.appendPlainText(message)

    def clear_account_views(self) -> None:
        from PySide6.QtGui import QTextCharFormat

        self.calendar.setDateTextFormat(QDate(), QTextCharFormat())
        self.history_timeline.setDateTextFormat(QDate(), QTextCharFormat())
        for dialog in self.findChildren(QDialog):
            dialog.reject()
        self._visible_items = []
        self._library_candidates = []
        self._calendar_items = []
        self._history_items = []
        self._library_signature = ""
        for widget in (self.library_rows, self.calendar_rows, self.history_rows):
            widget.clear()
        self.library_detail.clear()
        self.quotas.clear()
        self.auto_enabled.setChecked(False)
        for checkbox in self.auto_kinds.values():
            checkbox.setChecked(False)
        self.auto_messages.clear()
        self.enabled.setChecked(False)
        for template in self.templates.values():
            template.clear()

    def resizeEvent(self, event) -> None:
        super().resizeEvent(event)
        self.library_split.setOrientation(
            Qt.Orientation.Horizontal if self.width() >= 850 else Qt.Orientation.Vertical
        )
