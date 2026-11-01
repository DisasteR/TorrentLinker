"""Library searches look in the local Catalogue before spending c411's search budget."""

from pathlib import Path

from tests.torrents import make_torrent
from torrentlinker.domain import LocalItem
from torrentlinker.indexer import API_KEY_PLACEHOLDER, Candidate
from torrentlinker.matching import IgnoreList, MatchRules
from torrentlinker.review import catalogue_candidates, find_candidates
from torrentlinker.store import CatalogueRow, Store

RULES = MatchRules(0.02, 0.5, IgnoreList.default())


class Indexer:
    """Serves real .torrent files for known info hashes; records every fetch."""

    def __init__(self, torrents: dict[str, bytes]) -> None:
        self.torrents = torrents
        self.fetched: list[str] = []

    def fetch_torrent(self, candidate: Candidate) -> bytes:
        self.fetched.append(candidate.title)
        return self.torrents[candidate.infohash or ""]

    def unredact(self, text: str) -> str:
        return text.replace(API_KEY_PLACEHOLDER, "K")


def book(tmp_path: Path, name: str = "Le.Tigre.epub", size: int = 2000) -> LocalItem:
    path = tmp_path / "Livres" / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b"x" * size)
    return LocalItem(path, {name: size})


def row(infohash: str, title: str, size: int, seeders: int = 1) -> CatalogueRow:
    url = f"https://c411.org/api?t=get&id={infohash}&apikey={API_KEY_PLACEHOLDER}"
    return CatalogueRow(infohash, title, size, seeders, 7010, url, "https://c411.org/t", 0)


def test_exact_size_catalogue_torrents_become_candidates_with_a_usable_link(
    tmp_path: Path,
) -> None:
    store = Store(tmp_path / "s.db")
    store.save_catalogue([row("a" * 40, "Tigre", 2000, 3), row("b" * 40, "Other", 2001)])

    found = catalogue_candidates(store, Indexer({}), book(tmp_path))  # type: ignore[arg-type]

    assert [(c.title, c.seeders) for c in found] == [("Tigre", 3)]
    assert found[0].download_url.endswith("apikey=K")
    assert found[0].metadata == {"discovery": "catalogue"}


def test_a_catalogue_full_match_spends_no_search(tmp_path: Path) -> None:
    item = book(tmp_path)
    store = Store(tmp_path / "s.db")
    store.save_catalogue([row("a" * 40, "Le.Tigre", 2000)])
    indexer = Indexer({"a" * 40: make_torrent(item.path)})

    def search() -> list[Candidate]:
        raise AssertionError("the Catalogue already found a Full Match")

    found, checked = find_candidates(store, indexer, item, search, RULES)  # type: ignore[arg-type]

    assert [c.title for c in found] == ["Le.Tigre"]
    assert checked[0].match is not None and checked[0].match.is_full


def test_without_a_full_match_the_search_runs_and_skips_catalogue_duplicates(
    tmp_path: Path,
) -> None:
    item = book(tmp_path)
    other = tmp_path / "other" / "Two.Volumes"  # same total size, different files
    other.mkdir(parents=True)
    for name in ("Tome.1.epub", "Tome.2.epub"):
        (other / name).write_bytes(b"y" * 1000)
    store = Store(tmp_path / "s.db")
    store.save_catalogue([row("c" * 40, "Same size, other book", 2000)])
    indexer = Indexer({"c" * 40: make_torrent(other), "a" * 40: make_torrent(item.path)})
    searched = [
        Candidate("Same size, other book", 2000, "u", infohash="c" * 40),
        Candidate("Le.Tigre", 2000, "u", infohash="a" * 40),
    ]

    found, checked = find_candidates(store, indexer, item, lambda: searched, RULES)  # type: ignore[arg-type]

    assert [c.title for c in found] == ["Same size, other book", "Le.Tigre"]
    assert indexer.fetched == ["Same size, other book", "Le.Tigre"]  # each fetched once
    assert checked[-1].match is not None and checked[-1].match.is_full


def test_a_typed_query_searches_without_the_catalogue(tmp_path: Path) -> None:
    item = book(tmp_path)
    store = Store(tmp_path / "s.db")
    store.save_catalogue([row("a" * 40, "Le.Tigre", 2000)])
    indexer = Indexer({"a" * 40: make_torrent(item.path)})

    found, _ = find_candidates(  # type: ignore[arg-type]
        store, indexer, item, lambda: [], RULES, use_catalogue=False
    )

    assert found == [] and indexer.fetched == []
