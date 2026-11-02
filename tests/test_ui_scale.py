"""A desktop interface size, for screens where the system's scaling does not suit."""

from torrentlinker.app import apply_ui_scale
from torrentlinker.config import Settings, ui_scale


def test_the_interface_size_is_saved_and_old_settings_keep_the_system_size(
    tmp_path, qtbot, monkeypatch
):  # type: ignore[no-untyped-def]
    from PySide6.QtWidgets import QMessageBox

    from torrentlinker.gui.settings_dialog import SettingsDialog

    path = tmp_path / "settings.json"
    path.write_text("{}")
    settings = Settings.load(path)
    assert settings.ui_scale == 1.0
    told: list[str] = []
    monkeypatch.setattr(QMessageBox, "information", lambda *args: told.append(args[2]))
    dialog = SettingsDialog(settings)
    qtbot.addWidget(dialog)
    assert dialog.ui_scale.currentText() == "100%"
    dialog.ui_scale.setCurrentIndex(dialog.ui_scale.findData(1.25))
    dialog._save()

    assert settings.ui_scale == 1.25
    assert told == ["Restart TorrentLinker to apply the new interface size."]
    settings.save(path)
    assert Settings.load(path).ui_scale == 1.25


def test_odd_saved_sizes_stay_within_the_offered_range() -> None:
    assert ui_scale("2") == 2.0
    assert ui_scale(9) == 2.0
    assert ui_scale(0.1) == 0.75
    assert ui_scale("big") == 1.0


def test_the_size_reaches_qt_unless_the_user_set_it_already() -> None:
    environ: dict[str, str] = {}
    apply_ui_scale(1.0, environ)
    assert environ == {}  # the system size: nothing to tell Qt
    apply_ui_scale(1.25, environ)
    assert environ == {"QT_SCALE_FACTOR": "1.25"}
    mine = {"QT_SCALE_FACTOR": "2"}
    apply_ui_scale(0.75, mine)
    assert mine == {"QT_SCALE_FACTOR": "2"}
