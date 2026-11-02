"""Painted pieces of the Profile view: trend lines, charts, level ring, bars and calendar.

Colours follow docs/specs/profile-redesign/mockup.html, which mirrors gui/theme.py.
"""

from __future__ import annotations

import math
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from datetime import date, timedelta

from PySide6.QtCore import QPointF, QRectF, QSize, Qt
from PySide6.QtGui import QBrush, QColor, QFont, QPainter, QPainterPath, QPen
from PySide6.QtWidgets import QSizePolicy, QWidget

LIGHT = {
    "panel": "#ffffff",
    "raised": "#f7f9fc",
    "line": "#d5dfe9",
    "fg": "#203047",
    "muted": "#5c6c80",
    "tint": "#e8f3f2",
    "accent": "#087f76",
    "brand": "#087f76",
    "up": "#1f9d63",
    "down": "#2f6fd0",
    "zen": "#8a5800",
    "ratio": "#b07a12",
    "ok": "#1f9d63",
    "warn": "#8a5800",
    "err": "#b42332",
}
DARK = {
    **LIGHT,
    "panel": "#1d2530",
    "raised": "#222d39",
    "line": "#354454",
    "fg": "#e7eef6",
    "muted": "#a6b4c4",
    "tint": "#273c48",
    "brand": "#68cec5",
    "up": "#4ad394",
    "down": "#6fa8ff",
    "zen": "#edbf73",
    "ratio": "#e7c35a",
    "ok": "#4ad394",
    "warn": "#edbf73",
    "err": "#ff9c9c",
}


def colors(widget: QWidget) -> dict[str, str]:
    return DARK if widget.palette().window().color().lightness() < 128 else LIGHT


def mono(size: float) -> QFont:
    font = QFont("IBM Plex Mono")
    font.setStyleHint(QFont.StyleHint.Monospace)
    font.setPointSizeF(size)
    return font


def nice_max(value: float) -> float:
    if not value > 0:
        return 1.0
    p = 10 ** math.floor(math.log10(value))
    return float(math.ceil(value / p / 2) * 2 * p)


def line_path(points: Sequence[QPointF]) -> QPainterPath:
    path = QPainterPath(points[0])
    for p in points[1:]:
        path.lineTo(p)
    return path


class Sparkline(QWidget):
    """A tile's trend line, or a short empty-state text."""

    def __init__(self) -> None:
        super().__init__()
        self.values: list[float] = []
        self.color = "brand"
        self.empty = ""
        self.setFixedHeight(30)

    def set_data(self, values: Sequence[float], color: str, empty: str = "") -> None:
        self.values, self.color, self.empty = list(values), color, empty
        self.update()

    def paintEvent(self, event) -> None:  # type: ignore[no-untyped-def]
        c = colors(self)
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        w, h = self.width(), self.height()
        if len(self.values) < 2:
            painter.setPen(QColor(c["muted"]))
            font = painter.font()
            # The app stylesheet sizes fonts in pixels, so pointSizeF() is -1.
            if font.pixelSize() > 0:
                font.setPixelSize(max(1, round(font.pixelSize() * 0.85)))
            else:
                font.setPointSizeF(font.pointSizeF() * 0.85)
            painter.setFont(font)
            painter.drawText(self.rect(), Qt.AlignmentFlag.AlignVCenter, self.empty)
            return
        lo, hi = min(self.values), max(self.values)
        span = hi - lo or 1
        n = len(self.values)
        points = [
            QPointF(i / (n - 1) * w, h - 3 - (v - lo) / span * (h - 6))
            for i, v in enumerate(self.values)
        ]
        color = QColor(c[self.color])
        fill = line_path(points)
        fill.lineTo(w, h)
        fill.lineTo(0, h)
        fill.closeSubpath()
        shade = QColor(color)
        shade.setAlphaF(0.12)
        painter.fillPath(fill, shade)
        painter.setPen(QPen(color, 1.5))
        painter.drawPath(line_path(points))
        painter.setBrush(color)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.drawEllipse(points[-1], 2.2, 2.2)


