"""Reseed and Plan against a real rTorrent. Opt-in: point TL_RTORRENT_SANDBOX at a
throwaway rTorrent's SCGI port, and TL_RTORRENT_DATA at a folder it sees at the same path
(see scripts/rtorrent_sandbox.py). Every torrent added is removed at the end, data kept.
Library files are checked to keep their bytes, and no file is created beside them.
"""

import os
import shutil
import tempfile
from collections.abc import Iterator
from pathlib import Path

import pytest

from tests.test_qbit_integration import digest, library_with_ebook, scene_torrent, settle
from tests.torrents import make_torrent
from torrentlinker.domain import ItemState, LibraryFolder
from torrentlinker.matching import match
from torrentlinker.reconcile import classify
from torrentlinker.reseed import TAG, start_reseed
from torrentlinker.rtorrent import RtorrentApi, RtorrentError
from torrentlinker.scan import scan
from torrentlinker.store import Store
from torrentlinker.torrent_meta import read_torrent

HOST = os.environ.get("TL_RTORRENT_SANDBOX", "")
DATA = os.environ.get("TL_RTORRENT_DATA", "")
pytestmark = pytest.mark.skipif(
    not (HOST and DATA), reason="set TL_RTORRENT_SANDBOX and TL_RTORRENT_DATA"
)


@pytest.fixture
def rtorrent() -> Iterator[RtorrentApi]:
    api = RtorrentApi(HOST)
    before = {t.hash for t in api.torrents(with_files=False)}
    yield api
    for t in api.torrents(with_files=False):
        if t.hash not in before:
            api.remove(t.hash)


@pytest.fixture
def shared() -> Iterator[Path]:
    path = Path(tempfile.mkdtemp(dir=DATA))
    yield path
    shutil.rmtree(path, ignore_errors=True)


def test_the_connection_test_names_the_version(rtorrent: RtorrentApi) -> None:
    assert rtorrent.check().startswith("0.")


def test_full_match_with_the_same_names_is_verified_and_started(
    shared: Path, rtorrent: RtorrentApi
) -> None:
    lib = library_with_ebook(shared)
    [item] = scan(LibraryFolder(lib))
    before = digest(item.path)
    meta = read_torrent(make_torrent(item.path))
    store = Store(shared / "s.db")

    h = start_reseed(rtorrent, store, item, meta, match(item, meta.files), meta.infohash)  # type: ignore[arg-type]
    [outcome] = settle(rtorrent, store)  # type: ignore[arg-type]

    assert outcome.verified and outcome.started
    t = rtorrent.torrent(h)
    assert t is not None and t.tags == {TAG} and t.files == {item.path.name: item.size}
    assert classify([item], rtorrent.torrents()) == {item.path: ItemState.SEEDED}
    assert digest(item.path) == before


def test_a_folder_is_reseeded_into_its_own_parent(shared: Path, rtorrent: RtorrentApi) -> None:
    lib = shared / "lib"
    (lib / "Zweig").mkdir(parents=True)
    (lib / "Zweig" / "a.epub").write_bytes(os.urandom(40000))
    (lib / "Zweig" / "b.epub").write_bytes(os.urandom(50000))
    [item] = scan(LibraryFolder(lib))
    meta = read_torrent(make_torrent(lib / "Zweig"))
    store = Store(shared / "s.db")

    h = start_reseed(rtorrent, store, item, meta, match(item, meta.files), meta.infohash)  # type: ignore[arg-type]
    [outcome] = settle(rtorrent, store)  # type: ignore[arg-type]

    assert outcome.verified and outcome.started
    t = rtorrent.torrent(h)
    assert t is not None and t.save_path == lib
    assert set(t.files) == {"Zweig/a.epub", "Zweig/b.epub"}


def test_a_renamed_file_is_renamed_on_disk_once_approved(
    shared: Path, rtorrent: RtorrentApi
) -> None:
    lib = library_with_ebook(shared)
    [item] = scan(LibraryFolder(lib))
    before = digest(item.path)
    meta = read_torrent(scene_torrent(shared, item.path.read_bytes()))
    m = match(item, meta.files)
    assert m is not None and m.renames
    store = Store(shared / "s.db")

    start_reseed(rtorrent, store, item, meta, m, meta.infohash, approve_disk_renames=lambda _: True)  # type: ignore[arg-type]
    [outcome] = settle(rtorrent, store)  # type: ignore[arg-type]

    assert outcome.verified and outcome.started and outcome.disk_renamed
    [name] = meta.files
    assert digest(lib / name) == before and not item.path.exists()


def test_a_partial_match_is_refused_before_anything_is_added(
    shared: Path, rtorrent: RtorrentApi
) -> None:
    pack = shared / "scene" / "Zweig.Pack"
    pack.mkdir(parents=True)
    (pack / "a.epub").write_bytes(os.urandom(40000))
    (pack / "b.epub").write_bytes(os.urandom(40000))
    meta = read_torrent(make_torrent(pack))
    lib = shared / "lib"
    (lib / "Zweig.Pack").mkdir(parents=True)
    shutil.copy2(pack / "a.epub", lib / "Zweig.Pack" / "a.epub")
    [item] = scan(LibraryFolder(lib))
    m = match(item, meta.files)
    assert m is not None and not m.is_full

    with pytest.raises(RtorrentError, match="Partial"):
        start_reseed(rtorrent, Store(shared / "s.db"), item, meta, m, meta.infohash)  # type: ignore[arg-type]

    assert rtorrent.torrent(meta.infohash) is None
    assert sorted(p.name for p in (lib / "Zweig.Pack").iterdir()) == ["a.epub"]


def test_same_size_but_different_data_fails_and_keeps_the_file(
    shared: Path, rtorrent: RtorrentApi
) -> None:
    lib = library_with_ebook(shared)
    [item] = scan(LibraryFolder(lib))
    before = digest(item.path)
    other = shared / "other" / item.path.name
    other.parent.mkdir()
    other.write_bytes(os.urandom(item.size))
    meta = read_torrent(make_torrent(other))
    store = Store(shared / "s.db")

    h = start_reseed(rtorrent, store, item, meta, match(item, meta.files), meta.infohash)  # type: ignore[arg-type]
    [outcome] = settle(rtorrent, store)  # type: ignore[arg-type]

    assert not outcome.verified
    assert rtorrent.torrent(h) is None
    assert digest(item.path) == before


def test_a_plan_torrent_downloads_into_its_download_folder_labelled_zen(
    shared: Path, rtorrent: RtorrentApi
) -> None:
    from torrentlinker.plan import PlanCandidate
    from torrentlinker.qbit import AlreadyInClient
    from torrentlinker.send import ZEN_TAG, send_one

    src = shared / "elsewhere" / "Zen.Release"
    src.mkdir(parents=True)
    (src / "a.bin").write_bytes(os.urandom(40000))
    data = make_torrent(src)
    meta = read_torrent(data)
    folder = shared / "dl"

    h = send_one(rtorrent, PlanCandidate(meta.infohash, meta.name, 40000, 1, 1, folder, 1), data)  # type: ignore[arg-type]

    t = rtorrent.torrent(h)
    assert t is not None and t.save_path == folder and ZEN_TAG in t.tags
    assert not t.state.startswith("stopped")
    with pytest.raises(AlreadyInClient):
        rtorrent.download(data, str(shared / "dl3"), [ZEN_TAG])
