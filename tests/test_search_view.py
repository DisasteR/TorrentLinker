from dataclasses import replace

import pytest
from PySide6.QtCore import Qt

from tests.fake_qbit import FakeQbit
from tests.test_download_queue import setup_queue
from torrentlinker.config import ClientProfile, Settings
from torrentlinker.download_queue import enqueue, entries
from torrentlinker.gui.search_view import SearchTab
from torrentlinker.indexer import Candidate, Indexer
from torrentlinker.store import Store


def test_sorted_search_queues_the_selected_torrent_and_remembers_it(qtbot, tmp_path):
    store = Store(tmp_path / "state.db")
    indexer = Indexer("https://example.test/api", "key")
    settings = Settings()
    settings.download_folders.default = str(tmp_path)
    view = SearchTab(store, settings, lambda: indexer, FakeQbit)
    qtbot.addWidget(view)
    a = Candidate("A large", 1024**3, "https://example.test/a", 10, "a" * 40)
    z = Candidate("Z small", 9 * 1024**2, "https://example.test/z", 2, "b" * 40)
    view._show_results([a, z], indexer)
    view.results.sortItems(1, Qt.SortOrder.AscendingOrder)
    view.results.selectRow(0)
    assert view.selected_results() == [z]
    view.add_button.click()
    assert [e.candidate.title for e in entries(store)] == [z.title]
    view.add_button.click()
    assert len(entries(store)) == 1
    view.queue.selectRow(0)
    view.remove.click()
    assert entries(store) == []


def test_search_runs_off_thread_with_category_and_fresh_paging(qtbot, tmp_path):
    from torrentlinker.downloads import IndexerCategory

    indexer = Indexer("https://example.test/api", "key")
    calls = []

    def search(query, cached=True):
        calls.append((query, cached))
        return [Candidate(str(n), n, "url" + str(n)) for n in range(100)]

    indexer.search = search
    settings = Settings(
        indexer_categories={2000: IndexerCategory("Movies"), 2030: IndexerCategory("Film", 2000)}
    )
    view = SearchTab(Store(tmp_path / "state.db"), settings, lambda: indexer, FakeQbit)
    qtbot.addWidget(view)
    view.query.setText("Ocean")
    view.category.select_category(2030)
    view.search_button.click()
    qtbot.waitUntil(lambda: not view._searching)
    assert calls[0][0].q == "Ocean" and calls[0][0].categories == (2030,)
    assert calls[0][1] is False
    view.next.click()
    qtbot.waitUntil(lambda: len(calls) == 2 and not view._searching)
    assert calls[1][0].offset == 100
    assert view.previous.isEnabled()


def test_publication_column_sorts_by_timestamp_and_queue_preserves_date(
    qtbot, tmp_path, monkeypatch
):
    from PySide6.QtCore import QDateTime

    from torrentlinker.indexer import publication_time

    now = publication_time("2026-10-23T03:00:00Z")
    monkeypatch.setattr("torrentlinker.gui.search_view.time.time", lambda: now)
    store = Store(tmp_path / "dated.db")
    indexer = Indexer("https://example.test/api", "key")
    settings = Settings()
    settings.download_folders.default = str(tmp_path)
    view = SearchTab(store, settings, lambda: indexer, FakeQbit)
    qtbot.addWidget(view)
    view.show()
    old = Candidate("Old", 1, "old", published_at=publication_time("2026-10-22T23:30:00Z"))
    new = Candidate("New", 1, "new", published_at=publication_time("2026-10-23T01:00:00Z"))
    unknown = Candidate("Unknown", 1, "unknown")
    view._show_results([old, new, unknown], indexer)
    assert view.results.item(2, 7).text() == "–"
    view.results.sortItems(7, Qt.SortOrder.DescendingOrder)
    assert [view.results.item(i, 0).text() for i in range(3)] == ["New", "Old", "Unknown"]
    assert new.published_at is not None
    expected = QDateTime.fromMSecsSinceEpoch(round(new.published_at * 1000)).toLocalTime()
    assert view.results.item(0, 7).text() == "2 hours"
    assert view.results.item(1, 7).text() == "3 hours"
    assert view.results.item(0, 7).toolTip() == expected.toString("yyyy-MM-dd HH:mm:ss t")
    view.results.selectRow(0)
    view.add_selected()
    assert entries(store)[0].candidate.published_at == new.published_at
    assert now is not None
    now += 3600
    view.refresh_ages()
    assert view.results.item(0, 7).text() == "3 hours"
    assert view.selected_results() == [new]
    assert view.results.item(0, 7).data(Qt.ItemDataRole.UserRole) == new.published_at
    view.results.sortItems(7, Qt.SortOrder.AscendingOrder)
    assert [view.results.item(i, 0).text() for i in range(3)] == ["Unknown", "Old", "New"]