@dataclass
class Series:
    values: list[float | None]
    color: str
    axis: str = "left"  # left or right scale
    fill: bool = True
    dashed: bool = False
    mark: bool = True  # mark the last point


@dataclass
class Axis:
    low: float
    high: float
    label: Callable[[float], str]
    side: str = "right"  # where the labels are drawn


class LineChart(QWidget):
    """Lines over dates with one or two scales; labels drawn at their true values."""

    def __init__(self, ratio: float, label: str) -> None:
        super().__init__()
        self.ratio = ratio  # width / height, as the mockup's viewBox
        self.series: list[Series] = []
        self.axes: dict[str, Axis] = {}
        self.dates: list[str] = []
        self.empty = ""
        self.setAccessibleName(label)
        policy = QSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred)
        policy.setHeightForWidth(True)
        self.setSizePolicy(policy)
        self.setMinimumHeight(140)

    def hasHeightForWidth(self) -> bool:
        return True

    def heightForWidth(self, width: int) -> int:
        return max(140, min(300, round(width / self.ratio)))

    def sizeHint(self) -> QSize:
        return QSize(420, round(420 / self.ratio))

    def set_data(
        self, series: list[Series], axes: dict[str, Axis], dates: list[str], empty: str = ""
    ) -> None:
        self.series, self.axes, self.dates, self.empty = series, axes, dates, empty
        self.updateGeometry()
        self.update()

    def paintEvent(self, event) -> None:  # type: ignore[no-untyped-def]
        c = colors(self)
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        if len(self.dates) < 2:
            painter.setPen(QColor(c["muted"]))
            painter.drawText(self.rect(), Qt.AlignmentFlag.AlignCenter, self.empty)
            return
        painter.setFont(mono(7.5))
        metrics = painter.fontMetrics()
        sides = {a.side for a in self.axes.values()}
        widest = {
            side: max(
                metrics.horizontalAdvance(a.label(a.low + (a.high - a.low) * k / 4))
                for a in self.axes.values()
                if a.side == side
                for k in range(5)
            )
            for side in sides
        }
        left = (widest["left"] + 10) if "left" in sides else 8
        right = (widest["right"] + 10) if "right" in sides else 8
        top, bottom = 10, 24
        plot = QRectF(left, top, self.width() - left - right, self.height() - top - bottom)
        # Points sit at their dates, so days the app did not run keep their width.
        days = [date.fromisoformat(d) for d in self.dates]
        span = (days[-1] - days[0]).days or 1

        def x(i: int) -> float:
            return plot.left() + (days[i] - days[0]).days / span * plot.width()

        def y(value: float, axis: Axis) -> float:
            share = (value - axis.low) / ((axis.high - axis.low) or 1)
            return plot.bottom() - share * plot.height()

        muted = QColor(c["muted"])
        for k in range(5):
            yy = plot.bottom() - plot.height() * k / 4
            painter.setPen(QPen(QColor(c["line"]), 1))
            painter.drawLine(QPointF(plot.left(), yy), QPointF(plot.right(), yy))
            painter.setPen(muted)
            for axis in self.axes.values():
                text = axis.label(axis.low + (axis.high - axis.low) * k / 4)
                if axis.side == "left":
                    painter.drawText(
                        QRectF(0, yy - 7, left - 6, 14),
                        Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter,
                        text,
                    )
                else:
                    painter.drawText(
                        QRectF(plot.right() + 6, yy - 7, right, 14),
                        Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter,
                        text,
                    )
        ticks = [(plot.left(), self.dates[0], Qt.AlignmentFlag.AlignLeft)]
        if plot.width() > 360:
            middle = (days[0] + timedelta(days=span / 2)).isoformat()
            ticks.append((plot.center().x(), middle, Qt.AlignmentFlag.AlignHCenter))
        ticks.append((plot.right(), self.dates[-1], Qt.AlignmentFlag.AlignRight))
        for position, text, align in ticks:
            box = QRectF(position - 80, plot.bottom() + 6, 160, 14)
            if align == Qt.AlignmentFlag.AlignLeft:
                box.moveLeft(position)
            elif align == Qt.AlignmentFlag.AlignRight:
                box.moveRight(position)
            painter.drawText(box, align | Qt.AlignmentFlag.AlignVCenter, text)
        for s in self.series:
            axis = self.axes[s.axis]
            points = [
                QPointF(x(i), y(max(axis.low, v), axis))
                for i, v in enumerate(s.values)
                if v is not None
            ]
            if len(points) < 2:
                continue
            color = QColor(c[s.color])
            if s.fill:
                area = line_path(points)
                area.lineTo(points[-1].x(), plot.bottom())
                area.lineTo(points[0].x(), plot.bottom())
                area.closeSubpath()
                shade = QColor(color)
                shade.setAlphaF(0.15)
                painter.fillPath(area, shade)
            pen = QPen(color, 1.4 if s.dashed else 2)
            if s.dashed:
                pen.setStyle(Qt.PenStyle.DashLine)
            painter.setPen(pen)
            painter.setBrush(Qt.BrushStyle.NoBrush)
            painter.drawPath(line_path(points))
            if s.mark:
                painter.setPen(Qt.PenStyle.NoPen)
                painter.setBrush(color)
                painter.drawEllipse(points[-1], 3.5, 3.5)


