"""Plan against verified mounted destinations, using the existing selection engine."""

from __future__ import annotations

import threading
from collections.abc import Callable
from dataclasses import asdict
from typing import Any

import httpx

from torrentlinker.catalogue import crawl_categories, plan_categories
from torrentlinker.catalogue_sync import (
    SNAPSHOT_NAME,
    TORZNAB_ONLY_CATEGORIES,
    fetch_all,
    sync_catalogue,
)
from torrentlinker.domain import LibraryFolder
from torrentlinker.indexer import KeyRefused, RateLimited
from torrentlinker.plan import (
    Plan,
    PlanOptions,
    ReseedSource,
    catalogue_candidates,
    disk_of,
    disk_space,
    explain_inputs,
    make_plan,
    on_disk,
    reseed_candidates,
)
from torrentlinker.plan_diagnostics import PlanIssue, issue_text
from torrentlinker.scan import scan
from torrentlinker.seeding import seeding
from torrentlinker.web.library import Library
from torrentlinker.web.setup import Setup


def catalogue_status(setup: Setup) -> dict:
    store = setup.store
    snapshot = store.feed_state(SNAPSHOT_NAME)
    return {
        "count": store.catalogue_size(),
        "updated_at": store.catalogue_updated_at(),
        "limited_categories": sorted(store.limited_categories()),
        "paused": store.crawl_paused(),
        "snapshot_imported_at": snapshot.updated_at,
    }


def fetch_stopped(stop: threading.Event) -> bool:
    plan_now = getattr(stop, "plan_now", None)
    return stop.is_set() or (plan_now is not None and plan_now.is_set())


def refresh_for_plan(
    setup: Setup, stop: threading.Event, progress: Callable, chosen: list[int] | None
) -> str:
    """A failed refresh may use existing metadata, with an explicit provisional notice."""
    try:
        catalogue(setup, stop, progress, full=False, chosen=chosen)
    except (httpx.HTTPError, KeyRefused, RateLimited, ValueError):
        if not setup.store.catalogue_size():
            raise
        return "Catalogue refresh failed. This provisional Plan uses cached metadata."
    return ""


def catalogue(
    setup: Setup,
    stop: threading.Event,
    progress: Callable[[Any], None],
    *,
    full: bool = True,
    chosen: list[int] | None = None,
) -> dict:
    wanted = (
        crawl_categories(setup.settings.indexer_categories)
        if full
        else plan_categories(
            chosen, setup.settings.download_folders, setup.settings.indexer_categories
        )
    )
    indexer = setup.indexer()
    stopped = lambda: fetch_stopped(stop)  # noqa: E731
    report = lambda p: progress({"phase": "fetching", **asdict(p)})  # noqa: E731
    if full and indexer.is_c411:
        # Category by category, each to parity with the site; XXX only through Torznab.
        if not fetch_all(indexer, setup.store, stop=stopped, progress=report):
            return catalogue_status(setup)
        wanted = [c for c in wanted if c in TORZNAB_ONLY_CATEGORIES]
    sync_catalogue(
        indexer,
        setup.store,
        wanted,
        full=full,
        bootstrap=True,
        stop=stopped,
        progress=report,
        categories=setup.settings.indexer_categories,
    )
    return catalogue_status(setup)


