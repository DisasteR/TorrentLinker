import httpx
import pytest
from PySide6.QtCore import Qt

from tests.fake_qbit import FakeQbit
from tests.test_search_filters import choose
from tests.test_torrent_search import FACETS, TREE, fake_indexer, listing
from torrentlinker.config import Settings
from torrentlinker.gui.pagination import page_numbers
from torrentlinker.gui.search_view import SearchTab
from torrentlinker.store import Store


def test_page_numbers_show_the_ends_and_the_neighbours():
    assert page_numbers(1, 400) == [1, 2, 3, None, 400]
    assert page_numbers(2, 400) == [1, 2, 3, None, 400]
    assert page_numbers(50, 400) == [1, None, 49, 50, 51, None, 400]
    assert page_numbers(399, 400) == [1, None, 398, 399, 400]
    assert page_numbers(4, 400) == [1, 2, 3, 4, 5, None, 400]  # one hidden page is shown
    assert page_numbers(1, 3) == [1, 2, 3]
    assert page_numbers(1, 1) == [1]
    assert page_numbers(9, 0) == []
    assert page_numbers(99, 5) == [1, 2, 3, 4, 5]  # a stale page number is clamped


def listing_with_counts(page=1):
    data = listing(page, total=137565, total_pages=400)
    data["meta"].update(
        {
            "totalSize": 1757108743727203,
            "categories": [{"id": 1, "count": 138013}, {"id": 2, "count": 39239}],
        }
    )
    return data


def make_view(qtbot, tmp_path, requests):
    def handler(request):
        requests.append(request)
        if request.url.path == "/api/categories":
            data = {"data": [{**root, "color": "#3B82F6"} for root in TREE["data"]]}
        elif request.url.path.endswith("filter-options"):
            data = FACETS
        else:
            data = listing_with_counts(int(request.url.params["page"]))
        return httpx.Response(200, json=data)

    indexer = fake_indexer(handler)
    view = SearchTab(Store(tmp_path / "state.db"), Settings(), lambda: indexer, FakeQbit)
    qtbot.addWidget(view)
    view.resize(1100, 800)
    view.show()
    qtbot.waitUntil(lambda: view.filters.ready)
    return view


def test_pills_follow_the_category_and_filter_at_once(qtbot, tmp_path):
    requests: list = []
    view = make_view(qtbot, tmp_path, requests)
    assert list(view.pills.buttons) == [None, 1, 2]
    assert view.pills.buttons[None].isChecked()
    view.pills.buttons[2].click()  # even while the first listing is still loading
    qtbot.waitUntil(lambda: not view._searching and requests[-1].url.params.get("category") == "2")
    assert view.category.current_category == 2
    assert view.pills.buttons[2].isChecked()
    # A subcategory lights its parent's pill.
    view.category.select_category(-6)
    assert view.pills.buttons[1].isChecked()
    view.pills.buttons[None].click()
    qtbot.waitUntil(lambda: not view._searching)
    assert "category" not in requests[-1].url.params


def test_pill_counts_are_global_totals_and_the_header_shows_the_filtered_total(qtbot, tmp_path):
    requests: list = []
    view = make_view(qtbot, tmp_path, requests)
    # Opening the page lists the latest releases once, as c411's does.
    qtbot.waitUntil(lambda: view._native_page is not None and not view._searching)
    listings = [r for r in requests if r.url.path == "/api/torrents"]
    assert len(listings) == 1 and "name" not in listings[0].url.params
    view.query.setText("ocean")
    view.search_button.click()
    qtbot.waitUntil(lambda: view._native_page is not None and not view._searching)
    assert "138" in view.pills.buttons[1].text() and "39" in view.pills.buttons[2].text()
    assert view.pills.buttons[None].text().startswith("All")
    total = view.results_summary.text()
    assert "137" in total and "1.6 PiB" in total


def test_numbered_pages_search_the_chosen_page(qtbot, tmp_path):
    requests: list = []
    view = make_view(qtbot, tmp_path, requests)
    view.search_button.click()
    qtbot.waitUntil(lambda: view._native_page is not None and not view._searching)
    assert view.page_bar.isVisible() and not view.previous.isEnabled()
    view.page_bar.page_requested.emit(3)
    qtbot.waitUntil(lambda: not view._searching)
    assert requests[-1].url.params["page"] == "3"
    assert view.previous.isEnabled()