def test_queue_worker_and_restart_recovery(qtbot, tmp_path):
    store, indexer, client, c, data, folder = setup_queue(tmp_path)
    key = entries(store)[0].key
    store.download_outcome(key, "sending")
    store = Store(tmp_path / "state.db")
    view = SearchTab(store, Settings(), lambda: indexer, lambda: client)
    qtbot.addWidget(view)
    assert entries(store)[0].status == "uncertain"
    assert not view.start.isEnabled()
    assert "Check Torrent Client" in view.queue.item(0, 5).text()
    # After checking the client, the owner explicitly removes and queues it again.
    view.queue.selectRow(0)
    view.remove_selected()
    enqueue(store, indexer, c, folder)
    view.refresh_queue()
    assert entries(store)[0].status == "queued"
    assert not client.downloads
    view.start.click()
    qtbot.waitUntil(lambda: view.runner is None)
    assert client.downloads == [c.infohash]
    assert entries(store) == []
    assert view.queue.rowCount() == 0
    assert view.view_queue.text() == "Download queue (0)"
    assert not view.start.isEnabled()
    restored = SearchTab(store, Settings(), lambda: indexer, lambda: client)
    qtbot.addWidget(restored)
    assert restored.queue.rowCount() == 0
    assert not restored.start.isEnabled()
    restored.show()
    restored.view_queue.click()
    assert restored.queue.isVisible()


def test_search_zen_uses_profile_and_numeric_sort_and_website_uses_selected_identity(
    qtbot, tmp_path, monkeypatch
):
    import httpx

    from tests.test_torrent_search import fake_indexer
    from torrentlinker.zen import ZenClass, ZenProfile, ZenTorrent, added_values, note

    indexer = fake_indexer(lambda r: httpx.Response(200, json={}))
    settings = Settings(zen=ZenProfile(zen_class=ZenClass.SWARM_KEEPER, seed_hours=12))
    view = SearchTab(Store(tmp_path / "state.db"), settings, lambda: indexer, FakeQbit)
    qtbot.addWidget(view)
    rows = [
        Candidate("Large", 20 * 10**9, "download-a", 10, "a" * 40, categories=(2030,)),
        Candidate("Small", 10**9, "download-b", 5, "b" * 40, categories=(2030,)),
    ]
    view._show_results(rows, indexer)
    expected = added_values(
        settings.zen, [ZenTorrent(c.size, c.seeders + 1, category=2030) for c in rows]
    )
    for i, _row in enumerate(rows):
        assert view.results.item(i, 5).text() == f"≈ {expected[i]:.2f}"
        expected_note = note(
            settings.zen.zen_class, ZenTorrent(_row.size, _row.seeders + 1, category=2030)
        )
        assert view.results.item(i, 6).text() == f"≈ {expected_note:.2f}"
        assert view.results.item(i, 6).data(Qt.ItemDataRole.UserRole) == expected_note
    view.results.sortItems(5, Qt.SortOrder.AscendingOrder)
    view.results.selectRow(0)
    opened = []
    monkeypatch.setattr(
        "torrentlinker.gui.search_view.QDesktopServices.openUrl",
        lambda url: opened.append(url.toString()),
    )
    view.website_button.click()
    assert opened == ["https://c411.org/torrents/" + "b" * 40]
    assert view.selected_results() == [rows[1]]
    assert "Revival payouts are separate" in view.results.item(0, 5).toolTip()
    view.results.sortItems(6, Qt.SortOrder.AscendingOrder)
    view.results.clearSelection()
    view.results.selectRow(0)
    chosen_note = view.results.item(0, 6).text()
    view.show_details()
    assert f"Note: {chosen_note}" in view.browser.currentWidget().summary.text()


