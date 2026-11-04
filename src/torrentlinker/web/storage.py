"""Last-moment path containment for sends from the server."""

from __future__ import annotations

from torrentlinker.paths import MappedQbit, PathMapper
from torrentlinker.send import Refused
from torrentlinker.torrent_meta import read_torrent
from torrentlinker.web.setup import Setup


class DownloadClient(MappedQbit):
    def __init__(self, setup: Setup):
        super().__init__(
            setup.client(),
            PathMapper(setup.settings.path_mappings),
            sequential_video=setup.settings.sequential_video,
        )
        self.setup = setup

    def download(
        self, data: bytes, save_path: str, tags: list[str], *, sequential: bool = False
    ) -> str:
        if self.setup.download_guard is not None:
            try:
                self.setup.download_guard(data, save_path)
            except (ValueError, PermissionError) as error:
                raise Refused(str(error)) from error
        root = self.setup.path_under_root(save_path)
        for relative in read_torrent(data).files:
            target = (root / relative).resolve()
            if not target.is_relative_to(root):
                raise Refused("A torrent path leaves the verified Download Folder.")
            current = root / relative
            while current != root:
                if current.is_symlink():
                    raise Refused("A torrent path crosses a symbolic link.")
                current = current.parent
        return super().download(data, str(root), tags, sequential=sequential)