def test_sort_chips_research_and_flip_the_direction(qtbot, tmp_path):
    requests: list = []
    view = make_view(qtbot, tmp_path, requests)
    sort = view.filters.sort
    assert not sort.buttons["relevance"].isVisible()
    view.query.setText("ocean")
    assert sort.buttons["relevance"].isVisible()
    view.search_button.click()
    qtbot.waitUntil(lambda: view._native_page is not None and not view._searching)
    sort.buttons["seeders"].click()
    qtbot.waitUntil(lambda: not view._searching)
    assert (requests[-1].url.params["sortBy"], requests[-1].url.params["sortOrder"]) == (
        "seeders",
        "desc",
    )
    sort.buttons["seeders"].click()
    qtbot.waitUntil(lambda: not view._searching)
    assert requests[-1].url.params["sortOrder"] == "asc"
    sort.buttons["name"].click()
    qtbot.waitUntil(lambda: not view._searching)
    assert (requests[-1].url.params["sortBy"], requests[-1].url.params["sortOrder"]) == (
        "name",
        "asc",
    )
    sort.buttons["relevance"].click()
    qtbot.waitUntil(lambda: not view._searching)
    assert requests[-1].url.params["sortBy"] == "relevance"
    view.query.clear()  # Relevance means nothing without a query: back to Age
    assert sort.key == "createdAt" and not sort.buttons["relevance"].isVisible()


def test_cast_and_crew_only_applies_to_video_with_a_text_query(qtbot, tmp_path):
    view = make_view(qtbot, tmp_path, [])
    filters = view.filters
    assert filters.crew.isHidden()
    view.category.select_category(1)
    qtbot.waitUntil(lambda: filters.ready)
    assert not filters.crew.isHidden()
    filters.crew.setChecked(True)
    assert filters.params("Ocean")["crew"] == "true"
    assert "crew" not in filters.params("")
    view.category.select_category(2)
    qtbot.waitUntil(lambda: filters.ready)
    assert filters.crew.isHidden() and "crew" not in filters.params("Ocean")


def test_active_filters_are_marked_and_clear_all_resets_them(qtbot, tmp_path):
    view = make_view(qtbot, tmp_path, [])
    view.category.select_category(1)
    qtbot.waitUntil(lambda: view.filters.ready)
    resolution = view.filters.video["resolution"]
    assert resolution.property("active") is False
    choose(resolution, "1080p")
    assert resolution.property("active") is True
    view.filters.sort.buttons["size"].click()
    view.filters.clear_button.click()
    assert resolution.property("active") is False
    assert view.filters.sort.key == "createdAt"


def rich_listing(page=1):
    data = listing_with_counts(page)
    base = data["data"][0]
    data["data"] = [
        {
            **base,
            "infoHash": f"{i:040x}",
            "name": f"Release.{i}.1080p",
            "seeders": i,
            "leechers": 2 * i,
            "completions": 10 * i,
            "uploader": f"up{i}",
            "media": {"kind": "movie", "title": f"Title {i}", "year": 2000 + i, "rating": 7.0},
            "tech": {"resolution": "1080p", "source": "BluRay", "languages": ["VFF"]},
        }
        for i in (1, 2, 3)
    ]
    return data


def make_rich_view(qtbot, tmp_path, monkeypatch):
    from PySide6.QtCore import QSettings

    ini = str(tmp_path / "ui.ini")
    monkeypatch.setattr(
        SearchTab, "_ui_settings", lambda self: QSettings(ini, QSettings.Format.IniFormat)
    )

    def handler(request):
        if request.url.path == "/api/categories":
            data = TREE
        elif request.url.path.endswith("filter-options"):
            data = FACETS
        else:
            data = rich_listing(int(request.url.params["page"]))
        return httpx.Response(200, json=data)

    indexer = fake_indexer(handler)
    settings = Settings()
    settings.download_folders.default = str(tmp_path)
    view = SearchTab(Store(tmp_path / "state.db"), settings, lambda: indexer, FakeQbit)
    qtbot.addWidget(view)
    view.resize(1100, 800)
    view.show()
    qtbot.waitUntil(lambda: view.filters.ready)
    view.search_button.click()
    qtbot.waitUntil(lambda: view._native_page is not None and not view._searching)
    return view, ini


