"""The Catalogue shipped inside the app, encrypted, imported at the first refresh.

The release build seals the maintainers' Catalogue with a key of its own and writes
both into the package; neither is in the repository (ADR-0019). The encryption keeps
the titles and info hashes out of reach of bots that harvest public release files and
images: anyone who takes the app apart still finds the key, so it is not a secret from
people, only from crawlers.

    TLCAT1 | created_at (8 bytes) | rows (4 bytes) | nonce (12 bytes) | AES-GCM(xz(JSON))

The JSON is columnar, as Zen Lab's snapshot: `hash`, `title`, `size`, `seeders`,
`category` and `seen_at` lists of equal length.
"""

from __future__ import annotations

import json
import logging
import lzma
import os
import struct
import time
from collections.abc import Callable, Iterable
from pathlib import Path
from typing import Any

from torrentlinker.catalogue import CrawlProgress, Progress
from torrentlinker.catalogue_sync import HASH, download_url
from torrentlinker.store import CatalogueRow, FeedState, Store

BUNDLE = Path(__file__).parent / "assets" / "catalogue.tlcat"
BUNDLE_NAME = "bundled"
MAGIC = b"TLCAT1"
HEADER = struct.Struct(">6sdI")
NONCE_BYTES = 12
MAX_PAYLOAD_BYTES = 256 * 1024 * 1024
log = logging.getLogger(__name__)


def bundled_key() -> bytes | None:
    """The key the release build generated, or None in a build without a Catalogue."""
    try:
        from torrentlinker._catalogue_key import KEY  # type: ignore[import-not-found]
    except ImportError:
        return None
    return bytes(KEY)


def columns(rows: Iterable[CatalogueRow]) -> dict[str, list[Any]]:
    data: dict[str, list[Any]] = {
        k: [] for k in ("hash", "title", "size", "seeders", "category", "seen_at", "root")
    }
    for r in rows:
        data["hash"].append(r.infohash.lower())
        data["title"].append(r.title)
        data["size"].append(r.size)
        data["seeders"].append(r.seeders)
        data["category"].append(r.category)
        data["seen_at"].append(r.seen_at)
        data["root"].append(r.root)
    return data


def _count(value: Any) -> bool:
    return type(value) is int and 0 <= value <= 2**63 - 1


def rows_from_columns(data: Any) -> list[CatalogueRow]:
    """Validate the whole Catalogue before any row is written."""
    names = ("hash", "title", "size", "seeders", "category", "seen_at")
    if not isinstance(data, dict) or not all(isinstance(data.get(k), list) for k in names):
        raise ValueError("Invalid bundled Catalogue")
    lists = [data[k] for k in names]
    # c411's own category arrived later: an older export has none.
    roots = data.get("root") or [None] * len(lists[0])
    if len({len(c) for c in [*lists, roots]}) != 1:
        raise ValueError("Incomplete bundled Catalogue")
    rows, seen = [], set()
    for h, title, size, seeders, category, seen_at, root in zip(*lists, roots, strict=True):
        if (
            not isinstance(h, str)
            or not HASH.fullmatch(h)
            or h.lower() in seen
            or not isinstance(title, str)
            or not title.strip()
            or not (_count(size) and _count(seeders))
            or not (category is None or _count(category))
            or not isinstance(seen_at, int | float)
            or not (root is None or _count(root))
        ):
            raise ValueError("Invalid or duplicate torrent in the bundled Catalogue")
        h = h.lower()
        seen.add(h)
        rows.append(
            CatalogueRow(
                h,
                title,
                size,
                seeders,
                category,
                download_url(h),
                f"https://c411.org/torrents/{h}",
                float(seen_at),
                BUNDLE_NAME,
                root,
            )
        )
    return rows


def seal(payload: bytes, key: bytes, created_at: float, count: int) -> bytes:
    """Encrypt an xz-compressed columnar Catalogue; the header is authenticated too."""
    from cryptography.hazmat.primitives.ciphers.aead import AESGCM

    header = HEADER.pack(MAGIC, created_at, count)
    nonce = os.urandom(NONCE_BYTES)
    return header + nonce + AESGCM(key).encrypt(nonce, payload, header)


