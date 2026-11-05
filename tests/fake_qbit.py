"""In-memory qBittorrent implementing the Qbit port."""

from __future__ import annotations

from dataclasses import dataclass, field, replace
from pathlib import Path

from torrentlinker.domain import Torrent, TorrentFile
from torrentlinker.torrent_meta import read_torrent

CHECKING = "checkingUP"


@dataclass
class FakeQbit:
    supports_category_tags = True

    by_hash: dict[str, Torrent] = field(default_factory=dict)
    priorities: dict[str, dict[int, int]] = field(default_factory=dict)
    removed: list[tuple[str, bool]] = field(default_factory=list)
    started: list[str] = field(default_factory=list)
    rechecked: list[str] = field(default_factory=list)
    calls: list[str] = field(default_factory=list)
    pieces: dict[str, list[int]] = field(default_factory=dict)
    downloads: list[str] = field(default_factory=list)
    sequential: list[str] = field(default_factory=list)
    default_path: str = "/downloads"
    priorities_stick: bool = True  # False: the client never takes in a priority change

    def default_save_path(self) -> str:
        return self.default_path

    # Qbit port ---------------------------------------------------------------

    def torrents(self, tag: str | None = None, with_files: bool = True) -> list[Torrent]:
        return [
            t if with_files else replace(t, files={})
            for t in self.by_hash.values()
            if tag is None or tag in t.tags
        ]

    def torrent(self, hash: str, with_files: bool = True) -> Torrent | None:
        return self.by_hash.get(hash)

    def files(self, hash: str) -> dict[str, int]:
        return dict(self.by_hash[hash].files)

    def file_listing(self, hash: str) -> list[TorrentFile]:
        chosen = self.priorities.get(hash, {})
        return [
            TorrentFile(i, name, size, chosen.get(i, 1) > 0)
            for i, (name, size) in enumerate(self.files(hash).items())
        ]

    def add(self, data: bytes, save_path: str, tags: list[str]) -> str:
        meta = read_torrent(data)
        self.by_hash[meta.infohash] = Torrent(
            meta.infohash,
            meta.name,
            Path(save_path),
            dict(meta.files),
            frozenset(tags),
            0.0,
            "stoppedDL",
        )
        return meta.infohash

    def download(
        self, data: bytes, save_path: str, tags: list[str], *, sequential: bool = False
    ) -> str:
        h = self.add(data, save_path, tags)
        if sequential:
            self.sequential.append(h)
        self._set(h, state="downloading")
        self.downloads.append(h)
        return h

    def rename_file(self, hash: str, old: str, new: str) -> None:
        self.calls.append(f"rename {old} -> {new}")
        files = dict(self.by_hash[hash].files)
        files[new] = files.pop(old)
        self._set(hash, files=files)

    def set_file_priority(self, hash: str, indexes: list[int], priority: int) -> None:
        self.calls.append(f"priority {indexes} = {priority}")
        if not self.priorities_stick:
            return
        self.priorities.setdefault(hash, {}).update({i: priority for i in indexes})

    def recheck(self, hash: str) -> None:
        self.calls.append("recheck")
        self.rechecked.append(hash)
        self._set(hash, state=CHECKING)

    def pause(self, hash: str) -> None:
        self._set(hash, state="stoppedUP")

    def resume(self, hash: str) -> None:
        self.start(hash)

    def start(self, hash: str) -> None:
        self.started.append(hash)
        self._set(hash, state="uploading")

    def remove(self, hash: str, *, delete_files: bool = False) -> None:
        self.removed.append((hash, delete_files))
        del self.by_hash[hash]

    def add_tags(self, hash: str, tags: list[str]) -> None:
        t = self.by_hash[hash]
        self._set(hash, tags=t.tags | set(tags))

    def add_tags_many(self, hashes: list[str], tags: list[str]) -> None:
        for hash in hashes:
            self.add_tags(hash, tags)

    def remove_tags(self, hash: str, tags: list[str]) -> None:
        t = self.by_hash[hash]
        self._set(hash, tags=t.tags - set(tags))

    # Test controls -----------------------------------------------------------

    def piece_states(self, hash: str) -> list[int]:
        return self.pieces.get(hash, [])

    def finish_check(self, hash: str, progress: float, pieces: list[int] | None = None) -> None:
        if pieces is not None:
            self.pieces[hash] = pieces
        self._set(hash, progress=progress, state="stoppedUP" if progress == 1 else "stoppedDL")

    def _set(self, hash: str, **changes: object) -> None:
        self.by_hash[hash] = replace(self.by_hash[hash], **changes)  # type: ignore[arg-type]


@dataclass
class RacyQbit(FakeQbit):
    """A FakeQbit with the race seen on real qBittorrent (5.2.3 and 5.2.4).

    Right after an add the torrent settles for a few polls, and qBittorrent only learns
    of a rename one poll later. If the torrent settles, or a recheck starts, while
    qBittorrent still holds a stale path, it renames the file on disk back to that
    path, moving the user's file.
    """

    settle_polls: int = 2
    polls: dict[str, int] = field(default_factory=dict)
    known: dict[str, dict[str, str]] = field(default_factory=dict)  # current → qBit's path

    def add(self, data: bytes, save_path: str, tags: list[str]) -> str:
        h = super().add(data, save_path, tags)
        self._set(h, state="checkingResumeData")
        self.polls[h] = 0
        self.known[h] = {p: p for p in self.by_hash[h].files}
        return h

    def torrent(self, hash: str, with_files: bool = True) -> Torrent | None:
        t = self.by_hash.get(hash)
        if t is None:
            return None
        self.polls[hash] += 1
        if t.state == "checkingResumeData" and self.polls[hash] > self.settle_polls:
            self._reapply_stale_paths(hash)
            self._set(hash, state="stoppedDL")
        else:
            self.known[hash] = {p: p for p in self.by_hash[hash].files}  # renames sink in
        t = self.by_hash[hash]
        return replace(t, files={self.known[hash].get(p, p): s for p, s in t.files.items()})

    def rename_file(self, hash: str, old: str, new: str) -> None:
        super().rename_file(hash, old, new)
        self.known[hash][new] = self.known[hash].pop(old)

    def recheck(self, hash: str) -> None:
        self._reapply_stale_paths(hash)
        super().recheck(hash)

    def _reapply_stale_paths(self, hash: str) -> None:
        t = self.by_hash[hash]
        files = {}
        for current, size in t.files.items():
            stale = self.known[hash].get(current, current)
            if stale != current and (t.save_path / current).exists():
                (t.save_path / stale).parent.mkdir(parents=True, exist_ok=True)
                (t.save_path / current).rename(t.save_path / stale)
            files[stale] = size
        self._set(hash, files=files)
        self.known[hash] = {p: p for p in files}