def test_density_switch_changes_columns_and_is_remembered(qtbot, tmp_path, monkeypatch):
    from PySide6.QtCore import QSettings

    view, ini = make_rich_view(qtbot, tmp_path, monkeypatch)
    table = view.results
    assert view.density == "comfortable" and view.density_buttons["comfortable"].isChecked()
    assert table.isColumnHidden(2) and table.isColumnHidden(6) and not table.isColumnHidden(11)
    assert table.horizontalHeader().isHidden()
    comfortable_height = table.height()
    view.density_buttons["compact"].click()
    assert view.density == "compact"
    assert not table.isColumnHidden(2) and not table.isColumnHidden(8)
    assert table.isColumnHidden(4) and table.isColumnHidden(10) and not table.isColumnHidden(11)
    assert not table.horizontalHeader().isHidden()
    assert table.rowHeight(0) < 60 and table.height() <= comfortable_height
    assert QSettings(ini, QSettings.Format.IniFormat).value("search/density") == "compact"
    # The next page of results keeps the density that was chosen.
    view.page_bar.page_requested.emit(2)
    qtbot.waitUntil(lambda: not view._searching)
    assert table.isColumnHidden(4) and table.rowHeight(0) < 60
    restored = SearchTab(Store(tmp_path / "other.db"), Settings(), lambda: None, FakeQbit)
    qtbot.addWidget(restored)
    assert restored.density == "compact"


def test_listing_details_fill_the_table_and_the_painted_row_view(qtbot, tmp_path, monkeypatch):
    view, _ = make_rich_view(qtbot, tmp_path, monkeypatch)
    table = view.results
    rows = {table.item(r, 0).text(): r for r in range(table.rowCount())}
    row = rows["Release.2.1080p"]
    assert table.item(row, 2).text() == "2" and table.item(row, 8).text() == "4"
    assert table.item(row, 9).text() == "20" and table.item(row, 10).text() == "up2"
    shown = view._row_view(table.model().index(row, 0))
    assert shown.details.title == "Title 2" and shown.details.year == 2002
    assert shown.label == "Films & Vidéos / Film" and shown.color == view.pills.color_for(
        "Films & Vidéos"
    )
    assert [text for _, text in shown.details.tags] == ["VFF", "1080p", "BluRay"]


def test_selection_bar_and_row_download_button(qtbot, tmp_path, monkeypatch):
    from PySide6.QtCore import QModelIndex

    from torrentlinker.download_queue import entries
    from torrentlinker.gui.search_view import DOWNLOAD_COLUMN

    view, _ = make_rich_view(qtbot, tmp_path, monkeypatch)
    assert not view.selection_bar.isVisible()
    view.results.selectRow(0)
    assert view.selection_bar.isVisible() and "1" in view.selection_label.text()
    view.results.clearSelection()
    assert not view.selection_bar.isVisible()
    assert view._row_view(QModelIndex()) is None
    # The download button of one row queues that row only.
    view._cell_clicked(1, DOWNLOAD_COLUMN)
    queued = entries(view.store)
    assert len(queued) == 1 and queued[0].candidate.title == view.results.item(1, 0).text()
    assert "1 added" in view.result_status.text()


def test_palliative_toggle_cycles_its_three_states_and_shows_the_count(
    qtbot, tmp_path, monkeypatch
):
    view, _ = make_rich_view(qtbot, tmp_path, monkeypatch)
    toggle = view.filters.palliative
    assert toggle.currentData() == "" and not toggle.isChecked()
    toggle.click()
    assert toggle.currentData() == "include" and toggle.isChecked()
    assert view.filters.params("")["palliative"] == "include"
    toggle.click()
    assert toggle.currentData() == "only" and view.filters.params("")["palliative"] == "only"
    toggle.click()
    assert toggle.currentData() == "" and "palliative" not in view.filters.params("")
    toggle.setCurrentIndex(2)
    view.filters.clear_button.click()
    assert toggle.currentIndex() == 0


