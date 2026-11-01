"""Search the Indexer and build a persistent queue of selected downloads."""

from __future__ import annotations

import time
from collections.abc import Callable
from dataclasses import replace
from pathlib import Path
from urllib.parse import urlsplit

from PySide6.QtCore import (
    QDateTime,
    QEvent,
    QLocale,
    QModelIndex,
    QObject,
    QPersistentModelIndex,
    QPoint,
    QRect,
    QSettings,
    QStandardPaths,
    Qt,
    QTimer,
    QUrl,
    Signal,
)
from PySide6.QtGui import QAction, QDesktopServices, QMouseEvent, QShowEvent
from PySide6.QtWidgets import (
    QAbstractItemView,
    QButtonGroup,
    QFrame,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QMenu,
    QPushButton,
    QScrollArea,
    QTableWidget,
    QTableWidgetItem,
    QTabWidget,
    QVBoxLayout,
    QWidget,
)

from torrentlinker.category_tags import category_path
from torrentlinker.config import Settings
from torrentlinker.download_queue import QueueEntry, enqueue, entries, queue_key, run_queue
from torrentlinker.gui.browser_tabs import BrowserTabs
from torrentlinker.gui.category_picker import CategoryPicker
from torrentlinker.gui.controls import set_role
from torrentlinker.gui.errors import error_text
from torrentlinker.gui.hover_card import BACKDROP, POSTER, HoverCard
from torrentlinker.gui.library_model import human_size
from torrentlinker.gui.pagination import PageBar
from torrentlinker.gui.poster_loader import PosterLoader
from torrentlinker.gui.search_filters import SearchFilters
from torrentlinker.gui.search_rows import (
    COMPACT_HEIGHT,
    ROW_HEIGHT,
    AgeDelegate,
    ColoredNumberDelegate,
    CompactNameDelegate,
    DownloadDelegate,
    RowView,
    StatsDelegate,
    TitleDelegate,
    TypeBadgeDelegate,
    ZenDelegate,
)
from torrentlinker.gui.search_widgets import CategoryPills
from torrentlinker.gui.send_runner import SendRunner
from torrentlinker.gui.settings_dialog import choose_directory
from torrentlinker.gui.tables import EmptyState, NumberItem, sortable_table
from torrentlinker.gui.tasks import run_in_background
from torrentlinker.gui.torrent_details import TorrentDetailsDialog
from torrentlinker.indexer import Candidate, Indexer, SearchQuery
from torrentlinker.poster_cache import PosterCache
from torrentlinker.qbit import Qbit
from torrentlinker.seeding import Seeding
from torrentlinker.send import SendProgress, SendReport
from torrentlinker.store import Store
from torrentlinker.torrent_details import website_url
from torrentlinker.torrent_search import ListingRow, SearchPage, native_search
from torrentlinker.zen import ZenTorrent, added_values, note

PAGE_SIZE = 100
RESULT_WIDTHS = (420, 100, 80, 240, 150, 110, 90, 160, 80, 90, 120, 60, 70)
DOWNLOAD_COLUMN = 11
# Comfortable rows fold seeders, leechers, completed and uploader into painted cells.
COMFORTABLE_HIDDEN = (2, 3, 4, 6, 8, 9, 10, 12)
COMPACT_HIDDEN = (4, 10)
# Compact follows c411's table: type, name with tags, comments, age, size, completed, seeders,
# leechers; then our Zen columns and the download button.
COMPACT_ORDER = (3, 0, 12, 7, 1, 9, 2, 8, 5, 6, 11)
COMFORTABLE_ORDER = (0, 1, 7, 5, 11)
COMPACT_WIDTHS = {3: 110, 12: 70, 7: 90, 1: 90, 9: 80, 2: 70, 8: 70, 5: 100, 6: 80, 11: 56}
# The sort each table header applies on c411 (the same as the sort chips).
HEADER_SORTS = {
    3: "category",
    0: "name",
    12: "comments",
    7: "createdAt",
    1: "size",
    9: "completions",
    2: "seeders",
    8: "leechers",
}


