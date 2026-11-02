"""The account workspace renders profile data and updates without a manual refresh."""

import os
import socket
import threading
import time

import pytest

from tests.test_web_profile import ORIGIN, prepared, snapshot

pytestmark = pytest.mark.skipif(os.getenv("TL_WEB_BROWSER") != "1", reason="opt-in browser test")


def test_profile_first_pair_snapshot_disconnect_browser(tmp_path, monkeypatch):
    import uvicorn
    from playwright.sync_api import expect, sync_playwright

    app, setup = prepared(tmp_path, monkeypatch)
    setup.profile_workspace().refresh(setup.settings.indexer.url, "fake-key")
    with socket.socket() as listener:
        listener.bind(("127.0.0.1", 0))
        port = listener.getsockname()[1]
    server = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=port, log_level="warning"))
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    for _ in range(100):
        if server.started:
            break
        time.sleep(0.02)
    try:
        with sync_playwright() as pw:
            browser = pw.chromium.launch()
            page = browser.new_page(viewport={"width": 1500, "height": 1100}, locale="en-US")
            errors = []
            page.on("pageerror", lambda e: errors.append(str(e)))
            page.goto(f"http://127.0.0.1:{port}/")
            page.locator("#login-password").fill("test-password-long")
            page.locator("#login-form button").click()
            page.locator("[data-view=zen]").first.click()
            expect(page.locator("#zen-profile-tab")).to_have_attribute("aria-selected", "true")
            expect(page.locator("#profile-username")).to_contain_text("Demo account")
            expect(page.locator("#profile-tiles")).to_contain_text("124")
            expect(page.locator("#profile-sync .pf-cal i")).to_have_count(182)
            # A profile invalidated by a settings change refreshes without a click.
            import httpx

            from tests.test_profile import PROFILE

            monkeypatch.setattr(
                "torrentlinker.profile.net.client",
                lambda **kw: httpx.Client(
                    transport=httpx.MockTransport(
                        lambda request: httpx.Response(
                            200, json={**PROFILE, "username": "Refreshed account"}
                        )
                    )
                ),
            )
            setup.secrets.update({"indexer-api-key": "rotated-fake-key"})
            expect(page.locator("#profile-username")).to_contain_text(
                "Refreshed account", timeout=10000
            )
            code = page.request.post(
                f"http://127.0.0.1:{port}/api/zen/profile/pairing",
                headers={"X-TorrentLinker": "1"},
            ).json()["code"]
            ext = {"Origin": ORIGIN}
            response = page.request.post(
                f"http://127.0.0.1:{port}/api/companion/pair",
                headers=ext,
                data={"code": code, "accountId": "42"},
            )
            assert response.ok
            ext["Authorization"] = "Bearer " + response.json()["token"]
            assert page.request.post(
                f"http://127.0.0.1:{port}/api/companion/snapshot", headers=ext, data=snapshot()
            ).ok
            expect(page.locator("#profile-seeding")).to_contain_text("42", timeout=10000)
            if output := os.getenv("TL_PROFILE_SCREENSHOTS"):
                from pathlib import Path

                target = Path(output)
                target.mkdir(parents=True, exist_ok=True)
                page.locator("#theme").select_option("dark")
                page.evaluate("window.scrollTo(0, 0)")
                page.screenshot(path=str(target / "web-profile.png"), full_page=True)
            page.locator("[data-view=setup]").first.click()
            page.locator("[data-settings-section=companion]").click()
            page.locator("#profile-disconnect").click()
            page.locator("[data-view=zen]").first.click()
            expect(page.locator("#profile-seeding")).not_to_contain_text("42")
            page.locator("#zen-profile-tab").focus()
            page.keyboard.press("ArrowRight")
            expect(page.locator("#zen-seeding-tab")).to_have_attribute("aria-selected", "true")
            assert not errors

            browser.close()
    finally:
        server.should_exit = True
        thread.join(timeout=10)