def test_palliative_count_comes_from_the_listing(qtbot, tmp_path, monkeypatch):
    view, _ = make_rich_view(qtbot, tmp_path, monkeypatch)
    view.filters.palliative.set_count(693)
    assert "693" in view.filters.palliative.text()
    view.filters.palliative.set_count(None)
    assert "693" not in view.filters.palliative.text()


def test_chosen_filters_show_as_removable_chips_from_four_values(qtbot, tmp_path, monkeypatch):
    view, _ = make_rich_view(qtbot, tmp_path, monkeypatch)
    view.category.select_category(1)
    qtbot.waitUntil(lambda: view.filters.ready)
    chosen, resolution = view.filters.chosen, view.filters.video["resolution"]
    choose(resolution, "1080p", "720p", "SD")
    assert chosen.isHidden()
    choose(view.filters.video["source"], "BluRay")
    assert not chosen.isHidden() and chosen.flow.count() == 4
    first = chosen.flow.itemAt(0).widget()
    assert "✕" in first.text()
    first.click()
    assert chosen.isHidden() and chosen.flow.count() == 3
    assert len(resolution.selected()) == 2


def test_rows_known_to_the_client_or_the_queue_are_marked(qtbot, tmp_path, monkeypatch):
    from types import SimpleNamespace

    from torrentlinker.download_queue import enqueue
    from torrentlinker.seeding import Seeding, SeedingRow
    from torrentlinker.zen import ZenDay

    view, _ = make_rich_view(qtbot, tmp_path, monkeypatch)
    table = view.results

    def states():
        return [view._row_view(table.model().index(r, 0)).state for r in range(3)]

    hashes = [
        view._results[table.item(r, 0).data(Qt.ItemDataRole.UserRole)].infohash for r in range(3)
    ]
    assert states() == ["", "", ""]
    client = SimpleNamespace(hash=hashes[0].upper())
    view.set_seeding(Seeding([SeedingRow(client, None)], ZenDay(), []))
    assert states() == ["client", "", ""]
    candidate = view._results[table.item(1, 0).data(Qt.ItemDataRole.UserRole)]
    enqueue(view.store, view._result_indexer, candidate, tmp_path)
    view.refresh_queue()
    assert states() == ["client", "queued", ""]


def test_empty_and_error_states_say_what_happened(qtbot, tmp_path, monkeypatch):
    view, _ = make_rich_view(qtbot, tmp_path, monkeypatch)
    view._show_results([], view._result_indexer)
    assert "No torrent matches" in view.results_empty.label.text()
    assert view.result_status.property("role") == "muted"
    view._search_failed(ValueError("c411 is unreachable"))
    assert "unreachable" in view.results_empty.label.text()
    assert view.result_status.property("role") == "error"
    assert view.results_empty.label.property("role") == "error"


def png_bytes():
    from PySide6.QtCore import QBuffer, QByteArray, QIODevice
    from PySide6.QtGui import QColor, QImage

    image = QImage(80, 120, QImage.Format.Format_RGB32)
    image.fill(QColor("#336699"))
    data = QByteArray()
    buffer = QBuffer(data)
    buffer.open(QIODevice.OpenModeFlag.WriteOnly)
    image.save(buffer, "PNG")
    return bytes(data)


def make_poster_view(qtbot, tmp_path, monkeypatch):
    from torrentlinker.poster_cache import PosterCache

    poster_requests: list = []

    def poster_handler(request):
        poster_requests.append(request)
        return httpx.Response(200, content=png_bytes(), headers={"content-type": "image/png"})

    monkeypatch.setattr(
        SearchTab,
        "_poster_cache",
        lambda self: PosterCache(
            tmp_path / "posters",
            lambda: httpx.Client(transport=httpx.MockTransport(poster_handler)),
        ),
    )
    original = rich_listing

    def with_posters(page=1):
        data = original(page)
        for i, row in enumerate(data["data"], 1):
            row["posterUrl"] = f"https://image.tmdb.org/t/p/w500/{i}.jpg"
        return data

    monkeypatch.setitem(globals(), "rich_listing", with_posters)
    view, ini = make_rich_view(qtbot, tmp_path, monkeypatch)
    return view, ini, poster_requests


