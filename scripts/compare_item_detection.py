"""Compare depth-free item detection with the depth scan on real Library Folders.

Read-only: lists directories and reads Catalogue sizes. Never contacts a Torrent Client
or an Indexer. Prints paths, so review the output before sharing it.

With `--folder PATH:KIND[:DEPTH]`, compares folders that are not configured yet.
`--timing` reports the detection time and directory listings for each folder.
"""

from __future__ import annotations

import argparse
import json
import os
import sqlite3
import time
from collections import Counter
from pathlib import Path

from torrentlinker.domain import ContentKind
from torrentlinker.item_detection import detect_items
from torrentlinker.scan import ScanResult, _entries_at_depth


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("data_dir", type=Path, help="folder holding settings.json and state.db")
    parser.add_argument("--folder", action="append", default=[], help="PATH:KIND[:DEPTH]")
    parser.add_argument("--samples", type=int, default=15)
    parser.add_argument("--timing", action="store_true")
    args = parser.parse_args()
    with sqlite3.connect(f"file:{args.data_dir / 'state.db'}?mode=ro", uri=True) as db:
        sizes = frozenset(row[0] for row in db.execute("SELECT DISTINCT size FROM catalogue"))
    if args.folder:
        folders = []
        for spec in args.folder:
            path, kind, *depth = spec.rsplit(":", 2) if spec.count(":") == 2 else spec.split(":")
            folders.append({"path": path, "kind": kind, "depth": int(depth[0]) if depth else 1})
    else:
        settings = json.loads((args.data_dir / "settings.json").read_text(encoding="utf-8"))
        folders = settings.get("library_folders", [])
    for folder in folders:
        root, kind = Path(folder["path"]), ContentKind(folder["kind"])
        by_depth = set(_entries_at_depth(root, folder["depth"], ScanResult()))
        started = time.perf_counter()
        detected = detect_items(root, kind, catalogue_sizes=sizes)
        elapsed = time.perf_counter() - started
        items = {item.path for item in detected if item.within is None}
        print(f"\n== {root} ({kind}, depth {folder['depth']})")
        print(f"depth scan: {len(by_depth)} items; detected: {len(items)} items")
        print("reasons:", dict(Counter(item.reason.value for item in detected)))
        if args.timing:
            files = sum(len(names) for _dirpath, _dirs, names in os.walk(root))
            print(f"detection: {elapsed:.2f} s for {files} files")
        for label, paths in (
            ("only in depth scan", by_depth - items),
            ("only detected", items - by_depth),
        ):
            print(f"{label}: {len(paths)}")
            for path in sorted(paths)[: args.samples]:
                reason = next((i.reason.value for i in detected if i.path == path), "")
                print(f"  {path.relative_to(root)}  {reason}")


if __name__ == "__main__":
    main()
