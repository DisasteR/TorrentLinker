"""Category-aware c411 search filters, loaded without blocking the window."""

from __future__ import annotations

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QIntValidator
from PySide6.QtWidgets import (
    QFrame,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QPushButton,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from torrentlinker.gui.category_picker import CategoryPicker
from torrentlinker.gui.controls import set_role
from torrentlinker.gui.errors import error_text
from torrentlinker.gui.flow_layout import FlowLayout
from torrentlinker.gui.search_widgets import SortChips
from torrentlinker.gui.tasks import run_in_background
from torrentlinker.indexer import Indexer
from torrentlinker.indexer_search import VIDEO_FILTERS, visible_facets
from torrentlinker.torrent_search import SearchFacet, SearchTree, parse_facets, parse_tree


class MultiFilter(QToolButton):
    """A searchable checklist that stays open while choosing several values."""

    changed = Signal()

    def __init__(self, label: str, choices: tuple[tuple[str, str], ...]) -> None:
        super().__init__()
        self.label = label
        self.setAccessibleName(label)
        self.setArrowType(Qt.ArrowType.DownArrow)
        self.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextBesideIcon)
        self.setProperty("chip", "filter")
        self.popup = QFrame(self, Qt.WindowType.Popup)
        layout = QVBoxLayout(self.popup)
        find = QLineEdit()
        find.setPlaceholderText(self.tr("Filter choices…"))
        find.setAccessibleName(self.tr("Filter choices"))
        layout.addWidget(find)
        self.items = QListWidget()
        self.items.setMinimumSize(240, 220)
        for title, value in choices:
            item = QListWidgetItem(title, self.items)
            item.setData(Qt.ItemDataRole.UserRole, value)
            item.setCheckState(Qt.CheckState.Unchecked)
        layout.addWidget(self.items)
        clear = QPushButton(self.tr("Clear"))
        set_role(clear, "quiet")
        clear.clicked.connect(self.clear)
        layout.addWidget(clear)
        find.textChanged.connect(self._filter)
        self.items.itemChanged.connect(self._label)
        self.clicked.connect(self._open)
        self._label()

    def _filter(self, text: str) -> None:
        for row in range(self.items.count()):
            item = self.items.item(row)
            item.setHidden(text.casefold() not in item.text().casefold())

    def _open(self) -> None:
        screen = self.screen().availableGeometry()
        self.popup.adjustSize()
        pos = self.mapToGlobal(self.rect().bottomLeft())
        pos.setX(max(screen.left(), min(pos.x(), screen.right() - self.popup.width())))
        if pos.y() + self.popup.height() > screen.bottom():
            pos.setY(
                max(screen.top(), self.mapToGlobal(self.rect().topLeft()).y() - self.popup.height())
            )
        self.popup.move(pos)
        self.popup.show()
        self.items.setFocus()

    def selected(self) -> list[str]:
        return [
            self.items.item(i).data(Qt.ItemDataRole.UserRole)
            for i in range(self.items.count())
            if self.items.item(i).checkState() == Qt.CheckState.Checked
        ]

    def chosen(self) -> list[tuple[str, str]]:
        """(label, value) of every checked choice."""
        return [
            (item.text(), item.data(Qt.ItemDataRole.UserRole))
            for i in range(self.items.count())
            if (item := self.items.item(i)).checkState() == Qt.CheckState.Checked
        ]

    def uncheck(self, value: str) -> None:
        for row in range(self.items.count()):
            item = self.items.item(row)
            if item.data(Qt.ItemDataRole.UserRole) == value:
                item.setCheckState(Qt.CheckState.Unchecked)

    def clear(self) -> None:
        for row in range(self.items.count()):
            self.items.item(row).setCheckState(Qt.CheckState.Unchecked)

    def _label(self) -> None:
        selected = [
            self.items.item(i).text()
            for i in range(self.items.count())
            if self.items.item(i).checkState() == Qt.CheckState.Checked
        ]
        self.setText(self.label + (f" · {len(selected)}" if selected else ""))
        self.setProperty("active", bool(selected))
        self.style().unpolish(self)
        self.style().polish(self)
        self.setToolTip(", ".join(selected) if selected else self.label)
        self.changed.emit()


