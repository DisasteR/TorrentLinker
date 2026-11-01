"""Reconcile: tell which Local Items qBittorrent already seeds."""

from __future__ import annotations

from collections.abc import Iterable
from pathlib import Path, PurePosixPath

from torrentlinker.domain import ItemState, LocalItem, Torrent
from torrentlinker.matching import IgnoreList


def classify(
    items: Iterable[LocalItem], torrents: Iterable[Torrent], ignore: IgnoreList = IgnoreList()
) -> dict[Path, ItemState]:
    """An episode inside a pack (`LocalItem.within`) is Seeded only by torrents of its
    own: the pack's torrent does not make it Seeded, so it can be cross-seeded alone."""
    items, torrents = list(items), list(torrents)
    seeded_files = {t.save_path / rel for t in torrents for rel in t.files}
    episodes = {item.path for item in items if item.within}
    inside: dict[Path, set[Path]] = {}  # each episode: files of torrents rooted at or below it
    for t in torrents if episodes else ():
        roots = {t.save_path / PurePosixPath(rel).parts[0] for rel in t.files}
        if len(roots) != 1:
            continue
        root = roots.pop()
        for episode in episodes.intersection((root, *root.parents)):
            inside.setdefault(episode, set()).update(t.save_path / rel for rel in t.files)
    states: dict[Path, ItemState] = {}
    for item in items:
        own = seeded_files if item.within is None else inside.get(item.path, set())
        content = [rel for rel in item.files if not ignore.matches(rel)] or list(item.files)
        covered = sum(1 for rel in content if item.save_path / rel in own)
        if content and covered == len(content):
            states[item.path] = ItemState.SEEDED
        elif covered:
            states[item.path] = ItemState.PARTIALLY_SEEDED
        else:
            states[item.path] = ItemState.ORPHAN
    return states
