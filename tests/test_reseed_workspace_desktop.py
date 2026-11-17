from PySide6.QtWidgets import QMessageBox

from tests.test_library_folders import window_for
from tests.test_reseed_workspace import workspace


def test_native_board_runs_the_shared_workflow(qtbot, tmp_path, monkeypatch):
    root = tmp_path / "fixture"
    root.mkdir()
    service, client, original, links, meta = workspace(root)
    window, _ = window_for(qtbot, tmp_path, monkeypatch)
    board = window.library_workspace.reseed
    monkeypatch.setattr(board, "service", lambda: service)
    monkeypatch.setattr(window, "indexer", service.setup.indexer)
    monkeypatch.setattr(QMessageBox, "question", lambda *args: QMessageBox.StandardButton.Yes)
    window.tabs.setCurrentIndex(1)
    window.library_workspace.sections.setCurrentWidget(board)
    window.settings.profile().reseed_link_root = str(links)
    board.refresh()
    window.resize(1400, 1000)
    window.show()
    from PySide6.QtWidgets import QLineEdit

    assert board.findChild(QLineEdit, "reseedLinkRoot") is None
    assert board.link_root == str(links)
    assert len(board.rows) == 1 and board.rows[0]["stage"] == "find"
    before = original.read_bytes(), original.stat().st_ino
    board.run_all.click()
    qtbot.waitUntil(lambda: not board.running, timeout=10000)
    assert board.rows[0]["stage"] == "seeding", board.status.text()
    assert client.started == [meta.infohash]
    assert (original.read_bytes(), original.stat().st_ino) == before
    assert board.run_all.isEnabled() is False
    import os
    from pathlib import Path

    if output := os.getenv("TL_WEB_SCREENSHOTS"):
        Path(output).mkdir(parents=True, exist_ok=True)
        window.grab().save(str(Path(output) / "reseed-desktop.png"))


def test_link_folder_is_edited_in_client_settings_without_leaking_between_clients(
    qtbot, monkeypatch
):
    from torrentlinker.config import Settings
    from torrentlinker.gui.settings_dialog import SettingsDialog

    settings = Settings()
    original = settings.active_client_id
    other = settings.add_client("NAS")
    settings.select_client(other.id)
    monkeypatch.setattr(SettingsDialog, "_save_secrets", lambda self: None)
    dialog = SettingsDialog(settings, secret_values={})
    qtbot.addWidget(dialog)
    dialog.reseed_link_root.setText("/media/nas-links")
    dialog._save()
    assert settings.profile().reseed_link_root == "/media/nas-links"
    settings.select_client(original)
    assert settings.profile().reseed_link_root == ""
    cancelled = SettingsDialog(settings, secret_values={})
    qtbot.addWidget(cancelled)
    cancelled.reseed_link_root.setText("/do-not-save")
    cancelled.reject()
    assert settings.profile().reseed_link_root == ""