class InputFilter(QToolButton):
    """A filter button that opens a small text field (Year, Team, Uploader), shaped like the
    checklist filters. It keeps the QLineEdit calls the rest of the page uses."""

    changed = Signal()

    def __init__(
        self, label: str, placeholder: str, *, max_length: int = 100, validator=None
    ) -> None:
        super().__init__()
        self.label = label
        self.setAccessibleName(label)
        self.setArrowType(Qt.ArrowType.DownArrow)
        self.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextBesideIcon)
        self.setProperty("chip", "filter")
        self.popup = QFrame(self, Qt.WindowType.Popup)
        layout = QVBoxLayout(self.popup)
        self.edit = QLineEdit()
        self.edit.setPlaceholderText(placeholder)
        self.edit.setAccessibleName(label)
        self.edit.setMaxLength(max_length)
        self.edit.setMinimumWidth(220)
        if validator is not None:
            self.edit.setValidator(validator)
        layout.addWidget(self.edit)
        clear = QPushButton(self.tr("Clear"))
        set_role(clear, "quiet")
        clear.clicked.connect(self.clear)
        layout.addWidget(clear)
        self.edit.textChanged.connect(self._label)
        self.edit.returnPressed.connect(self.popup.hide)
        self.clicked.connect(self._open)
        self._label()

    def _open(self) -> None:
        self.popup.adjustSize()
        self.popup.move(self.mapToGlobal(self.rect().bottomLeft()))
        self.popup.show()
        self.edit.setFocus()

    def text(self) -> str:
        return self.edit.text()

    def setText(self, text: str) -> None:
        self.edit.setText(text)

    def clear(self) -> None:
        self.edit.clear()

    def hasAcceptableInput(self) -> bool:
        return self.edit.hasAcceptableInput()

    def _label(self) -> None:
        value = self.edit.text().strip()
        shown = self.fontMetrics().elidedText(value, Qt.TextElideMode.ElideRight, 120)
        QToolButton.setText(self, self.label + (f" · {shown}" if value else ""))
        self.setProperty("active", bool(value))
        self.style().unpolish(self)
        self.style().polish(self)
        self.setToolTip(value or self.label)
        self.changed.emit()


class PalliativeToggle(QPushButton):
    """c411's heart toggle. A click goes Available -> Include -> Only -> Available; the
    combo box it replaces had the same three values."""

    changed = Signal()

    def __init__(self) -> None:
        super().__init__()
        self.setCheckable(True)
        self.setProperty("chip", "filter")
        self.setAccessibleName(self.tr("Revival torrents"))
        self.values = ("", "include", "only")
        self.index = 0
        self.count: int | None = None
        self.clicked.connect(lambda: self.setCurrentIndex((self.index + 1) % 3))
        self._paint()

    def currentIndex(self) -> int:
        return self.index

    def currentData(self) -> str:
        return self.values[self.index]

    def setCurrentIndex(self, index: int) -> None:
        changed = index != self.index
        self.index = index
        self._paint()
        if changed:
            self.changed.emit()

    def set_count(self, count: int | None) -> None:
        self.count = count
        self._paint()

    def _paint(self) -> None:
        self.setChecked(self.index != 0)
        shown = f" · {self.count}" if self.count is not None else ""
        self.setText(
            (
                self.tr("♡ Soins palliatifs") + shown,
                self.tr("♥ With soins palliatifs"),
                self.tr("♥ Soins palliatifs only"),
            )[self.index]
        )
        self.setToolTip(
            (
                self.tr("Click to include soins palliatifs torrents in the results."),
                self.tr("Soins palliatifs are included. Click to show only those."),
                self.tr("Only soins palliatifs. Click to hide them again."),
            )[self.index]
        )


class ActiveFilters(QWidget):
    """The chosen filter values as removable chips, for when the buttons cannot show them all."""

    SHOW_FROM = 4

    def __init__(self) -> None:
        super().__init__()
        self.flow = FlowLayout(self, 6)
        self.hide()

    def show_choices(self, controls: list[MultiFilter]) -> None:
        while item := self.flow.takeAt(0):
            if widget := item.widget():
                widget.hide()
                widget.deleteLater()
        chosen = [(c, label, value) for c in controls for label, value in c.chosen()]
        for control, label, value in chosen:
            chip = QPushButton(f"{control.label}: {label}  ✕")
            chip.setProperty("chip", "pill")
            chip.setToolTip(self.tr("Remove this filter"))
            chip.clicked.connect(lambda _=False, c=control, v=value: c.uncheck(v))
            self.flow.addWidget(chip)
        self.setVisible(len(chosen) >= self.SHOW_FROM)
        self.flow.invalidate()