class SearchTab(QWidget):
    manage_requested = Signal(str)
    subscribe_requested = Signal(str)

    def __init__(
        self,
        store: Store,
        settings: Settings,
        indexer: Callable[[], Indexer],
        client: Callable[[], Qbit],
    ) -> None:
        super().__init__()
        self.store, self.settings = store, settings
        self.indexer, self.client = indexer, client
        self.runner: SendRunner | None = None
        self._searching = False
        self._seeding: Seeding | None = None
        self._client_hashes: set[str] = set()
        self._queued_hashes: set[str] = set()
        self._queue_version: tuple[object, str, int, int] | None = None
        self._results: list[Candidate] = []
        self._offset = 0
        self._query = SearchQuery()
        self._native_params: dict[str, str] = {}
        self._native_page: SearchPage | None = None
        self._result_indexer: Indexer | None = None
        workspace = QVBoxLayout(self)
        workspace.setContentsMargins(0, 0, 0, 0)
        header = QHBoxLayout()
        title = QLabel(self.tr("Torrents"))
        title.setProperty("role", "heading")
        header.addWidget(title)
        self.queue_notice = QLabel()
        self.queue_notice.setWordWrap(True)
        self.queue_notice.setTextFormat(Qt.TextFormat.PlainText)
        header.addWidget(self.queue_notice, 1)
        self.density_group = QButtonGroup(self)
        self.density_buttons: dict[str, QPushButton] = {}
        for key, label in (
            ("compact", self.tr("Compact")),
            ("comfortable", self.tr("Comfortable")),
        ):
            button = QPushButton(label)
            button.setCheckable(True)
            button.setProperty("chip", "filter")
            button.clicked.connect(lambda _=False, k=key: self.set_density(k, remember=True))
            self.density_group.addButton(button)
            self.density_buttons[key] = button
            header.addWidget(button)
        self.posters_button = QPushButton(self.tr("Posters"))
        self.posters_button.setCheckable(True)
        self.posters_button.setProperty("chip", "filter")
        self.posters_button.setToolTip(
            self.tr("Show posters. They are fetched from TMDB, a few at a time, and kept on disk.")
        )
        self.posters_button.clicked.connect(self._posters_toggled)
        header.addWidget(self.posters_button)
        self.view_queue = QPushButton()
        set_role(self.view_queue, "quiet")
        self.view_queue.clicked.connect(self.show_queue)
        header.addWidget(self.view_queue)
        workspace.addLayout(header)
        self.home = QWidget()
        self.browser = BrowserTabs(self.home)
        self.browser.close_requested.connect(self.close_detail)
        workspace.addWidget(self.browser)
        self._detail_tabs: dict[str, TorrentDetailsDialog] = {}
        layout = QVBoxLayout(self.home)
        layout.setContentsMargins(0, 8, 0, 0)
        self.category = CategoryPicker()
        self.category.setAccessibleName(self.tr("Category"))
        self.filters = SearchFilters(self.category)
        self.query = QLineEdit()
        self.query.setPlaceholderText(self.tr("Title, original title, release name…"))
        self.query.setAccessibleName(self.tr("Search torrents"))
        self.query.setClearButtonEnabled(True)
        self.query.setMaxLength(200)
        self.query.returnPressed.connect(lambda: self.search())
        self.query.textChanged.connect(
            lambda text: self.filters.sort.set_relevance_available(bool(text.strip()))
        )
        self.search_button = QPushButton(self.tr("Search"))
        set_role(self.search_button, "primary")
        self.search_button.clicked.connect(lambda: self.search())
        self.search_bar = QFrame()
        self.search_bar.setObjectName("searchBar")
        search = QHBoxLayout(self.search_bar)
        search.setContentsMargins(14, 6, 6, 6)
        search.addWidget(self.query, 1)
        search.addWidget(self.filters.crew)
        search.addWidget(self.search_button)
        self.subscribe = QPushButton(self.tr("Subscribe to RSS"))
        set_role(self.subscribe, "quiet")
        self.subscribe.clicked.connect(
            lambda: self.subscribe_requested.emit(self.query.text().strip())
        )
        self.filters.action_row.insertWidget(self.filters.action_row.count() - 1, self.subscribe)
        self.pills = CategoryPills()
        self.pills.category_chosen.connect(self._pill_chosen)
        self.category.category_changed.connect(self.pills.select)
        self.category.categories_changed.connect(self.pills.set_categories)
        self.pills.set_categories(self.category.categories, self.category.colors)
        self.page_bar = PageBar()
        self.page_bar_bottom = PageBar()
        for bar in (self.page_bar, self.page_bar_bottom):
            bar.page_requested.connect(lambda n: self.search((n - 1) * PAGE_SIZE))
        self.previous, self.next = self.page_bar.previous, self.page_bar.next
        self.filters.sort_changed.connect(self._sort_changed)
        self._search_pending = False
        # Filters search by themselves a moment after the last change, as c411's do.
        self._auto_search = QTimer(self)
        self._auto_search.setSingleShot(True)
        self._auto_search.setInterval(700)
        self._auto_search.timeout.connect(self._filters_settled)
        self.filters.criteria_changed.connect(self._auto_search.start)
        self.pages = QTabWidget()
        layout.addWidget(self.pages, 1)
        results_page = QWidget()
        results_layout = QVBoxLayout(results_page)
        results_layout.setContentsMargins(0, 0, 0, 0)
        # The search controls and the results scroll together; the actions below stay in reach.
        body = QWidget()
        body_layout = QVBoxLayout(body)
        body_layout.setContentsMargins(0, 0, 8, 0)
        body_layout.setSpacing(12)
        body_layout.addWidget(self.search_bar)
        body_layout.addWidget(self.pills)
        body_layout.addWidget(self.filters)
        body_layout.addWidget(self.page_bar)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        scroll.setWidget(body)
        results_layout.addWidget(scroll, 1)
        self.results = self._table(
            [
                self.tr("Torrent"),
                self.tr("Size"),
                self.tr("Seeders"),
                self.tr("Category"),
                self.tr("Revival"),
                self.tr("≈ Zen/day"),
                self.tr("≈ Note"),
                self.tr("Age"),
                self.tr("Leechers"),
                self.tr("Completed"),
                self.tr("Uploader"),
                "",
                self.tr("Comm."),
            ]
        )
        for column, width in enumerate(RESULT_WIDTHS):
            self.results.setColumnWidth(column, width)
        self.results.horizontalHeader().moveSection(5, 3)
        self.results.horizontalHeader().moveSection(6, 4)
        self.results.horizontalHeader().moveSection(7, 3)
        # Leechers sit next to Seeders in the compact view.
        columns = self.results.horizontalHeader()
        columns.moveSection(columns.visualIndex(8), columns.visualIndex(2) + 1)
        published_header = self.results.horizontalHeaderItem(7)
        assert published_header is not None
        published_header.setToolTip(
            self.tr("Time since publication. Hover for the exact date. Sorts by publication time.")
        )
        self.results_empty = EmptyState(
            self.results, self.tr("Search for torrents, then select the ones to download.")
        )
        self.results.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.results.setHorizontalScrollMode(QAbstractItemView.ScrollMode.ScrollPerPixel)
        self.results.cellClicked.connect(self._cell_clicked)
        self.results.horizontalHeader().sectionClicked.connect(self._header_clicked)
        self.results_card = QFrame()
        self.results_card.setObjectName("resultsCard")
        card = QVBoxLayout(self.results_card)
        card.setContentsMargins(0, 0, 0, 0)
        card.setSpacing(0)
        card_head = QWidget()
        head = QHBoxLayout(card_head)
        head.setContentsMargins(16, 12, 16, 4)
        title = QLabel(self.tr("Search results"))
        title.setObjectName("resultsTitle")
        head.addWidget(title)
        head.addStretch()
        self.results_summary = QLabel()
        self.results_summary.setProperty("role", "muted")
        self.results_summary.setTextFormat(Qt.TextFormat.PlainText)
        head.addWidget(self.results_summary)
        sort_bar = QWidget()
        sort_row = QHBoxLayout(sort_bar)
        sort_row.setContentsMargins(12, 0, 12, 8)
        sort_label = QLabel(self.tr("SORT"))
        sort_label.setProperty("role", "muted")
        sort_row.addWidget(sort_label)
        sort_row.addWidget(self.filters.sort, 1)
        card.addWidget(card_head)
        card.addWidget(sort_bar)
        card.addWidget(self.results)
        body_layout.addWidget(self.results_card)
        body_layout.addWidget(self.page_bar_bottom)
        body_layout.addStretch(1)
        self.result_status = QLabel()
        self.result_status.setWordWrap(True)
        self.result_status.setTextFormat(Qt.TextFormat.PlainText)
        results_layout.addWidget(self.result_status)
        self.selection_bar = QFrame()
        self.selection_bar.setObjectName("selectionBar")
        selection = QHBoxLayout(self.selection_bar)
        selection.setContentsMargins(12, 8, 8, 8)
        self.selection_label = QLabel()
        selection.addWidget(self.selection_label)
        selection.addStretch()
        self.details_button = QPushButton(self.tr("Open in tabs"))
        self.details_button.clicked.connect(self.show_details)
        self.website_button = QPushButton(self.tr("Open on website"))
        set_role(self.website_button, "quiet")
        self.website_button.clicked.connect(self.open_website)
        self.results.itemDoubleClicked.connect(self._open_result_item)
        self.folder = QLineEdit()
        self.folder.setPlaceholderText(self.tr("Use category folders from Settings"))
        self.folder.setAccessibleName(self.tr("Download Folder"))
        self.folder.setMaximumWidth(280)
        browse = QPushButton(self.tr("Browse…"))
        browse.clicked.connect(self._browse)
        self.add_button = QPushButton(self.tr("Add selected to queue"))
        set_role(self.add_button, "primary")
        self.add_button.clicked.connect(self.add_selected)
        for widget in (self.details_button, self.website_button, self.folder, browse):
            selection.addWidget(widget)
        selection.addWidget(self.add_button)
        results_layout.addWidget(self.selection_bar)
        self.selection_bar.hide()
        self.queue_tabs = QAction(self.tr("Queue all tabs"), self)
        self.queue_tabs.setToolTip(
            self.tr("Queue the torrents in open tabs without starting downloads.")
        )
        self.queue_tabs.triggered.connect(lambda: self.queue_open_tabs(False))
        self.download_tabs = QAction(self.tr("Download all tabs"), self)
        self.download_tabs.setToolTip(
            self.tr("Download only the torrents in open tabs. Other queued torrents stay waiting.")
        )
        self.download_tabs.triggered.connect(lambda: self.queue_open_tabs(True))
        self.browser.extra_actions = [self.queue_tabs, self.download_tabs]
        self.pages.addTab(results_page, self.tr("Results"))
        queue_page = QWidget()
        queue_layout = QVBoxLayout(queue_page)
        queue_layout.setContentsMargins(0, 0, 0, 0)
        self.back_button = QPushButton(self.tr("← Back to results"))
        set_role(self.back_button, "quiet")
        self.back_button.clicked.connect(self.show_results)
        queue_layout.addWidget(self.back_button, 0, Qt.AlignmentFlag.AlignLeft)
        hint = QLabel(
            self.tr(
                "Start sends queued torrents to your torrent client. "
                "Pause stops further sends; existing downloads continue there."
            )
        )
        hint.setWordWrap(True)
        queue_layout.addWidget(hint)
        self.manage_button = QPushButton(self.tr("Open in Torrents"))
        set_role(self.manage_button, "quiet")
        self.manage_button.clicked.connect(self._open_in_torrents)
        queue_layout.addWidget(self.manage_button)
        self.queue = self._table(
            [
                self.tr("Torrent"),
                self.tr("Size"),
                self.tr("≈ Zen/day"),
                self.tr("≈ Note"),
                self.tr("Download Folder"),
                self.tr("Status"),
                self.tr("Details"),
                self.tr("Client"),
            ]
        )
        self.queue.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.queue.customContextMenuRequested.connect(self._queue_context_menu)
        self.queue_summary = QLabel()
        self.queue_summary.setWordWrap(True)
        queue_layout.addWidget(self.queue_summary)
        self.queue_empty = EmptyState(
            self.queue, self.tr("Your queue is empty. Add torrents from the search results.")
        )
        for column, width in enumerate((330, 100, 100, 90, 230, 140, 230)):
            self.queue.setColumnWidth(column, width)
        queue_layout.addWidget(self.queue, 1)
        controls = QHBoxLayout()
        self.start = QPushButton(self.tr("Start queue"))
        set_role(self.start, "primary")
        self.start.clicked.connect(lambda: self.start_queue())
        self.pause = QPushButton(self.tr("Pause"))
        self.pause.clicked.connect(self.stop)
        self.retry = QPushButton(self.tr("Retry selected"))
        self.retry.clicked.connect(self.retry_selected)
        self.remove = QPushButton(self.tr("Remove selected"))
        set_role(self.remove, "quiet")
        self.remove.clicked.connect(self.remove_selected)
        self.clear_done = QPushButton(self.tr("Clear sent"))
        set_role(self.clear_done, "quiet")
        self.clear_done.clicked.connect(self.clear_sent)
        for button in (self.start, self.pause, self.retry, self.remove, self.clear_done):
            controls.addWidget(button)
        controls.addStretch()
        queue_layout.addLayout(controls)
        self.queue_status = QLabel(self.tr("Queue saved. Press Start queue when ready."))
        self.queue_status.setTextFormat(Qt.TextFormat.PlainText)
        self.queue_status.setWordWrap(True)
        queue_layout.addWidget(self.queue_status)
        self.pages.addTab(queue_page, self.tr("Download queue"))
        # The header's queue button and the back button replace the page tabs.
        self.pages.tabBar().hide()
        self.results.itemSelectionChanged.connect(self._buttons)
        self.queue.itemSelectionChanged.connect(self._buttons)
        self.filters.ready_changed.connect(self._buttons)
        self.filters.ready_changed.connect(self._run_pending_search)
        self.refresh_categories()
        self.refresh_queue()
        self._default_delegate = self.results.itemDelegate()
        self.hover = HoverCard()
        self._hover_row = -1
        self._hover_timer = QTimer(self)
        self._hover_timer.setSingleShot(True)
        self._hover_timer.setInterval(450)
        self._hover_timer.timeout.connect(self._show_hover)
        self.results.viewport().setMouseTracking(True)
        self.results.viewport().installEventFilter(self)
        self.results.verticalScrollBar().valueChanged.connect(lambda _: self._hide_hover())
        self._delegates = {
            0: TitleDelegate(self._row_view, self.results),
            1: StatsDelegate(self._row_view, self.results),
            5: ZenDelegate(self._row_view, self.results),
            7: AgeDelegate(self._row_view, self.results),
            DOWNLOAD_COLUMN: DownloadDelegate(self._row_view, self.results),
        }
        self.posters = PosterLoader(self._poster_cache(), self)
        self.posters.loaded.connect(lambda _url: self._posters_arrived())
        shown = bool(self._ui_settings().value("search/posters", True, bool))
        self.posters.enabled = shown
        self.posters_button.setChecked(shown)
        self._compact_title = CompactNameDelegate(self._row_view, self.results)
        self._compact_delegates = {
            3: TypeBadgeDelegate(self._row_view, self.results),
            0: self._compact_title,
            2: ColoredNumberDelegate("#22b573", self.results),
            8: ColoredNumberDelegate("#ef4444", self.results),
            9: ColoredNumberDelegate(None, self.results),
            12: ColoredNumberDelegate(None, self.results),
            DOWNLOAD_COLUMN: DownloadDelegate(self._row_view, self.results),
        }
        saved = self._ui_settings().value("search/density", "comfortable")
        self.set_density(saved if saved in self.density_buttons else "comfortable")
        self.queue_timer = QTimer(self)
        self.queue_timer.setInterval(1000)
        self.queue_timer.timeout.connect(self._poll_queue)
        self.queue_timer.start()
        self.age_timer = QTimer(self)
        self.age_timer.setInterval(60_000)
        self.age_timer.timeout.connect(self.refresh_ages)
        self.age_timer.start()

    def _table(self, headings: list[str]) -> QTableWidget:
        table = QTableWidget(0, len(headings))
        table.setHorizontalHeaderLabels(headings)
        table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        table.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection)
        sortable_table(table)
        table.horizontalHeader().resizeSection(0, 380)
        return table

    def showEvent(self, event: QShowEvent) -> None:
        super().showEvent(event)
        self._configure_filters()
        self.refresh_queue()
        self.refresh_ages()

    def _configure_filters(self) -> Indexer | None:
        try:
            indexer = self.indexer()
            if not indexer.api_key:
                raise ValueError(self.tr("Set your Indexer API key in Settings first."))
            self.filters.configure(indexer)
            self.filters.sort.setVisible(indexer.is_c411)
            if not indexer.is_c411:
                self.category.set_categories(self.settings.indexer_categories)
            return indexer
        except Exception as error:
            self.result_status.setText(error_text(error))
            if not self._results:
                self._empty_message(error_text(error), error=True)
            return None

    def _pill_chosen(self, category: int | None) -> None:
        """A category pill filters at once, as c411's does."""
        self.category.select_category(category)
        self._search_pending = True
        self._run_pending_search()

    def _filters_settled(self) -> None:
        if self.filters.year.text() and not self.filters.year.hasAcceptableInput():
            return  # a half-typed year is not a search yet
        self._search_pending = True
        self._run_pending_search()

    def _sort_changed(self) -> None:
        if self._native_page is not None:
            self.search()

    def _run_pending_search(self) -> None:
        if not self._search_pending:
            return
        if self.filters.ready:
            self._search_pending = False
            self.search()
        elif not self.filters.retry.isHidden():
            self._search_pending = False  # the filters failed to load; do not search later

    def refresh_categories(self) -> None:
        if self.filters.tree is None:
            self.category.set_categories(self.settings.indexer_categories)
        if self.isVisible():
            self._configure_filters()

    def search(self, offset: int | None = None) -> None:
        if self._searching:
            return
        indexer = self._configure_filters()
        if indexer is None or not self.filters.ready:
            return
        # A changed Indexer cannot page through the previous Indexer's results.
        if self._result_indexer is not None and self._result_indexer is not indexer:
            offset = None
        if offset is None:
            cid = self.category.current_category
            self._query = SearchQuery(
                self.query.text().strip(),
                categories=(cid,) if cid is not None else (),
                limit=PAGE_SIZE,
            )
            if indexer.is_c411:
                try:
                    self._native_params = self.filters.params(self.query.text().strip())
                except ValueError as error:
                    self.result_status.setText(str(error))
                    return
            offset = 0
        self._offset = offset
        from dataclasses import replace

        query = replace(self._query, offset=offset)
        self._searching = True
        self.result_status.setText(self.tr("Searching…"))
        self._buttons()
        if indexer.is_c411:
            params = dict(self._native_params)
            page = offset // PAGE_SIZE + 1
            run_in_background(
                lambda: native_search(indexer, params, page),
                lambda result: self._show_native(result, indexer),
                self._search_failed,
            )
        else:
            self._native_page = None
            run_in_background(
                lambda: indexer.search(query, cached=False),
                lambda rows: self._show_results(rows, indexer),
                self._search_failed,
            )

    def _show_native(self, page: SearchPage, indexer: Indexer) -> None:
        self._native_page = page
        self._offset = (page.page - 1) * PAGE_SIZE
        self._show_results(page.rows, indexer)
        self.pills.set_counts(page.root_counts)
        self.filters.palliative.set_count(page.palliative_count)
        summary = self.tr("{0} torrents").format(QLocale().toString(page.total))
        if page.total_size is not None:
            summary += " · " + human_size(page.total_size)
        self.results_summary.setText(summary)
        message = self.tr("{0} results · {1} matches · page {2} of {3}").format(
            len(page.rows), page.total, page.page, max(1, page.total_pages)
        )
        if page.limited:
            message += " · " + self.tr(
                "c411 limits this search to {0} accessible results. Narrow your filters."
            ).format(page.per_page * page.total_pages)
        self.result_status.setText(message)

    def _empty_message(self, text: str, *, error: bool = False) -> None:
        """What the empty results area says, and whether the status line looks like an error."""
        self.results_empty.label.setText(text)
        for label in (self.results_empty.label, self.result_status):
            label.setProperty("role", "error" if error else "muted")
            label.style().unpolish(label)
            label.style().polish(label)

    def _search_failed(self, error: Exception) -> None:
        self._searching = False
        # Avoid offering paging against a new query whose request failed.
        self._results = []
        self._native_page = None
        self._offset = 0
        self.results.setRowCount(0)
        self.results_summary.clear()
        self.result_status.setText(error_text(error))
        self._empty_message(error_text(error), error=True)
        self._buttons()

    def _show_results(self, rows: list[Candidate], indexer: Indexer) -> None:
        self._searching = False
        self._results, self._result_indexer = rows, indexer
        if self._native_page is None:
            self.results_summary.clear()
        self.results.setSortingEnabled(False)
        self.results.setRowCount(len(rows))
        for row, c in enumerate(rows):
            item = QTableWidgetItem(c.title)
            item.setData(Qt.ItemDataRole.UserRole, row)
            # c411's rows open a card on hover; the plain tooltip is for rows without details.
            if not (self._native_page and (c.infohash or "") in self._native_page.details):
                item.setToolTip(c.title)
            self.results.setItem(row, 0, item)
            self.results.setItem(row, 1, NumberItem(human_size(c.size), c.size))
            self.results.setItem(row, 2, NumberItem(str(c.seeders), c.seeders))
            if c.published_at is not None:
                date = QDateTime.fromMSecsSinceEpoch(round(c.published_at * 1000)).toLocalTime()
                published = NumberItem(self._age(c.published_at, time.time()), c.published_at)
                published.setToolTip(date.toString("yyyy-MM-dd HH:mm:ss t"))
            else:
                published = NumberItem("–", float("-inf"))
                published.setToolTip(self.tr("Publication date unavailable."))
            self.results.setItem(row, 7, published)
            self.results.setItem(row, 3, QTableWidgetItem(self._category_label(c)))
            listing = self._native_page.details.get(c.infohash or "") if self._native_page else None
            for column, value in (
                (8, listing and listing.leechers),
                (9, listing and listing.completions),
            ):
                self.results.setItem(
                    row,
                    column,
                    NumberItem("–", float("-inf"))
                    if listing is None
                    else NumberItem(str(value), value or 0),
                )
            self.results.setItem(row, 10, QTableWidgetItem(listing.uploader if listing else ""))
            self.results.setItem(
                row,
                12,
                NumberItem("–", float("-inf"))
                if listing is None
                else NumberItem(str(listing.comments), listing.comments),
            )
            action = QTableWidgetItem()
            action.setToolTip(self.tr("Add to the download queue"))
            self.results.setItem(row, 11, action)
            revival = bool(self._native_page and c.infohash in self._native_page.palliative)
            self.results.setItem(
                row, 4, QTableWidgetItem(self.tr("Soins palliatifs") if revival else "")
            )
        self.results.setSortingEnabled(True)
        self._empty_message(
            self.tr("No torrent matches. Try fewer filters, or another spelling of the title.")
        )
        self._fit_table()
        self._sync_header_indicator()
        self.result_status.setText(
            self.tr("{0} results · page {1}").format(len(rows), self._offset // PAGE_SIZE + 1)
        )
        self.pages.setCurrentIndex(0)
        self.refresh_estimates()
        self._buttons()

    def _category_label(self, candidate: Candidate) -> str:
        if self._native_page:
            return self._native_page.labels.get(candidate.infohash or "", "")
        cats = self.settings.indexer_categories
        return " / ".join(
            cats[cid].name if cid in cats else str(cid) for cid in candidate.categories
        )

    def _ui_settings(self) -> QSettings:
        return QSettings()

    def _poster_cache(self) -> PosterCache:
        folder = Path(
            QStandardPaths.writableLocation(QStandardPaths.StandardLocation.AppDataLocation)
        )
        return PosterCache(folder / "posters")

    def _posters_toggled(self, shown: bool) -> None:
        self.posters.enabled = shown
        self._ui_settings().setValue("search/posters", shown)
        self.results.viewport().update()

    def set_density(self, density: str, *, remember: bool = False) -> None:
        self.density = density
        self.density_buttons[density].setChecked(True)
        if remember:
            self._ui_settings().setValue("search/density", density)
        comfortable = density == "comfortable"
        table, header = self.results, self.results.horizontalHeader()
        chosen = self._delegates if comfortable else self._compact_delegates
        for column in range(table.columnCount()):
            table.setItemDelegateForColumn(column, chosen.get(column, self._default_delegate))
        for position, column in enumerate(COMFORTABLE_ORDER if comfortable else COMPACT_ORDER):
            header.moveSection(header.visualIndex(column), position)
        for column in range(table.columnCount()):
            table.setColumnHidden(
                column, column in (COMFORTABLE_HIDDEN if comfortable else COMPACT_HIDDEN)
            )
        header.setVisible(not comfortable)
        table.setAlternatingRowColors(not comfortable)
        table.setHorizontalScrollBarPolicy(
            Qt.ScrollBarPolicy.ScrollBarAlwaysOff
            if comfortable
            else Qt.ScrollBarPolicy.ScrollBarAsNeeded
        )
        mode = QHeaderView.ResizeMode
        for column in range(table.columnCount()):
            header.setSectionResizeMode(column, mode.Interactive)
            table.setColumnWidth(column, RESULT_WIDTHS[column])
        if comfortable:
            header.setSectionResizeMode(0, mode.Stretch)
            for column, width in {1: 130, 5: 150, 7: 130, 11: 64}.items():
                header.setSectionResizeMode(column, mode.Fixed)
                table.setColumnWidth(column, width)
        else:
            header.setSectionResizeMode(0, mode.Stretch)
            for column, width in COMPACT_WIDTHS.items():
                header.setSectionResizeMode(column, mode.Fixed)
                table.setColumnWidth(column, width)
        size = ROW_HEIGHT if comfortable else COMPACT_HEIGHT
        table.verticalHeader().setDefaultSectionSize(size)
        for row in range(table.rowCount()):
            table.setRowHeight(row, size)
        self._fit_table()

    def _header_clicked(self, column: int) -> None:
        """On c411 a compact header sorts the whole search, not only this page."""
        if self.density == "compact" and self._native_page is not None and column in HEADER_SORTS:
            self.filters.sort.choose(HEADER_SORTS[column])

    def _sync_header_indicator(self) -> None:
        sort = self.filters.sort
        column = next((c for c, key in HEADER_SORTS.items() if key == sort.key), -1)
        header = self.results.horizontalHeader()
        header.blockSignals(True)
        order = (
            Qt.SortOrder.DescendingOrder if sort.order == "desc" else Qt.SortOrder.AscendingOrder
        )
        header.setSortIndicator(column if self._native_page is not None else -1, order)
        header.blockSignals(False)

    def _fit_table(self) -> None:
        """The table is as tall as its rows, so the page scrolls instead of the table."""
        table = self.results
        comfortable = self.density == "comfortable"
        head = 0 if comfortable else table.horizontalHeader().sizeHint().height()
        bar = 0 if comfortable else 18
        rows = table.rowCount() * table.verticalHeader().defaultSectionSize()
        table.setFixedHeight(max(300, rows + head + bar + 4))

    def _row_view(self, index: QModelIndex | QPersistentModelIndex) -> RowView | None:
        item = self.results.item(index.row(), 0)
        number = item.data(Qt.ItemDataRole.UserRole) if item else None
        if not isinstance(number, int) or not 0 <= number < len(self._results):
            return None
        candidate = self._results[number]
        page = self._native_page
        label = self._category_label(candidate)
        age = self.results.item(index.row(), 7)
        details = page.details.get(candidate.infohash or "") if page else None
        return RowView(
            candidate.title,
            label,
            human_size(candidate.size),
            candidate.seeders,
            age.text() if age else "",
            details,
            bool(page and candidate.infohash in page.palliative),
            self.pills.color_for(label.split(" / ")[0]) or "#64748b",
            state=self._known_state(candidate.infohash),
            poster=self.posters.pixmap(details.poster_url) if details else None,
        )

    def _known_state(self, infohash: str | None) -> str:
        """Local knowledge only: the last seeding view and our queue, no client request."""
        key = (infohash or "").lower()
        if key in self._client_hashes:
            return "client"
        return "queued" if key in self._queued_hashes else ""

    def eventFilter(self, watched: QObject, event: QEvent) -> bool:
        if watched is self.results.viewport():
            kind = event.type()
            if kind == QEvent.Type.MouseMove and isinstance(event, QMouseEvent):
                row = self.results.rowAt(int(event.position().y()))
                if row != self._hover_row:
                    self._hide_hover()
                    self._hover_row = row
                    if row >= 0:
                        self._hover_timer.start()
            elif kind in (QEvent.Type.Leave, QEvent.Type.Wheel, QEvent.Type.MouseButtonPress):
                self._hide_hover()
        return super().eventFilter(watched, event)

    def _hide_hover(self) -> None:
        self._hover_timer.stop()
        self._hover_row = -1
        self.hover.hide()

    def _hover_info(self, row: int) -> tuple[Candidate, ListingRow] | None:
        item = self.results.item(row, 0)
        number = item.data(Qt.ItemDataRole.UserRole) if item else None
        page = self._native_page
        if page is None or not isinstance(number, int) or not 0 <= number < len(self._results):
            return None
        candidate = self._results[number]
        info = page.details.get(candidate.infohash or "")
        return (candidate, info) if info is not None and (info.title or info.overview) else None

    def _show_hover(self) -> None:
        found = self._hover_info(self._hover_row) if self._hover_row >= 0 else None
        if found is None or not self.isVisible():
            return
        candidate, info = found
        label = self._category_label(candidate).split(" / ")[-1]
        self.hover.fill(
            info,
            candidate.title,
            label,
            self.posters.pixmap(info.poster_url, POSTER),
            self.posters.pixmap(info.backdrop_url, BACKDROP),
        )
        model_index = self.results.model().index(self._hover_row, 0)
        rect = self.results.visualRect(model_index)
        top_left = self.results.viewport().mapToGlobal(rect.topLeft())
        anchor = QRect(top_left, rect.size())
        anchor.setRight(
            self.results.viewport().mapToGlobal(self.results.viewport().rect().topRight()).x()
        )
        self.hover.place(anchor, self.window().frameGeometry() & self.screen().availableGeometry())
        self.hover.show()

    def _posters_arrived(self) -> None:
        self.results.viewport().update()
        if self.hover.isVisible() and self._hover_row >= 0:
            self._show_hover()

    def _cell_clicked(self, row: int, column: int) -> None:
        item = self.results.item(row, 0)
        if column == DOWNLOAD_COLUMN and item is not None:
            self._add_candidates([self._results[item.data(Qt.ItemDataRole.UserRole)]])

    def _age(self, published_at: float, now: float) -> str:
        seconds = now - published_at
        if seconds < -60:
            return self.tr("Future date")
        if seconds < 60:
            return self.tr("Just now")
        for unit, singular, plural in (
            (365 * 86400, self.tr("1 year"), self.tr("{0} years")),
            (30 * 86400, self.tr("1 month"), self.tr("{0} months")),
            (86400, self.tr("1 day"), self.tr("{0} days")),
            (3600, self.tr("1 hour"), self.tr("{0} hours")),
            (60, self.tr("1 minute"), self.tr("{0} minutes")),
        ):
            count = int(seconds // unit)
            if count:
                return singular if count == 1 else plural.format(count)
        return self.tr("Just now")

    def refresh_ages(self) -> None:
        if not self.isVisible():
            return
        now = time.time()
        # Keep the stored timestamp, selection and ordering while refreshing labels.
        self.results.setSortingEnabled(False)
        for row in range(self.results.rowCount()):
            item = self.results.item(row, 7)
            if item is not None:
                timestamp = item.data(Qt.ItemDataRole.UserRole)
                if timestamp != float("-inf"):
                    item.setText(self._age(timestamp, now))
        self.results.setSortingEnabled(True)

    def set_seeding(self, view: Seeding) -> None:
        self._seeding = view
        self._client_hashes = {row.torrent.hash.lower() for row in view.rows}
        self.results.viewport().update()
        self.refresh_estimates()

    def refresh_estimates(self) -> None:
        indexer = self._result_indexer
        if indexer is not None:
            self._refresh_table_estimates(
                self.results,
                {i: (candidate, indexer.is_c411) for i, candidate in enumerate(self._results)},
                5,
                6,
            )
        self._refresh_queue_estimates()

    def _refresh_queue_estimates(self) -> None:
        # Use each saved origin, before any search and after changing Indexers.
        # Estimate the displayed snapshot: a background send can remove entries
        # from the Store before the next queue poll rebuilds the table.
        self._refresh_table_estimates(
            self.queue,
            {
                entry.key: (entry.candidate, urlsplit(entry.indexer_url).hostname == "c411.org")
                for entry in self._queue_snapshot
            },
            2,
            3,
        )

    def _refresh_table_estimates(
        self,
        table: QTableWidget,
        candidates: dict[int | str, tuple[Candidate, bool]],
        zen_column: int,
        note_column: int,
    ) -> None:
        alongside = self._seeding.active if self._seeding else []
        current = (
            {row.torrent.hash.lower(): row.zen for row in self._seeding.rows if row.zen is not None}
            if self._seeding
            else {}
        )
        current_notes = (
            {
                row.torrent.hash.lower(): note(self.settings.zen.zen_class, torrent)
                for row, torrent in zip(
                    (row for row in self._seeding.rows if row.zen is not None),
                    self._seeding.active,
                    strict=True,
                )
            }
            if self._seeding
            else {}
        )
        prospective = {
            identity: ZenTorrent(
                c.size, c.seeders + 1, category=c.categories[-1] if c.categories else None
            )
            for identity, (c, _) in candidates.items()
        }
        values = dict(
            zip(
                candidates,
                added_values(self.settings.zen, list(prospective.values()), alongside),
                strict=True,
            )
        )
        table.setSortingEnabled(False)
        for row in range(table.rowCount()):
            title = table.item(row, 0)
            if title is None:
                continue
            identity = title.data(Qt.ItemDataRole.UserRole)
            candidate, is_c411 = candidates[identity]
            existing = current.get((candidate.infohash or "").lower())
            value = existing if existing is not None else values[identity]
            if not is_c411:
                item = NumberItem("–", float("-inf"))
                tooltip = self.tr("Zen estimates are available for c411 torrents.")
            else:
                item = NumberItem(f"≈ {value:.2f}", value)
                tooltip = (
                    self.tr("Already seeding: current Zen Estimate, including your seed age.")
                    if existing is not None
                    else self.tr(
                        "Estimated once downloaded and seeding, starting at zero seed age. "
                        "Swarm includes you."
                    )
                )
                tooltip += "\n" + self.tr(
                    "Uses your Zen Profile and {0} seed hours/day. Revival payouts are separate."
                ).format(self.settings.zen.seed_hours)
                if self._seeding is None:
                    tooltip += "\n" + self.tr(
                        "Current seeding count unavailable; Class count bonus is provisional."
                    )
            item.setToolTip(tooltip)
            table.setItem(row, zen_column, item)
            torrent_note = current_notes.get(
                (candidate.infohash or "").lower(),
                note(self.settings.zen.zen_class, prospective[identity]),
            )
            note_item = (
                NumberItem(f"≈ {torrent_note:.2f}", torrent_note)
                if is_c411
                else NumberItem("–", float("-inf"))
            )
            note_item.setToolTip(
                self.tr("Estimated contribution note for your Class, category, swarm and seed age.")
                + "\n"
                + tooltip
                if is_c411
                else tooltip
            )
            table.setItem(row, note_column, note_item)
        table.setSortingEnabled(True)

    def open_website(self) -> None:
        selected = self.selected_results()
        if len(selected) == 1 and self._result_indexer:
            url = website_url(selected[0], self._result_indexer)
            if url:
                QDesktopServices.openUrl(QUrl(url))

    def focus_search(self) -> None:
        self.browser.setCurrentWidget(self.home)
        self.pages.setCurrentIndex(0)
        self.query.setFocus()
        self.query.selectAll()

    def show_results(self) -> None:
        self.browser.setCurrentWidget(self.home)
        self.pages.setCurrentIndex(0)

    def show_queue(self) -> None:
        self.browser.setCurrentWidget(self.home)
        self.pages.setCurrentIndex(1)

    def _open_result_item(self, clicked: QTableWidgetItem) -> None:
        item = self.results.item(clicked.row(), 0)
        if item is not None:
            self.open_details(self._results[item.data(Qt.ItemDataRole.UserRole)])

    def show_details(self) -> None:
        for candidate in self.selected_results():
            self.open_details(candidate)

    def open_details(self, candidate: Candidate) -> None:
        indexer = self._result_indexer
        if indexer is None:
            return
        key = queue_key(indexer, candidate)
        existing = self._detail_tabs.get(key)
        if existing is not None:
            self.browser.setCurrentWidget(existing)
            return
        estimate = None
        torrent_note = None
        for row in range(self.results.rowCount()):
            item = self.results.item(row, 0)
            if item and self._results[item.data(Qt.ItemDataRole.UserRole)] == candidate:
                estimate = self.results.item(row, 5)
                torrent_note = self.results.item(row, 6)
                break
        page = TorrentDetailsDialog(
            candidate,
            indexer,
            estimate.text() if estimate else "–",
            estimate.toolTip() if estimate else "",
            self,
            download=lambda folder, now: self.download_candidate(candidate, indexer, folder, now),
            folder=str(self._download_folder(candidate) or ""),
            embedded=True,
            torrent_note=torrent_note.text() if torrent_note else "–",
        )
        page.close_requested.connect(lambda: self.close_detail(page))
        self._detail_tabs[key] = page
        self.browser.add_preview(page, candidate.title)
        self.browser.setCurrentWidget(page)
        self._buttons()

    def close_detail(self, page: QWidget) -> None:
        if not isinstance(page, TorrentDetailsDialog):
            return
        key = queue_key(page.indexer, page.candidate)
        self._detail_tabs.pop(key, None)
        self.browser.removeTab(self.browser.indexOf(page))
        page.dispose()
        self._buttons()

    def queue_open_tabs(self, now: bool) -> None:
        pages = [self.browser.widget(i) for i in range(self.browser.count())]
        details = [page for page in pages if isinstance(page, TorrentDetailsDialog)]
        if not details:
            return
        # Validate the entire selection before writing anything to the queue.
        prepared = []
        for page in details:
            try:
                prepared.append(
                    (
                        page,
                        self._prepare_download(
                            page.candidate, page.indexer, page.folder.text().strip()
                        ),
                    )
                )
            except Exception as error:
                message = error_text(error)
                page.action_status.setText(message)
                self.result_status.setText(message)
                self.queue_notice.setText(self.tr("Could not queue tabs: {0}").format(message))
                self.browser.setCurrentWidget(page)
                page.folder.setFocus()
                return
        added = 0
        pending: set[str] = set()
        for page, (indexer, destination, key, existing) in prepared:
            if existing and (
                existing.status in {"sending", "uncertain"}
                or (existing.status == "failed" and not existing.retry_safe)
            ):
                page.action_status.setText(
                    self.tr(
                        "Check the Torrent Client before removing and queueing this entry again."
                    )
                )
                continue
            if existing and existing.status == "sent":
                page.action_status.setText(self.tr("Already sent to the torrent client."))
                continue
            added += enqueue(self.store, indexer, page.candidate, destination)
            if now:
                self.store.download_outcome(key, "queued")
                pending.add(key)
            page.action_status.setText(
                self.tr("Added to queue.") if existing is None else self.tr("Already in the queue.")
            )
        self.refresh_queue()
        message = self.tr("{0} added to the queue; {1} already listed.").format(
            added, len(details) - added
        )
        self.result_status.setText(message)
        self.queue_notice.setText(message)
        self.queue_status.setText(
            message + " " + self.tr("Queue saved. Press Start queue when ready.")
        )
        self.show_queue()
        if now and pending:
            self.start_queue(only=pending)

    def _browse(self) -> None:
        folder = choose_directory(self, self.tr("Download Folder"), self.folder.text())
        if folder:
            self.folder.setText(folder)

    def selected_results(self) -> list[Candidate]:
        return [
            self._results[item.data(Qt.ItemDataRole.UserRole)]
            for i in self.results.selectionModel().selectedRows()
            if (item := self.results.item(i.row(), 0)) is not None
        ]

    def _download_folder(self, candidate: Candidate) -> Path | None:
        cats = self.settings.indexer_categories
        cid = max(
            candidate.categories, key=lambda value: len(category_path(value, cats)), default=0
        )
        return (
            Path(self.folder.text().strip()).expanduser()
            if self.folder.text().strip()
            else self.settings.download_folders.resolve(cid, cats)
        )

    def _prepare_download(
        self, candidate: Candidate, indexer: Indexer, folder: str
    ) -> tuple[Indexer, Path, str, QueueEntry | None]:
        if self.runner:
            raise ValueError(
                self.tr(
                    "A queue send is running. Wait or pause it before starting another download."
                )
            )
        current = self.indexer()
        if current.url != indexer.url:
            raise ValueError(
                self.tr("The Indexer changed. Search again before downloading this torrent.")
            )
        if not candidate.download_url:
            raise ValueError(self.tr("This torrent has no download link."))
        key = queue_key(indexer, candidate)
        existing = next((entry for entry in entries(self.store) if entry.key == key), None)
        if existing and (
            existing.status in {"sending", "uncertain"}
            or (existing.status == "failed" and not existing.retry_safe)
        ):
            raise ValueError(
                self.tr("Check the Torrent Client before removing and queueing this entry again.")
            )
        if existing and existing.status == "sent":
            return current, existing.folder, key, existing
        destination = Path(folder).expanduser() if folder else None
        if destination is None or not destination.is_dir():
            raise ValueError(self.tr("Choose an accessible Download Folder."))
        destination = destination.resolve()
        if existing and existing.status != "sent" and existing.folder != destination:
            raise ValueError(
                self.tr(
                    "This torrent is already queued to {0}. Remove that entry to change its folder."
                ).format(existing.folder)
            )
        return current, destination, key, existing

    def download_candidate(
        self, candidate: Candidate, indexer: Indexer, folder: str, now: bool
    ) -> str:
        current, destination, key, existing = self._prepare_download(candidate, indexer, folder)
        if existing and existing.status == "sent":
            return self.tr("Already sent to the torrent client.")
        added = enqueue(self.store, current, candidate, destination)
        if now:
            self.store.download_outcome(key, "queued")
            self.refresh_queue()
            if not self.start_queue(only={key}):
                return self.queue_status.text()
            return self.tr("Download started. See Download queue for progress.")
        self.refresh_queue()
        if existing and existing.status in {"failed", "refused"}:
            return self.tr(
                "Already listed with an error. Use Download now to retry, or review Download queue."
            )
        return self.tr("Added to queue.") if added else self.tr("Already in the queue.")

    def add_selected(self) -> None:
        self._add_candidates(self.selected_results())

    def _add_candidates(self, chosen: list[Candidate]) -> None:
        indexer = self._result_indexer
        if indexer is None or self.runner or not chosen:
            return
        destinations = []
        for c in chosen:
            folder = self._download_folder(c)
            if folder is None:
                self.result_status.setText(
                    self.tr(
                        "No Download Folder for {0}. Choose a folder below "
                        "or configure category folders in Settings."
                    ).format(self._category_label(c) or c.title)
                )
                self.folder.setFocus()
                return
            if not folder.is_dir():
                self.result_status.setText(
                    self.tr(
                        "Choose an accessible Download Folder, "
                        "or configure category folders in Settings."
                    )
                )
                return
            if not c.download_url:
                self.result_status.setText(self.tr("This torrent has no download link."))
                return
            destinations.append((c, folder.resolve()))
        added = sum(enqueue(self.store, indexer, c, folder) for c, folder in destinations)
        message = self.tr("{0} added to the queue; {1} already listed.").format(
            added, len(chosen) - added
        )
        self.result_status.setText(message)
        self.queue_notice.setText(message)
        self.queue_status.setText(message)
        self.refresh_queue()
        self.show_queue()

    def selected_keys(self) -> list[str]:
        return [
            item.data(Qt.ItemDataRole.UserRole)
            for i in self.queue.selectionModel().selectedRows()
            if (item := self.queue.item(i.row(), 0)) is not None
        ]

    def _poll_queue(self) -> None:
        if self.isVisible():
            self._sync_queue()

    def _sync_queue(self) -> None:
        version = self.store.change_token()
        if version == self._queue_version:
            return
        rows = entries(self.store)
        if rows == self._queue_snapshot:
            self._queue_version = version
            return
        # A send outcome does not change release metadata or Zen estimates, and a
        # successful send only removes its entry. Keep the displayed items and exact
        # selection instead of rebuilding every row.
        shown = {entry.key: entry for entry in self._queue_snapshot}
        status_only = all(
            (previous := shown.get(row.key)) is not None
            and (
                row == previous
                or replace(
                    row,
                    status=previous.status,
                    reason=previous.reason,
                    retry_safe=previous.retry_safe,
                )
                == previous
            )
            for row in rows
        )
        self._show_queue(rows, status_only=status_only)
        self._queue_version = version

    def refresh_queue(self) -> None:
        version = self.store.change_token()
        self._show_queue(entries(self.store))
        self._queue_version = version

    def _show_queue(self, rows: list[QueueEntry], *, status_only: bool = False) -> None:
        selected = set(self.selected_keys())
        self._queue_snapshot = rows
        self._queued_hashes = {
            (e.candidate.infohash or "").lower() for e in rows if e.status in {"queued", "sending"}
        }
        self.results.viewport().update()
        labels = {
            "queued": self.tr("Queued"),
            "sending": self.tr("Sending…"),
            "sent": self.tr("Sent to client"),
            "failed": self.tr("Failed"),
            "uncertain": self.tr("Check Torrent Client before retrying"),
            "refused": self.tr("Skipped"),
        }
        self.queue.blockSignals(True)
        self.queue.setSortingEnabled(False)
        if status_only:
            by_key = {entry.key: entry for entry in rows}
            for row in reversed(range(self.queue.rowCount())):
                title = self.queue.item(row, 0)
                assert title is not None
                if title.data(Qt.ItemDataRole.UserRole) not in by_key:
                    self.queue.removeRow(row)
            for row in range(self.queue.rowCount()):
                title = self.queue.item(row, 0)
                assert title is not None
                entry = by_key[title.data(Qt.ItemDataRole.UserRole)]
                for column, text in (
                    (5, labels.get(entry.status, entry.status)),
                    (6, entry.reason),
                    (7, self.settings.profile(entry.client_id).name),
                ):
                    cell = self.queue.item(row, column)
                    assert cell is not None
                    if cell.text() != text:
                        cell.setText(text)
                        cell.setToolTip(text)
            self.queue.setSortingEnabled(True)
        else:
            self.queue.setRowCount(len(rows))
            for row, entry in enumerate(rows):
                item = QTableWidgetItem(entry.candidate.title)
                item.setData(Qt.ItemDataRole.UserRole, entry.key)
                self.queue.setItem(row, 0, item)
                self.queue.setItem(
                    row, 1, NumberItem(human_size(entry.candidate.size), entry.candidate.size)
                )
                self.queue.setItem(row, 2, QTableWidgetItem())
                self.queue.setItem(row, 3, QTableWidgetItem())
                self.queue.setItem(row, 4, QTableWidgetItem(str(entry.folder)))
                self.queue.setItem(row, 5, QTableWidgetItem(labels.get(entry.status, entry.status)))
                self.queue.setItem(row, 6, QTableWidgetItem(entry.reason))
                self.queue.setItem(
                    row, 7, QTableWidgetItem(self.settings.profile(entry.client_id).name)
                )
                for column in range(8):
                    cell = self.queue.item(row, column)
                    assert cell is not None
                    cell.setSelected(entry.key in selected)
                    cell.setToolTip(cell.text())
            self._refresh_queue_estimates()
        self.queue.blockSignals(False)
        count = len(rows)
        self.queue_summary.setText(
            self.tr("{0} queued · {1} sending · {2} sent · {3} need attention").format(
                sum(e.status == "queued" for e in rows),
                sum(e.status == "sending" for e in rows),
                sum(e.status == "sent" for e in rows),
                sum(e.status in {"failed", "refused", "uncertain"} for e in rows),
            )
        )
        self.pages.setTabText(1, self.tr("Download queue ({0})").format(count))
        self.view_queue.setText(self.tr("Download queue ({0})").format(count))
        self._buttons()

    def _queue_context_menu(self, position: QPoint) -> None:
        item = self.queue.itemAt(position)
        if item is not None and not item.isSelected():
            self.queue.selectRow(item.row())
        elif item is None:
            self.queue.clearSelection()
        menu = QMenu(self)
        start = menu.addAction(self.tr("Start selected"))
        selected = set(self.selected_keys())
        start.setEnabled(
            self.runner is None
            and any(e.key in selected and e.status == "queued" for e in entries(self.store))
        )
        start.triggered.connect(lambda: self.start_queue(only=set(self.selected_keys())))
        for button in (self.pause, self.retry, self.remove, self.clear_done):
            action = menu.addAction(button.text())
            action.setEnabled(button.isEnabled())
            action.triggered.connect(button.click)
        menu.addSeparator()
        manage = menu.addAction(self.manage_button.text())
        manage.setEnabled(bool(selected))
        manage.triggered.connect(self._open_in_torrents)
        menu.exec(self.queue.viewport().mapToGlobal(position))
        menu.deleteLater()

    def _open_in_torrents(self) -> None:
        selected = self.queue.selectionModel().selectedRows()
        hash = ""
        if len(selected) == 1:
            item = self.queue.item(selected[0].row(), 0)
            key = item.data(Qt.ItemDataRole.UserRole) if item else None
            entry = next((e for e in entries(self.store) if e.key == key), None)
            if entry is not None:
                hash = entry.candidate.infohash or ""
        self.manage_requested.emit(hash)

    def start_queue(self, *, only: set[str] | None = None) -> bool:
        if self.runner:
            return False
        try:
            indexer, client = self.indexer(), self.client()
            if not indexer.api_key:
                raise ValueError(self.tr("Set your Indexer API key in Settings first."))
        except Exception as error:
            self.queue_status.setText(error_text(error))
            return False
        categories = dict(self.settings.indexer_categories)
        self.runner = SendRunner(
            lambda stop, progress: run_queue(
                self.store, indexer, client, categories, stop, progress, only=only
            )
        )
        self.runner.signals.progress.connect(self._progress)
        self.runner.signals.finished.connect(self._finished)
        self.runner.signals.failed.connect(self._failed)
        self._buttons()
        self.runner.start()
        return True

    def _progress(self, progress: SendProgress) -> None:
        self._sync_queue()
        message = self.tr("{0}/{1} processed · {2}").format(
            progress.done, progress.total, progress.title
        )
        if progress.retry_at:
            message += self.tr(" · waiting until {0}").format(
                time.strftime("%H:%M:%S", time.localtime(progress.retry_at))
            )
        self.queue_status.setText(message)

    def _finished(self, report: SendReport) -> None:
        self.runner = None
        self._sync_queue()
        self._buttons()  # The worker can finish without another database change.
        self.queue_status.setText(
            self.tr("{0} sent · {1} skipped · {2} failed").format(
                len(report.added), len(report.refused), len(report.failed)
            )
        )

    def _failed(self, error: Exception) -> None:
        self.runner = None
        for entry in entries(self.store):
            if entry.status == "sending":
                self.store.download_outcome(
                    entry.key,
                    "uncertain",
                    "Interrupted send. Check the Torrent Client before retrying.",
                )
        self._sync_queue()
        self._buttons()
        self.queue_status.setText(error_text(error))

    def stop(self) -> None:
        if self.runner:
            self.runner.stop()
            self.pause.setEnabled(False)
            self.queue_status.setText(self.tr("Pausing after the current request…"))

    def retry_selected(self) -> None:
        if self.runner:
            return
        selected = set(self.selected_keys())
        for entry in entries(self.store):
            if entry.key in selected and (
                entry.status == "refused" or (entry.status == "failed" and entry.retry_safe)
            ):
                self.store.download_outcome(entry.key, "queued")
        self.refresh_queue()

    def remove_selected(self) -> None:
        if not self.runner:
            self.store.remove_downloads(self.selected_keys())
            self.refresh_queue()

    def clear_sent(self) -> None:
        if not self.runner:
            self.store.remove_downloads([e.key for e in entries(self.store) if e.status == "sent"])
            self.refresh_queue()

    def _buttons(self) -> None:
        busy = self.runner is not None
        can_search = not self._searching and self.filters.ready
        self.search_button.setEnabled(can_search)
        more = self._native_page.has_more if self._native_page else len(self._results) == PAGE_SIZE
        for bar in (self.page_bar, self.page_bar_bottom):
            bar.set_state(
                self._offset // PAGE_SIZE + 1,
                self._native_page.total_pages if self._native_page else None,
                more,
                can_search,
            )
            bar.setVisible(bool(self._results) or self._offset > 0)
        selected_results = self.selected_results()
        one = len(selected_results) == 1 and not self._searching
        self.details_button.setEnabled(bool(selected_results) and not self._searching)
        self.selection_bar.setVisible(bool(selected_results))
        self.selection_label.setText(self.tr("{0} selected").format(len(selected_results)))
        count = len(self._detail_tabs)
        self.browser.tab_list.setText(
            self.tr("Open tabs ({0})").format(count) if count else self.tr("Open tabs")
        )
        self.queue_tabs.setEnabled(count > 0 and not busy)
        self.download_tabs.setEnabled(count > 0 and not busy)
        self.website_button.setEnabled(
            bool(
                one
                and self._result_indexer
                and website_url(selected_results[0], self._result_indexer)
            )
        )
        self.add_button.setEnabled(
            not busy and not self._searching and bool(self.results.selectionModel().selectedRows())
        )
        # Button state follows the displayed snapshot. Dispatch and edits still
        # re-read and validate current persisted entries in their action handlers.
        rows = self._queue_snapshot
        self.start.setEnabled(not busy and any(e.status == "queued" for e in rows))
        self.pause.setEnabled(busy)
        selected = set(self.selected_keys())
        self.retry.setEnabled(
            not busy
            and any(
                e.key in selected
                and (e.status == "refused" or (e.status == "failed" and e.retry_safe))
                for e in rows
            )
        )
        self.remove.setEnabled(not busy and bool(selected))
        self.clear_done.setEnabled(not busy and any(e.status == "sent" for e in rows))
