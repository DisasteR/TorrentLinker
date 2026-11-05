"""Seal an exported Catalogue into the package with a new key: a release build step.

    python scripts/bundle_catalogue.py catalogue.json.xz [--max-age-hours 48]

Writes src/torrentlinker/assets/catalogue.tlcat and src/torrentlinker/_catalogue_key.py,
both ignored by git (ADR-0019). Refuses an export older than --max-age-hours, so a
release never ships a stale Catalogue: export a fresh one first (export_catalogue.py).
"""

import argparse
import sys
import time
from pathlib import Path

from torrentlinker.bundled_catalogue import build, header

ROOT = Path(__file__).resolve().parent.parent / "src" / "torrentlinker"

if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("export", type=Path)
    parser.add_argument("--max-age-hours", type=float, default=None)
    args = parser.parse_args()
    bundle = ROOT / "assets" / "catalogue.tlcat"
    count = build(args.export, bundle, ROOT / "_catalogue_key.py")
    age = (time.time() - header(bundle)[0]) / 3600
    print(f"Bundled {count} torrents, exported {age:.1f} h ago")
    if args.max_age_hours is not None and age > args.max_age_hours:
        bundle.unlink()
        (ROOT / "_catalogue_key.py").unlink()
        sys.exit(
            f"The Catalogue export is {age:.0f} h old (limit {args.max_age_hours:.0f} h): "
            "run scripts/export_catalogue.py, then re-run this job"
        )