class SearchFilters(QWidget):
    ready_changed = Signal()
    sort_changed = Signal()
    criteria_changed = Signal()  # any filter changed; the page may search again

    def __init__(self, category: CategoryPicker) -> None:
        super().__init__()
        self.category = category
        self.indexer: Indexer | None = None
        self.tree: SearchTree | None = None
        self.adult = False  # XXX among the categories (Settings → Adult content)
        self._adult_shown = False
        self.ready = True
        self._generation = 0
        self._root: int | None = None
        self._cache: dict[int, list[SearchFacet]] = {}
        self.options: dict[str, MultiFilter] = {}
        self.video: dict[str, MultiFilter] = {}
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(8)
        global_filters = QHBoxLayout()
        global_filters.setSpacing(8)
        self.year = InputFilter(
            self.tr("Year"),
            self.tr("Year"),
            max_length=4,
            validator=QIntValidator(1900, 2100, self),
        )
        self.team = InputFilter(self.tr("Team"), self.tr("Team name or slug"))
        self.uploader = InputFilter(self.tr("Uploader"), self.tr("Uploader"))
        self.exclusive = QPushButton("☆ " + self.tr("Exclusive"))
        self.exclusive.setCheckable(True)
        self.exclusive.setProperty("chip", "filter")
        self.palliative = PalliativeToggle()
        self.crew = QPushButton(self.tr("Cast && crew"))
        self.crew.setCheckable(True)
        self.crew.setProperty("chip", "filter")
        self.crew.setToolTip(self.tr("Also search actors, directors and screenwriters."))
        self.crew.hide()
        self.sort = SortChips()
        self.sort.changed.connect(self.sort_changed)
        self.clear_button = QPushButton(self.tr("Clear all"))
        set_role(self.clear_button, "quiet")
        self.clear_button.clicked.connect(self.clear)
        category.setMaximumWidth(260)
        category.setProperty("chip", "filter")
        global_filters.addWidget(category)
        # Everything but the category menu needs c411's native search.
        self.inputs = (self.year, self.team, self.uploader)
        self.native_controls = (*self.inputs, self.exclusive, self.palliative)
        global_filters.addWidget(self.exclusive)
        global_filters.addWidget(self.palliative)
        for control in self.native_controls:
            control.hide()
        global_filters.addStretch()
        self.status = QLabel()
        self.status.setWordWrap(True)
        self.status.setTextFormat(Qt.TextFormat.PlainText)
        global_filters.addWidget(self.status)
        self.retry = QPushButton(self.tr("Retry filters"))
        self.retry.clicked.connect(self._retry)
        self.retry.hide()
        global_filters.addWidget(self.retry)
        # The page puts its own actions (Follow in RSS) between the filters and Clear all.
        self.action_row = global_filters
        global_filters.addWidget(self.clear_button)
        layout.addLayout(global_filters)
        self.flow = FlowLayout(spacing=8)
        layout.addLayout(self.flow)
        for field in self.inputs:
            self.flow.addWidget(field)
        for signal in (
            *(widget.changed for widget in (*self.inputs, self.palliative)),
            self.exclusive.toggled,
            self.crew.toggled,
        ):
            signal.connect(self.criteria_changed)
        self.chosen = ActiveFilters()
        layout.addWidget(self.chosen)
        category.category_changed.connect(self._category_changed)

    def configure(self, indexer: Indexer) -> None:
        if self.indexer is indexer and self._adult_shown == self.adult:
            return
        self._generation += 1
        self.indexer = indexer
        self._adult_shown = self.adult
        self.tree = None
        self._cache.clear()
        self._root = None
        self._remove_controls()
        self.category.colors = {}
        for widget in self.native_controls:
            widget.setVisible(indexer.is_c411)
        if not indexer.is_c411:
            self.category.setEnabled(True)
            self._set_ready(True)
            return
        self.category.blockSignals(True)
        self.category.select_category(None)
        self.category.blockSignals(False)
        self._load_tree()

    def _set_ready(self, ready: bool, message: str = "") -> None:
        self.ready = ready
        self.status.setText(message)
        self.ready_changed.emit()

    def _load_tree(self) -> None:
        indexer = self.indexer
        if indexer is None:
            return
        self._generation += 1
        generation = self._generation
        self.retry.hide()
        self.category.setEnabled(False)
        self._set_ready(False, self.tr("Loading categories…"))

        def done(tree: SearchTree) -> None:
            if generation != self._generation:
                return
            self.tree = tree
            self.category.colors = tree.colors
            self.category.set_categories(tree.categories)
            self.category.setEnabled(True)
            self._set_ready(True)

        run_in_background(
            lambda: parse_tree(indexer.search_categories(), adult=self.adult),
            done,
            lambda error: self._failed(generation, error),
        )

    def _failed(self, generation: int, error: Exception) -> None:
        if generation != self._generation:
            return
        self.retry.show()
        self._set_ready(False, error_text(error))

    def _retry(self) -> None:
        if self.tree is None:
            self._load_tree()
        else:
            self._category_changed(self.category.current_category)

    def _show_chosen(self) -> None:
        self.chosen.show_choices([*self.video.values(), *self.options.values()])

    def _place(self, facets: list[MultiFilter]) -> None:
        """The facets first, then Year, Team and Uploader, as c411 orders them."""
        for widget in self.inputs:
            self.flow.removeWidget(widget)
        for control in facets:
            self.flow.addWidget(control)
        for widget in self.inputs:
            self.flow.addWidget(widget)

    def _remove_controls(self) -> None:
        for control in [*self.video.values(), *self.options.values()]:
            self.flow.removeWidget(control)
            control.hide()
            control.deleteLater()
        self.options.clear()
        self.video.clear()
        self._show_chosen()

    def _category_changed(self, selected: int | None) -> None:
        tree, indexer = self.tree, self.indexer
        if tree is None or indexer is None or not indexer.is_c411:
            return
        root = tree.root(selected)
        keep_video = (
            {key: control.selected() for key, control in self.video.items()}
            if root == self._root
            else {}
        )
        self._root = root
        self.crew.setVisible(root == 1)
        if root != 1:
            self.crew.setChecked(False)
        self._generation += 1
        generation = self._generation
        self._remove_controls()
        self.retry.hide()

        def done(facets: list[SearchFacet]) -> None:
            if generation != self._generation:
                return
            if root is not None:
                self._cache[root] = facets
            if root == 1:
                self._video_controls()
                for key, values in keep_video.items():
                    control = self.video[key]
                    for i in range(control.items.count()):
                        item = control.items.item(i)
                        if item.data(Qt.ItemDataRole.UserRole) in values:
                            item.setCheckState(Qt.CheckState.Checked)
            for facet in visible_facets(tree, selected, facets):
                self.options[facet.slug] = MultiFilter(facet.label, facet.choices)
            controls = [*self.video.values(), *self.options.values()]
            self._place(controls)
            for control in controls:
                control.changed.connect(self._show_chosen)
                control.changed.connect(self.criteria_changed)
            self.flow.invalidate()
            self._show_chosen()
            self._set_ready(True)

        if root is None:
            done([])
        elif root in self._cache:
            done(self._cache[root])
        else:
            self._set_ready(False, self.tr("Loading category filters…"))
            run_in_background(
                lambda: parse_facets(indexer.search_filter_options(root)),
                done,
                lambda error: self._failed(generation, error),
            )

    def _video_controls(self) -> None:
        # First-party values verified 2026-10-23; docs/research/search-filters.md.
        definitions = [
            ("resolution", self.tr("Resolution"), VIDEO_FILTERS["resolution"]),
            ("source", self.tr("Source"), VIDEO_FILTERS["source"]),
            ("codec", self.tr("Codec"), VIDEO_FILTERS["codec"]),
            ("hdr", self.tr("HDR"), VIDEO_FILTERS["hdr"]),
            ("audioType", self.tr("Audio"), VIDEO_FILTERS["audioType"]),
            ("channels", self.tr("Channels"), VIDEO_FILTERS["channels"]),
            ("lang", self.tr("Language"), VIDEO_FILTERS["lang"]),
        ]
        for key, label, values in definitions:
            self.video[key] = MultiFilter(label, tuple((value, value) for value in values))
        self.video["profile"] = MultiFilter(
            self.tr("Profile"),
            (
                (self.tr("Compatibility"), "compatibility"),
                (self.tr("Home Cinema Pure"), "pure"),
                (self.tr("Home Cinema Optimized"), "hc_optimized"),
                (self.tr("Optimization"), "optimization"),
            ),
        )

    def clear(self) -> None:
        self.year.clear()
        self.uploader.clear()
        self.team.clear()
        self.exclusive.setChecked(False)
        self.palliative.setCurrentIndex(0)
        self.crew.setChecked(False)
        self.sort.reset()
        for control in [*self.video.values(), *self.options.values()]:
            control.clear()
        self.category.select_category(None)

    def params(self, text: str) -> dict[str, str]:
        if not self.ready or self.tree is None:
            raise ValueError(self.tr("Wait for the search filters to load."))
        if self.year.text() and not self.year.hasAcceptableInput():
            raise ValueError(self.tr("Enter a year between 1900 and 2100."))
        params = {"sortBy": self.sort.key, "sortOrder": self.sort.order}
        if text:
            params["name"] = text[:200]
        elif params["sortBy"] == "relevance":
            params["sortBy"] = "createdAt"
        selected = self.category.current_category
        root = self.tree.root(selected)
        if root is not None:
            params["category"] = str(root)
        if selected is not None and selected < 0:
            params["subcat"] = str(-selected)
        for key, value in (
            ("year", self.year.text()),
            ("uploader", self.uploader.text().strip()),
            ("team", self.team.text().strip()),
            ("palliative", self.palliative.currentData()),
        ):
            if value:
                params[key] = value
        if self.exclusive.isChecked():
            params["exclusive"] = "true"
        if root == 1 and self.crew.isChecked() and text:
            params["crew"] = "true"
        for key, control in self.video.items():
            if control.selected():
                params[key] = ",".join(control.selected())
        options = [value for control in self.options.values() for value in control.selected()]
        if options:
            params["options"] = ",".join(options)
        return params
