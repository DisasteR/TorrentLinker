import pytest
from PySide6.QtCore import Qt

from tests.test_web_profile import ORIGIN, prepared, snapshot
from torrentlinker.gui.companion_panel import CompanionPanel
from torrentlinker.gui.zen_view import ZenTab
from torrentlinker.zen import ZenProfile


def test_profile_is_first_desktop_tab_and_shows_api_and_companion(qtbot, tmp_path, monkeypatch):
    _, setup = prepared(tmp_path, monkeypatch)
    workspace = setup.profile_workspace()
    workspace.refresh(setup.settings.indexer.url, "fake-key")
    tab = ZenTab(lambda: lambda: None, ZenProfile)
    qtbot.addWidget(tab)
    tab.resize(1200, 850)
    tab.show()
    assert tab.pages.tabText(0) == "Profile"
    view = tab.profile_view
    view.show_state(workspace.state())
    assert "Demo account" in view.identity.text()
    assert "124" in view.balance.text()
    assert "Connect" in view.active_seeds.text()
    panel = CompanionPanel()
    qtbot.addWidget(panel)
    panel.show_state(workspace.state())
    assert not panel.session_downloads.isEnabled()
    with qtbot.waitSignal(panel.pair_requested):
        qtbot.mouseClick(panel.pair_button, Qt.MouseButton.LeftButton)
    code = workspace.pairing()["code"]
    token = workspace.receive("/api/companion/pair", ORIGIN, "", {"code": code, "accountId": "42"})[
        "token"
    ]
    workspace.receive("/api/companion/snapshot", ORIGIN, "Bearer " + token, snapshot())
    view.show_state(workspace.state())
    assert "42" in view.active_seeds.text()
    panel.show_state(workspace.state())
    assert panel.disconnect_button.isEnabled()
    assert panel.session_downloads.isEnabled() and not panel.session_downloads.isChecked()
    with qtbot.waitSignal(panel.session_downloads_toggled) as toggled:
        panel.session_downloads.setChecked(True)
    assert toggled.args == [True]
    workspace.set_session_downloads(True)
    panel.show_state(workspace.state())
    assert panel.session_downloads.isChecked()
    import os
    from pathlib import Path

    if output := os.getenv("TL_PROFILE_SCREENSHOTS"):
        from PySide6.QtWidgets import QApplication

        from torrentlinker.gui.theme import apply_theme

        apply_theme(QApplication.instance(), "dark")
        tab.resize(1400, 1000)
        qtbot.wait(100)
        target = Path(output)
        target.mkdir(parents=True, exist_ok=True)
        assert tab.grab().save(str(target / "desktop-profile.png"))
    workspace.disconnect()
    view.show_state(workspace.state())
    assert "Connect" in view.active_seeds.text()


def test_desktop_connect_opens_handoff_and_receiver_returns_after_restart(
    qtbot, tmp_path, monkeypatch
):
    import json
    import socket
    from urllib.parse import unquote, urlsplit

    import httpx

    from torrentlinker.config import Settings
    from torrentlinker.credentials import set_secret
    from torrentlinker.gui.main_window import MainWindow
    from torrentlinker.store import Store

    prepared(tmp_path, monkeypatch)
    set_secret("indexer-api-key", "fake-key")
    opened = []
    monkeypatch.setattr(
        "PySide6.QtGui.QDesktopServices.openUrl", lambda url: opened.append(url.toString()) or True
    )
    settings = Settings()
    window = MainWindow(settings, tmp_path / "settings.json", Store(tmp_path / "desktop.db"))
    qtbot.addWidget(window)
    window.show()
    qtbot.waitUntil(lambda: "Demo account" in window.zen_tab.profile_view.identity.text())
    with socket.socket() as listener:
        listener.bind(("127.0.0.1", 0))
        window.account_workspace.remember_receiver(listener.getsockname()[1])
    qtbot.mouseClick(window.companion_panel_widget().pair_button, Qt.MouseButton.LeftButton)
    assert len(opened) == 1
    payload = json.loads(unquote(urlsplit(opened[0]).fragment))
    address = payload["target"]
    with httpx.Client(base_url=address, trust_env=False) as browser:
        assert browser.get("/companion/connect").status_code == 200
        assert browser.get("/companion/connect.js").status_code == 200
        token = browser.post(
            "/api/companion/pair", headers={"Origin": ORIGIN}, json=payload
        ).json()["token"]
    window.close()
    restarted = MainWindow(
        settings, tmp_path / "settings.json", Store(tmp_path / "desktop-restarted.db")
    )
    qtbot.addWidget(restarted)
    restarted.show()
    qtbot.waitUntil(lambda: restarted.companion_receiver is not None)
    with httpx.Client(base_url=address, trust_env=False) as browser:
        assert (
            browser.post(
                "/api/companion/snapshot",
                headers={"Origin": ORIGIN, "Authorization": "Bearer " + token},
                json=snapshot(),
            ).status_code
            == 200
        )
    qtbot.waitUntil(
        lambda: restarted.zen_tab.profile_view.active_seeds.text() == "42", timeout=5000
    )
    assert len(opened) == 1  # No new handoff or consent on restart.
    restarted.close()


