"""Browser acceptance of the real workflow; only the Torrent Client is simulated."""

import os
import socket
import threading
import time
from pathlib import Path

import pytest

from tests.test_reseed_workspace import CheckingClient, workspace
from torrentlinker.web.app import create_app
from torrentlinker.web.operations import Operations


def reseed_app(root, client=None):
    board, client, original, links, meta = workspace(root, client)
    board.setup.save()

    def operations(setup):
        result = Operations(setup)
        result.library.entries = board.library.entries
        return result

    app = create_app(
        root / "config",
        password="test-password-long",
        service_override=board.setup,
        operation_factory=operations,
    )
    return app, board, client, original, links, meta


@pytest.mark.skipif(os.getenv("TL_WEB_BROWSER") != "1", reason="opt-in browser test")
def test_reseed_board_runs_pauses_and_preserves_original_on_small_screen(tmp_path):
    import uvicorn
    from playwright.sync_api import expect, sync_playwright

    class SlowCheck(CheckingClient):
        release = threading.Event()

        def torrent(self, hash, **kwargs):
            if not self.release.is_set():
                return self.by_hash.get(hash)
            return super().torrent(hash, **kwargs)

    client = SlowCheck()
    app, board, _, original, links, meta = reseed_app(tmp_path, client)
    before = original.read_bytes(), original.stat().st_ino
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        port = sock.getsockname()[1]
    server = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=port, log_level="warning"))
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    for _ in range(100):
        if server.started:
            break
        time.sleep(0.02)
    try:
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch()
            page = browser.new_page(viewport={"width": 1280, "height": 900}, locale="en-US")
            errors = []
            page.on("pageerror", lambda e: errors.append(str(e)))
            page.goto(f"http://127.0.0.1:{port}/#/library/reseed")
            page.locator("#login-password").fill("test-password-long")
            page.locator("#login-form button").click()
            expect(page.locator("#reseed-board")).to_contain_text(original.name)
            expect(page.locator("#reseed-root")).to_be_hidden()
            page.locator("#reseed-settings").click()
            page.locator("#reseed-browse").click()
            page.locator("#dialog").get_by_role("button", name=str(links), exact=True).click()
            page.locator("#dialog").get_by_role(
                "button", name="Use this folder", exact=True
            ).click()
            expect(page.locator("#reseed-root")).to_have_value(str(links))
            page.locator("#reseed-folder-form button[type=submit]").click()
            expect(page.locator("#toast")).to_contain_text("Link folder saved")
            page.reload()
            page.locator("#reseed-folder-settings summary").click()
            expect(page.locator("#reseed-root")).to_have_value(str(links))
            page.goto(f"http://127.0.0.1:{port}/#/library/reseed")
            expect(page.locator("#reseed-root-status")).to_have_text("Link folder configured")
            expect(page.locator("#reseed-root")).to_be_hidden()
            page.locator("#reseed-run-all").click()
            page.locator("#reseed-details").get_by_role(
                "button", name="Run all steps", exact=True
            ).click()
            expect(page.locator("#reseed-progress-text")).to_contain_text(
                "Client verification", timeout=20000
            )
            expect(page.locator("#reseed-refresh")).to_be_disabled()
            expect(page.locator("#reseed-scan")).to_be_disabled()
            page.locator("#reseed-pause").click()
            expect(page.locator("#reseed-pause")).to_have_text("Resume", timeout=10000)
            client.release.set()
            assert not client.started
            # Reload reattaches to the server-owned run, including its pause state.
            page.reload()
            expect(page.locator("#reseed-pause")).to_have_text("Resume", timeout=10000)
            assert not client.started
            page.set_viewport_size({"width": 390, "height": 844})
            assert page.evaluate("document.documentElement.scrollWidth <= innerWidth")
            page.locator("#reseed-pause").click()
            expect(page.locator('[data-stage="seeding"]')).to_contain_text(
                original.name, timeout=20000
            )
            expect(page.locator("#reseed-progress-text")).to_contain_text("Finished", timeout=10000)
            expect(page.locator("#reseed-run-buttons")).to_be_hidden()
            assert client.started == [meta.infohash]
            page.locator('[data-stage="seeding"]').get_by_role("button", name="Inspect").click()
            expect(page.locator("#reseed-details")).to_contain_text("Release.mkv")
            expect(page.locator("#reseed-details")).to_contain_text(str(original))
            page.get_by_role("button", name="Close", exact=True).click()
            destination = os.getenv("TL_WEB_SCREENSHOTS")
            if destination:
                Path(destination).mkdir(parents=True, exist_ok=True)
                page.screenshot(path=str(Path(destination) / "reseed-mobile.png"), full_page=True)
                page.set_viewport_size({"width": 1560, "height": 1000})
                page.screenshot(path=str(Path(destination) / "reseed-web.png"), full_page=True)
            assert not errors
            assert (original.read_bytes(), original.stat().st_ino) == before
            browser.close()
    finally:
        client.release.set()
        server.should_exit = True
        thread.join(15)
