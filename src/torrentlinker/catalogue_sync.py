"""Bootstrap c411 metadata from Zen Lab, then refresh it directly from c411.

The public snapshot never receives the user's key. It has coarse categories and
unknown upstream age; live c411 rows take precedence, and paging limits remain
visible even when a snapshot has been imported.
"""

from __future__ import annotations

import json
import logging
import re
import time
from collections.abc import Callable, Iterable, Mapping
from contextlib import nullcontext
from dataclasses import replace
from typing import Any, TypeGuard
from urllib.parse import urlencode

import httpx

from torrentlinker import net
from torrentlinker.catalogue import (
    PAGE,
    CrawlProgress,
    Progress,
    crawl_categories,
    update_categories,
)
from torrentlinker.downloads import IndexerCategory
from torrentlinker.indexer import API_KEY_PLACEHOLDER, Indexer
from torrentlinker.store import CatalogueRow, CrawlState, FeedState, Store

SNAPSHOT_URL = "https://zen.mma.dev/api/catalog"
SNAPSHOT_NAME = "zen-lab"
SNAPSHOT_MAX_AGE = 24 * 3600
# The whole Catalogue's newest torrents are fetched this often, while the app runs.
REFRESH_INTERVAL = 4 * 3600
REFRESH_FEED = "whole-catalogue"
MAX_SNAPSHOT_BYTES = 64 * 1024 * 1024
HASH = re.compile(r"[0-9a-fA-F]{40}\Z")
log = logging.getLogger(__name__)

# c411's internal category IDs translated to the Torznab IDs in its capabilities.
# Multiple native categories can share a Torznab ID. Keep all of them when filtering.
ROOTS = {1: 2000, 2: 7000, 3: 3000, 4: 4000, 5: 1000, 6: 1000, 7: 4050, 8: 6000, 9: 4000, 10: 8010}
SUBCATS = {
    1: {
        1: 2060,
        2: 5070,
        3: 2090,
        4: 2070,
        5: 5080,
        6: 2030,
        7: 5000,
        8: 2080,
        9: 5060,
        10: 2050,
        54: 2010,
        57: 2000,
    },
    2: {11: 3030, 12: 7030, 13: 7030, 14: 7020, 15: 7030, 16: 7010, 59: 7020, 60: 7020},
    3: {17: 3050, 18: 3010, 19: 3050, 20: 3050},
    4: {21: 4050, 22: 4040, 23: 4030, 24: 4030, 25: 4060, 26: 4060, 27: 4000},
    5: {
        28: 1090,
        29: 4050,
        30: 4050,
        31: 1040,
        32: 1030,
        33: 4070,
        34: 1080,
        35: 4070,
        36: 4050,
        55: 1000,
    },
    6: {37: 4050, 38: 1090},
    7: {39: 4060, 40: 4060, 41: 4050},
    8: {42: 6050, 43: 6010, 44: 6070, 45: 6060, 46: 6080, 56: 6000},
    9: {47: 4050, 48: 4060, 49: 4050, 50: 4050},
    10: {51: 8010, 52: 8010, 53: 8010},
}

# Verified 2026-10-25: c411's JSON tree/listing omits XXX (native root 8),
# while Torznab returns its torrents with the correct category IDs.
TORZNAB_ONLY_ROOTS = {8}
TORZNAB_ONLY_CATEGORIES = {ROOTS[r] for r in TORZNAB_ONLY_ROOTS} | {
    cat for r in TORZNAB_ONLY_ROOTS for cat in SUBCATS[r].values()
}


def download_url(infohash: str) -> str:
    """A direct c411 URL, with a placeholder until the Indexer fetches it."""
    return f"https://c411.org/api?t=get&id={infohash}&apikey={API_KEY_PLACEHOLDER}"


def _integer(value: Any) -> TypeGuard[int]:
    return type(value) is int and 0 <= value <= 2**63 - 1


def snapshot_rows(data: Any, now: float) -> list[CatalogueRow]:
    """Validate the entire columnar response before writing any rows."""
    if not isinstance(data, dict) or not _integer(data.get("stored")):
        raise ValueError("Invalid Zen Lab snapshot header")
    columns = [data.get(k) for k in ("hash", "title", "sizeBytes", "seeders", "category")]
    if any(not isinstance(c, list) or len(c) != data["stored"] for c in columns):
        raise ValueError("Incomplete Zen Lab snapshot")
    rows = []
    seen = set()
    for h, title, size, seeders, category in zip(*columns, strict=True):
        if (
            not isinstance(h, str)
            or not HASH.fullmatch(h)
            or not isinstance(title, str)
            or not title.strip()
            or not all(_integer(n) for n in (size, seeders, category))
            or h.lower() in seen
        ):
            raise ValueError("Invalid or duplicate Zen Lab torrent metadata")
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
                now,
                SNAPSHOT_NAME,
            )
        )
    return rows


