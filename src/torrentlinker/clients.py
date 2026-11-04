"""Connect to the Torrent Client the user chose (ADR-0009, ADR-0010)."""

from __future__ import annotations

from torrentlinker.deluge import DelugeApi
from torrentlinker.domain import TorrentClient
from torrentlinker.qbit import ClientApi, QbitApi
from torrentlinker.rtorrent import RtorrentApi
from torrentlinker.transmission import TransmissionApi
from torrentlinker.utorrent import UtorrentApi


def connect(
    client: TorrentClient, host: str, username: str, password: str, api_key: str = ""
) -> ClientApi:
    """The client's adapter; Deluge's Web UI has a password and no username."""
    if client is TorrentClient.DELUGE:
        return DelugeApi(host, password)
    if client is TorrentClient.TRANSMISSION:
        return TransmissionApi(host, username, password)
    if client is TorrentClient.RTORRENT:
        return RtorrentApi(host, username, password)
    if client in {TorrentClient.UTORRENT, TorrentClient.BITTORRENT}:
        return UtorrentApi(host, username, password, client_name=client.display_name)
    return QbitApi(host, username, password, api_key)