def test_search_existing_seeds_keep_their_current_estimate_after_sorting(qtbot, tmp_path):
    from types import SimpleNamespace

    import httpx

    from tests.test_torrent_search import fake_indexer
    from torrentlinker.seeding import Seeding, SeedingRow
    from torrentlinker.zen import ZenDay, ZenTorrent, note

    indexer = fake_indexer(lambda r: httpx.Response(200, json={}))
    view = SearchTab(Store(tmp_path / "state.db"), Settings(), lambda: indexer, FakeQbit)
    qtbot.addWidget(view)
    a = Candidate("A", 10**9, "a", 1, "a" * 40)
    b = Candidate("B", 2 * 10**9, "b", 2, "b" * 40)
    view._show_results([a, b], indexer)
    view.results.sortItems(0, Qt.SortOrder.DescendingOrder)
    active = [ZenTorrent(10**9, 2, 180)]
    view.set_seeding(Seeding([SeedingRow(SimpleNamespace(hash="a" * 40), 0.89)], ZenDay(), active))
    assert view.results.item(1, 0).text() == "A"
    assert view.results.item(1, 5).text() == "≈ 0.89"
    assert "Already seeding" in view.results.item(1, 5).toolTip()
    expected_note = note(view.settings.zen.zen_class, active[0])
    assert view.results.item(1, 6).text() == f"≈ {expected_note:.2f}"


def test_download_now_sends_only_open_torrent_and_keeps_others_queued(qtbot, tmp_path):
    from torrentlinker.download_queue import enqueue

    store, indexer, client, c, data, folder = setup_queue(tmp_path)
    other = Candidate("Other", 100, "https://example.test/other", 1, "f" * 40)
    enqueue(store, indexer, other, folder)
    view = SearchTab(store, Settings(), lambda: indexer, lambda: client)
    qtbot.addWidget(view)
    message = view.download_candidate(c, indexer, str(folder), True)
    assert "started" in message
    qtbot.waitUntil(lambda: view.runner is None)
    assert client.downloads == [c.infohash]
    states = {entry.candidate.infohash: entry.status for entry in entries(store)}
    assert states == {other.infohash: "queued"}


def test_add_from_details_does_not_start_queue_or_silently_change_folder(qtbot, tmp_path):
    import pytest

    store, indexer, client, c, data, folder = setup_queue(tmp_path)
    view = SearchTab(store, Settings(), lambda: indexer, lambda: client)
    qtbot.addWidget(view)
    assert view.download_candidate(c, indexer, str(folder), False) == "Already in the queue."
    assert client.downloads == []
    different = tmp_path / "other"
    different.mkdir()
    with pytest.raises(ValueError, match="already queued"):
        view.download_candidate(c, indexer, str(different), True)
    with pytest.raises(ValueError, match="accessible"):
        view.download_candidate(c, indexer, "", False)
    assert len(entries(store)) == 1 and entries(store)[0].folder == folder