@pytest.mark.parametrize("failed", [False, True])
def test_profile_completion_after_window_destruction_is_ignored(
    qtbot, tmp_path, monkeypatch, failed
):
    import threading

    from shiboken6 import delete, isValid

    from torrentlinker.config import Settings
    from torrentlinker.gui import tasks
    from torrentlinker.gui.main_window import MainWindow
    from torrentlinker.store import Store

    window = MainWindow(Settings(), tmp_path / "settings.json", Store(tmp_path / "state.db"))
    window.profile_timer.stop()
    started, release = threading.Event(), threading.Event()

    def read_profile(*args):
        started.set()
        assert release.wait(5)
        if failed:
            raise ValueError("controlled profile failure")
        from torrentlinker.profile import UserProfile

        return UserProfile("Fixture", None, None, 0, 0, True, None)

    monkeypatch.setattr(window.account_workspace, "refresh", read_profile)
    window.refresh_profile()
    assert started.wait(1)
    try:
        window.close()
        delete(window)
        assert not isValid(window)
    finally:
        release.set()
    qtbot.waitUntil(lambda: not tasks._alive, timeout=5000)


def test_profile_view_draws_history_period_clients_and_wraps_tiles(qtbot, tmp_path, monkeypatch):
    from datetime import date, timedelta

    from PySide6.QtCore import QSettings

    from torrentlinker.gui.profile_view import ProfileView
    from torrentlinker.profile_history import ProfileDay

    _, setup = prepared(tmp_path, monkeypatch)
    workspace = setup.profile_workspace()
    workspace.refresh(setup.settings.indexer.url, "fake-key")
    today = date.today()
    history = [
        ProfileDay(
            (today - timedelta(days=back)).isoformat(),
            {"uploaded_actual": 1000 - back, "downloaded_actual": 100, "balance": 50 - back},
            api=True,
        )
        for back in range(40, -1, -1)
    ]
    settings = QSettings(str(tmp_path / "ui.ini"), QSettings.Format.IniFormat)
    view = ProfileView(settings)
    qtbot.addWidget(view)
    view.resize(1400, 1200)
    view.show()
    view.show_state(workspace.state(), [])
    assert view.volumes.dates == []  # history is being collected
    view.show_state(workspace.state(), history)
    assert len(view.volumes.dates) == 31  # 30 days back, both ends included
    assert view.tiles["uploaded"].change.text()
    qtbot.mouseClick(view.period_buttons[7], Qt.MouseButton.LeftButton)
    assert len(view.volumes.dates) == 8
    assert settings.value("profile/period") in (7, "7")
    assert ProfileView(settings).period == 7

    view.show_torrent_clients(
        {
            "clients": [
                {"name": "seedbox", "count": 3, "size": 30, "error": None},
                {"name": "NAS", "count": None, "size": None, "error": "refused"},
            ],
            "unique": 3,
            "gap": None,
            "zen": None,
        }
    )
    texts = [
        view.seeding_table.itemAt(i).widget().text() for i in range(view.seeding_table.count())
    ]
    assert "seedbox" in texts and "NAS" in texts
    assert any("Unavailable" in t for t in texts)
    assert view.tiles_grid.getItemPosition(view.tiles_grid.indexOf(view.tiles["zen"]))[:2] == (
        0,
        4,
    )
    view.resize(900, 1200)
    qtbot.waitUntil(lambda: view._columns == (3, False))
    view.resize(600, 1200)
    qtbot.waitUntil(lambda: view._columns == (2, False))
