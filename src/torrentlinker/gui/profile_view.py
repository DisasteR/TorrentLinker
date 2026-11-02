"""The account workspace, shared in structure with the WebUI Profile tab.

Layout follows docs/specs/profile-redesign/mockup.html; the figures come from
profile_overview so desktop and web show the same values.
"""

from __future__ import annotations

import html
import math
from datetime import date, datetime
from typing import Any

from PySide6.QtCore import QEvent, QSettings, Qt, Signal
from PySide6.QtGui import QPainter, QPainterPath, QPixmap
from PySide6.QtWidgets import (
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QLayout,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from torrentlinker.gui.controls import set_role
from torrentlinker.gui.flow_layout import FlowLayout
from torrentlinker.gui.profile_charts import (
    Axis,
    Bar,
    Calendar,
    LineChart,
    Ring,
    Segment,
    Series,
    Sparkline,
    Swatch,
    colors,
    nice_max,
)
from torrentlinker.profile_history import ProfileDay
from torrentlinker.profile_overview import PERIODS, known, overview
from torrentlinker.zen import ZenClass


def number(value: Any, digits: int = 0) -> str:
    """Grouped like the WebUI; "—" when unknown."""
    if not known(value):
        return "—"
    text = f"{value:,.{digits}f}"
    return text.rstrip("0").rstrip(".") if digits and "." in text else text


def size_parts(value: Any) -> tuple[str, str]:
    """Bytes as value and unit: two decimals from TiB, as in the mockup."""
    if not known(value):
        return "—", ""
    n, unit = abs(float(value)), 0
    units = ("B", "KiB", "MiB", "GiB", "TiB", "PiB")
    while n >= 1024 and unit < 5:
        n /= 1024
        unit += 1
    digits = 2 if unit >= 4 else 1 if unit else 0
    return ("−" if value < 0 else "") + f"{n:.{digits}f}", units[unit]


def size(value: Any) -> str:
    return " ".join(size_parts(value)) if known(value) else "—"


def signed(value: Any, format: Any) -> str:
    return f"{'+' if value >= 0 else '−'}{format(abs(value))}" if known(value) else ""


def timestamp(value: Any) -> str:
    return datetime.fromtimestamp(value).astimezone().strftime("%d %b %Y, %H:%M") if value else "—"


def ago(value: Any, now: float | None = None) -> str:
    if not value:
        return "—"
    seconds = max(0.0, (now or datetime.now().timestamp()) - value)
    if seconds < 60:
        return "now"
    if seconds < 3600:
        return f"{int(seconds // 60)} min"
    if seconds < 86400:
        return f"{int(seconds // 3600)} h"
    return f"{int(seconds // 86400)} d"


def clear(layout: QLayout) -> None:
    while (item := layout.takeAt(0)) is not None:
        if widget := item.widget():
            widget.hide()  # until the event loop deletes it
            widget.deleteLater()
        elif child := item.layout():
            clear(child)


def label(text: str = "", kind: str = "", rich: bool = False) -> QLabel:
    result = QLabel(text)
    result.setWordWrap(True)
    result.setTextFormat(Qt.TextFormat.RichText if rich else Qt.TextFormat.PlainText)
    result.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
    if kind:
        result.setProperty("pf", kind)
    return result


def panel(name: str = "pfPanel") -> tuple[QFrame, QVBoxLayout]:
    frame = QFrame()
    frame.setObjectName(name)
    layout = QVBoxLayout(frame)
    layout.setContentsMargins(16, 14, 16, 14)
    layout.setSpacing(8)
    return frame, layout


class Tile(QFrame):
    def __init__(self, key: str) -> None:
        super().__init__()
        self.setObjectName("pfTile")
        layout = QVBoxLayout(self)
        layout.setContentsMargins(12, 10, 12, 6)
        layout.setSpacing(2)
        self.key = label(key.upper(), "k")
        self.value = label("—", "value", rich=True)
        self.value.setWordWrap(False)
        self.change = label("", "change", rich=True)
        self.trend = Sparkline()
        for widget in (self.key, self.value, self.change, self.trend):
            layout.addWidget(widget)


class ProfileView(QWidget):
    refresh_requested = Signal()

    def __init__(self, settings: QSettings | None = None) -> None:
        super().__init__()
        self._settings = settings if settings is not None else QSettings()
        saved = self._settings.value("profile/period", 30)
        try:
            self.period = int(saved) if int(saved) in PERIODS else 30  # type: ignore[call-overload]
        except (TypeError, ValueError):
            self.period = 30
        self.state: dict[str, Any] = {}
        self.history: list[ProfileDay] = []
        self.clients: dict[str, Any] | None = None
        self._dark: bool | None = None
        self._avatar: QPixmap | None = None
        self._columns: tuple[int, bool] | None = None
        layout = QVBoxLayout(self)
        layout.setContentsMargins(8, 16, 8, 8)
        layout.setSpacing(16)
        self.notice = label()
        layout.addWidget(self.notice)
        layout.addWidget(self._build_header())
        self.tiles_grid = QGridLayout()
        self.tiles_grid.setSpacing(10)
        self.tiles = {
            key: Tile(title)
            for key, title in (
                ("ratio", self.tr("Ratio")),
                ("buffer", self.tr("Buffer")),
                ("uploaded", self.tr("Uploaded")),
                ("downloaded", self.tr("Downloaded")),
                ("zen", "Zen"),
                ("active_seeds", self.tr("Active seeds")),
            )
        }
        layout.addLayout(self.tiles_grid)
        self.charts_grid = QGridLayout()
        self.charts_grid.setSpacing(16)
        self.volumes_panel = self._build_volumes()
        self.zen_chart_panel = self._build_zen_chart()
        layout.addLayout(self.charts_grid)
        self.panels_grid = QGridLayout()
        self.panels_grid.setSpacing(16)
        self.progression_panel = self._build_progression()
        self.zen_panel = self._build_zen()
        self.transfers_panel = self._build_transfers()
        self.seeding_panel = self._build_seeding()
        self.sync_panel = self._build_sync()
        layout.addLayout(self.panels_grid)
        self.privacy = label(
            self.tr(
                "Connect and manage the companion in Settings → Companion. "
                "Your login stays in the browser."
            ),
            "muted",
        )
        layout.addWidget(self.privacy)
        layout.addStretch()
        self._arrange(1400)
        self._style()
        self.redraw()

    # Building ----------------------------------------------------------------

    def eyebrow(self, title: str, *sources: str) -> QWidget:
        row = QWidget()
        layout = QHBoxLayout(row)
        layout.setContentsMargins(0, 0, 0, 2)
        layout.setSpacing(8)
        heading = label(title.upper(), "eyebrow")
        heading.setWordWrap(False)
        layout.addWidget(heading)
        for source in sources:
            chip = label(source, "src")
            chip.setWordWrap(False)
            layout.addWidget(chip)
        layout.addStretch()
        return row

    def _build_header(self) -> QFrame:
        frame, outer = panel()
        outer.setContentsMargins(16, 14, 16, 14)
        layout = QHBoxLayout()
        layout.setSpacing(16)
        outer.addLayout(layout)
        self.avatar = QLabel("?")
        self.avatar.setProperty("pf", "avatar")
        self.avatar.setFixedSize(56, 56)
        self.avatar.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(self.avatar)
        who = QVBoxLayout()
        who.setSpacing(2)
        self.identity = label("", "username")
        self.meta = label("", "muted")
        pills = QWidget()
        self.pills = FlowLayout(pills)
        who.addWidget(self.identity)
        who.addWidget(self.meta)
        who.addWidget(pills)
        layout.addLayout(who, 1)
        segment = QFrame()
        segment.setObjectName("pfSeg")
        seg_layout = QHBoxLayout(segment)
        seg_layout.setContentsMargins(0, 0, 0, 0)
        seg_layout.setSpacing(0)
        segment.setAccessibleName(self.tr("Period"))
        self.period_buttons: dict[int, QPushButton] = {}
        for days in PERIODS:
            button = QPushButton(self.tr("{0} d").format(days) if days else self.tr("All"))
            button.setCheckable(True)
            button.setProperty("pf", "seg")
            button.clicked.connect(lambda _=False, d=days: self.set_period(d))
            seg_layout.addWidget(button)
            self.period_buttons[days] = button
        layout.addWidget(segment, 0, Qt.AlignmentFlag.AlignVCenter)
        self.refresh_button = QPushButton(self.tr("Refresh"))
        set_role(self.refresh_button, "primary")
        self.refresh_button.clicked.connect(self.refresh_requested)
        layout.addWidget(self.refresh_button, 0, Qt.AlignmentFlag.AlignVCenter)
        return frame

    def _chart_panel(
        self, title: str, legend: list[tuple[str, str, str]], chart: LineChart
    ) -> tuple[QFrame, QLabel, QGridLayout]:
        frame, layout = panel()
        head = QHBoxLayout()
        left = QVBoxLayout()
        left.setSpacing(0)
        left.addWidget(label(title, "title"))
        range_label = label("", "range")
        left.addWidget(range_label)
        head.addLayout(left, 1)
        for text, color, shape in legend:
            head.addWidget(Swatch(color, shape), 0, Qt.AlignmentFlag.AlignTop)
            item = label(text, "muted")
            item.setWordWrap(False)
            head.addWidget(item, 0, Qt.AlignmentFlag.AlignTop)
        layout.addLayout(head)
        layout.addWidget(chart)
        foot = QGridLayout()
        foot.setSpacing(8)
        layout.addLayout(foot)
        return frame, range_label, foot

    def _build_volumes(self) -> QFrame:
        self.volumes = LineChart(640 / 220, self.tr("Upload and download totals over time"))
        frame, self.volumes_range, self.volumes_foot = self._chart_panel(
            self.tr("Volumes"),
            [
                (self.tr("Uploaded"), "up", "dot"),
                (self.tr("Downloaded"), "down", "dot"),
                (self.tr("Buffer"), "muted", "dot"),
            ],
            self.volumes,
        )
        return frame

    def _build_zen_chart(self) -> QFrame:
        self.zen_chart = LineChart(420 / 220, self.tr("Zen balance and ratio over time"))
        frame, self.zen_range, self.zen_foot = self._chart_panel(
            self.tr("Zen balance & ratio"),
            [("Zen", "zen", "dot"), ("Ratio", "ratio", "line")],
            self.zen_chart,
        )
        return frame

    def _build_progression(self) -> QFrame:
        frame, layout = panel()
        layout.addWidget(self.eyebrow(self.tr("Progression"), "API"))
        level = QHBoxLayout()
        level.setSpacing(16)
        self.ring = Ring()
        level.addWidget(self.ring)
        text = QVBoxLayout()
        text.setSpacing(2)
        self.xp = label("", "xp")
        self.xp_note = label("", "muted")
        badges = QHBoxLayout()
        badges.setSpacing(8)
        self.grade = label("", "grade")
        self.grade.setWordWrap(False)
        self.stars = label("", "stars")
        self.stars.setWordWrap(False)
        self.quests = label("", "muted")
        badges.addWidget(self.grade)
        badges.addWidget(self.stars)
        badges.addWidget(self.quests, 1)
        text.addWidget(self.xp)
        text.addWidget(self.xp_note)
        text.addLayout(badges)
        level.addLayout(text, 1)
        layout.addLayout(level)
        paths = QGridLayout()
        paths.setHorizontalSpacing(10)
        paths.setVerticalSpacing(9)
        paths.setColumnStretch(1, 1)
        self.paths: dict[str, tuple[Bar, QLabel]] = {}
        for row, (key, title) in enumerate(
            (
                ("seed", self.tr("Seeding")),
                ("upload", self.tr("Uploading")),
                ("specialization", self.tr("Specialization")),
                ("competitivity", self.tr("Competitiveness")),
            )
        ):
            bar = Bar(7, border=True)
            count = label("", "count", rich=True)
            count.setWordWrap(False)
            count.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
            paths.addWidget(label(title), row, 0)
            paths.addWidget(bar, row, 1)
            paths.addWidget(count, row, 2)
            self.paths[key] = (bar, count)
        layout.addSpacing(6)
        layout.addLayout(paths)
        layout.addStretch()
        return frame

    def _build_zen(self) -> QFrame:
        frame, layout = panel()
        layout.addWidget(self.eyebrow("Zen", "API", self.tr("local estimate")))
        self.balance = label("", "balance", rich=True)
        layout.addWidget(self.balance)
        self.zen_split = Bar(12, track="line")
        layout.addWidget(self.zen_split)
        totals = QHBoxLayout()
        self.earned = label("", "muted", rich=True)
        self.spent = label("", "muted", rich=True)
        self.spent.setAlignment(Qt.AlignmentFlag.AlignRight)
        totals.addWidget(self.earned)
        totals.addWidget(self.spent)
        layout.addLayout(totals)
        self.zen_rows = QVBoxLayout()
        self.zen_rows.setSpacing(0)
        layout.addLayout(self.zen_rows)
        layout.addStretch()
        return frame

    def _build_transfers(self) -> QFrame:
        frame, layout = panel()
        layout.addWidget(self.eyebrow(self.tr("Transfer accounting"), "API"))
        self.transfers: dict[str, tuple[QLabel, Bar]] = {}
        for key, title in (
            ("uploaded", self.tr("Uploaded")),
            ("downloaded", self.tr("Downloaded")),
        ):
            head = QHBoxLayout()
            head.addWidget(label(title, "muted"), 0, Qt.AlignmentFlag.AlignTop)
            figures = label("", "figures", rich=True)
            figures.setAlignment(Qt.AlignmentFlag.AlignRight)
            head.addWidget(figures, 1)
            bar = Bar(14)
            layout.addLayout(head)
            layout.addWidget(bar)
            layout.addSpacing(6)
            self.transfers[key] = (figures, bar)
        layout.addWidget(
            label(
                self.tr(
                    "Credit comes from bonuses and freeleech. Total upload adds it; "
                    "total download subtracts it, never below zero."
                ),
                "note",
            )
        )
        layout.addStretch()
        return frame

    def _build_seeding(self) -> QFrame:
        frame, layout = panel()
        layout.addWidget(
            self.eyebrow(self.tr("Seeding · tracker vs clients"), self.tr("companion"), "clients")
        )
        self.seeding_table = QGridLayout()
        self.seeding_table.setHorizontalSpacing(0)
        self.seeding_table.setVerticalSpacing(0)
        self.seeding_table.setColumnStretch(0, 1)
        layout.addLayout(self.seeding_table)
        self.active_seeds = label("", "cell")
        self.active_seeds.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        self.seeding_rows = QVBoxLayout()
        self.seeding_rows.setSpacing(0)
        layout.addSpacing(6)
        layout.addLayout(self.seeding_rows)
        layout.addStretch()
        return frame

    def _build_sync(self) -> QFrame:
        frame, layout = panel()
        layout.addWidget(self.eyebrow(self.tr("Sync history · last 26 weeks")))
        self.calendar = Calendar()
        self.calendar.setAccessibleName(self.tr("Daily profile sync results"))
        layout.addWidget(self.calendar)
        key = QWidget()
        flow = FlowLayout(key, spacing=14)
        for outcome, color in (
            ("ok", "ok"),
            ("api", "warn"),
            ("failed", "err"),
            ("none", "raised"),
        ):
            item = QWidget()
            row = QHBoxLayout(item)
            row.setContentsMargins(0, 0, 0, 0)
            row.setSpacing(5)
            row.addWidget(Swatch(color, "square"))
            text = label(self.outcome_names()[outcome], "muted")
            text.setWordWrap(False)
            row.addWidget(text)
            flow.addWidget(item)
        layout.addWidget(key)
        return frame

    def outcome_names(self) -> dict[str, str]:
        return {
            "ok": self.tr("API and companion OK"),
            "api": self.tr("API only"),
            "failed": self.tr("Failed"),
            "none": self.tr("App not running"),
        }

    # Layout and style ------------------------------------------------------------

    def _arrange(self, width: int) -> None:
        """6, 3 or 2 tiles across; panels side by side only when wide enough."""
        tiles = 6 if width >= 1100 else 3 if width >= 720 else 2
        wide = width >= 1100
        if self._columns == (tiles, wide):
            return
        self._columns = (tiles, wide)
        for grid in (self.tiles_grid, self.charts_grid, self.panels_grid):
            while grid.takeAt(0) is not None:
                pass
            for column in range(6):
                grid.setColumnStretch(column, 0)
        for index, tile in enumerate(self.tiles.values()):
            self.tiles_grid.addWidget(tile, index // tiles, index % tiles)
        for column in range(tiles):
            self.tiles_grid.setColumnStretch(column, 1)
        if wide:
            self.charts_grid.addWidget(self.volumes_panel, 0, 0)
            self.charts_grid.addWidget(self.zen_chart_panel, 0, 1)
            self.charts_grid.setColumnStretch(0, 3)
            self.charts_grid.setColumnStretch(1, 2)
        else:
            self.charts_grid.addWidget(self.volumes_panel, 0, 0)
            self.charts_grid.addWidget(self.zen_chart_panel, 1, 0)
        panels = [self.progression_panel, self.zen_panel, self.transfers_panel, self.seeding_panel]
        columns = 2 if wide else 1
        for index, frame in enumerate(panels):
            self.panels_grid.addWidget(frame, index // columns, index % columns)
        self.panels_grid.addWidget(self.sync_panel, len(panels) // columns, 0, 1, columns)
        for column in range(columns):
            self.panels_grid.setColumnStretch(column, 1)

    def resizeEvent(self, event) -> None:  # type: ignore[no-untyped-def]
        self._arrange(event.size().width())
        super().resizeEvent(event)

    def changeEvent(self, event: QEvent) -> None:
        if event.type() in (QEvent.Type.PaletteChange, QEvent.Type.ApplicationPaletteChange):
            self._style()
        super().changeEvent(event)

    def _style(self) -> None:
        c = colors(self)
        dark = c["panel"] != "#ffffff"
        if dark == self._dark:
            return
        self._dark = dark
        mono = '"IBM Plex Mono", "Cascadia Mono", monospace'
        self.setStyleSheet(f"""
            QFrame#pfPanel, QFrame#pfTile {{ background: {c["panel"]};
                border: 1px solid {c["line"]}; border-radius: 8px; }}
            QFrame#pfPanel QLabel, QFrame#pfTile QLabel {{ background: transparent; }}
            QFrame#pfFoot {{ background: {c["raised"]}; border: 0; border-radius: 6px; }}
            QFrame#pfRow {{ border: 0; border-top: 1px solid {c["line"]}; border-radius: 0;
                background: transparent; }}
            QFrame#pfSeg {{ border: 1px solid {c["line"]}; border-radius: 6px; }}
            QPushButton[pf="seg"] {{ border: 0; border-radius: 0; padding: 4px 10px;
                background: transparent; color: {c["muted"]}; font-size: 12px; }}
            QPushButton[pf="seg"]:checked {{ background: {c["tint"]}; color: {c["brand"]}; }}
            QFrame#pfPanel QLabel[pf="avatar"] {{ background: {c["accent"]}; color: white;
                border-radius: 28px;
                font-size: 22px; font-weight: 600; }}
            QLabel[pf="username"] {{ font-size: 22px; font-weight: 600; }}
            QLabel[pf="muted"], QLabel[pf="range"], QLabel[pf="note"] {{ color: {c["muted"]}; }}
            QLabel[pf="note"] {{ font-size: 12px; }}
            QLabel[pf="range"] {{ font-family: {mono}; font-size: 12px; }}
            QLabel[pf="title"] {{ font-size: 14px; font-weight: 600; }}
            QLabel[pf="eyebrow"] {{ color: {c["muted"]}; font-size: 11px; font-weight: 600; }}
            QFrame#pfPanel QLabel[pf="src"] {{ color: {c["muted"]}; font-family: {mono};
                font-size: 10px;
                border: 1px solid {c["line"]}; border-radius: 4px; padding: 0 5px; }}
            QLabel[pf="k"] {{ color: {c["muted"]}; font-size: 10.5px; font-weight: 600; }}
            QLabel[pf="value"] {{ font-family: {mono}; font-size: 22px; }}
            QLabel[pf="change"], QLabel[pf="figures"], QLabel[pf="count"], QLabel[pf="xp"],
            QLabel[pf="cell"] {{ font-family: {mono}; }}
            QLabel[pf="change"] {{ color: {c["muted"]}; font-size: 12px; }}
            QLabel[pf="figures"] {{ color: {c["muted"]}; font-size: 12px; }}
            QLabel[pf="xp"] {{ font-size: 15px; }}
            QLabel[pf="balance"] {{ font-family: {mono}; font-size: 26px; }}
            QFrame#pfPanel QLabel[pf="grade"] {{ color: {c["zen"]}; border: 1px solid {c["zen"]};
                border-radius: 4px; padding: 1px 8px; font-size: 11px; font-weight: 600; }}
            QLabel[pf="stars"] {{ color: {c["zen"]}; }}
            QFrame#pfPanel QLabel[pf="th"] {{ color: {c["muted"]}; font-size: 11px;
                font-weight: 600;
                padding: 6px 8px; border-bottom: 1px solid {c["line"]}; }}
            QFrame#pfPanel QLabel[pf="cell"], QFrame#pfPanel QLabel[pf="name"] {{ padding: 7px 8px;
                border-bottom: 1px solid {c["line"]}; }}
            QFrame#pfPanel QLabel[pf="pill-ok"], QFrame#pfPanel QLabel[pf="pill-warn"],
            QFrame#pfPanel QLabel[pf="pill-err"], QFrame#pfPanel QLabel[pf="pill-info"],
            QFrame#pfPanel QLabel[pf="pill-off"] {{ border: 1px solid; border-radius: 9px;
                padding: 1px 8px; font-size: 11px; font-weight: 500; }}
            QFrame#pfPanel QLabel[pf="pill-ok"] {{ color: {c["ok"]};
                border-color: {c["ok"]}; }}
            QFrame#pfPanel QLabel[pf="pill-warn"] {{ color: {c["warn"]};
                border-color: {c["warn"]}; }}
            QFrame#pfPanel QLabel[pf="pill-err"] {{ color: {c["err"]};
                border-color: {c["err"]}; }}
            QFrame#pfPanel QLabel[pf="pill-info"] {{ color: {c["brand"]};
                border-color: {c["brand"]}; }}
            QFrame#pfPanel QLabel[pf="pill-off"] {{ color: {c["muted"]};
                border-color: {c["muted"]}; }}
        """)
        if self.state or self.history:
            self.redraw()

    # Data --------------------------------------------------------------------------

    def set_period(self, days: int) -> None:
        self.period = days
        self._settings.setValue("profile/period", days)
        self.redraw()

    def show_state(self, state: dict[str, Any], history: list[ProfileDay] | None = None) -> None:
        if not self.isVisible() and state == self.state and history in (None, self.history):
            return  # nothing new to draw while hidden
        self.state = state
        if history is not None:
            self.history = history
        self.redraw()

    def show_avatar(self, data: bytes | None) -> None:
        """The account's avatar clipped to the header circle; the initial without one."""
        image = QPixmap()
        if not data or not image.loadFromData(data):
            self._avatar = None
            self.avatar.setText((self.state.get("api") or {}).get("username", "?")[:1].upper())
            return
        ratio = self.devicePixelRatioF()
        side = round(56 * ratio)
        image = image.scaled(
            side,
            side,
            Qt.AspectRatioMode.KeepAspectRatioByExpanding,
            Qt.TransformationMode.SmoothTransformation,
        )
        circle = QPixmap(side, side)
        circle.fill(Qt.GlobalColor.transparent)
        painter = QPainter(circle)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        path = QPainterPath()
        path.addEllipse(0, 0, side, side)
        painter.setClipPath(path)
        painter.drawPixmap((side - image.width()) // 2, (side - image.height()) // 2, image)
        painter.end()
        circle.setDevicePixelRatio(ratio)
        self._avatar = circle
        self.avatar.setPixmap(circle)

    def show_torrent_clients(self, clients: dict[str, Any] | None) -> None:
        self.clients = clients
        o = overview(self.state, self.history, self.period, date.today())
        self._show_zen(o)
        self._show_seeding(o)

    def redraw(self) -> None:
        state = self.state
        errors = {
            "no-key": self.tr("Add an API key with profile:read access in Settings → Indexer."),
            "unauthorized": self.tr("API key refused. Check Settings → Indexer and refresh."),
            "missing-scope": self.tr("The API key needs profile:read access."),
            "rate-limited": self.tr("The tracker asked us to wait. Try refreshing later."),
            "failed": self.tr("Profile unavailable. Check your connection and refresh."),
        }
        self.notice.setText(
            errors.get(state.get("error") or "", "")
            or (
                self.tr("Waiting for an API update. Retrying automatically.")
                if state.get("api_stale")
                else ""
            )
        )
        self.notice.setVisible(bool(self.notice.text()))
        o = overview(state, self.history, self.period, date.today())
        self._show_header(o)
        self._show_tiles(o)
        self._show_charts(o)
        self._show_progression(o)
        self._show_zen(o)
        self._show_transfers(o)
        self._show_seeding(o)
        self._show_sync(o)

    def color(self, name: str) -> str:
        return colors(self)[name]

    def span(self, text: str, color: str, bold: bool = False) -> str:
        weight = "font-weight:600;" if bold else ""
        return f'<span style="color:{self.color(color)};{weight}">{html.escape(text)}</span>'

    def collecting(self) -> str:
        return self.tr("History is being collected")

    def _show_header(self, o: dict[str, Any]) -> None:
        h, api = o["header"], self.state.get("api")
        if self._avatar is not None:
            self.avatar.setPixmap(self._avatar)
        else:
            self.avatar.setText(h["initial"])
        self.identity.setText(h["username"] or self.tr("Connect your c411 account"))
        self.meta.setText(
            " · ".join(
                (
                    h["class"] or "—",
                    self.tr("Level {0}").format(number(h["level"])),
                    self.tr("Team {0}").format(h["team"] or "—"),
                    self.tr("Member since {0}").format(
                        date.fromisoformat(h["member_since"]).strftime("%d %b %Y")
                        if h["member_since"]
                        else "—"
                    ),
                )
            )
        )
        pills: list[tuple[str, str]] = []
        if api:
            if h["download"]["allowed"]:
                pills.append(("ok", self.tr("Download allowed")))
            else:
                reason = h["download"]["reason"]
                pills.append(
                    ("err", self.tr("Download blocked") + (f" · {reason}" if reason else ""))
                )
            if h["freeleech"]["active"]:
                pills.append(("info", " · ".join(["Freeleech", *h["freeleech"]["sources"]])))
        stale = h["api"]["stale"] or bool(self.state.get("error"))
        pills.append(
            (
                "off" if not api else "warn" if stale else "ok",
                f"API · {ago(h['api']['updated_at'])}",
            )
        )
        companion = h["companion"]
        if companion["received_at"]:
            pills.append(
                (
                    "warn" if companion["stale"] else "ok",
                    self.tr("Companion · {0}").format(ago(companion["received_at"])),
                )
            )
        else:
            pills.append(
                (
                    "off",
                    self.tr("Companion · waiting")
                    if companion["paired"]
                    else self.tr("Companion · not connected"),
                )
            )
        clear(self.pills)
        for kind, text in pills:
            pill = QLabel(text)
            pill.setProperty("pf", "pill-" + kind)
            self.pills.addWidget(pill)
        for days, button in self.period_buttons.items():
            button.setChecked(days == self.period)

    def _show_tiles(self, o: dict[str, Any]) -> None:
        t = o["tiles"]
        period = self.tr("{0} d").format(o["span"]) if o["span"] else self.tr("all")

        def ratio(v: Any) -> str:
            return f"{v:.2f}" if known(v) else "—"

        formats = {
            "ratio": (lambda v: (ratio(v), ""), ratio, "ratio"),
            "buffer": (size_parts, size, "brand"),
            "uploaded": (size_parts, size, "up"),
            "downloaded": (size_parts, size, "down"),
            "zen": (lambda v: (number(v), ""), number, "zen"),
            "active_seeds": (lambda v: (number(v), ""), number, "brand"),
        }
        for key, tile in self.tiles.items():
            parts, format, color = formats[key]
            value, unit = parts(t[key]["value"])
            tile.value.setText(
                html.escape(value)
                + (
                    f' <span style="font-size:12px;color:{self.color("muted")}">{unit}</span>'
                    if unit
                    else ""
                )
            )
            change = t[key]["change"]
            tile.change.setText(
                self.span(signed(change, format), "ok" if change >= 0 else "err")
                + html.escape(f" · {period}")
                if known(change)
                else ""
            )
            tile.trend.set_data(t[key]["trend"], color, "" if o["period"] else self.collecting())

    def _foot(self, grid: QGridLayout, items: list[tuple[str, str]]) -> None:
        clear(grid)
        columns = 3 if self.width() >= 720 else 2
        for index, (title, value) in enumerate(items):
            frame = QFrame()
            frame.setObjectName("pfFoot")
            layout = QVBoxLayout(frame)
            layout.setContentsMargins(10, 6, 10, 6)
            layout.setSpacing(0)
            caption = label(title, "muted")
            caption.setStyleSheet("font-size: 11px;")
            figure = label(value, "xp")
            figure.setStyleSheet("font-size: 14px;")
            layout.addWidget(caption)
            layout.addWidget(figure)
            grid.addWidget(frame, index // columns, index % columns)

    def _show_charts(self, o: dict[str, Any]) -> None:
        p = o["period"]
        if not p:
            for chart, range_label, foot in (
                (self.volumes, self.volumes_range, self.volumes_foot),
                (self.zen_chart, self.zen_range, self.zen_foot),
            ):
                chart.set_data([], {}, [], self.collecting())
                range_label.setText("")
                clear(foot)
            return
        s = p["series"]
        dates = [d["day"] for d in s]
        text = f"{p['first']} → {p['last']}"
        self.volumes_range.setText(text)
        self.zen_range.setText(text)
        top = nice_max(max([d["uploaded"] or 0 for d in s] + [d["downloaded"] or 0 for d in s]))
        self.volumes.set_data(
            [
                Series([d["uploaded"] for d in s], "up"),
                Series([d["downloaded"] for d in s], "down"),
                Series([d["buffer"] for d in s], "muted", fill=False, dashed=True, mark=False),
            ],
            # A negative buffer goes below zero.
            {
                "left": Axis(
                    min([0.0] + [d["buffer"] for d in s if known(d["buffer"])]),
                    top,
                    lambda v: size(v) if v else "0",
                    "right",
                )
            },
            dates,
        )
        average = p["average"]["uploaded"]
        self._foot(
            self.volumes_foot,
            [
                (self.tr("Uploaded in period"), size(p["change"]["uploaded"])),
                (self.tr("Downloaded in period"), size(p["change"]["downloaded"])),
                (
                    self.tr("Average upload"),
                    self.tr("{0}/d").format(size(average)) if known(average) else "—",
                ),
            ],
        )
        ratios = [d["ratio"] for d in s if known(d["ratio"])]
        low = math.floor(min(ratios) * 2) / 2 if ratios else 0.0
        high = math.ceil(max(ratios) * 2) / 2 if ratios else 1.0
        if high <= low:
            high = low + 0.5
        self.zen_chart.set_data(
            [
                Series([d["balance"] for d in s], "zen"),
                Series(
                    [d["ratio"] for d in s], "ratio", "right", fill=False, dashed=True, mark=False
                ),
            ],
            {
                "left": Axis(0, nice_max(max(d["balance"] or 0 for d in s)), number, "left"),
                "right": Axis(low, high, lambda v: f"{v:.2f}" if ratios else "", "right"),
            },
            dates,
        )
        now = s[-1]["ratio"]
        self._foot(
            self.zen_foot,
            [
                (self.tr("Zen change"), signed(p["change"]["balance"], number) or "—"),
                (self.tr("Purchases seen"), str(p["purchases"])),
                (self.tr("Ratio now"), f"{now:.2f}" if known(now) else "—"),
            ],
        )

    def _show_progression(self, o: dict[str, Any]) -> None:
        g = o["progression"]
        self.ring.set_data(g["fraction"], number(g["level"]), self.tr("LEVEL"))
        into, remaining = g["into"], g["to_next"]
        total = into + remaining if known(into) and known(remaining) else None
        self.xp.setText(f"{number(into)} / {number(total)} XP")
        self.xp_note.setText(
            self.tr("{0} XP to level {1} · {2} XP total").format(
                number(remaining),
                g["level"] + 1 if known(g["level"]) else "—",
                number(g["total_xp"]),
            )
        )
        self.grade.setText((g["grade"] or "").upper())
        self.grade.setVisible(bool(g["grade"]))
        stars = g["stars"]
        self.stars.setText("★" * int(stars) + "☆" * max(0, 4 - int(stars)) if known(stars) else "")
        bonus = g["tree_bonus"]
        self.quests.setText(
            self.tr("Quests {0} / {1} · Tree bonus {2}").format(
                number(g["quests_completed"]),
                number(g["quests_total"]),
                f"+{number(bonus * 100, 1)} %" if known(bonus) else "—",
            )
        )
        for path in g["paths"]:
            bar, count = self.paths[path["key"]]
            bar.set_segments([Segment(path["fraction"], "brand")])
            count.setText(
                html.escape(f"{number(path['completed'])}/{number(path['total'])} ")
                + self.span("★" * path["stars"], "zen")
            )

    def _row(self, layout: QVBoxLayout, title: str, value: str) -> None:
        frame = QFrame()
        frame.setObjectName("pfRow")
        row = QHBoxLayout(frame)
        row.setContentsMargins(0, 7, 0, 7)
        name = label(title, "muted")
        figure = label(value)
        figure.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        row.addWidget(name, 1)
        row.addWidget(figure, 1)
        layout.addWidget(frame)

    def best_class(self, classes: list[dict[str, Any]]) -> str:
        current = next((c for c in classes if c["current"]), None)

        def name(c: dict[str, Any]) -> str:
            from torrentlinker.gui.zen_view import class_label

            return class_label(ZenClass(c["id"]))

        def change(c: dict[str, Any]) -> str:
            if not current or current["daily"] <= 0:
                return ""
            return " " + signed((c["daily"] / current["daily"] - 1) * 100, number) + " %"

        current_text = self.tr("{0} (current)").format(name(current)) if current else ""
        best = classes[0]
        if not current or best["current"]:
            other = next((c for c in classes if not c["current"]), None)
            return " · ".join(
                t for t in (current_text, name(other) + change(other) if other else "") if t
            )
        return f"{name(best)}{change(best)} · {current_text}"

    def _show_zen(self, o: dict[str, Any]) -> None:
        z = o["zen"]
        self.balance.setText(
            html.escape(number(z["balance"]))
            + f' <span style="font-size:13px;color:{self.color("muted")}">Zen</span>'
        )
        balance, spent = z["balance"], z["spent"]
        if known(balance) and known(spent) and max(0, balance) + spent > 0:
            self.zen_split.set_segments(
                [Segment(max(0, balance) / (max(0, balance) + spent), "zen")]
            )
        else:
            self.zen_split.set_segments([])
        fg = self.color("fg")
        self.earned.setText(
            html.escape(self.tr("Earned") + " ")
            + f'<b style="color:{fg}">{number(z["earned"])}</b>'
        )
        self.spent.setText(
            html.escape(self.tr("Spent") + " ") + f'<b style="color:{fg}">{number(spent)}</b>'
        )
        clear(self.zen_rows)
        title = self.tr("Estimated today (from your clients)")
        estimate = (self.clients or {}).get("zen")
        if self.clients is None:
            self._row(self.zen_rows, title, self.tr("Loading…"))
        elif not estimate:
            self._row(self.zen_rows, title, self.tr("Connect a Torrent Client to estimate"))
        else:
            self._row(self.zen_rows, title, signed(estimate["daily"], number) + " Zen")
        measured = (o["period"] or {}).get("measured_zen")
        if known(measured):
            self._row(
                self.zen_rows,
                self.tr("Measured over {0} days").format(o["span"])
                if o["span"]
                else self.tr("Measured over all history"),
                self.tr("{0} Zen / day").format(signed(measured, number)),
            )
        if estimate:
            self._row(
                self.zen_rows,
                self.tr("Best class for your seeds"),
                self.best_class(estimate["classes"]),
            )
            top = estimate["top"]
            self._row(
                self.zen_rows,
                self.tr("Top earner"),
                self.tr("{0} · {1} Zen/day").format(top["name"], number(top["zen"], 1))
                if top
                else "—",
            )

    def _show_transfers(self, o: dict[str, Any]) -> None:
        fg = self.color("fg")
        for key, color in (("uploaded", "up"), ("downloaded", "down")):
            figures, bar = self.transfers[key]
            a = o["transfers"][key]
            if not a:
                figures.setText("—")
                bar.set_segments([])
                continue
            sign = "+" if key == "uploaded" else "−"
            total = self.tr("total {0}").format(size(a["total"]))
            figures.setText(
                html.escape(
                    self.tr("actual {0} · credit {1} · ").format(
                        size(a["actual"]), sign + size(a["credit"])
                    )
                )
                + f'<b style="color:{fg}">{html.escape(total)}</b>'
            )
            bar.set_segments(
                [
                    Segment(a["solid"], color),
                    Segment(a["extra"], color, 0.35, a["hatched"]),
                ]
            )

    def _show_seeding(self, o: dict[str, Any]) -> None:
        companion, clients = o["companion"], self.clients
        table = self.seeding_table
        while (item := table.takeAt(0)) is not None:
            if (widget := item.widget()) is not None and widget is not self.active_seeds:
                widget.hide()
                widget.deleteLater()

        def cell(
            text: str, row: int, column: int, span: int = 1, kind: str = "cell", tone: str = ""
        ) -> QLabel:
            result = label(text, kind, rich=bool(tone))
            if tone:
                result.setText(self.span(text, tone))
            if column:
                result.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
            table.addWidget(result, row, column, 1, span)
            return result

        for column, title in enumerate((self.tr("Source"), self.tr("Torrents"), self.tr("Size"))):
            cell(title.upper(), 0, column, kind="th")
        connect = self.tr("Connect the companion")
        seeds = (companion or {}).get("active_seeds")
        cell(self.tr("Active on c411"), 1, 0, kind="name")
        if known(seeds):
            self.active_seeds.setText(number(seeds))
            table.addWidget(self.active_seeds, 1, 1)
            cell("—", 1, 2)
        else:
            self.active_seeds.setText(connect)
            table.addWidget(self.active_seeds, 1, 1, 1, 2)
        row = 2
        if clients is None:
            cell(self.tr("Loading Torrent Clients…"), row, 0, 3, "name")
            row += 1
        elif not clients["clients"]:
            cell(self.tr("No Torrent Client is enabled."), row, 0, 3, "name")
            row += 1
        for client in (clients or {}).get("clients", []):
            cell(client["name"], row, 0, kind="name")
            if client["error"]:
                unavailable = cell(self.tr("Unavailable"), row, 1, 2, tone="warn")
                unavailable.setToolTip(client["error"])
            else:
                cell(number(client["count"]), row, 1)
                cell(size(client["size"]), row, 2)
            row += 1
        if clients is not None:
            gap_label = cell(self.tr("Gap"), row, 0, kind="name")
            gap_label.setStyleSheet("font-weight: 600;")
            gap = clients["gap"]
            if not known(gap):
                cell(self.tr("No client reachable") if known(seeds) else connect, row, 1, 2)
            else:
                text = (
                    self.tr("{0} not seen").format(gap)
                    if gap > 0
                    else self.tr("{0} more in clients").format(-gap)
                    if gap < 0
                    else self.tr("None")
                )
                cell(text, row, 1, tone="warn" if gap else "ok")
                cell("—", row, 2)
        clear(self.seeding_rows)
        details = companion or {}
        seed_time = details.get("seed_time")
        self._row(
            self.seeding_rows,
            self.tr("Validated uploads"),
            number(details.get("validated_uploads")),
        )
        self._row(
            self.seeding_rows,
            self.tr("Total seed time"),
            self.tr("{0} days").format(number(seed_time / 86400))
            if seed_time is not None and known(seed_time)
            else "—",
        )

    def _show_sync(self, o: dict[str, Any]) -> None:
        self.calendar.set_cells(
            [(c["day"], c["outcome"]) for c in o["calendar"]], self.outcome_names()
        )
