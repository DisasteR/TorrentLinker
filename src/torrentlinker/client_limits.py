"""What some Torrent Clients cannot do, said before the user asks it of them.

µTorrent Classic cannot verify a Partial Match (ADR-0013); rTorrent cannot either, nor
delete a torrent's files (ADR-0017). The adapters declare it; these messages name the
client and say what to do instead. The desktop has its own translated wording.
"""

from __future__ import annotations

from typing import Any


def client_name(client: Any) -> str:
    return str(getattr(client, "client_name", "") or "Your Torrent Client")


def partial_refusal(client: Any) -> str | None:
    """Why this client cannot Reseed a Partial Match, or None when it can."""
    if getattr(client, "supports_partial_reseeds", True):
        return None
    return (
        f"{client_name(client)} cannot reseed a Partial Match: it cannot check only the "
        "files you have without touching the missing ones. Choose a Full Match, or "
        "reseed this one with qBittorrent, Transmission or Deluge."
    )


def disk_renames_elsewhere(client: Any) -> str:
    """Why the web interface sends a Reseed needing disk renames to the desktop."""
    return (
        f"{client_name(client)} cannot rename files inside a torrent, so this Reseed renames "
        "files on disk. Review and approve those renames in the desktop application."
    )


def delete_refusal(client: Any) -> str | None:
    """Why this client cannot delete a torrent's files, or None when it can."""
    if getattr(client, "can_delete_files", True):
        return None
    return (
        f"{client_name(client)} cannot delete a torrent's files. Remove the torrent instead, "
        "which keeps its files, then delete them yourself if you no longer need them."
    )