def header(path: Path) -> tuple[float, int]:
    """When the bundled Catalogue was made, and how many torrents it holds."""
    with path.open("rb") as f:
        magic, created_at, count = HEADER.unpack(f.read(HEADER.size))
    if magic != MAGIC:
        raise ValueError("Not a bundled Catalogue")
    return created_at, count


def open_bundle(path: Path, key: bytes) -> list[CatalogueRow]:
    from cryptography.exceptions import InvalidTag
    from cryptography.hazmat.primitives.ciphers.aead import AESGCM

    data = path.read_bytes()
    head = data[: HEADER.size]
    nonce = data[HEADER.size : HEADER.size + NONCE_BYTES]
    try:
        payload = AESGCM(key).decrypt(nonce, data[HEADER.size + NONCE_BYTES :], head)
    except InvalidTag:
        raise ValueError("The bundled Catalogue does not match this build's key") from None
    decompressor = lzma.LZMADecompressor()
    text = decompressor.decompress(payload, MAX_PAYLOAD_BYTES)
    if not decompressor.eof:
        raise ValueError("The bundled Catalogue exceeded the size limit")
    rows = rows_from_columns(json.loads(text))
    if len(rows) != HEADER.unpack(head)[2]:
        raise ValueError("The bundled Catalogue does not hold the torrents it announces")
    return rows


def import_bundled(
    store: Store,
    *,
    path: Path | None = None,
    key: bytes | None = None,
    stop: Callable[[], bool] = lambda: False,
    progress: Progress = lambda p: None,
    clock: Callable[[], float] = time.time,
) -> bool:
    """Import the bundled Catalogue unless this one, or a newer one, already was.

    True when the app ships a Catalogue, imported now or before. Live c411 rows keep
    precedence over bundled ones (Store.save_catalogue)."""
    path = path or BUNDLE
    key = key if key is not None else bundled_key()
    if key is None or not path.is_file():
        return False
    created_at, _ = header(path)
    state = store.feed_state(BUNDLE_NAME)
    if state.started_at is not None and state.started_at >= created_at and state.updated_at:
        return True
    rows = open_bundle(path, key)
    progress(CrawlProgress(0, 0, 0, 0, BUNDLE_NAME))
    added = 0
    for offset in range(0, len(rows), 1000):
        if stop():
            return True
        batch = rows[offset : offset + 1000]
        added += len(batch) - len(store.known_hashes([r.infohash for r in batch]))
        store.save_catalogue(batch)
        progress(CrawlProgress(0, offset // 1000 + 1, added, offset + len(batch), BUNDLE_NAME))
    # started_at records which bundle was imported; updated_at when it finished.
    store.save_feed_state(
        FeedState(BUNDLE_NAME, updated_at=clock(), count=len(rows), started_at=created_at)
    )
    log.info("Imported the bundled Catalogue: %d torrents, %d new", len(rows), added)
    return True


def export(store: Store, out: Path, clock: Callable[[], float] = time.time) -> int:
    """Write the Store's Catalogue, xz-compressed but not encrypted, for the release build."""
    rows = store.catalogue()
    data = columns(rows)
    out.write_bytes(lzma.compress(json.dumps({"created_at": clock(), **data}).encode()))
    return len(rows)


def build(source: Path, bundle: Path, key_module: Path) -> int:
    """Seal an exported Catalogue with a new key: the release build's step."""
    from cryptography.hazmat.primitives.ciphers.aead import AESGCM

    data = json.loads(lzma.decompress(source.read_bytes()))
    created_at = float(data.pop("created_at"))
    rows = rows_from_columns(data)
    key = AESGCM.generate_key(bit_length=256)
    payload = lzma.compress(json.dumps(columns(rows)).encode())
    bundle.parent.mkdir(parents=True, exist_ok=True)
    bundle.write_bytes(seal(payload, key, created_at, len(rows)))
    key_module.write_text(
        '"""Generated by the release build; never committed."""\n\n'
        f"KEY = bytes.fromhex({key.hex()!r})\n"
    )
    return len(rows)