def test_posters_load_in_the_background_and_can_be_turned_off(qtbot, tmp_path, monkeypatch):
    from PySide6.QtCore import QSettings

    view, ini, poster_requests = make_poster_view(qtbot, tmp_path, monkeypatch)
    index = view.results.model().index(0, 0)
    view._row_view(index)  # painting asks for the poster of each visible row
    qtbot.waitUntil(lambda: view._row_view(index).poster is not None)
    assert not view._row_view(index).poster.isNull()
    view.results.grab()
    assert 1 <= len(poster_requests) <= 3
    view.posters_button.click()
    assert not view.posters_button.isChecked() and view._row_view(index).poster is None
    assert QSettings(ini, QSettings.Format.IniFormat).value("search/posters", True, bool) is False


def test_posters_stay_off_when_switched_off_before_any_request(qtbot, tmp_path, monkeypatch):
    from PySide6.QtCore import QSettings

    ini = str(tmp_path / "ui.ini")
    QSettings(ini, QSettings.Format.IniFormat).setValue("search/posters", False)
    view, _, poster_requests = make_poster_view(qtbot, tmp_path, monkeypatch)
    assert not view.posters_button.isChecked()
    view.results.grab()
    view._row_view(view.results.model().index(0, 0))
    qtbot.wait(150)
    assert poster_requests == []


def test_team_filter_sends_the_team_and_year_dropdown_keeps_its_validation(qtbot, tmp_path):
    view = make_view(qtbot, tmp_path, [])
    filters = view.filters
    filters.team.setText(" BOUBA ")
    filters.year.setText("2024")
    params = filters.params("")
    assert params["team"] == "BOUBA" and params["year"] == "2024"
    assert filters.team.property("active") is True and "BOUBA" in filters.team.text()
    filters.year.setText("20")
    assert not filters.year.hasAcceptableInput()
    with pytest.raises(ValueError, match="year"):
        filters.params("")
    filters.clear_button.click()
    assert filters.team.text() == "" and filters.team.property("active") is False


def test_a_filter_change_searches_once_after_a_pause(qtbot, tmp_path):
    requests: list = []
    view = make_view(qtbot, tmp_path, requests)
    view.category.select_category(1)
    qtbot.waitUntil(lambda: view.filters.ready)
    before = len(requests)
    view.filters.exclusive.setChecked(True)
    view.filters.team.setText("bouba")
    view.filters.palliative.setCurrentIndex(1)
    assert len(requests) == before  # still waiting for the changes to settle
    qtbot.waitUntil(lambda: len(requests) > before and not view._searching, timeout=4000)
    qtbot.wait(900)
    sent = [r for r in requests[before:] if r.url.path == "/api/torrents"]
    assert len(sent) == 1
    params = sent[0].url.params
    assert (params["exclusive"], params["team"], params["palliative"]) == (
        "true",
        "bouba",
        "include",
    )


def test_a_half_typed_year_does_not_search(qtbot, tmp_path):
    requests: list = []
    view = make_view(qtbot, tmp_path, requests)
    before = len(requests)
    view.filters.year.setText("20")
    qtbot.wait(1000)
    assert len(requests) == before
    view.filters.year.setText("2024")
    qtbot.waitUntil(lambda: any(r.url.params.get("year") == "2024" for r in requests), timeout=4000)


def test_results_live_in_one_card_with_page_links_above_and_below(qtbot, tmp_path, monkeypatch):
    view, _ = make_rich_view(qtbot, tmp_path, monkeypatch)
    assert view.results.parent() is view.results_card
    assert view.filters.sort.parent().parent() is view.results_card
    assert "137" in view.results_summary.text() and view.results_summary.parent().parent() is (
        view.results_card
    )
    view.page_bar.page_requested.emit(1)
    qtbot.waitUntil(lambda: not view._searching)
    assert view.page_bar.isVisible() and view.page_bar_bottom.isVisible()
    assert view.page_bar_bottom.current == view.page_bar.current


def test_the_queue_is_reached_from_the_header_and_left_with_a_back_button(
    qtbot, tmp_path, monkeypatch
):
    view, _ = make_rich_view(qtbot, tmp_path, monkeypatch)
    assert not view.pages.tabBar().isVisible()
    view.view_queue.click()
    assert view.pages.currentIndex() == 1 and view.back_button.isVisible()
    view.back_button.click()
    assert view.pages.currentIndex() == 0 and view.results.isVisible()


