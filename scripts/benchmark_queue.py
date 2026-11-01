"""Isolated Linux download-queue timings; no Indexer or Torrent Client calls."""

from __future__ import annotations

import argparse
import json
import os
import platform
import resource
import tempfile
import time
from pathlib import Path
from types import SimpleNamespace


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--count", type=int, default=1000)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    if args.count < 1:
        parser.error("count must be positive")
    with tempfile.TemporaryDirectory(prefix="tl-queue-benchmark-") as temporary:
        for name in ("XDG_DATA_HOME", "XDG_CONFIG_HOME", "XDG_CACHE_HOME"):
            os.environ[name] = str(Path(temporary) / name)
        os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
        from PySide6.QtCore import QTimer, qVersion
        from PySide6.QtWidgets import QApplication

        from torrentlinker.config import Settings
        from torrentlinker.gui.search_view import SearchTab
        from torrentlinker.send import SendProgress
        from torrentlinker.store import Store

        store = Store(Path(temporary) / "state.db")
        with store._db:
            store._db.executemany(
                "INSERT INTO download_queue (id,payload,added_at) VALUES (?,?,?)",
                (
                    (
                        str(i),
                        json.dumps(
                            {
                                "client_id": "default",
                                "folder": "/fixture/downloads",
                                "indexer_url": "https://c411.org/api/torznab",
                                "candidate": {
                                    "title": f"Fixture release {i:06d}",
                                    "size": (i + 1) * 1024**2,
                                    "download_url": f"https://fixture.invalid/{i}",
                                    "seeders": i % 30,
                                    "categories": [2030],
                                },
                            }
                        ),
                        i,
                    )
                    for i in range(args.count)
                ),
            )
        app = QApplication([])
        app.setOrganizationName("TorrentLinkerBenchmark")
        app.setApplicationName("IsolatedQueue")

        def no_client():
            raise AssertionError("Benchmark must not contact a Torrent Client")

        constructed = time.perf_counter()
        view = SearchTab(store, Settings(), lambda: SimpleNamespace(api_key=""), no_client)
        view.queue_timer.stop()
        view.age_timer.stop()
        view.show_queue()
        view.resize(1280, 850)
        construction_ms = (time.perf_counter() - constructed) * 1000
        heartbeat = QTimer()
        heartbeat.setInterval(10)
        ticks = []
        heartbeat.timeout.connect(lambda: ticks.append(time.perf_counter()))
        heartbeat.start()

        def measure(action):
            ticks.clear()
            start = time.perf_counter()
            ticks.append(start)
            action()
            until = time.perf_counter() + 0.02
            while time.perf_counter() < until:
                app.processEvents()
                time.sleep(0.001)
            return {
                "elapsed_ms": round((time.perf_counter() - start) * 1000, 2),
                "max_heartbeat_gap_ms": round(
                    max((b - a for a, b in zip(ticks, ticks[1:], strict=False)), default=0) * 1000,
                    2,
                ),
            }

        try:
            measurements = {"first_display": measure(view.show)}
            measurements["first_usable_ms"] = round(
                construction_ms + measurements["first_display"]["elapsed_ms"], 2
            )
            assert view.queue.rowCount() == args.count
            measurements["unchanged_poll"] = [measure(view._poll_queue) for _ in range(3)]
            measurements["estimate_refresh"] = [measure(view.refresh_estimates) for _ in range(3)]
            measurements["selection"] = measure(lambda: view.queue.selectRow(0))
            selected = view.selected_keys()
            assert len(selected) == 1
            for i in range(3):
                store.download_outcome(
                    str(args.count - 1), "refused" if i % 2 == 0 else "queued", "Fixture outcome"
                )
                measurements.setdefault("changed_poll", []).append(measure(view._poll_queue))
                assert view.selected_keys() == selected
            progress = SendProgress(0, args.count, "Fixture release", 0, 0, 0)
            for i in range(3):
                store.download_outcome(str(args.count - 1), "sending" if i % 2 == 0 else "queued")
                measurements.setdefault("native_progress", []).append(
                    measure(lambda: view._progress(progress))
                )
                assert view.selected_keys() == selected
            measurements["unchanged_native_progress"] = [
                measure(lambda: view._progress(progress)) for _ in range(3)
            ]
            for i in range(3):
                # A successful send removes its entry from the queue.
                store.remove_downloads([str(args.count - 2 - i)])
                measurements.setdefault("sent_progress", []).append(
                    measure(lambda: view._progress(progress))
                )
                assert view.queue.rowCount() == args.count - i - 1
                assert view.selected_keys() == selected
            result = {
                "count": args.count,
                "python": platform.python_version(),
                "qt": qVersion(),
                "qt_platform": app.platformName(),
                "measurements": measurements,
                "peak_rss_mib": round(resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / 1024, 1),
            }
            text = json.dumps(result, indent=2)
            if args.output:
                args.output.write_text(text + "\n", encoding="utf-8")
            print(text)
        finally:
            heartbeat.stop()
            view.close()
            store._db.close()


if __name__ == "__main__":
    main()
