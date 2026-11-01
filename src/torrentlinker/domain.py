"""Domain types. Vocabulary follows CONTEXT.md."""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum
from pathlib import Path


class ContentKind(StrEnum):
    MOVIE = "movie"
    SERIES = "series"
    MUSIC = "music"
    EBOOK = "ebook"
    GAME = "game"
    MODEL_3D = "3d-model"
    OTHER = "other"

    @property
    def has_external_ids(self) -> bool:
        """Whether items of this kind can be identified by IMDb/TMDb ids."""
        return self in (ContentKind.MOVIE, ContentKind.SERIES)


class TorrentClient(StrEnum):
    """The program that seeds: Reseeds and Plans are added to it (ADR-0009)."""

    QBITTORRENT = "qbittorrent"
    DELUGE = "deluge"
    TRANSMISSION = "transmission"
    UTORRENT = "utorrent"
    BITTORRENT = "bittorrent"

    @property
    def display_name(self) -> str:
        return {
            "qbittorrent": "qBittorrent",
            "deluge": "Deluge",
            "transmission": "Transmission",
            "utorrent": "µTorrent Classic",
            "bittorrent": "BitTorrent Classic",
        }[self.value]


@dataclass(frozen=True)
class PathMapping:
    """Maps a path prefix as this app sees it to the same location as the Torrent Client
    sees it (`qbit`, named when qBittorrent was the only one).

    One list of them applies to every path exchanged with the Torrent Client: Library
    Folders, Download Folders and Reseeds alike (ADR-0006).
    """

    local: str
    qbit: str


@dataclass(frozen=True)
class LibraryFolder:
    path: Path
    depth: int | None = 1  # None: Automatic, Local Items are detected (item_detection)
    kind: ContentKind = ContentKind.OTHER


@dataclass(frozen=True)
class LocalItem:
    """One entry of a Library Folder at its scan depth, or as detected in Automatic mode.

    `files` maps each file's path, relative to the item's parent folder and using
    forward slashes (the convention qBittorrent uses under a save path), to its size.
    """

    path: Path
    files: dict[str, int] = field(default_factory=dict)
    search_name: str = ""  # identity for a virtual combined manifest; never a disk path
    within: Path | None = None  # the season or show pack an episode also belongs to

    @property
    def size(self) -> int:
        return sum(self.files.values())

    @property
    def save_path(self) -> Path:
        return self.path.parent


class ItemState(StrEnum):
    UNKNOWN = "unknown"
    SEEDED = "seeded"
    PARTIALLY_SEEDED = "partially-seeded"
    ORPHAN = "orphan"


@dataclass(frozen=True)
class Torrent:
    """A torrent in the Torrent Client, with paths in this app's view (after Path Mapping).

    `files` maps each file's path relative to `save_path` to its size.
    """

    hash: str
    name: str
    save_path: Path
    files: dict[str, int] = field(default_factory=dict)
    tags: frozenset[str] = frozenset()
    progress: float = 1.0
    state: str = ""
    tracker: str = ""  # the current tracker's URL
    swarm_seeders: int = -1  # tracker-reported seeders, user included; -1 = not yet known
    seeding_time: float = 0.0  # seconds this client has seeded it
    size: int = 0  # total size, also known when files were not fetched

    trackers: tuple[str, ...] = ()  # all announce URLs, never displayed with credentials
    category: str = ""  # the client's category or label, separate from personal tags
    download_rate: int = 0
    upload_rate: int = 0
    ratio: float = 0.0
    eta: int = -1  # seconds, negative means unknown
    connected_seeds: int = 0
    connected_peers: int = 0
    swarm_peers: int = -1  # tracker-reported incomplete peers; -1 = not yet known

    @property
    def total_size(self) -> int:
        return self.size or sum(self.files.values())

    @property
    def is_seeding(self) -> bool:
        return self.progress >= 1.0 and self.state in SEEDING_STATES


SEEDING_STATES = {"uploading", "stalledUP", "forcedUP", "queuedUP", "checkingUP"}


@dataclass(frozen=True)
class TorrentFile:
    index: int
    name: str
    size: int
    wanted: bool = True