def test_saved_queue_shows_zen_and_note_without_search_results(qtbot, tmp_path):
    from torrentlinker.download_queue import enqueue
    from torrentlinker.zen import ZenClass, ZenProfile, ZenTorrent, added_values, note

    store = Store(tmp_path / "queue-estimates.db")
    indexer = Indexer("https://c411.org/api", "key")
    candidate = Candidate("Queued", 10**9, "download", 5, "a" * 40, categories=(2030,))
    enqueue(store, indexer, candidate, tmp_path)
    settings = Settings(zen=ZenProfile(zen_class=ZenClass.SWARM_KEEPER, seed_hours=12))
    view = SearchTab(store, settings, lambda: indexer, FakeQbit)
    qtbot.addWidget(view)
    torrent = ZenTorrent(candidate.size, candidate.seeders + 1, category=2030)
    expected = added_values(settings.zen, [torrent])[0]
    assert view.queue.horizontalHeaderItem(2).text() == "≈ Zen/day"
    assert view.queue.item(0, 2).text() == f"≈ {expected:.2f}"
    assert view.queue.item(0, 3).text() == f"≈ {note(settings.zen.zen_class, torrent):.2f}"
    assert "Revival payouts are separate" in view.queue.item(0, 2).toolTip()
    view._show_results([candidate], indexer)
    for queue_column, result_column in ((2, 5), (3, 6)):
        queued = view.queue.item(0, queue_column)
        result = view.results.item(0, result_column)
        assert queued.text() == result.text()
        assert queued.toolTip() == result.toolTip()
        assert queued.data(Qt.ItemDataRole.UserRole) == result.data(Qt.ItemDataRole.UserRole)


def test_queue_estimates_refresh_with_profile_and_current_seeding_after_sorting(qtbot, tmp_path):
    from types import SimpleNamespace

    from torrentlinker.download_queue import enqueue
    from torrentlinker.seeding import Seeding, SeedingRow
    from torrentlinker.zen import ZenClass, ZenDay, ZenProfile, ZenTorrent, added_values, note

    store = Store(tmp_path / "refresh-queue.db")
    indexer = Indexer("https://c411.org/api", "key")
    small = Candidate("Small", 10**9, "small", 40, "a" * 40, categories=(2030,))
    large = Candidate("Large", 20 * 10**9, "large", 1, "b" * 40, categories=(2030,))
    for candidate in (small, large):
        enqueue(store, indexer, candidate, tmp_path)
    settings = Settings(zen=ZenProfile(zen_class=ZenClass.SWARM_KEEPER))
    view = SearchTab(store, settings, lambda: indexer, FakeQbit)
    qtbot.addWidget(view)
    view.queue.sortItems(2, Qt.SortOrder.DescendingOrder)
    assert view.queue.item(0, 0).text() == "Large"
    view.queue.selectRow(0)
    selected = view.selected_keys()
    previous = view.queue.item(0, 2).data(Qt.ItemDataRole.UserRole)
    settings.zen = replace(settings.zen, seed_hours=12)
    view.refresh_estimates()
    assert view.queue.item(0, 2).data(Qt.ItemDataRole.UserRole) == previous / 2
    assert view.selected_keys() == selected
    view.queue.sortItems(3, Qt.SortOrder.DescendingOrder)
    assert view.queue.item(0, 0).text() == "Large"
    # An already seeded torrent uses its observed swarm/age and current earnings.
    active = ZenTorrent(small.size, 2, 180, category=2030)
    view.set_seeding(
        Seeding([SeedingRow(SimpleNamespace(hash=small.infohash), 0.89)], ZenDay(), [active])
    )
    rows = {view.queue.item(i, 0).text(): i for i in range(2)}
    assert view.queue.item(rows["Small"], 2).text() == "≈ 0.89"
    assert "Already seeding" in view.queue.item(rows["Small"], 2).toolTip()
    assert view.queue.item(rows["Small"], 3).data(Qt.ItemDataRole.UserRole) == note(
        settings.zen.zen_class, active
    )
    expected = added_values(
        settings.zen, [ZenTorrent(large.size, large.seeders + 1, category=2030)], [active]
    )[0]
    assert view.queue.item(rows["Large"], 2).data(Qt.ItemDataRole.UserRole) == expected
    assert "count unavailable" not in view.queue.item(rows["Large"], 2).toolTip()
    assert view.selected_keys() == selected
    view.refresh_queue()
    assert view.selected_keys() == selected


