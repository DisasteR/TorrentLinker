"""Find Local Items in a Library Folder without a scan depth (prototype).

See docs/research/library-item-detection.md. Each directory is either an item or a
container whose children are examined in turn. Season and show packs stay items; their
episodes are also items of their own, marked with the pack they belong to.
"""

from __future__ import annotations

import os
import re
from collections.abc import Callable
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path, PurePosixPath

from torrentlinker.domain import ContentKind, Torrent
from torrentlinker.queries import _guess


class Reason(StrEnum):
    SEEDED = "seeded"  # a Torrent Client torrent's root
    DISC = "disc"  # BDMV, VIDEO_TS or AUDIO_TS inside
    CATALOGUE_SIZE = "catalogue-size"  # exactly the size of a Catalogue torrent
    SEASON = "season"  # a season pack: "Saison 01", "Show.S01.1080p-GRP"
    EPISODE = "episode"  # one episode, inside a pack or loose
    CONTENT = "content"  # directly holds files of the folder's Content Kind
    RELEASE_NAME = "release-name"  # a title with release details
    LOOSE_FILE = "loose-file"  # a content file between containers
    DEPTH_LIMIT = "depth-limit"  # stopped descending: kept whole


@dataclass(frozen=True)
class DetectedItem:
    path: Path
    reason: Reason
    within: Path | None = None  # the season or show pack that also holds this episode


VIDEO = {".mkv", ".m4v", ".mp4", ".avi", ".m2ts", ".ts", ".mov", ".webm", ".wmv", ".mpg"}
AUDIO = {".flac", ".mp3", ".m4a", ".ogg", ".opus", ".wav", ".ape", ".wv", ".aac", ".dsf", ".alac"}
EBOOK = {".epub", ".kepub", ".pdf", ".mobi", ".azw", ".azw3", ".cbz", ".cbr", ".djvu", ".fb2"}
EBOOK |= {".prc", ".lit", ".cb7"}
MODEL_3D = {".stl", ".3mf", ".obj", ".step", ".stp", ".blend", ".lys", ".chitubox", ".ply"}
MODEL_3D |= {".pwmx", ".pwma", ".pwms", ".ctb", ".goo", ".gcode", ".sl1", ".photon"}  # slices
GAME = {".iso", ".nsp", ".xci", ".exe", ".bin", ".cue", ".chd", ".pkg"}
ARCHIVES = {".rar", ".zip", ".7z"}  # unpacked before a Reseed (ADR-0007)
CONTENT = {
    ContentKind.MOVIE: VIDEO,
    ContentKind.SERIES: VIDEO,
    ContentKind.ADULT: VIDEO,
    ContentKind.MUSIC: AUDIO,
    ContentKind.EBOOK: EBOOK,
    ContentKind.MODEL_3D: MODEL_3D,
    ContentKind.GAME: GAME,
}
SUPPORT = {".nfo", ".txt", ".jpg", ".jpeg", ".png", ".srt", ".sub", ".idx", ".ass", ".sfv"}
SUPPORT |= {".url", ".md", ".xml", ".diz", ".cue", ".log", ".m3u", ".m3u8", ".db", ".ini"}
VIDEO_KINDS = {ContentKind.MOVIE, ContentKind.SERIES, ContentKind.ADULT, ContentKind.OTHER}
DISC_FOLDERS = {"BDMV", "VIDEO_TS", "AUDIO_TS"}
MULTI_DISC = re.compile(r"(?i)^(cd|dis[ck]|disque)[ ._-]?\d{1,2}$")  # CD1, Disc 2, Disque 1
# S01E02, S1.EP 01, 1x02
EPISODE_MARKER = re.compile(
    r"(?i)(?<![a-z0-9])s\d{1,2}[ ._-]?ep?[ ._-]?\d{1,3}(?!\d)|(?<!\d)\d{1,2}x\d{2,3}(?!\d)"
)
RELEASE_DETAILS = {"year", "screen_size", "source", "release_group", "video_codec", "season"}


