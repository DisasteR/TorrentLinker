"""The Catalogue shipped inside the app: sealed by the release build, imported once."""

import lzma
from pathlib import Path

import httpx
import pytest

from tests.test_catalogue_sync import live_item, native_indexer
from torrentlinker import bundled_catalogue
from torrentlinker.bundled_catalogue import BUNDLE_NAME, build, export, import_bundled
from torrentlinker.catalogue_sync import sync_catalogue
from torrentlinker.store import CatalogueRow, Store


def row(i: int, title: str = "", seeders: int = 3, source: str = "indexer") -> CatalogueRow:
    h = f"{i:040x}"
    return CatalogueRow(h, title or f"Book {i}", 1000 + i, seeders, 7010, "u", "d", 5.0, source)


def bundle(tmp_path: Path, rows: list[CatalogueRow], when: float = 100.0) -> tuple[Path, bytes]:
    """Export `rows` as the maintainers would, then seal them as the release build does."""
    dev = Store(tmp_path / "dev" / "state.db")
    dev.save_catalogue(rows)
    export(dev, tmp_path / "catalogue.json.xz", clock=lambda: when)
    sealed, key_module = tmp_path / f"catalogue-{when}.tlcat", tmp_path / "key.py"
    build(tmp_path / "catalogue.json.xz", sealed, key_module)
    namespace: dict = {}
    exec(key_module.read_text(), namespace)
    return sealed, namespace["KEY"]


def test_a_sealed_catalogue_hides_its_torrents_and_imports_whole(tmp_path: Path) -> None:
    sealed, key = bundle(tmp_path, [row(1, "Le Joueur d'échecs"), row(2)])

    assert b"Joueur" not in sealed.read_bytes()
    assert f"{1:040x}".encode() not in sealed.read_bytes()
    store = Store(tmp_path / "s.db")
    assert import_bundled(store, path=sealed, key=key)
    [first] = store.catalogue([f"{1:040x}"])
    assert (first.title, first.source) == ("Le Joueur d'échecs", BUNDLE_NAME)
    assert first.download_url.startswith("https://c411.org/api?t=get&id=")  # never a key
    assert store.catalogue_size() == 2


def test_the_same_bundle_is_imported_once_and_a_newer_one_tops_up(tmp_path: Path) -> None:
    store = Store(tmp_path / "s.db")
    first, key = bundle(tmp_path, [row(1)], when=100.0)
    assert import_bundled(store, path=first, key=key)
    store.save_catalogue([row(1, "Renamed live", source=BUNDLE_NAME)])  # would be undone
    assert import_bundled(store, path=first, key=key)
    assert store.catalogue([f"{1:040x}"])[0].title == "Renamed live"

    newer, newer_key = bundle(tmp_path / "next", [row(1), row(2)], when=200.0)
    assert import_bundled(store, path=newer, key=newer_key)
    assert store.catalogue_size() == 2


def test_live_c411_rows_keep_precedence_over_the_bundle(tmp_path: Path) -> None:
    store = Store(tmp_path / "s.db")
    store.save_catalogue([row(1, "Live title", seeders=40)])
    sealed, key = bundle(tmp_path, [row(1, "Old title", seeders=1)])

    import_bundled(store, path=sealed, key=key)

    [kept] = store.catalogue([f"{1:040x}"])
    assert (kept.title, kept.seeders) == ("Live title", 40)


def test_a_wrong_key_or_a_tampered_bundle_is_refused_before_any_row(tmp_path: Path) -> None:
    sealed, key = bundle(tmp_path, [row(1)])
    store = Store(tmp_path / "s.db")

    with pytest.raises(ValueError, match="key"):
        import_bundled(store, path=sealed, key=bytes(32))
    data = bytearray(sealed.read_bytes())
    data[10] ^= 1  # the creation time, authenticated with the rows
    sealed.write_bytes(bytes(data))
    with pytest.raises(ValueError):
        import_bundled(store, path=sealed, key=key)
    assert store.catalogue_size() == 0


def test_a_build_without_a_bundle_reports_none(tmp_path: Path) -> None:
    assert not import_bundled(Store(tmp_path / "s.db"), path=tmp_path / "absent", key=bytes(32))
    assert not import_bundled(Store(tmp_path / "t.db"), path=tmp_path / "absent")


def test_an_export_holds_every_catalogue_row(tmp_path: Path) -> None:
    dev = Store(tmp_path / "s.db")
    dev.save_catalogue([row(i) for i in range(5)])

    assert export(dev, tmp_path / "out.json.xz") == 5
    assert b"Book 3" in lzma.decompress((tmp_path / "out.json.xz").read_bytes())


def test_the_first_sync_imports_the_bundle_instead_of_zen_lab(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    sealed, key = bundle(tmp_path, [row(1), row(2)])
    monkeypatch.setattr(bundled_catalogue, "BUNDLE", sealed)
    monkeypatch.setattr(bundled_catalogue, "bundled_key", lambda: key)

    def zen(request: httpx.Request) -> httpx.Response:
        raise AssertionError("Zen Lab is not needed when the app ships a Catalogue")

    ix = native_indexer(
        lambda r: httpx.Response(
            200,
            json={
                "data": [live_item(9)],
                "meta": {"page": 1, "perPage": 100, "total": 1, "totalPages": 1},
            },
        )
    )
    store = Store(tmp_path / "s.db")

    sync_catalogue(
        ix,
        store,
        [7000],
        bootstrap=True,
        snapshot_http=httpx.Client(transport=httpx.MockTransport(zen)),
    )

    assert store.catalogue_size() == 3  # two bundled, one live
    assert store.feed_state(BUNDLE_NAME).count == 2