@pytest.mark.parametrize("during_refresh", [False, True])
def test_queue_estimates_keep_displayed_snapshot_when_background_send_removes_entry(
    qtbot, tmp_path, monkeypatch, during_refresh
):
    from types import SimpleNamespace

    from torrentlinker.gui import search_view
    from torrentlinker.seeding import Seeding, SeedingRow
    from torrentlinker.zen import ZenDay, ZenTorrent

    store = Store(tmp_path / "background-send.db")
    indexer = Indexer("https://c411.org/api", "key")
    sent = Candidate("Sent film", 10**9, "sent", 5, "a" * 40, categories=(2030,))
    kept = Candidate("Keep queued", 2 * 10**9, "kept", 10, "b" * 40, categories=(2030,))
    for candidate in (sent, kept):
        enqueue(store, indexer, candidate, tmp_path)
    view = SearchTab(store, Settings(), lambda: indexer, FakeQbit)
    qtbot.addWidget(view)
    view.queue_timer.stop()
    monkeypatch.setattr(view, "isVisible", lambda: True)
    view.queue.sortItems(0, Qt.SortOrder.AscendingOrder)
    view.queue.selectRow(0)
    selected = view.selected_keys()
    sent_key = next(e.key for e in entries(store) if e.candidate == sent)
    if during_refresh:
        original = search_view.entries

        def read_then_send(current_store):
            snapshot = original(current_store)
            # Successful sends remove the row after the UI's database read.
            store.remove_downloads([sent_key])
            return snapshot

        monkeypatch.setattr(search_view, "entries", read_then_send)
        view.refresh_queue()
    else:
        store.remove_downloads([sent_key])
    view.set_seeding(
        Seeding(
            [SeedingRow(SimpleNamespace(hash=sent.infohash), 0.89)],
            ZenDay(),
            [ZenTorrent(sent.size, 6, category=2030)],
        )
    )
    assert view.queue.rowCount() == 2
    assert view.queue.item(1, 0).text() == "Sent film"
    assert view.queue.item(1, 2).text() == "≈ 0.89"
    assert view.selected_keys() == selected
    assert view.queue.isSortingEnabled()
    assert not view.queue.signalsBlocked()
    # The normal queue poll reconciles the displayed rows with the next snapshot.
    view._poll_queue()
    assert view.queue.rowCount() == 1
    assert view.queue.item(0, 0).text() == "Keep queued"
    assert view.selected_keys() == selected
    assert view.view_queue.text() == "Download queue (1)"


def test_queue_estimates_use_saved_origin_when_current_indexer_changes(qtbot, tmp_path):
    from torrentlinker.download_queue import enqueue

    store = Store(tmp_path / "origins.db")
    c411 = Indexer("https://c411.org/api", "key")
    other = Indexer("https://other.test/api", "key")
    candidate = Candidate("C411", 10**9, "download", 3, "a" * 40)
    foreign = Candidate("Other", 10**9, "other", 3, "b" * 40)
    enqueue(store, c411, candidate, tmp_path)
    enqueue(store, other, foreign, tmp_path)
    view = SearchTab(store, Settings(), lambda: other, FakeQbit)
    qtbot.addWidget(view)
    view._show_results([foreign], other)
    view.refresh_estimates()
    view.queue.sortItems(2, Qt.SortOrder.DescendingOrder)
    assert view.queue.item(0, 0).text() == "C411"
    assert view.queue.item(0, 2).text().startswith("≈ ")
    assert view.queue.item(0, 3).text().startswith("≈ ")
    assert view.queue.item(1, 2).text() == "–"
    assert view.queue.item(1, 3).text() == "–"
    assert "available for c411" in view.queue.item(1, 2).toolTip()