def detect_items(
    root: Path,
    kind: ContentKind,
    *,
    seeded_roots: frozenset[Path] = frozenset(),
    catalogue_sizes: frozenset[int] = frozenset(),
    max_depth: int = 8,
    on_error: Callable[[Path, OSError], None] = lambda path, error: None,
) -> list[DetectedItem]:
    """Local Items under `root`, which is always a container. Read-only; folders that
    cannot be listed are reported to `on_error` and skipped."""
    detector = _Detector(kind, seeded_roots, catalogue_sizes, max_depth, on_error)
    detector.container(root, 0)
    return detector.found


def seeded_roots(torrents: list[Torrent]) -> frozenset[Path]:
    """Where each client torrent starts on disk: its folder, or its single file.
    Paths must already be in this app's view (after Path Mapping)."""
    return frozenset(
        torrent.save_path / PurePosixPath(rel).parts[0]
        for torrent in torrents
        for rel in torrent.files
    )


def is_content(path: Path, kind: ContentKind) -> bool:
    suffix = path.suffix.lower()
    if suffix in ARCHIVES:
        return True
    if kind in CONTENT:
        return suffix in CONTENT[kind]
    return suffix not in SUPPORT  # OTHER: anything but support files


def _info(name: str) -> dict:
    return dict(_guess(name))


# GuessIt costs milliseconds per name; only names with a season word are worth asking.
SEASON_HINT = re.compile(r"(?i)(?<![a-z])(s\d{1,2}(?!\d)|saisons?|seasons?|int[ée]grale)")


def _is_episode(name: str, is_dir: bool, kind: ContentKind) -> bool:
    """Outside a Series folder, an explicit marker is required: GuessIt reads movie
    titles such as "Star.Wars.Episode.1", "Episode.50.2011" or a bare "1080" (S10E80)
    as episodes."""
    if not (is_dir or Path(name).suffix.lower() in VIDEO):
        return False
    if EPISODE_MARKER.search(name):
        return True
    return kind is ContentKind.SERIES and "episode" in _info(name)  # "E05 - Titre.mkv"


def _is_season(name: str) -> bool:
    if not SEASON_HINT.search(name):
        return False
    info = _info(name)
    return "season" in info and "episode" not in info


def _is_release(name: str) -> bool:
    info = _info(name)
    return bool(info.get("title")) and bool(RELEASE_DETAILS & info.keys())


@dataclass(frozen=True)
class _Entry:
    path: Path
    is_dir: bool  # a real directory, not a symlink to one
    is_file: bool


