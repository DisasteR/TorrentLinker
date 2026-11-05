"""XXX stays hidden unless the user enables adult content; then it has its own place."""

import threading
from pathlib import Path

from torrentlinker.config import Settings
from torrentlinker.domain import ContentKind, LibraryFolder
from torrentlinker.downloads import IndexerCategory
from torrentlinker.torrent_search import parse_tree


def test_the_setting_is_off_by_default_and_survives_a_save(tmp_path: Path) -> None:
    assert not Settings().adult_content
    s = Settings(adult_content=True)
    s.save(tmp_path / "settings.json")
    assert Settings.load(tmp_path / "settings.json").adult_content


def test_xxx_categories_and_results_are_hidden_until_enabled() -> None:
    known = {
        2000: IndexerCategory("Films"),
        6000: IndexerCategory("XXX"),
        6010: IndexerCategory("XXX/DVD", 6000),
    }
    off, on = Settings(), Settings(adult_content=True)

    assert off.visible_categories(known) == [2000]
    assert on.visible_categories(known) == [2000, 6000, 6010]
    assert off.hides([6010]) and not off.hides([2000, 6010]) and not off.hides([])
    assert not on.hides([6010])


def test_the_native_search_tree_leaves_xxx_out_unless_asked() -> None:
    payload = {
        "data": [
            {"id": 1, "name": "Films", "subcategories": [{"id": 6, "name": "Films"}]},
            {"id": 8, "name": "XXX", "subcategories": [{"id": 42, "name": "Vidéos"}]},
        ]
    }
    assert set(parse_tree(payload, adult=False).categories) == {1, -6}
    assert set(parse_tree(payload).categories) == {1, -6, 8, -42}


def test_a_plan_never_takes_xxx_unless_enabled(tmp_path: Path) -> None:
    from torrentlinker.downloads import DownloadFolders
    from torrentlinker.plan import catalogue_candidates
    from torrentlinker.store import CatalogueRow

    rows = [
        CatalogueRow("a" * 40, "Film", 10, 5, 2000, "u", "d", 0.0),
        CatalogueRow("b" * 40, "Adult", 10, 5, 6010, "u", "d", 0.0),
    ]
    folders = DownloadFolders(default=str(tmp_path))
    known = {2000: IndexerCategory("Films"), 6010: IndexerCategory("XXX/DVD")}

    def titles(adult: bool) -> list[str]:
        found = catalogue_candidates(rows, folders, known, set(), lambda p: 0, adult=adult)
        return sorted(c.title for c in found)

    assert titles(False) == ["Film"]
    assert titles(True) == ["Adult", "Film"]


def test_xxx_folders_have_their_own_media_panel_only_once_enabled(tmp_path: Path) -> None:
    from torrentlinker.web.app import create_app

    films, adult = tmp_path / "films", tmp_path / "xxx"
    films.mkdir()
    adult.mkdir()
    (films / "The Revenant 2015 1080p.mkv").write_bytes(b"\0" * 64)
    (adult / "Private Scene 2019 1080p.mkv").write_bytes(b"\0" * 64)
    config = tmp_path / "config"
    config.mkdir()
    settings = Settings(adult_content=True)
    settings.library_folders = [
        LibraryFolder(films, None, ContentKind.MOVIE),
        LibraryFolder(adult, None, ContentKind.ADULT),
    ]
    settings.save(config / "settings.json")
    app = create_app(
        config, password="test-password-long", roots=(films, adult), monitor_verifications=False
    )
    media = app.state.assistant.agent.media
    media.scan("default", threading.Event(), lambda _p: None)

    def titles(**filters) -> list[str]:
        return [r["title"] for r in media.listing("default", kind="video", **filters)["rows"]]

    assert titles(view="all") == ["The Revenant"]
    assert titles(view="films") == ["The Revenant"]
    assert titles(view="recent", adult=True) == ["Private Scene"]
    rows = media.home_rows("default", scope="adult")["rows"]
    assert rows and all(r["filters"].get("adult") for r in rows)

    media.setup("default").settings.adult_content = False
    assert titles(view="recent", adult=True) == []
    assert media.home_rows("default", scope="adult")["rows"] == []
    assert titles(view="all") == ["The Revenant"]