def test_search_queue_tracks_external_writes(qtbot, tmp_path):
    from torrentlinker.download_queue import enqueue

    store = Store(tmp_path / "state.db")
    writer = Store(tmp_path / "state.db")
    indexer = Indexer("https://example.test/api", "key")
    view = SearchTab(store, Settings(), lambda: indexer, FakeQbit)
    qtbot.addWidget(view)
    view.show()
    candidate = Candidate("Added by Assistant", 1024, "https://example.test/a", 2, "a" * 40)
    enqueue(writer, indexer, candidate, tmp_path)
    qtbot.waitUntil(lambda: view.queue.rowCount() == 1, timeout=3000)
    assert view.queue.item(0, 0).text() == "Added by Assistant"
    view.queue.selectRow(0)
    selected = view.selected_keys()
    view.hide()
    enqueue(writer, indexer, replace(candidate, title="Second", infohash="b" * 40), tmp_path)
    view.show()
    assert view.queue.rowCount() == 2
    assert view.selected_keys() == selected
    writer.download_outcome(selected[0], "sent")
    qtbot.waitUntil(lambda: view.queue.item(0, 5).text() == "Sent to client", timeout=3000)
    assert view.selected_keys() == selected
    writer.remove_downloads(selected)
    qtbot.waitUntil(lambda: view.queue.rowCount() == 1, timeout=3000)
    assert view.queue.item(0, 0).text() == "Second"


def test_search_queue_poll_keeps_client_scope(qtbot, tmp_path):
    from torrentlinker.download_queue import enqueue

    store = Store(tmp_path / "state.db")
    writer = Store(tmp_path / "state.db")
    indexer = Indexer("https://example.test/api", "key")
    settings = Settings()
    settings.ensure_profiles()
    settings.client_profiles.append(ClientProfile("other", "Other"))
    view = SearchTab(store, settings, lambda: indexer, FakeQbit)
    qtbot.addWidget(view)
    view.show()
    candidate = Candidate("Other client", 1024, "https://example.test/a", 2, "a" * 40)
    enqueue(writer.for_client("other"), indexer, candidate, tmp_path)
    view._poll_queue()
    assert view.queue.rowCount() == 0
    view.store = store.for_client("other")
    view._poll_queue()
    assert view.queue.rowCount() == 1
    assert view.queue.item(0, 0).text() == "Other client"


def test_queue_selection_uses_displayed_snapshot_without_reloading_store(
    qtbot, tmp_path, monkeypatch
):
    from torrentlinker.gui import search_view

    store = Store(tmp_path / "state.db")
    indexer = Indexer("https://example.test/api", "key")
    enqueue(store, indexer, Candidate("Queued", 1024, "fixture"), tmp_path)
    view = SearchTab(store, Settings(), lambda: indexer, FakeQbit)
    qtbot.addWidget(view)
    reads = []
    original = search_view.entries

    def read(current):
        reads.append(True)
        return original(current)

    monkeypatch.setattr(search_view, "entries", read)
    view.queue.selectRow(0)
    assert not reads, "Selection reloaded the entire queue"
    assert view.remove.isEnabled()
    assert view.start.isEnabled()
    assert not view.retry.isEnabled()


