from pathlib import Path

from torrentlinker.domain import ContentKind
from torrentlinker.item_detection import DetectedItem, detect_items


def tree(root: Path, *files: str) -> Path:
    for name in files:
        path = root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b"x" * 10)
    return root


def found(root: Path, items: list[DetectedItem]) -> set[tuple[str, str, str]]:
    return {
        (
            item.path.relative_to(root).as_posix(),
            item.reason.value,
            item.within.relative_to(root).as_posix() if item.within else "",
        )
        for item in items
    }


def test_mixed_movie_layout_needs_no_depth(tmp_path: Path) -> None:
    root = tree(
        tmp_path / "Films",
        "Film.2019.mkv",
        "Film.2019.nfo",
        "Film.2020.MULTi.1080p-GRP/Film.2020.MULTi.1080p-GRP.mkv",
        "Collection Nolan/Inception.2010.1080p.BluRay-GRP/Inception.mkv",
        "Collection Nolan/Inception.2010.1080p.BluRay-GRP/Featurettes/Making of.mkv",
        "Collection Nolan/Tenet (2020)/Tenet.mkv",
        "4K/Dune.2021.2160p.UHD.BluRay-GRP/BDMV/index.bdmv",
        "Vieux film/film.avi",
    )

    assert found(root, detect_items(root, ContentKind.MOVIE)) == {
        ("Film.2019.mkv", "loose-file", ""),
        ("Film.2020.MULTi.1080p-GRP", "content", ""),
        ("Collection Nolan/Inception.2010.1080p.BluRay-GRP", "content", ""),
        ("Collection Nolan/Tenet (2020)", "content", ""),
        ("4K/Dune.2021.2160p.UHD.BluRay-GRP", "disc", ""),
        ("Vieux film", "content", ""),
    }


def test_season_packs_and_their_episodes_are_both_items(tmp_path: Path) -> None:
    root = tree(
        tmp_path / "Séries",
        "Breaking Bad/Saison 01/Breaking.Bad.S01E01.mkv",
        "Breaking Bad/Saison 01/Breaking.Bad.S01E02.mkv",
        "Breaking Bad/Saison 01/cover.jpg",
        "Breaking Bad/Season 2/E01 - Seven Thirty-Seven.mkv",
        "Show.S03.MULTi.1080p.WEB-GRP/Show.S03E01.1080p.WEB-GRP/Show.S03E01.mkv",
        "Show.S03.MULTi.1080p.WEB-GRP/Show.S03E02.1080p.WEB-GRP/Show.S03E02.mkv",
        "Loose.Show.S01E05.1080p.WEB-GRP.mkv",
    )

    assert found(root, detect_items(root, ContentKind.SERIES)) == {
        ("Breaking Bad/Saison 01", "season", ""),
        ("Breaking Bad/Saison 01/Breaking.Bad.S01E01.mkv", "episode", "Breaking Bad/Saison 01"),
        ("Breaking Bad/Saison 01/Breaking.Bad.S01E02.mkv", "episode", "Breaking Bad/Saison 01"),
        ("Breaking Bad/Season 2", "season", ""),
        ("Breaking Bad/Season 2/E01 - Seven Thirty-Seven.mkv", "episode", "Breaking Bad/Season 2"),
        ("Show.S03.MULTi.1080p.WEB-GRP", "season", ""),
        (
            "Show.S03.MULTi.1080p.WEB-GRP/Show.S03E01.1080p.WEB-GRP",
            "episode",
            "Show.S03.MULTi.1080p.WEB-GRP",
        ),
        (
            "Show.S03.MULTi.1080p.WEB-GRP/Show.S03E02.1080p.WEB-GRP",
            "episode",
            "Show.S03.MULTi.1080p.WEB-GRP",
        ),
        ("Loose.Show.S01E05.1080p.WEB-GRP.mkv", "episode", ""),
    }


def test_a_complete_series_keeps_its_seasons_and_episodes(tmp_path: Path) -> None:
    root = tree(
        tmp_path / "Séries",
        "Show.Integrale.MULTi.1080p.WEB-GRP/Saison 1/Show.S01E01.mkv",
        "Show.Integrale.MULTi.1080p.WEB-GRP/Saison 2/Show.S02E01.mkv",
    )
    pack = "Show.Integrale.MULTi.1080p.WEB-GRP"

    assert found(root, detect_items(root, ContentKind.SERIES)) == {
        (pack, "release-name", ""),
        (f"{pack}/Saison 1", "season", pack),
        (f"{pack}/Saison 1/Show.S01E01.mkv", "episode", f"{pack}/Saison 1"),
        (f"{pack}/Saison 2", "season", pack),
        (f"{pack}/Saison 2/Show.S02E01.mkv", "episode", f"{pack}/Saison 2"),
    }