class Ring(QWidget):
    """The level ring: progress into the level around the level number."""

    def __init__(self) -> None:
        super().__init__()
        self.fraction = 0.0
        self.level = "—"
        self.caption = ""
        self.setFixedSize(96, 96)

    def set_data(self, fraction: float, level: str, caption: str) -> None:
        self.fraction, self.level, self.caption = fraction, level, caption
        self.setAccessibleName(f"{caption} {level}, {round(fraction * 100)} %")
        self.update()

    def paintEvent(self, event) -> None:  # type: ignore[no-untyped-def]
        c = colors(self)
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        box = QRectF(8, 8, 80, 80)
        pen = QPen(QColor(c["raised"]), 9)
        painter.setPen(pen)
        painter.drawEllipse(box)
        if self.fraction > 0:
            pen = QPen(QColor(c["brand"]), 9)
            pen.setCapStyle(Qt.PenCapStyle.RoundCap)
            painter.setPen(pen)
            painter.drawArc(box, 90 * 16, -round(self.fraction * 360 * 16))
        painter.setPen(QColor(c["fg"]))
        font = painter.font()
        font.setPointSizeF(17)
        font.setWeight(QFont.Weight.DemiBold)
        painter.setFont(font)
        painter.drawText(QRectF(0, 22, 96, 32), Qt.AlignmentFlag.AlignCenter, self.level)
        painter.setPen(QColor(c["muted"]))
        painter.setFont(mono(6.5))
        painter.drawText(QRectF(0, 54, 96, 14), Qt.AlignmentFlag.AlignCenter, self.caption)


@dataclass
class Segment:
    share: float
    color: str
    alpha: float = 1.0
    hatched: bool = False


class Bar(QWidget):
    """A horizontal bar of segments: progress, earned vs spent, transfer stacks."""

    def __init__(self, height: int, track: str = "raised", border: bool = False) -> None:
        super().__init__()
        self.segments: list[Segment] = []
        self.track = track
        self.border = border
        self.setFixedHeight(height)

    def set_segments(self, segments: list[Segment]) -> None:
        self.segments = segments
        self.update()

    def paintEvent(self, event) -> None:  # type: ignore[no-untyped-def]
        c = colors(self)
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        rect = QRectF(self.rect()).adjusted(0.5, 0.5, -0.5, -0.5)
        radius = min(6.0, rect.height() / 2)
        clip = QPainterPath()
        clip.addRoundedRect(rect, radius, radius)
        painter.fillPath(clip, QColor(c[self.track]))
        painter.setClipPath(clip)
        x = rect.left()
        for s in self.segments:
            width = max(0.0, min(1.0, s.share)) * rect.width()
            color = QColor(c[s.color])
            color.setAlphaF(s.alpha)
            part = QRectF(x, rect.top(), width, rect.height())
            painter.fillRect(part, color)
            if s.hatched:
                painter.fillRect(part, QBrush(QColor(c["panel"]), Qt.BrushStyle.BDiagPattern))
            x += width
        painter.setClipping(False)
        if self.border:
            painter.setPen(QPen(QColor(c["line"]), 1))
            painter.setBrush(Qt.BrushStyle.NoBrush)
            painter.drawRoundedRect(rect, radius, radius)