@pytest.mark.parametrize("sort_column", [0, 5])
def test_queue_status_poll_updates_only_status_cells_and_keeps_exact_selection(
    qtbot, tmp_path, monkeypatch, sort_column
):
    from torrentlinker.gui import search_view

    store = Store(tmp_path / "state.db")
    indexer = Indexer("https://c411.org/api", "key")
    for i in range(3):
        enqueue(store, indexer, Candidate(f"Fixture {i}", 10**9, f"fixture-{i}", i), tmp_path)
    view = SearchTab(store, Settings(), lambda: indexer, FakeQbit)
    qtbot.addWidget(view)
    view.queue_timer.stop()
    monkeypatch.setattr(view, "isVisible", lambda: True)
    view.queue.sortItems(sort_column, Qt.SortOrder.AscendingOrder)
    view.queue.selectRow(0)
    selected = view.selected_keys()
    changed_columns = set()
    view.queue.model().dataChanged.connect(
        lambda top, bottom, roles: changed_columns.update(range(top.column(), bottom.column() + 1))
    )
    reads = []
    original = search_view.entries

    def read(current):
        reads.append(True)
        return original(current)

    monkeypatch.setattr(search_view, "entries", read)
    store.download_outcome(selected[0], "refused", "Fixture refusal")
    view._poll_queue()
    assert len(reads) == 1, "One queue poll decoded the database more than once"
    assert changed_columns <= {5, 6}, "Status-only change rebuilt unrelated queue cells"
    selected_row = view.queue.selectionModel().selectedRows()[0].row()
    assert view.queue.item(selected_row, 5).text() == "Skipped"
    assert view.queue.item(selected_row, 6).toolTip() == "Fixture refusal"
    assert view.selected_keys() == selected
    assert view.retry.isEnabled()
    assert "1 need attention" in view.queue_summary.text()
    assert view.queue.isSortingEnabled() and not view.queue.signalsBlocked()

    store.download_outcome(selected[0], "failed", "Uncertain transport", retry_safe=False)
    view._poll_queue()
    assert not view.retry.isEnabled()
    store.download_outcome(selected[0], "failed", "Known refusal", retry_safe=True)
    view._poll_queue()
    assert view.retry.isEnabled()
    assert view.selected_keys() == selected


def test_a_sent_entry_leaves_the_queue_without_rebuilding_the_other_rows(
    qtbot, tmp_path, monkeypatch
):
    store = Store(tmp_path / "state.db")
    indexer = Indexer("https://c411.org/api", "key")
    for i in range(3):
        enqueue(store, indexer, Candidate(f"Fixture {i}", 10**9, f"fixture-{i}", i), tmp_path)
    view = SearchTab(store, Settings(), lambda: indexer, FakeQbit)
    qtbot.addWidget(view)
    view.queue_timer.stop()
    monkeypatch.setattr(view, "isVisible", lambda: True)
    view.queue.selectRow(2)
    selected = view.selected_keys()
    kept = [view.queue.item(row, 0) for row in range(2)]
    sent = view.queue.item(0, 0).data(Qt.ItemDataRole.UserRole)

    store.remove_downloads([sent])
    view._poll_queue()

    assert view.queue.rowCount() == 2
    assert view.queue.item(0, 0) is kept[1], "The remaining rows were rebuilt"
    assert view.selected_keys() == selected
    assert "Download queue (2)" in view.view_queue.text()


def test_unchanged_queue_poll_does_not_decode_entries(qtbot, tmp_path, monkeypatch):
    from torrentlinker.gui import search_view

    store = Store(tmp_path / "state.db")
    indexer = Indexer("https://example.test/api", "key")
    enqueue(store, indexer, Candidate("Queued", 1024, "fixture"), tmp_path)
    view = SearchTab(store, Settings(), lambda: indexer, FakeQbit)
    qtbot.addWidget(view)
    view.queue_timer.stop()
    monkeypatch.setattr(view, "isVisible", lambda: True)
    monkeypatch.setattr(
        search_view, "entries", lambda *a: pytest.fail("Unchanged poll decoded the entire queue")
    )
    view._poll_queue()


def test_queue_poll_detects_a_replacement_database_with_the_same_client_id(
    qtbot, tmp_path, monkeypatch
):
    store = Store(tmp_path / "first.db")
    other = Store(tmp_path / "second.db")
    indexer = Indexer("https://example.test/api", "key")
    enqueue(store, indexer, Candidate("First", 1024, "first"), tmp_path)
    enqueue(other, indexer, Candidate("Second", 1024, "second"), tmp_path)
    view = SearchTab(store, Settings(), lambda: indexer, FakeQbit)
    qtbot.addWidget(view)
    view.queue_timer.stop()
    monkeypatch.setattr(view, "isVisible", lambda: True)
    assert view.queue.item(0, 0).text() == "First"
    view.store = other
    view._poll_queue()
    assert view.queue.item(0, 0).text() == "Second"