def bootstrap_snapshot(
    store: Store,
    *,
    stop: Callable[[], bool] = lambda: False,
    progress: Progress = lambda p: None,
    http: httpx.Client | None = None,
    clock: Callable[[], float] = time.time,
) -> None:
    state = store.feed_state(SNAPSHOT_NAME)
    if stop() or (state.updated_at is not None and clock() - state.updated_at < SNAPSHOT_MAX_AGE):
        return
    progress(CrawlProgress(0, 0, 0, 0, SNAPSHOT_NAME))
    # A separate client is essential: no Indexer headers, cookies or API key.
    with (
        net.client(timeout=90, follow_redirects=False)
        if http is None
        else nullcontext(http) as client
    ):
        with client.stream("GET", SNAPSHOT_URL) as response:
            response.raise_for_status()
            content = bytearray()
            for chunk in response.iter_bytes():
                if stop():
                    return
                content.extend(chunk)
                if len(content) > MAX_SNAPSHOT_BYTES:
                    raise ValueError("Zen Lab snapshot exceeded the size limit")
    rows = snapshot_rows(json.loads(content), clock())
    added = 0
    for offset in range(0, len(rows), 1000):
        if stop():
            return
        batch = rows[offset : offset + 1000]
        added += len(batch) - len(store.known_hashes([r.infohash for r in batch]))
        store.save_catalogue(batch)
        store.save_feed_state(
            FeedState(SNAPSHOT_NAME, next_page=offset // 1000 + 2, count=offset + len(batch))
        )
        progress(CrawlProgress(0, offset // 1000 + 1, added, offset + len(batch), SNAPSHOT_NAME))
    store.save_feed_state(FeedState(SNAPSHOT_NAME, updated_at=clock(), count=len(rows)))


def _filters(wanted: set[int]) -> list[tuple[int, tuple[int, ...]]]:
    result: list[tuple[int, tuple[int, ...]]] = []
    for root, torznab in ROOTS.items():
        if root in TORZNAB_ONLY_ROOTS:
            continue
        if torznab in wanted:
            result.append((root, ()))
        else:
            children = tuple(s for s, c in SUBCATS[root].items() if c in wanted)
            if children:
                result.append((root, children))
    return result


def _native_row(item: dict[str, Any], now: float) -> CatalogueRow:
    h, title = item.get("infoHash"), item.get("name")
    size, seeds = item.get("size"), item.get("seeders")
    root = (item.get("category") or {}).get("id")
    subcat = (item.get("subcategory") or {}).get("id")
    if (
        not isinstance(h, str)
        or not HASH.fullmatch(h)
        or not isinstance(title, str)
        or not _integer(size)
        or not _integer(seeds)
        or root not in ROOTS
    ):
        raise ValueError("Invalid c411 catalogue metadata")
    category = SUBCATS[root].get(subcat, ROOTS[root]) if isinstance(subcat, int) else ROOTS[root]
    h = h.lower()
    return CatalogueRow(
        h,
        title,
        int(size),
        int(seeds),
        category,
        download_url(h),
        f"https://c411.org/torrents/{h}",
        now,
    )


def _refresh_filter(
    indexer: Indexer,
    store: Store,
    root: int,
    children: tuple[int, ...],
    *,
    stop: Callable[[], bool],
    progress: Progress,
    full: bool = True,
    max_pages: int | None = None,
) -> bool | None:
    """Read new live rows up to the last sync; split the initial historical fetch."""
    params = {
        "category": str(root),
        "viewMode": "flat",
        "perPage": str(PAGE),
        "sortBy": "createdAt",
        "sortOrder": "desc",
        "palliative": "include",
    }
    if children:
        params["subcat"] = ",".join(map(str, children))
    query = urlencode(params)
    key = ("c411-json:" if full else "c411-latest:") + query
    state = store.feed_state(key)
    if not full and state.updated_at is None and not state.head_hashes:
        previous = store.feed_state("c411-json:" + query)
        if previous.updated_at is not None:
            state = replace(state, head_hashes=previous.head_hashes)
    incremental = not full or state.updated_at is not None
    if not full and state.updated_at is None:
        # Startup may precede the first snapshot import. That import supplies the
        # initial historical baseline; rows fetched after it are not that baseline.
        baseline = max(
            state.started_at or time.time(), store.feed_state(SNAPSHOT_NAME).updated_at or 0
        )
        if baseline != state.started_at:
            state = replace(state, started_at=baseline)
            store.save_feed_state(state)
    # Restart an interrupted update from the newest page. Its last successful
    # checkpoint stays intact, so new arrivals during the pause cannot be skipped.
    page = 1 if incremental else state.next_page
    head = state.head_hashes
    seen_pages: set[frozenset[str]] = set()
    added = 0
    pages = 0
    category = ROOTS[root] if not children else SUBCATS[root].get(children[0], ROOTS[root])
    while not stop() and (max_pages is None or pages < max_pages):
        data = indexer.catalogue_json({**params, "page": str(page)})
        pages += 1
        meta = data.get("meta", {})
        items = data.get("data")
        if (
            not isinstance(items, list)
            or not all(_integer(meta.get(k)) for k in ("page", "perPage", "total", "totalPages"))
            or meta["perPage"] != PAGE
        ):
            raise ValueError("Invalid c411 catalogue pagination")
        if meta["page"] != page:
            store.save_feed_state(replace(state, next_page=1, updated_at=time.time(), limited=True))
            return True
        rows = [_native_row(item, time.time()) for item in items]
        hashes = frozenset(r.infohash for r in rows)
        if page == 1:
            head = tuple(r.infohash for r in rows)
            if not incremental:
                state = replace(state, head_hashes=head)
        known = store.known_hashes(list(hashes))
        boundary_known = (
            store.known_hashes(list(hashes), seen_before=state.started_at)
            if state.updated_at is None and state.started_at is not None
            else known
        )
        repeated = bool(rows) and hashes in seen_pages
        short = len(rows) < min(PAGE, max(0, meta["total"] - (page - 1) * PAGE))
        incomplete = short or len(hashes) != len(rows)
        seen_pages.add(hashes)
        store.save_catalogue(rows)
        added += len(hashes - known)
        progress(CrawlProgress(category, page, added, page * PAGE, "c411-json"))
        capped = meta["total"] > meta["totalPages"] * PAGE
        reached_checkpoint = incremental and (
            bool(hashes.intersection(state.head_hashes))
            # With no successful checkpoint, use the local snapshot as the baseline.
            # Planning never requires finishing the historical crawl first.
            if state.head_hashes
            else hashes <= boundary_known
        )
        if reached_checkpoint:
            store.save_feed_state(
                replace(
                    state,
                    next_page=1,
                    updated_at=time.time(),
                    limited=state.limited or repeated or incomplete,
                    count=meta["total"],
                    head_hashes=head,
                    started_at=None,
                )
            )
            return state.limited or repeated or incomplete
        # The API supplies native subcategories, including ones added since this
        # app's mapping was written. Fetch them too, falling back to the root label.
        splits = tuple(s["id"] for s in meta.get("subcategories", []) if _integer(s.get("id")))
        if (
            full
            and capped
            and (not incremental or page >= meta["totalPages"])
            and ((not children and splits) or len(children) > 1)
        ):
            # A category can contain rows without a subcategory. Do not certify
            # coverage merely because every listed child finished.
            counts = [s.get("count") for s in meta.get("subcategories", [])]
            limited = not children and (
                not all(_integer(n) for n in counts) or sum(counts) != meta["total"]
            )
            for sub in children or splits:
                if stop():
                    return None
                child_limited = _refresh_filter(
                    indexer, store, root, (sub,), stop=stop, progress=progress
                )
                if child_limited is None:
                    return None
                limited |= child_limited
            store.save_feed_state(
                replace(
                    state,
                    next_page=1,
                    updated_at=time.time(),
                    limited=limited,
                    count=meta["total"],
                    head_hashes=head,
                    started_at=None,
                )
            )
            return limited
        end = page >= meta["totalPages"] or not rows or repeated or incomplete
        if end:
            limited = repeated or capped or incomplete or (incremental and state.limited)
            store.save_feed_state(
                replace(
                    state,
                    next_page=1,
                    updated_at=time.time(),
                    limited=limited,
                    count=meta["total"],
                    head_hashes=head,
                    started_at=None,
                )
            )
            return limited
        page += 1
        state = replace(state, next_page=page)
        store.save_feed_state(state)
    return None


def sync_catalogue(
    indexer: Indexer,
    store: Store,
    wanted: Iterable[int],
    *,
    full: bool = False,
    bootstrap: bool = False,
    max_pages: int | None = None,
    stop: Callable[[], bool] = lambda: False,
    progress: Progress = lambda p: None,
    categories: Mapping[int, IndexerCategory] = {},
    snapshot_http: httpx.Client | None = None,
) -> None:
    """Refresh newest-first; only an explicit full fetch reads historical pages.

    Planning requests the one-time snapshot bootstrap, then uses the same latest-only
    refresh as startup. Its checkpoints are separate from resumable history cursors.
    """
    wanted = set(wanted)
    if not wanted:
        return
    if not indexer.is_c411:
        update_categories(
            indexer,
            store,
            sorted(wanted),
            full=full,
            max_pages=max_pages,
            stop=stop,
            progress=progress,
            categories=categories,
        )
        return
    snapshot_error = None
    from torrentlinker.bundled_catalogue import import_bundled

    try:
        # The Catalogue shipped with the app; Zen Lab only for a build without one.
        bundled = import_bundled(store, stop=stop, progress=progress)
    except (OSError, ValueError) as error:
        bundled = False
        log.warning("The bundled Catalogue could not be imported: %s", error)
    try:
        if (
            not bundled
            and (full or bootstrap)
            and store.feed_state(SNAPSHOT_NAME).updated_at is None
        ):
            bootstrap_snapshot(store, stop=stop, progress=progress, http=snapshot_http)
    except (httpx.HTTPError, ValueError) as error:
        snapshot_error = error
        log.warning("Zen Lab metadata unavailable: %s", error)
    filters = _filters(wanted)
    mapped = (set(ROOTS.values()) | {c for subs in SUBCATS.values() for c in subs.values()}) - (
        TORZNAB_ONLY_CATEGORIES
    )
    # A full parent fetch also supersedes older child crawl markers. Only include
    # a child when every native source for that Torznab category is covered:
    # some IDs combine subcategories from several different native roots.
    covered = dict(filters)
    tracked = wanted | {
        cat
        for cat in mapped
        if full
        and all(
            root in covered
            and (not covered[root] or bool(children) and set(children).issubset(covered[root]))
            for root, children in _filters({cat})
        )
    }
    if not full:
        for root, children in filters:
            if stop():
                return
            limited = _refresh_filter(
                indexer,
                store,
                root,
                children,
                stop=stop,
                progress=progress,
                full=False,
                max_pages=max_pages,
            )
            affected = wanted & ({ROOTS[root]} | set(SUBCATS[root].values()))
            for cat in affected:
                previous = store.crawl_state(cat)
                store.save_crawl_state(
                    replace(
                        previous,
                        updated_at=time.time(),
                        limited=previous.limited or bool(limited),
                    )
                )
    pending: dict[int, int] = {}
    limits: set[int] = set()
    history_filters = filters if full else []
    for root, _ in history_filters:
        for cat in tracked & ({ROOTS[root]} | set(SUBCATS[root].values())):
            pending[cat] = pending.get(cat, 0) + 1
    for cat in pending:
        store.save_crawl_state(replace(store.crawl_state(cat), complete=False, next_offset=1))
    for root, children in history_filters:
        if stop():
            return
        limited = _refresh_filter(indexer, store, root, children, stop=stop, progress=progress)
        affected = tracked & ({ROOTS[root]} | set(SUBCATS[root].values()))
        for cat in affected:
            pending[cat] -= 1
            if limited:
                limits.add(cat)
            paused = limited is None or stop() or pending[cat] > 0
            store.save_crawl_state(
                CrawlState(
                    cat,
                    next_offset=1 if paused else 0,
                    complete=cat not in limits and not paused,
                    updated_at=time.time(),
                    limited=cat in limits,
                )
            )
    if unsupported := wanted - mapped:
        for cat in sorted(unsupported & TORZNAB_ONLY_CATEGORIES):
            # Discard old JSON "complete but empty" markers once. Subsequent
            # Torznab crawls keep their own pagination checkpoints and limits.
            key = f"c411-torznab:{cat}"
            if store.feed_state(key).updated_at is None:
                store.save_crawl_state(CrawlState(cat))
                store.save_feed_state(FeedState(key, updated_at=time.time()))
        update_categories(
            indexer,
            store,
            sorted(unsupported),
            full=full,
            max_pages=max_pages,
            stop=stop,
            progress=progress,
            categories=categories,
        )
    if snapshot_error is not None and not stop():
        raise ValueError(
            "Zen Lab metadata unavailable; the available c411 pages were refreshed"
        ) from snapshot_error


def _capped_page(
    indexer: Indexer, root: int, sub: int | None, page: int, **extra: str
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    params = {
        "category": str(root),
        "viewMode": "flat",
        "perPage": str(PAGE),
        "sortBy": "createdAt",
        "sortOrder": "desc",
        "palliative": "include",
        "page": str(page),
        **extra,
    }
    if sub is not None:
        params["subcat"] = str(sub)
    data = indexer.catalogue_json(params)
    items, meta = data.get("data"), data.get("meta", {})
    if not isinstance(items, list) or not all(
        _integer(meta.get(k)) for k in ("page", "perPage", "total", "totalPages")
    ):
        raise ValueError("Invalid c411 catalogue pagination")
    return items, meta


FIRST_YEAR = 1900


def fill_capped(
    indexer: Indexer,
    store: Store,
    *,
    stop: Callable[[], bool] = lambda: False,
    progress: Progress = lambda p: None,
    this_year: int | None = None,
) -> None:
    """Read the subcategories too big for c411's listing, which returns at most 100
    pages per query: oldest-first to the same cap, then year by year (the work's
    release year), each year small enough to be read in full. Only torrents with no
    year that are neither among the newest nor the oldest stay out of reach (473 of
    63,974 films on 2026-11-05). Part of an explicit full crawl; resumable, each part
    keeping the next page to read."""
    if not indexer.is_c411:
        return
    last = this_year or time.gmtime().tm_year
    for root in ROOTS:
        if root in TORZNAB_ONLY_ROOTS or stop():
            continue
        _, meta = _capped_page(indexer, root, None, 1)
        if meta["total"] <= meta["totalPages"] * PAGE:
            continue
        capped = [
            s["id"]
            for s in meta.get("subcategories", [])
            if _integer(s.get("id")) and _integer(s.get("count")) and s["count"] > 100 * PAGE
        ]
        for sub in capped or [None]:
            parts: list[dict[str, str]] = [{"sortOrder": "asc"}]
            parts += [{"year": str(y)} for y in range(FIRST_YEAR, last + 2)]
            for part in parts:
                if stop() or not _read_part(indexer, store, root, sub, part, stop, progress):
                    return


def _read_part(
    indexer: Indexer,
    store: Store,
    root: int,
    sub: int | None,
    part: dict[str, str],
    stop: Callable[[], bool],
    progress: Progress,
) -> bool:
    """Every page of one part; False when stopped before its end."""
    key = f"c411-capped:{root}:{sub}:{urlencode(part)}"
    state = store.feed_state(key)
    page = state.next_page
    category = SUBCATS[root].get(sub, ROOTS[root]) if sub else ROOTS[root]
    while state.updated_at is None:
        if stop():
            return False
        items, meta = _capped_page(indexer, root, sub, page, **part)
        rows = [_native_row(item, time.time()) for item in items]
        known = store.known_hashes([r.infohash for r in rows])
        store.save_catalogue(rows)
        added = len({r.infohash for r in rows} - known)
        progress(CrawlProgress(category, page, added, page * PAGE, "c411-capped"))
        end = meta["page"] != page or page >= meta["totalPages"] or len(rows) < PAGE
        state = replace(
            state,
            next_page=page + 1,
            updated_at=time.time() if end else None,
            count=meta["total"],
        )
        store.save_feed_state(state)
        page += 1
    return True


def refresh_due(store: Store, now: float | None = None) -> bool:
    """Whether the last whole-Catalogue refresh is older than REFRESH_INTERVAL."""
    last = store.feed_state(REFRESH_FEED).updated_at
    return last is None or (time.time() if now is None else now) - last >= REFRESH_INTERVAL


def refresh_catalogue(
    indexer: Indexer,
    store: Store,
    categories: Mapping[int, IndexerCategory],
    *,
    stop: Callable[[], bool] = lambda: False,
    progress: Progress = lambda p: None,
    clock: Callable[[], float] = time.time,
) -> None:
    """Every category's newest torrents, down to what the Catalogue already holds: with
    the bundled Catalogue as the baseline, this keeps the whole of it current for a few
    requests per category. Older pages are only read by an explicit crawl. Only c411's
    listing stops at what is known; other Indexers are left to their own updates."""
    if not indexer.is_c411:
        return
    if not categories:
        with indexer.background():
            caps = indexer.caps()
        categories = {
            c: IndexerCategory(name, caps.parents.get(c)) for c, name in caps.categories.items()
        }
    with indexer.background():  # the user's own searches go first
        sync_catalogue(
            indexer,
            store,
            crawl_categories(categories),
            full=False,
            bootstrap=True,
            stop=stop,
            progress=progress,
            categories=categories,
        )
    if not stop():
        store.save_feed_state(FeedState(REFRESH_FEED, updated_at=clock()))