class Calendar(QWidget):
    """One square per day, wrapping to the width; tooltips name the day and state."""

    CELL, GAP = 13, 3

    def __init__(self) -> None:
        super().__init__()
        self.cells: list[tuple[str, str]] = []  # (day, outcome)
        self.names: dict[str, str] = {}
        self.setMouseTracking(True)
        policy = QSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred)
        policy.setHeightForWidth(True)
        self.setSizePolicy(policy)

    def set_cells(self, cells: list[tuple[str, str]], names: dict[str, str]) -> None:
        self.cells, self.names = cells, names
        self.updateGeometry()
        self.update()

    def columns(self, width: int) -> int:
        return max(1, (width + self.GAP) // (self.CELL + self.GAP))

    def hasHeightForWidth(self) -> bool:
        return True

    def heightForWidth(self, width: int) -> int:
        rows = math.ceil(len(self.cells) / self.columns(width)) if self.cells else 1
        return rows * (self.CELL + self.GAP)

    def sizeHint(self) -> QSize:
        return QSize(600, self.heightForWidth(600))

    def minimumSizeHint(self) -> QSize:
        return QSize(self.CELL, self.CELL)

    def _cell_at(self, x: float, y: float) -> int | None:
        step = self.CELL + self.GAP
        col, row = int(x // step), int(y // step)
        index = row * self.columns(self.width()) + col
        return index if col < self.columns(self.width()) and 0 <= index < len(self.cells) else None

    def mouseMoveEvent(self, event) -> None:  # type: ignore[no-untyped-def]
        index = self._cell_at(event.position().x(), event.position().y())
        if index is None:
            self.setToolTip("")
        else:
            day, outcome = self.cells[index]
            self.setToolTip(f"{day} · {self.names.get(outcome, outcome)}")

    def paintEvent(self, event) -> None:  # type: ignore[no-untyped-def]
        c = colors(self)
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        fills = {"ok": "ok", "api": "warn", "failed": "err"}
        columns, step = self.columns(self.width()), self.CELL + self.GAP
        for index, (_, outcome) in enumerate(self.cells):
            rect = QRectF(
                (index % columns) * step + 0.5,
                (index // columns) * step + 0.5,
                self.CELL - 1,
                self.CELL - 1,
            )
            if outcome in fills:
                color = QColor(c[fills[outcome]])
                if outcome == "ok":
                    color.setAlphaF(0.85)
                painter.setPen(Qt.PenStyle.NoPen)
                painter.setBrush(color)
            else:
                painter.setPen(QPen(QColor(c["line"]), 1))
                painter.setBrush(QColor(c["raised"]))
            painter.drawRoundedRect(rect, 3, 3)


class Swatch(QWidget):
    """A small legend mark: a dot, a square or a short line."""

    def __init__(self, color: str, shape: str = "dot") -> None:
        super().__init__()
        self.color, self.shape = color, shape
        self.setFixedSize(10, 10)

    def paintEvent(self, event) -> None:  # type: ignore[no-untyped-def]
        c = colors(self)
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QColor(c[self.color]))
        if self.shape == "line":
            painter.drawRect(QRectF(0, 4, 10, 2))
        elif self.shape == "square":
            if self.color == "raised":
                painter.setPen(QPen(QColor(c["line"]), 1))
            painter.drawRoundedRect(QRectF(0.5, 0.5, 9, 9), 2, 2)
        else:
            painter.drawEllipse(QRectF(1, 1, 8, 8))