def test_open_tab_actions_live_in_the_tab_menu_with_a_count(qtbot, tmp_path, monkeypatch):
    view, _ = make_rich_view(qtbot, tmp_path, monkeypatch)
    assert view.browser.tab_list.text() == "Open tabs" and not view.queue_tabs.isEnabled()
    view.open_details(view._results[0])
    assert view.browser.tab_list.text() == "Open tabs (1)"
    assert view.queue_tabs.isEnabled() and view.download_tabs.isEnabled()
    view.browser._fill_tab_menu()
    labels = [a.text() for a in view.browser.tab_menu.actions() if not a.isSeparator()]
    assert labels[-2:] == ["Queue all tabs", "Download all tabs"]


def test_compact_rows_carry_the_same_marks(qtbot, tmp_path, monkeypatch):
    from types import SimpleNamespace

    from torrentlinker.seeding import Seeding, SeedingRow
    from torrentlinker.zen import ZenDay

    view, _ = make_rich_view(qtbot, tmp_path, monkeypatch)
    view.set_density("compact")
    assert view.results.itemDelegateForColumn(0) is view._compact_title
    plain = view.results.grab().toImage()
    first = view._results[view.results.item(0, 0).data(Qt.ItemDataRole.UserRole)]
    view.set_seeding(
        Seeding([SeedingRow(SimpleNamespace(hash=first.infohash), None)], ZenDay(), [])
    )
    marked = view.results.grab().toImage()
    assert marked != plain  # the In client chip is painted at the row's right edge
    view.set_density("comfortable")
    assert view.results.itemDelegateForColumn(0) is view._delegates[0]


def test_a_torznab_indexer_gets_the_page_without_native_controls(qtbot, tmp_path, monkeypatch):
    from PySide6.QtCore import QSettings

    from torrentlinker.downloads import IndexerCategory
    from torrentlinker.indexer import Candidate, Indexer

    monkeypatch.setattr(
        SearchTab,
        "_ui_settings",
        lambda self: QSettings(str(tmp_path / "ui.ini"), QSettings.Format.IniFormat),
    )
    indexer = Indexer("https://example.test/api", "key")
    calls: list = []

    def search(query, cached=True):
        calls.append(query)
        return [
            Candidate(f"Book {n}", 10**6 * n, f"url{n}", n, f"{n:040x}", categories=(7020,))
            for n in range(100)
        ]

    indexer.search = search
    settings = Settings(
        indexer_categories={7000: IndexerCategory("Books"), 7020: IndexerCategory("EPUB", 7000)}
    )
    view = SearchTab(Store(tmp_path / "state.db"), settings, lambda: indexer, FakeQbit)
    qtbot.addWidget(view)
    view.resize(1100, 800)
    view.show()
    qtbot.waitUntil(lambda: view.filters.ready)
    # Pills come from the Indexer's own categories; the native controls stay away.
    assert list(view.pills.buttons) == [None, 7000]
    assert all(not control.isVisible() for control in view.filters.native_controls)
    assert not view.filters.sort.isVisible() and view.filters.crew.isHidden()
    qtbot.waitUntil(lambda: len(calls) == 1 and not view._searching)  # the first listing
    assert calls[0].categories == ()
    view.pills.buttons[7000].click()
    qtbot.waitUntil(lambda: len(calls) == 2 and not view._searching)
    assert calls[1].categories == (7000,)
    # Rows without c411's details still draw in both densities, and paging is open-ended.
    view.set_density("comfortable")
    view.results.grab()
    shown = view._row_view(view.results.model().index(0, 0))
    assert shown.details is None and shown.poster is None and shown.label == "EPUB"
    assert view.page_bar.isVisible() and view.next.isEnabled() and not view.previous.isEnabled()
    view.page_bar.page_requested.emit(2)
    qtbot.waitUntil(lambda: len(calls) == 3 and not view._searching)
    assert calls[2].offset == 100 and view.previous.isEnabled()
    view.set_density("compact")
    assert view.results.grab().width() > 0
    assert view.results_summary.text() == ""


