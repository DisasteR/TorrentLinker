import logging
import threading
import time
from itertools import pairwise

from PySide6.QtCore import QObject, QTimer

from torrentlinker.gui.stall_watchdog import StallWatchdog, install


def test_permanent_stall_is_logged_before_the_gui_can_recover(qtbot, caplog):
    parent = QObject()
    watchdog = StallWatchdog(30, parent)
    caplog.set_level(logging.WARNING, logger="torrentlinker.stall")
    # Deliberately stop processing Qt events. A real freeze cannot tick again.
    # Wait on a threading event so the watchdog can sample the blocked GUI.
    blocked = threading.Event()
    worker = threading.Thread(target=blocked.wait, name="library-scan-fixture")
    worker.start()
    try:
        blocked.wait(0.15)
        messages = [r.getMessage() for r in caplog.records]
        assert any("stalled" in m and "test_permanent_stall" in m for m in messages)
        assert any("library-scan-fixture" in m for m in messages)
    finally:
        blocked.set()
        worker.join(timeout=1)
        watchdog.running = False
        watchdog.worker.join(timeout=1)


def slow_database_call():
    time.sleep(0.12)


def test_stall_is_logged_with_the_gui_stack(qtbot, caplog):
    parent = QObject()
    watchdog = StallWatchdog(40, parent)
    qtbot.wait(60)
    caplog.set_level(logging.WARNING, logger="torrentlinker.stall")
    QTimer.singleShot(0, slow_database_call)
    qtbot.wait(400)
    watchdog.running = False
    # A watchdog left running would report the next test's teardown as a stall.
    watchdog.worker.join(timeout=1)
    messages = [r.getMessage() for r in caplog.records]
    assert any("stalled" in m and "slow_database_call" in m for m in messages)


def test_quick_ticks_log_nothing(qtbot, caplog):
    parent = QObject()
    watchdog = StallWatchdog(200, parent)
    # A busy CI runner can pause the whole process past the threshold. Measure the real
    # gaps between heartbeats so only a stall the GUI never had counts as a false alarm.
    ticks = [time.perf_counter()]
    watchdog.timer.timeout.connect(lambda: ticks.append(time.perf_counter()))
    caplog.set_level(logging.WARNING, logger="torrentlinker.stall")
    qtbot.wait(300)
    watchdog.running = False
    watchdog.worker.join(timeout=1)
    ticks.append(time.perf_counter())
    longest = max(later - earlier for earlier, later in pairwise(ticks))
    if longest <= watchdog.threshold:
        assert not [r for r in caplog.records if "stalled" in r.getMessage()]


def test_off_unless_asked(monkeypatch):
    monkeypatch.delenv("TORRENTLINKER_STALL_MS", raising=False)
    assert install(QObject()) is None
    monkeypatch.setenv("TORRENTLINKER_STALL_MS", "nope")
    assert install(QObject()) is None
