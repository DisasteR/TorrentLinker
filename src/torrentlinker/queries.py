"""Query building: turn a Local Item into Indexer search parameters."""

from __future__ import annotations

import re
from dataclasses import dataclass
from functools import lru_cache
from pathlib import PurePosixPath
from typing import Any

from guessit import guessit

from torrentlinker.domain import ContentKind, LocalItem
from torrentlinker.indexer import SearchMode, SearchQuery

MODE_BY_KIND = {
    ContentKind.MOVIE: SearchMode.MOVIE,
    ContentKind.SERIES: SearchMode.TV,
    ContentKind.MUSIC: SearchMode.MUSIC,
    ContentKind.EBOOK: SearchMode.BOOK,
}
IMDB_RE = re.compile(r"\b(tt\d{7,9})\b")
TMDB_RE = re.compile(r"tmdb(?:id)?[-=: ]?(\d+)", re.IGNORECASE)
NFO_MAX_BYTES = 256 * 1024


@dataclass(frozen=True)
class ExternalIds:
    imdb: str | None = None  # with its "tt" prefix
    tmdb: str | None = None


def is_single_file(item: LocalItem) -> bool:
    return list(item.files) == [item.path.name]


def name_query(item: LocalItem) -> str:
    name = item.path.stem if is_single_file(item) else item.path.name
    name = re.sub(r"\[[^\]]*\]|\{[^}]*\}|[()]", " ", name)
    name = re.sub(r"[._\-\s]+", " ", name)
    return name.strip()


def find_ids(item: LocalItem) -> ExternalIds:
    """IMDb/TMDb ids from the item's name (e.g. `{imdb-tt0211915}`) or its .nfo files."""
    texts = [item.path.name]
    for rel in item.files:
        if rel.lower().endswith(".nfo") and item.files[rel] <= NFO_MAX_BYTES:
            try:
                texts.append((item.save_path / rel).read_text(encoding="utf-8", errors="replace"))
            except OSError:
                pass
    imdb = tmdb = None
    for text in texts:
        if imdb is None and (m := IMDB_RE.search(text)):
            imdb = m.group(1)
        if tmdb is None and (m := TMDB_RE.search(text)):
            tmdb = m.group(1)
    return ExternalIds(imdb, tmdb)


def parse_name(item: LocalItem) -> dict[str, Any]:
    """Identity from the name, with episode evidence for unnamed season folders."""
    info = dict(_guess(item.path.name))
    if info.get("title") or "season" not in info or is_single_file(item):
        return info
    titles = {}
    has_episodes = False
    for name in item.files:
        episode = dict(_guess(PurePosixPath(name).name))
        title = episode.get("title")
        if "episode" in episode and str(episode.get("mimetype", "")).startswith("video/"):
            has_episodes = True
            if title:
                titles[str(title).casefold()] = title
    if len(titles) == 1:
        info["title"] = next(iter(titles.values()))
    elif not titles and has_episodes:
        parent = dict(_guess(item.path.parent.name))
        if parent.get("title") and "season" not in parent and "episode" not in parent:
            info["title"] = parent["title"]
    return info


@lru_cache(maxsize=4096)
def _guess(name: str) -> tuple[tuple[str, Any], ...]:
    name = re.sub(r"\{[^}]*\}", " ", name)  # Radarr/Sonarr id tags confuse guessit
    return tuple(dict(guessit(name)).items())


def build_queries(
    item: LocalItem,
    kind: ContentKind,
    categories: tuple[int, ...],
    ids: ExternalIds,
    caps_modes: dict[SearchMode, set[str]] | None,
    override: str | None = None,
) -> list[SearchQuery]:
    """Searches to try in order: by ID first when possible, then by name."""
    mode = MODE_BY_KIND.get(kind, SearchMode.SEARCH)

    def allows(m: SearchMode, param: str) -> bool:
        return caps_modes is None or param in caps_modes.get(m, set())

    if caps_modes is not None and mode not in caps_modes:
        mode = SearchMode.SEARCH

    season = episode = None
    title = name_query(item)
    if kind.has_external_ids:
        info = parse_name(item)
        title = str(info.get("title") or title)
        if kind is ContentKind.MOVIE and info.get("year"):
            title = f"{title} {info['year']}"
        if kind is ContentKind.SERIES:
            season = _single_int(info.get("season"))
            episode = _single_int(info.get("episode"))
    if override:
        title = override

    queries: list[SearchQuery] = []
    s_param = season if allows(mode, "season") else None
    e_param = episode if allows(mode, "ep") else None
    if not override and mode in (SearchMode.MOVIE, SearchMode.TV):
        imdb = ids.imdb.removeprefix("tt") if ids.imdb and allows(mode, "imdbid") else None
        tmdb = ids.tmdb if not imdb and ids.tmdb and allows(mode, "tmdbid") else None
        if imdb or tmdb:  # Torznab wants the IMDb id without "tt"
            queries.append(
                SearchQuery(
                    mode=mode,
                    categories=categories,
                    season=s_param,
                    ep=e_param,
                    imdbid=imdb,
                    tmdbid=tmdb,
                )
            )
    if mode is SearchMode.SEARCH:
        s_param = e_param = None
    queries.append(
        SearchQuery(q=title, mode=mode, categories=categories, season=s_param, ep=e_param)
    )
    return queries


def _single_int(value: Any) -> int | None:
    if isinstance(value, list):
        return None  # multi-season / multi-episode packs: search the whole show
    return int(value) if isinstance(value, int) else None


# Newznab standard category ranges; the Indexer's caps say which ids actually exist.
CATEGORY_RANGES: dict[ContentKind, list[range]] = {
    ContentKind.MOVIE: [range(2000, 3000)],
    ContentKind.SERIES: [range(5000, 6000)],
    ContentKind.MUSIC: [range(3000, 4000)],
    ContentKind.EBOOK: [range(7000, 8000)],
    ContentKind.GAME: [range(1000, 2000), range(4050, 4060)],
    ContentKind.MODEL_3D: [range(8000, 9000)],  # c411 files 3D printing as 8010 (Other/Misc)
    ContentKind.ADULT: [range(6000, 7000)],
}


def default_categories(caps_categories: dict[int, str]) -> dict[ContentKind, list[int]]:
    return {
        kind: sorted(c for c in caps_categories for r in CATEGORY_RANGES.get(kind, []) if c in r)
        for kind in ContentKind
    }
