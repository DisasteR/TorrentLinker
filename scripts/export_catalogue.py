"""Export your Catalogue for the release build, and replace the private release's file.

    python scripts/export_catalogue.py              # export, upload, delete the local file
    python scripts/export_catalogue.py --keep OUT   # export to OUT only, no upload

Run it before tagging a release (docs/guide/en/development.md). It reads a read-only
copy of the desktop app's state, so the app may stay open. The release build seals the
file with a new key (scripts/bundle_catalogue.py) and refuses one older than two days.
The file holds the whole Catalogue in plain text: it is deleted once uploaded.
"""

import argparse
import sqlite3
import subprocess
import sys
import tempfile
from pathlib import Path

from torrentlinker.bundled_catalogue import export
from torrentlinker.store import Store

REPO = "Vietnoirien/torrentlinker-catalogue"
RELEASE = "catalogue"
NAME = "catalogue.json.xz"


def default_state() -> Path:
    """The desktop app's state, where Qt puts it on this system."""
    from PySide6.QtCore import QCoreApplication, QStandardPaths

    QCoreApplication.setApplicationName("TorrentLinker")
    QCoreApplication.setOrganizationName("TorrentLinker")
    location = QStandardPaths.StandardLocation.AppDataLocation
    return Path(QStandardPaths.writableLocation(location)) / "state.db"


def snapshot(state: Path, copy: Path) -> None:
    """A consistent copy of a database the app may be writing. Opening the live file as
    a Store would also tidy its download queue, which belongs to the running app."""
    source = sqlite3.connect(f"file:{state}?mode=ro", uri=True)
    target = sqlite3.connect(copy)
    try:
        source.backup(target)
    finally:
        target.close()
        source.close()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--state", type=Path, help="state.db (default: the desktop app's)")
    parser.add_argument("--keep", type=Path, metavar="OUT", help="write here, do not upload")
    args = parser.parse_args()
    state = args.state or default_state()
    if not state.is_file():
        sys.exit(f"No TorrentLinker state at {state}")
    with tempfile.TemporaryDirectory() as folder:
        copy = Path(folder) / "state.db"
        snapshot(state, copy)
        out = args.keep or Path(folder) / NAME
        count = export(Store(copy), out)
        print(f"{count} torrents exported ({out.stat().st_size // 1024} KiB)")
        if args.keep:
            return 0
        subprocess.run(
            ["gh", "release", "upload", RELEASE, str(out), "--clobber", "--repo", REPO],
            check=True,
        )
        print(f"Uploaded to {REPO}, release {RELEASE}; the local copy is deleted")
    return 0


if __name__ == "__main__":
    sys.exit(main())
