"""Anime has its own Media panel: its series, their OAVs apart, and its films."""

import json
import threading
from pathlib import Path

from torrentlinker.media import anime_episode, is_anime_details


def test_anime_file_names_give_episode_numbers_and_tell_oavs_apart():
    assert anime_episode(Path("Cowboy.Bebop.Session.01.DVDRip.[x264].mkv")) == (1, False)
    assert anime_episode(Path("oki-tenjo.tenge.e24m.final.mkv")) == (24, False)
    assert anime_episode(Path("oki-tenjo.tenge.oav02m..mkv")) == (2, True)
    assert anime_episode(Path("[Grp] Naruto OVA 2 [1080p].mkv")) == (2, True)
    for film in ("Star.Wars.Episode.4.mkv", "Movie.2019.HDR10.EAC3.x265.mkv", "E.T.1982.mkv"):
        assert anime_episode(Path(film)) is None


def test_c411_details_call_japanese_animation_anime():
    assert is_anime_details({"genres": ["Animation"], "countries": ["Japan"]})
    assert is_anime_details({"genres": ["Horreur"], "keywords": ["anime"]})
    assert not is_anime_details(
        {"genres": ["Animation"], "countries": ["United States of America"]}
    )


def test_anime_panel_holds_series_oavs_and_films_that_films_and_series_leave_out(tmp_path):
    from torrentlinker.config import Settings
    from torrentlinker.domain import LibraryFolder
    from torrentlinker.web.app import create_app

    root = tmp_path / "videos"
    anime = root / "Anime"
    (anime / "Tenjou.Tenge").mkdir(parents=True)
    names = [
        "Anime/Tenjou.Tenge/oki-tenjo.tenge.e01m..mkv",
        "Anime/Tenjou.Tenge/oki-tenjo.tenge.e02m..mkv",
        "Anime/Tenjou.Tenge/oki-tenjo.tenge.oav01m..mkv",
        "Anime/Akira.1988.1080p.mkv",
        "Show.S01E01.mkv",
        "The Revenant 2015 VOSTFR 1080 Bluray.mkv",
    ]
    for name in names:
        (root / name).write_bytes(b"\0" * 64)
    config = tmp_path / "config"
    config.mkdir()
    settings = Settings()
    settings.library_folders = [LibraryFolder(root)]
    settings.save(config / "settings.json")
    app = create_app(
        config, password="test-password-long", roots=(root,), monitor_verifications=False
    )
    media = app.state.assistant.agent.media
    media.scan("default", threading.Event(), lambda _p: None)

    def titles(**filters):
        return sorted(r["title"] for r in media.listing("default", kind="video", **filters)["rows"])

    assert titles(view="series", anime=True) == ["tenjo tenge", "tenjo tenge (OAV)"]
    assert titles(view="series", anime=True, oav=True) == ["tenjo tenge (OAV)"]
    assert titles(view="films", anime=True) == ["Akira"]
    assert titles(view="films", anime=False) == ["The Revenant"]
    assert titles(view="series", anime=False) == ["Show"]
    rows = {r["key"]: r for r in media.home_rows("default", scope="anime")["rows"]}
    assert rows["oav"]["filters"] == {"view": "series", "anime": True, "oav": True}
    assert [r["title"] for r in rows["series"]["rows"]] == ["tenjo tenge"]
    assert [r["title"] for r in rows["films"]["rows"]] == ["Akira"]
    oav = rows["oav"]["rows"][0]
    episodes = media.detail("default", oav["id"])["episodes"]
    assert [(e["season"], e["episode"]) for e in episodes] == [(0, 1)]
    films = media.home_rows("default", scope="films")["rows"]
    assert "Akira" not in {r["title"] for row in films for r in row["rows"]}


def test_c411_details_decide_over_the_anime_folder(tmp_path):
    from torrentlinker.config import Settings
    from torrentlinker.domain import LibraryFolder
    from torrentlinker.web.app import create_app

    root = tmp_path / "Anime"
    root.mkdir()
    (root / "Futurama.S01E01.mkv").write_bytes(b"\0" * 64)
    config = tmp_path / "config"
    config.mkdir()
    settings = Settings()
    settings.library_folders = [LibraryFolder(root)]
    settings.save(config / "settings.json")
    app = create_app(
        config, password="test-password-long", roots=(root,), monitor_verifications=False
    )
    media = app.state.assistant.agent.media
    media.scan("default", threading.Event(), lambda _p: None)
    assert media.listing("default", anime=True)["total"] == 1
    with media.db:
        row = media.db.execute("SELECT id, body FROM media_items").fetchone()
        body = json.loads(row["body"])
        body["release_details"] = "f" * 40
        media.db.execute(
            "INSERT INTO media_options VALUES (?,?)",
            (
                "c411-details:" + "f" * 40,
                json.dumps({"genres": ["Animation"], "countries": ["United States of America"]}),
            ),
        )
        media.db.execute("UPDATE media_items SET body=? WHERE id=?", (json.dumps(body), row["id"]))

    class Setup:
        def indexer(self):
            raise RuntimeError("no indexer")

    media.enrich("default", Setup(), threading.Event(), lambda _p: None)
    assert media.listing("default", anime=True)["total"] == 0