def plan(
    setup: Setup,
    library: Library,
    maximum_bytes: int,
    maximum_torrents: int,
    stop: threading.Event,
    progress: Callable[[Any], None],
    *,
    cached_only: bool = False,
    category: int | None = None,
    categories: list[int] | None = None,
    options: PlanOptions | None = None,
    on_plan: Callable[[Plan], None] = lambda plan: None,
) -> dict:
    settings = setup.settings
    chosen = categories if categories is not None else [category] if category is not None else None
    if chosen is not None:
        relevant = plan_categories(chosen, settings.download_folders, settings.indexer_categories)
        destinations = (setup.download_folder(c) for c in relevant)
        folders = list(dict.fromkeys(path for path in destinations if path is not None))
        for folder in folders:
            setup.configure_download_folder(str(folder))
    else:
        configured = settings.download_folders
        paths = dict.fromkeys([configured.default, *configured.by_category.values()])
        folders = []
        for value in paths:
            if value:
                setup.configure_download_folder(value)
                folders.append(setup.path_under_root(value))
    for folder in folders:
        library.check_tree(folder, stop)
    notice = ""
    if not cached_only and not fetch_stopped(stop):
        notice = refresh_for_plan(setup, stop, progress, chosen)
    if stop.is_set():
        return {"cancelled": True, "catalogue": catalogue_status(setup)}
    progress({"phase": "planning"})
    torrents = setup.client().torrents(with_files=False)
    cached = {r.infohash: r for r in setup.store.catalogue()}
    current = seeding(settings.zen, torrents, settings.indexer.url, catalogue=cached)
    issues: list[PlanIssue] = []
    candidates = catalogue_candidates(
        cached.values(),
        settings.download_folders,
        settings.indexer_categories,
        {t.hash.lower() for t in torrents},
        disk_of,
        set(chosen) if chosen is not None else None,
        settings.plan_exclusions,
        issues=issues,
        adult=settings.adult_content,
    )
    existing = on_disk(candidates, [entry.item for entry in library.entries]) + [
        row
        for folder in folders
        for row in on_disk(candidates, scan(LibraryFolder(folder), setup.store).require_complete())
    ]
    existing = list({row.infohash: row for row in existing}.values())
    exclude = {r.infohash for r in existing}
    spaces = disk_space(folders, settings.free_space_margin)
    available = {space.disk: space.available for space in spaces}
    slots = max(0, settings.zen.torrent_cap - current.count) if settings.zen.torrent_cap else None
    options = options or PlanOptions(
        max_download_bytes=maximum_bytes, max_torrents=maximum_torrents
    )
    reseeds = reseed_candidates(
        settings.zen,
        library.planning_matches(stop),
        {t.hash.lower() for t in torrents},
        current.active,
        options=options,
    )
    reseed_hashes = {c.infohash for c in reseeds}
    proposed = make_plan(
        settings.zen,
        reseeds + [c for c in candidates if c.infohash not in exclude | reseed_hashes],
        available,
        current.active,
        slots,
        options=options,
        disk_spaces=spaces,
    )
    proposed = explain_inputs(proposed, issues, existing)
    on_plan(proposed)
    return {**plan_result(setup, proposed), "notice": notice}


def plan_result(setup: Setup, proposed: Plan) -> dict:
    """The desktop Plan's summary and rows, without serializing private torrent data."""
    return {
        "issues": [{**asdict(issue), "message": issue_text(issue)} for issue in proposed.issues],
        "provisional": True,
        "catalogue": catalogue_status(setup),
        "rows": [
            {
                "hash": i.candidate.infohash,
                "title": i.candidate.title,
                "size": i.candidate.size,
                "download_size": i.candidate.download_size,
                "reseed": i.candidate.reseed,
                "path": (
                    str(i.candidate.source.entry.item.path)
                    if isinstance(i.candidate.source, ReseedSource)
                    else ""
                ),
                "seeders": i.candidate.seeders,
                "zen": i.zen,
                "note": i.note,
                "folder": str(i.candidate.folder) if i.candidate.folder else "",
                "client_id": i.candidate.client_id or setup.settings.active_client_id,
            }
            for i in proposed.items
        ],
        "zen": proposed.zen_added,
        "size": proposed.download_size,
        "excluded": len(proposed.on_disk),
        "on_disk": [asdict(row) | {"path": str(row.path)} for row in proposed.on_disk],
        "average_note": proposed.average_note,
        "payback_days": proposed.payback_days,
        "sending_seconds": proposed.sending_seconds(max(1, setup.indexer_key_count())),
        "download_seconds": (
            proposed.download_seconds(setup.settings.download_speed)
            if setup.settings.download_speed
            else None
        ),
        "storage": plan_storage(proposed),
    }


def plan_storage(proposed: Plan) -> list[dict]:
    return [
        {
            "folder": str(space.folder),
            "free": space.free,
            "reserved": space.reserved,
            "download": space.download,
            "after_plan": space.after_plan,
        }
        for space in proposed.disk_spaces
    ]
