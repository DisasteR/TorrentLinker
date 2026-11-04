"""Tests never take ports or clients that belong to the user's running app."""

from pathlib import Path

from torrentlinker.config import Settings
from torrentlinker.store import Store


def test_a_desktop_assistant_server_gets_a_free_port_unless_one_is_named(tmp_path: Path) -> None:
    from torrentlinker.agent.desktop import DesktopServer

    store = Store(tmp_path / "state.db")
    assert DesktopServer(Settings(), tmp_path / "settings.json", store).port == 0
    named = DesktopServer(Settings(), tmp_path / "settings.json", store, port=12345)
    assert named.port == 12345
