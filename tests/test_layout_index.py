"""Cached .torrent files are found by the sizes of a Local Item's files, whatever their total."""

import os
from pathlib import Path

from tests.torrents import make_torrent
from torrentlinker.domain import LocalItem
from torrentlinker.indexer import API_KEY_PLACEHOLDER, Candidate
from torrentlinker.matching import IgnoreList, MatchRules
from torrentlinker.review import candidate_key, find_candidates, layout_candidates
from torrentlinker.store import CatalogueRow, Store
from torrentlinker.torrent_meta import read_torrent

RULES = MatchRules(0.02, 0.5, IgnoreList.default())
EPISODE = 300 * 1024


class Indexer:
    """Never fetches: everything here must come from the cache."""

    def __init__(self) -> None:
        self.kept: list[str] = []

    def fetch_torrent(self, candidate: Candidate) -> bytes:
        raise AssertionError(f"fetched {candidate.title}")

    def unredact(self, text: str) -> str:
        return text.replace(API_KEY_PLACEHOLDER, "K")

    def keep_torrent(self, candidate: Candidate, data: bytes) -> None:
        self.kept.append(candidate.title)


def season_pack(tmp_path: Path, episodes: int = 4) -> Path:
    pack = tmp_path / "scene" / "Show.S01"
    pack.mkdir(parents=True)
    for i in range(1, episodes + 1):
        (pack / f"Show.S01E0{i}.mkv").write_bytes(os.urandom(EPISODE + i))
    return pack


def local_season(tmp_path: Path, pack: Path, keep: int) -> LocalItem:
    """The first `keep` episodes, renamed as a media server would."""
    season = tmp_path / "Séries" / "Show" / "Saison 01"
    season.mkdir(parents=True)
    files = {}
    for i, episode in enumerate(sorted(pack.iterdir())[:keep], 1):
        name = f"Show - S01E0{i}.mkv"
        (season / name).write_bytes(episode.read_bytes())
        files[name] = episode.stat().st_size
    return LocalItem(season, files)


def row(infohash: str, title: str, size: int) -> CatalogueRow:
    url = f"https://c411.org/api?t=get&id={infohash}&apikey={API_KEY_PLACEHOLDER}"
    return CatalogueRow(infohash, title, size, 4, 2183, url, "https://c411.org/t", 0)


def cached(store: Store, data: bytes, title: str = "Show.S01", key: str = "k1") -> str:
    meta = read_torrent(data)
    store.cache_torrent(key, data)
    store.save_catalogue([row(meta.infohash, title, sum(meta.files.values()))])
    return meta.infohash


def test_a_cached_pack_missing_an_episode_locally_is_a_partial_match_without_a_search(
    tmp_path: Path,
) -> None:
    pack = season_pack(tmp_path)
    item = local_season(tmp_path, pack, keep=3)  # 75% of the pack: its total is far off
    store = Store(tmp_path / "s.db")
    infohash = cached(store, make_torrent(pack))
    indexer = Indexer()

    found, checked = find_candidates(store, indexer, item, lambda: [], RULES)  # type: ignore[arg-type]

    assert [c.infohash for c in found] == [infohash]
    [v] = checked
    assert v.candidate.metadata == {"discovery": "layout"}
    assert v.candidate.download_url.endswith("apikey=K")
    assert v.match is not None and not v.match.is_full and len(v.match.pairs) == 3
    assert indexer.kept == ["Show.S01"]  # restored later from the cache under its own key


def test_a_cached_single_episode_torrent_matches_inside_a_local_season(tmp_path: Path) -> None:
    pack = season_pack(tmp_path)
    item = local_season(tmp_path, pack, keep=4)
    store = Store(tmp_path / "s.db")
    infohash = cached(store, make_torrent(sorted(pack.iterdir())[1]), "Show.S01E02")

    [v] = layout_candidates(store, Indexer(), item, RULES)  # type: ignore[arg-type]

    assert v.candidate.infohash == infohash
    assert v.match is not None and v.match.is_full
    assert v.match.pairs == {"Show.S01E02.mkv": "Show - S01E02.mkv"}


def test_too_little_of_a_cached_torrent_is_not_offered(tmp_path: Path) -> None:
    pack = season_pack(tmp_path)
    item = local_season(tmp_path, pack, keep=1)  # 25% of the pack
    store = Store(tmp_path / "s.db")
    cached(store, make_torrent(pack))

    assert layout_candidates(store, Indexer(), item, RULES) == []  # type: ignore[arg-type]


def test_rejected_known_and_uncatalogued_torrents_are_not_offered(tmp_path: Path) -> None:
    pack = season_pack(tmp_path)
    item = local_season(tmp_path, pack, keep=3)
    store = Store(tmp_path / "s.db")
    data = make_torrent(pack)
    infohash = cached(store, data)
    [v] = layout_candidates(store, Indexer(), item, RULES)  # type: ignore[arg-type]

    rejected = {candidate_key(v.candidate): "Verification failed"}
    assert layout_candidates(store, Indexer(), item, RULES, rejected) == []  # type: ignore[arg-type]
    assert layout_candidates(store, Indexer(), item, RULES, known=frozenset({infohash})) == []  # type: ignore[arg-type]

    other = Store(tmp_path / "other" / "s.db")  # cached, but not in the Catalogue
    other.cache_torrent("k1", data)
    assert layout_candidates(other, Indexer(), item, RULES) == []  # type: ignore[arg-type]


def test_cached_torrents_are_indexed_once_and_unreadable_ones_are_skipped(
    tmp_path: Path,
) -> None:
    pack = season_pack(tmp_path)
    item = local_season(tmp_path, pack, keep=3)
    store = Store(tmp_path / "s.db")
    store.cache_torrent("broken", b"not a torrent")
    cached(store, make_torrent(pack))

    assert len(layout_candidates(store, Indexer(), item, RULES)) == 1  # type: ignore[arg-type]
    assert store.unindexed_torrent_keys() == []
    # A later download is picked up at the next lookup.
    store.cache_torrent("k2", make_torrent(sorted(pack.iterdir())[0]))
    assert store.unindexed_torrent_keys() == ["k2"]