def test_music_albums_inside_artist_folders(tmp_path: Path) -> None:
    root = tree(
        tmp_path / "Musique",
        "Daft Punk/Discovery (2001)/01 - One More Time.flac",
        "Daft Punk/Discovery (2001)/cover.jpg",
        "Daft Punk/Homework/01 - Daftendirekt.flac",
        "Single.mp3",
    )

    assert found(root, detect_items(root, ContentKind.MUSIC)) == {
        ("Daft Punk/Discovery (2001)", "content", ""),
        ("Daft Punk/Homework", "content", ""),
        ("Single.mp3", "loose-file", ""),
    }


def test_seeded_torrents_and_catalogue_sizes_decide_first(tmp_path: Path) -> None:
    root = tree(
        tmp_path / "Divers",
        "Pack de films/A/a.mkv",
        "Pack de films/B/b.mkv",
        "Archives/Logiciel/setup.part1.rar",
        "Archives/Logiciel/notes.txt",
    )
    seeded = frozenset({root / "Pack de films"})

    items = detect_items(
        root, ContentKind.OTHER, seeded_roots=seeded, catalogue_sizes=frozenset({20})
    )

    assert found(root, items) == {
        ("Pack de films", "seeded", ""),
        ("Archives/Logiciel", "catalogue-size", ""),
    }


def test_a_seeded_torrent_deeper_down_keeps_its_parents_as_containers(tmp_path: Path) -> None:
    root = tree(tmp_path / "Films", "Inception.2010.1080p-GRP/Extras/Bonus.2010.1080p-GRP/b.mkv")
    seeded = frozenset({root / "Inception.2010.1080p-GRP/Extras/Bonus.2010.1080p-GRP"})

    assert found(root, detect_items(root, ContentKind.MOVIE, seeded_roots=seeded)) == {
        ("Inception.2010.1080p-GRP/Extras/Bonus.2010.1080p-GRP", "seeded", ""),
    }


def test_unrecognised_folders_are_kept_whole_at_the_depth_limit(tmp_path: Path) -> None:
    root = tree(tmp_path / "Films", "a/b/c/readme.txt")

    assert found(root, detect_items(root, ContentKind.MOVIE, max_depth=2)) == {
        ("a/b", "depth-limit", ""),
    }


def test_genre_folders_of_loose_movies_are_containers(tmp_path: Path) -> None:
    root = tree(
        tmp_path / "Films",
        "Action-Thriller/Fury.2014.TRUEFRENCH.1080p.x264-GRP.mkv",
        "Action-Thriller/Glass.2019.TRUEFRENCH.1080p.HDLight.x264-GRP.mkv",
        "Action-Thriller/Episode.50.2011.French.720p.WebRip.x264.mkv",
        "Action-Thriller/Primer.2004.srt",
        "Glass.2019.1080p-GRP/Glass.2019.1080p-GRP.mkv",
        "Glass.2019.1080p-GRP/Glass.2019.1080p-GRP.sample.mkv",
    )

    assert found(root, detect_items(root, ContentKind.MOVIE)) == {
        ("Action-Thriller/Fury.2014.TRUEFRENCH.1080p.x264-GRP.mkv", "loose-file", ""),
        ("Action-Thriller/Glass.2019.TRUEFRENCH.1080p.HDLight.x264-GRP.mkv", "loose-file", ""),
        ("Action-Thriller/Episode.50.2011.French.720p.WebRip.x264.mkv", "loose-file", ""),
        ("Glass.2019.1080p-GRP", "content", ""),
    }


def test_a_3d_model_keeps_its_variant_folders(tmp_path: Path) -> None:
    root = tree(
        tmp_path / "STL",
        "B3dserk - Hellboy/Bust/Hellboy Bust (One Piece)/bust.stl",
        "B3dserk - Hellboy/Bust/Hellboy Bust (Images)/render.jpg",
        "B3dserk - Hellboy/Sculpture (X Pose)/sculpture.stl",
        "pwmx_files/Cloud.pwmx",
        "Images/preview.png",
    )

    assert found(root, detect_items(root, ContentKind.MODEL_3D)) == {
        ("B3dserk - Hellboy", "content", ""),
        ("pwmx_files", "content", ""),
    }
