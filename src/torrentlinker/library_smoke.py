"""Controlled native Library rescans for packaged-build acceptance."""

from __future__ import annotations

import tempfile
import threading
import time
from collections.abc import Callable
from contextlib import contextmanager
from pathlib import Path
from typing import cast

import httpx
import keyring
from keyring.backends.null import Keyring
from PySide6.QtCore import QCoreApplication, QEvent, QEventLoop, QTimer

from torrentlinker.config import Settings
from torrentlinker.domain import LibraryFolder, Torrent
from torrentlinker.gui import tasks
from torrentlinker.gui.main_window import MainWindow
from torrentlinker.indexer import Indexer
from torrentlinker.library import LibraryEntry
from torrentlinker.paths import MappedQbit, PathMapper
from torrentlinker.qbit import Qbit
from torrentlinker.store import Store


@contextmanager
def _empty_keyring():
    previous = keyring.get_keyring()
    keyring.set_keyring(Keyring())
    try:
        yield
    finally:
        keyring.set_keyring(previous)


def _wait_until(done: Callable[[], bool], timeout: float = 5) -> None:
    deadline = time.monotonic() + timeout
    while not done():
        if time.monotonic() >= deadline:
            raise RuntimeError("Bundled Library rescan did not finish within its deadline")
        loop = QEventLoop()
        QTimer.singleShot(10, loop.quit)
        loop.exec()


def library_smoke_checks() -> None:
    """Scan temporary files ten times through the actual window and worker callbacks."""
    released = threading.Event()
    reads: list[int] = []
    beats: list[float] = []
    errors: list[str] = []

    class Client:
        def torrents(self, **kwargs: object) -> list[Torrent]:
            reads.append(threading.get_ident())
            if not released.wait(2):
                raise RuntimeError("GUI heartbeat did not release the Library client read")
            return []

    def no_request(request: httpx.Request) -> httpx.Response:
        raise AssertionError("Library smoke check must not contact an Indexer")

    with (
        _empty_keyring(),
        tempfile.TemporaryDirectory(prefix="torrentlinker-library-smoke-") as temporary,
        httpx.Client(transport=httpx.MockTransport(no_request)) as http,
    ):
        directory = Path(temporary)
        folder = directory / "Library"
        folder.mkdir()
        expected = {folder / f"Book-{i}.epub": b"book" * (i + 1) for i in range(25)}
        for path, contents in expected.items():
            path.write_bytes(contents)
        store = Store(directory / "state.db")
        indexer = Indexer("https://fixture.invalid/api", "", http=http, cache=store)
        # This fixture has no torrents, so the read-only client only needs torrents().
        client = MappedQbit(cast(Qbit, Client()), PathMapper([]))

        class Window(MainWindow):
            def apply_proxy(self) -> None:
                pass  # The isolated adapters never use a proxy or read its saved password.

            def show_account_profile(self) -> None:
                pass  # This Library probe does not bind a real tracker account.

            def qbit(self) -> MappedQbit:
                return client

            def indexer(self) -> Indexer:
                return indexer

            def _scan_done(self, entries: list[LibraryEntry]) -> None:
                # Fail in the command, not a modal dialog on a headless CI runner.
                if getattr(entries, "client_error", None) or getattr(entries, "errors", []):
                    errors.append("Controlled scan returned client or filesystem errors")
                    self.scan_action.setEnabled(True)
                    return
                super()._scan_done(entries)

            def _scan_failed(self, error: Exception) -> None:
                errors.append(str(error))
                self.scan_action.setEnabled(True)

            def _restore_failed(self, error: Exception) -> None:
                errors.append(str(error))

        window = Window(
            Settings(library_folders=[LibraryFolder(folder)]), directory / "settings.json", store
        )
        timer = QTimer(window)
        timer.setInterval(10)

        def heartbeat() -> None:
            beats.append(time.monotonic())
            released.set()

        timer.timeout.connect(heartbeat)
        main_thread = threading.get_ident()
        try:
            window.show()
            timer.start()
            for iteration in range(10):
                if iteration % 2:
                    changed = folder / "Book-0.epub"
                    expected[changed] = b"updated" * (iteration + 1)
                    changed.write_bytes(expected[changed])
                before = len(beats)
                released.clear()
                window.scan(refresh=bool(iteration % 2))
                assert not window.scan_action.isEnabled()
                _wait_until(lambda: window.scan_action.isEnabled() and not tasks._alive)
                assert not errors, errors
                assert len(beats) > before
                assert window.rescan_action.isEnabled() and window.add_folder_action.isEnabled()
                assert {
                    entry.item.path: entry.item.size for entry in window.library_model.entries
                } == {path: len(data) for path, data in expected.items()}
            assert len(reads) == 10 and all(thread != main_thread for thread in reads)
            assert {path: path.read_bytes() for path in expected} == expected
        finally:
            released.set()
            timer.stop()
            _wait_until(lambda: not tasks._alive)
            window.close()
            window.deleteLater()
            QCoreApplication.sendPostedEvents(window, QEvent.Type.DeferredDelete)
            store._db.close()
    print("native Library rescan smoke test OK (10 scans, 25 items)")
