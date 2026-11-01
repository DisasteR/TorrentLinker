"""Find Local Items in a Library Folder without a scan depth (prototype).

See docs/research/library-item-detection.md. Each directory is either an item or a
container whose children are examined in turn. Season and show packs stay items; their
episodes are also items of their own, marked with the pack they belong to.
"""

from __future__ import annotations

import os
import re
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path

from torrentlinker.domain import ContentKind
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
EBOOK = {".epub", ".pdf", ".mobi", ".azw3", ".cbz", ".cbr", ".djvu", ".fb2"}
MODEL_3D = {".stl", ".3mf", ".obj", ".step", ".stp", ".blend", ".lys", ".chitubox", ".ply"}
MODEL_3D |= {".pwmx", ".pwma", ".pwms", ".ctb", ".goo", ".gcode", ".sl1", ".photon"}  # slices
GAME = {".iso", ".nsp", ".xci", ".exe", ".bin", ".cue", ".chd", ".pkg"}
ARCHIVES = {".rar", ".zip", ".7z"}  # unpacked before a Reseed (ADR-0007)
CONTENT = {
    ContentKind.MOVIE: VIDEO,
    ContentKind.SERIES: VIDEO,
    ContentKind.MUSIC: AUDIO,
    ContentKind.EBOOK: EBOOK,
    ContentKind.MODEL_3D: MODEL_3D,
    ContentKind.GAME: GAME,
}
SUPPORT = {".nfo", ".txt", ".jpg", ".jpeg", ".png", ".srt", ".sub", ".idx", ".ass", ".sfv"}
SUPPORT |= {".url", ".md", ".xml", ".diz", ".cue", ".log", ".m3u", ".m3u8", ".db", ".ini"}
VIDEO_KINDS = {ContentKind.MOVIE, ContentKind.SERIES, ContentKind.OTHER}
DISC_FOLDERS = {"BDMV", "VIDEO_TS", "AUDIO_TS"}
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
) -> list[DetectedItem]:
    """Local Items under `root`, which is always a container. Read-only."""
    detector = _Detector(kind, seeded_roots, catalogue_sizes, max_depth)
    detector.container(root, 0)
    return detector.found


def is_content(path: Path, kind: ContentKind) -> bool:
    suffix = path.suffix.lower()
    if suffix in ARCHIVES:
        return True
    if kind in CONTENT:
        return suffix in CONTENT[kind]
    return suffix not in SUPPORT  # OTHER: anything but support files


def _info(name: str) -> dict:
    return dict(_guess(name))


def _is_episode(path: Path, kind: ContentKind) -> bool:
    """Outside a Series folder, an explicit marker is required: GuessIt reads movie
    titles such as "Star.Wars.Episode.1", "Episode.50.2011" or a bare "1080" (S10E80)
    as episodes."""
    info = _info(path.name)
    if "episode" not in info or not (path.is_dir() or path.suffix.lower() in VIDEO):
        return False
    return kind is ContentKind.SERIES or bool(EPISODE_MARKER.search(path.name))


def _is_season(name: str) -> bool:
    info = _info(name)
    return "season" in info and "episode" not in info


def _is_release(name: str) -> bool:
    info = _info(name)
    return bool(info.get("title")) and bool(RELEASE_DETAILS & info.keys())


class _Detector:
    def __init__(
        self,
        kind: ContentKind,
        seeded_roots: frozenset[Path],
        catalogue_sizes: frozenset[int],
        max_depth: int,
    ) -> None:
        self.kind = kind
        self.seeded = seeded_roots
        self.above_seeded = {parent for root in seeded_roots for parent in root.parents}
        self.catalogue_sizes = catalogue_sizes
        self.max_depth = max_depth
        self.found: list[DetectedItem] = []
        self._sizes: dict[Path, int] = {}

    def container(self, directory: Path, depth: int) -> None:
        try:
            children = sorted(directory.iterdir())
        except OSError:
            return  # the scan reports unreadable folders; detection only skips them
        for child in children:
            if child.is_dir() and not child.is_symlink():
                self.directory(child, depth + 1)
            elif child.is_file() and (child in self.seeded or is_content(child, self.kind)):
                episode = self.kind in VIDEO_KINDS and _is_episode(child, self.kind)
                reason = Reason.EPISODE if episode else Reason.LOOSE_FILE
                if child in self.seeded:
                    reason = Reason.SEEDED
                self.found.append(DetectedItem(child, reason))

    def directory(self, directory: Path, depth: int) -> None:
        reason = self.item_reason(directory, depth)
        if reason is None:
            self.container(directory, depth)
            return
        self.found.append(DetectedItem(directory, reason))
        if (
            self.kind in VIDEO_KINDS
            and reason is not Reason.DISC
            and not _is_episode(directory, self.kind)
        ):
            self.episodes(directory)

    def item_reason(self, directory: Path, depth: int) -> Reason | None:
        if directory in self.seeded:
            return Reason.SEEDED
        if directory in self.above_seeded:
            return None
        try:
            children = list(directory.iterdir())
        except OSError:
            return None
        if any(child.name.upper() in DISC_FOLDERS for child in children):
            return Reason.DISC
        # A folder wrapping one subfolder has the same size; the inner name is the release.
        wrapper = len(children) == 1 and children[0].is_dir()
        if self.catalogue_sizes and not wrapper and self.size(directory) in self.catalogue_sizes:
            return Reason.CATALOGUE_SIZE
        if _is_season(directory.name):
            return Reason.SEASON
        content = [child for child in children if child.is_file() and is_content(child, self.kind)]
        if content and not self.distinct_releases(content):
            return Reason.CONTENT
        if self.kind is ContentKind.MODEL_3D and self.has_content_below(directory):
            return Reason.CONTENT  # a model's variants and parts stay one item
        if _is_release(directory.name):
            return Reason.RELEASE_NAME
        if depth >= self.max_depth:
            return Reason.DEPTH_LIMIT
        return None

    def distinct_releases(self, files: list[Path]) -> bool:
        """A genre folder of movies, not one movie with its sample or extras."""
        if self.kind not in VIDEO_KINDS:
            return False
        releases = {
            (_info(file.name).get("title"), _info(file.name).get("year"))
            for file in files
            if not _is_episode(file, self.kind) and _is_release(file.name)
        }
        return len(releases) >= 2

    def has_content_below(self, directory: Path) -> bool:
        for _dirpath, _dirs, names in os.walk(directory):
            if any(is_content(Path(name), self.kind) for name in names):
                return True
        return False

    def episodes(self, pack: Path) -> None:
        """Each episode of a pack, file or release folder, is also an item; so are the
        season folders of a complete series, and their episodes."""
        try:
            children = sorted(pack.iterdir())
        except OSError:
            return
        for child in children:
            if _is_episode(child, self.kind):
                self.found.append(DetectedItem(child, Reason.EPISODE, within=pack))
            elif child.is_dir() and _is_season(child.name):
                self.found.append(DetectedItem(child, Reason.SEASON, within=pack))
                self.episodes(child)

    def size(self, directory: Path) -> int:
        if directory not in self._sizes:
            total = 0
            for dirpath, _dirs, names in os.walk(directory):
                for name in names:
                    try:
                        total += (Path(dirpath) / name).stat().st_size
                    except OSError:
                        pass
            self._sizes[directory] = total
        return self._sizes[directory]