def test_native_send_progress_keeps_rows_and_finishes_without_another_database_read(
    qtbot, tmp_path, monkeypatch
):
    from torrentlinker.gui import search_view
    from torrentlinker.send import SendProgress, SendReport

    store = Store(tmp_path / "state.db")
    indexer = Indexer("https://example.test/api", "key")
    enqueue(store, indexer, Candidate("Queued", 1024, "fixture"), tmp_path)
    view = SearchTab(store, Settings(), lambda: indexer, FakeQbit)
    qtbot.addWidget(view)
    view.queue_timer.stop()
    view.queue.selectRow(0)
    key = view.selected_keys()[0]
    title = view.queue.item(0, 0)
    # Native progress must also update while this page is hidden.
    assert not view.isVisible()
    view.runner = object()
    store.download_outcome(key, "sending")
    view._progress(SendProgress(0, 1, "Queued", 0, 0, 0))
    assert view.queue.item(0, 0) is title, "Send progress recreated the queue rows"
    assert view.queue.item(0, 5).text() == "Sending…"
    assert view.pause.isEnabled() and not view.start.isEnabled()
    monkeypatch.setattr(
        search_view, "entries", lambda *a: pytest.fail("Unchanged progress decoded the queue")
    )
    view._progress(SendProgress(0, 1, "Queued", 0, 0, 0, retry_at=1))
    assert "waiting until" in view.queue_status.text()
    view._finished(SendReport(stopped=True))
    assert view.runner is None
    assert not view.pause.isEnabled()
    assert view.remove.isEnabled()
    assert view.selected_keys() == [key]


def test_native_queue_pause_interrupts_retry_wait_and_resume_sends_once(
    qtbot, tmp_path, monkeypatch
):
    from torrentlinker.indexer import RateLimited

    store, indexer, client, candidate, data, folder = setup_queue(tmp_path)

    def limited(*args, **kwargs):
        raise RateLimited(120)

    monkeypatch.setattr(indexer, "fetch_torrent", limited)
    view = SearchTab(store, Settings(), lambda: indexer, lambda: client)
    qtbot.addWidget(view)
    view.queue_timer.stop()
    view.queue.selectRow(0)
    selected = view.selected_keys()
    assert view.start_queue()
    try:
        qtbot.waitUntil(lambda: "waiting until" in view.queue_status.text())
        view.pause.click()
        qtbot.waitUntil(lambda: view.runner is None, timeout=2000)
        assert not client.downloads
        assert entries(store)[0].status == "queued"
        assert view.selected_keys() == selected
        assert view.start.isEnabled() and not view.pause.isEnabled()
        monkeypatch.setattr(indexer, "fetch_torrent", lambda *a, **kw: data)
        assert view.start_queue()
        qtbot.waitUntil(lambda: view.runner is None)
        assert client.downloads == [candidate.infohash]
        assert not entries(store) and view.queue.rowCount() == 0
        assert not view.start.isEnabled() and not view.pause.isEnabled()
    finally:
        if view.runner:
            view.stop()
            qtbot.waitUntil(lambda: view.runner is None)


def test_native_queue_worker_failure_keeps_uncertain_entry_without_retry(qtbot, tmp_path):
    store = Store(tmp_path / "state.db")
    indexer = Indexer("https://example.test/api", "key")
    enqueue(store, indexer, Candidate("Queued", 1024, "fixture"), tmp_path)
    view = SearchTab(store, Settings(), lambda: indexer, FakeQbit)
    qtbot.addWidget(view)
    view.queue_timer.stop()
    view.queue.selectRow(0)
    key = view.selected_keys()[0]
    view.runner = object()
    store.download_outcome(key, "sending")
    view._failed(RuntimeError("Fixture worker failure"))
    assert view.runner is None
    assert entries(store)[0].status == "uncertain"
    assert view.queue.item(0, 5).text() == "Check Torrent Client before retrying"
    assert view.selected_keys() == [key]
    assert not view.pause.isEnabled() and not view.retry.isEnabled()
    assert view.queue_status.text() == "Fixture worker failure"
