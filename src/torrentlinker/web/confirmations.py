"""Short-lived, single-use consent bound to an operation and client configuration."""

from __future__ import annotations

import secrets
import time
from typing import Any

from torrentlinker.client_limits import delete_refusal
from torrentlinker.web.operations import JobRequest
from torrentlinker.web.setup import Setup


class Confirmations:
    def __init__(self, setup: Setup):
        self.setup = setup
        self.pending: dict[str, tuple[float, str]] = {}

    def identity(self, body: JobRequest) -> str:
        return str(self.setup.snapshot()) + body.model_dump_json(exclude={"confirmation"})

    def preview(self, body: JobRequest) -> dict[str, Any]:
        if body.action == "delete" and (refusal := delete_refusal(self.setup.client())):
            raise ValueError(refusal)
        rows = []
        for hash in body.hashes:
            torrent = self.setup.client().torrent(hash, with_files=False)
            if torrent:
                rows.append({"hash": hash, "name": torrent.name, "folder": str(torrent.save_path)})
            else:
                raise ValueError("A selected torrent no longer exists. Refresh the list first.")
        if not rows:
            raise ValueError("Select torrents that still exist in the Torrent Client.")
        token = secrets.token_urlsafe(32)
        now = time.monotonic()
        self.pending = {k: v for k, v in self.pending.items() if v[0] > now}
        if len(self.pending) >= 100:
            self.pending.clear()
        self.pending[token] = (now + 300, self.identity(body))
        return {
            "token": token,
            "torrents": rows,
            "warning": (
                "Delete torrents and their files on the Torrent Client's machine? "
                "Other torrents may share these files."
            )
            if body.action == "delete"
            else "Remove the selected torrents from the Torrent Client? Their files will be kept.",
        }

    def consume(self, body: JobRequest) -> bool:
        record = self.pending.pop(body.confirmation, None)
        return (
            record is not None and record[0] > time.monotonic() and record[1] == self.identity(body)
        )