def test_compact_follows_c411s_table_and_its_headers_sort_the_whole_search(
    qtbot, tmp_path, monkeypatch
):
    seen: list = []

    def handler(request):
        seen.append(request)
        if request.url.path == "/api/categories":
            data = TREE
        elif request.url.path.endswith("filter-options"):
            data = FACETS
        else:
            data = rich_listing(int(request.url.params["page"]))
        return httpx.Response(200, json=data)

    from PySide6.QtCore import QSettings

    from torrentlinker.gui.search_view import COMPACT_ORDER, HEADER_SORTS

    monkeypatch.setattr(
        SearchTab,
        "_ui_settings",
        lambda self: QSettings(str(tmp_path / "ui.ini"), QSettings.Format.IniFormat),
    )
    indexer = fake_indexer(handler)
    view = SearchTab(Store(tmp_path / "state.db"), Settings(), lambda: indexer, FakeQbit)
    qtbot.addWidget(view)
    view.resize(1200, 800)
    view.show()
    qtbot.waitUntil(lambda: view.filters.ready)
    view.search_button.click()
    qtbot.waitUntil(lambda: view._native_page is not None and not view._searching)
    view.set_density("compact")
    header = view.results.horizontalHeader()
    assert [header.logicalIndex(i) for i in range(len(COMPACT_ORDER))] == list(COMPACT_ORDER)
    assert view.results.itemDelegateForColumn(3) is view._compact_delegates[3]
    view.results.grab()
    # Comments come from the listing; a header click sorts on c411, not just this page.
    assert view.results.item(0, 12).text() == "0"
    header.sectionClicked.emit(8)
    qtbot.waitUntil(lambda: not view._searching and seen[-1].url.params.get("sortBy") == "leechers")
    assert seen[-1].url.params["sortOrder"] == "desc"
    assert header.sortIndicatorSection() == 8
    header.sectionClicked.emit(8)
    qtbot.waitUntil(lambda: not view._searching and seen[-1].url.params.get("sortOrder") == "asc")
    assert set(HEADER_SORTS.values()) <= {
        "category",
        "name",
        "comments",
        "createdAt",
        "size",
        "completions",
        "seeders",
        "leechers",
    }


def test_resting_on_a_row_opens_c411s_hover_card(qtbot, tmp_path, monkeypatch):
    from PySide6.QtCore import QEvent, QPoint
    from PySide6.QtTest import QTest

    original = rich_listing

    def with_media(page=1):
        data = original(page)
        for row in data["data"]:
            row["media"].update(
                {
                    "overview": "A long synopsis. " * 40,
                    "genres": ["Aventure", "Comédie"],
                    "cast": ["Dwayne Johnson", "Kevin Hart", "Jack Black", "Karen"],
                    "directors": ["Jake Kasdan"],
                    "runtime": 119,
                    "originalTitle": "Welcome to the Jungle",
                    "backdropUrl": "https://image.tmdb.org/t/p/w1280/b.jpg",
                }
            )
        return data

    monkeypatch.setitem(globals(), "rich_listing", with_media)
    view, _ = make_rich_view(qtbot, tmp_path, monkeypatch)
    table = view.results
    assert table.item(0, 0).toolTip() == ""  # the card replaces the plain tooltip
    viewport = table.viewport()
    QTest.mouseMove(viewport, QPoint(200, 30))
    qtbot.waitUntil(lambda: view.hover.isVisible(), timeout=3000)
    header = view.hover.header
    assert header.kind == "FILM" and header.rating == "★ 7.0"
    assert header.subtitle == "Welcome to the Jungle" and header.meta.endswith(
        " · 1h59 · Jake Kasdan"
    )
    assert header.title.startswith("Release.")
    assert view.hover.overview.text().endswith("…") and len(view.hover.overview.text()) <= 261
    assert view.hover.cast.count() == 3  # three names, as c411 shows
    QApplication_sendLeave = QEvent(QEvent.Type.Leave)
    view.eventFilter(viewport, QApplication_sendLeave)
    assert not view.hover.isVisible()
    # Rows without details keep the plain tooltip and open no card.
    view._native_page = None
    assert view._hover_info(0) is None