class _Detector:
    def __init__(
        self,
        kind: ContentKind,
        seeded_roots: frozenset[Path],
        catalogue_sizes: frozenset[int],
        max_depth: int,
        on_error: Callable[[Path, OSError], None] = lambda path, error: None,
    ) -> None:
        self.kind = kind
        self.on_error = on_error
        self.seeded = seeded_roots
        self.above_seeded = {parent for root in seeded_roots for parent in root.parents}
        self.catalogue_sizes = catalogue_sizes
        self.max_depth = max_depth
        self.found: list[DetectedItem] = []
        self._sizes: dict[Path, int] = {}
        self._listings: dict[Path, list[_Entry]] = {}

    def listing(self, directory: Path) -> list[_Entry]:
        """Visible children, listed once. Directory entries carry their type, so a
        network share answers with one listing instead of a stat per child."""
        if directory not in self._listings:
            entries = []
            try:
                with os.scandir(directory) as scanned:
                    for entry in scanned:
                        if entry.name.startswith("."):
                            continue  # hidden, such as qBittorrent's .parts folders
                        try:
                            is_dir = entry.is_dir(follow_symlinks=False)
                            is_file = not is_dir and entry.is_file()
                        except OSError:
                            continue
                        entries.append(_Entry(Path(entry.path), is_dir, is_file))
            except OSError as error:
                self.on_error(directory, error)
            self._listings[directory] = sorted(entries, key=lambda e: e.path)
        return self._listings[directory]

    def container(self, directory: Path, depth: int) -> None:
        for child in self.listing(directory):
            if child.is_dir:
                self.directory(child.path, depth + 1)
            elif child.is_file and (child.path in self.seeded or self.is_content(child)):
                if child.path in self.seeded:
                    reason = Reason.SEEDED
                elif self.kind in VIDEO_KINDS and _is_episode(child.path.name, False, self.kind):
                    reason = Reason.EPISODE
                else:
                    reason = Reason.LOOSE_FILE
                self.found.append(DetectedItem(child.path, reason))

    def directory(self, directory: Path, depth: int) -> None:
        reason = self.item_reason(directory, depth)
        if reason is None:
            self.container(directory, depth)
            return
        self.found.append(DetectedItem(directory, reason))
        if (
            self.kind in VIDEO_KINDS
            and reason is not Reason.DISC
            and not _is_episode(directory.name, True, self.kind)
        ):
            self.episodes(directory)

    def is_content(self, entry: _Entry) -> bool:
        return entry.is_file and is_content(entry.path, self.kind)

    def item_reason(self, directory: Path, depth: int) -> Reason | None:
        if directory in self.seeded:
            return Reason.SEEDED
        if directory in self.above_seeded:
            return None
        children = self.listing(directory)
        if any(
            child.path.name.upper() in DISC_FOLDERS
            or (child.is_dir and MULTI_DISC.match(child.path.name))
            for child in children
        ):
            return Reason.DISC
        # A folder wrapping one subfolder has the same size; the inner name is the release.
        wrapper = len(children) == 1 and children[0].is_dir
        if self.catalogue_sizes and not wrapper and self.size(directory) in self.catalogue_sizes:
            return Reason.CATALOGUE_SIZE
        if _is_season(directory.name):
            return Reason.SEASON
        content = [child.path for child in children if self.is_content(child)]
        if content and not self.distinct_releases(content):
            return Reason.CONTENT
        if self.kind is ContentKind.MODEL_3D and self.has_content_below(directory):
            return Reason.CONTENT  # a model's variants and parts stay one item
        # "Les 100/The.100.S01.FRENCH…, The.100.S02…" groups releases; a complete series
        # whose seasons are plain "Saison 1" folders stays one pack.
        # A release name alone is not enough: "Sculpture (Images)" holds only previews.
        if (
            _is_release(directory.name)
            and not any(child.is_dir and _is_release(child.path.name) for child in children)
            and self.has_content_below(directory)
        ):
            return Reason.RELEASE_NAME
        if depth >= self.max_depth:
            return Reason.DEPTH_LIMIT
        return None

    def distinct_releases(self, files: list[Path]) -> bool:
        """A genre folder of movies, not one movie with its sample or extras."""
        if self.kind not in VIDEO_KINDS or len(files) < 2:
            return False
        releases = set()
        for file in files:  # GuessIt is slow: stop at the second release
            if not _is_episode(file.name, False, self.kind) and _is_release(file.name):
                info = _info(file.name)
                releases.add((info.get("title"), info.get("year")))
                if len(releases) >= 2:
                    return True
        return False

    def has_content_below(self, directory: Path) -> bool:
        children = self.listing(directory)
        return any(self.is_content(child) for child in children) or any(
            self.has_content_below(child.path) for child in children if child.is_dir
        )

    def episodes(self, pack: Path) -> None:
        """Each episode of a pack, file or release folder, is also an item; so are the
        season folders of a complete series, and their episodes."""
        for child in self.listing(pack):
            if _is_episode(child.path.name, child.is_dir, self.kind):
                self.found.append(DetectedItem(child.path, Reason.EPISODE, within=pack))
            elif child.is_dir and _is_season(child.path.name):
                self.found.append(DetectedItem(child.path, Reason.SEASON, within=pack))
                self.episodes(child.path)

    def size(self, directory: Path) -> int:
        if directory not in self._sizes:
            total = 0
            for child in self.listing(directory):
                if child.is_dir:
                    total += self.size(child.path)
                elif child.is_file:
                    try:
                        total += child.path.stat().st_size
                    except OSError:
                        pass
            self._sizes[directory] = total
        return self._sizes[directory]
