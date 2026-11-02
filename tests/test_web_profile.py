from datetime import UTC, datetime

import httpx
import pytest
from fastapi.testclient import TestClient

from tests.test_profile import PROFILE
from tests.test_web_jobs import login
from torrentlinker.web.app import create_app
from torrentlinker.web.security import ServerSecrets
from torrentlinker.web.setup import Setup

OWNER = {"X-TorrentLinker": "1"}
ORIGIN = "chrome-extension://" + "a" * 32


def prepared(tmp_path, monkeypatch):
    setup = Setup(tmp_path, (), ServerSecrets(tmp_path))
    setup.secrets.update({"indexer-api-key": "fake-key"})
    monkeypatch.setattr(
        "torrentlinker.profile.net.client",
        lambda **kw: httpx.Client(
            transport=httpx.MockTransport(
                lambda request: httpx.Response(200, json={**PROFILE, "username": "Demo account"})
            )
        ),
    )
    app = create_app(
        tmp_path, password="test-password-long", service_override=setup, monitor_verifications=False
    )
    return app, setup


def snapshot(account="42"):
    return {
        "schema": 1,
        "source": "https://c411.org",
        "capturedAt": datetime.now(UTC).isoformat(),
        "account": {"id": account, "username": "Demo account"},
        "profile": {"balance": 123, "classSlug": "tanker", "level": 12},
        "details": {
            "actualDownloaded": 5,
            "actualUploaded": 10,
            "creditedDownloaded": 5,
            "creditedUploaded": 20,
            "totalSeedingTimeSec": 864000,
            "validatedUploadsCount": 3,
        },
        "activeSeeds": {"total": 42},
    }


def test_profile_is_private_api_only_and_preserves_unknown_fields(tmp_path, monkeypatch):
    app, setup = prepared(tmp_path, monkeypatch)
    with TestClient(app) as browser:
        assert browser.get("/api/zen/profile").status_code == 401
        login(browser)
        assert browser.get("/api/zen/profile").json()["api"] is None
        response = browser.post("/api/zen/profile/refresh", headers=OWNER)
        assert response.status_code == 200
        data = response.json()
        assert data["api"]["account_id"] == "42"
        assert data["api"]["level"] == 12
        assert data["companion"] is None
        assert data["paired"] is False
        assert "fake-key" not in response.text


def test_companion_pair_sync_and_disconnect_are_bound_to_the_api_account(tmp_path, monkeypatch):
    app, setup = prepared(tmp_path, monkeypatch)
    with TestClient(app) as browser:
        login(browser)
        browser.post("/api/zen/profile/refresh", headers=OWNER)
        code = browser.post("/api/zen/profile/pairing", headers=OWNER).json()["code"]
        ext = {"Origin": ORIGIN}
        assert (
            browser.post(
                "/api/companion/pair", headers=ext, json={"code": code, "accountId": "other"}
            ).status_code
            == 403
        )
        pair = browser.post(
            "/api/companion/pair", headers=ext, json={"code": code, "accountId": "42"}
        )
        assert pair.status_code == 200
        token = pair.json()["token"]
        ext["Authorization"] = "Bearer " + token
        assert (
            browser.post("/api/companion/snapshot", headers=ext, json=snapshot("other")).status_code
            == 403
        )
        sent = browser.post("/api/companion/snapshot", headers=ext, json=snapshot())
        assert sent.status_code == 200
        data = browser.get("/api/zen/profile").json()
        assert data["companion"]["activeSeeds"]["total"] == 42
        assert data["api"]["balance"] == 123.5  # Companion never overrides API values.
        assert data["paired"] and not data["stale"]
        assert token not in str(data)
        assert browser.post("/api/zen/profile/disconnect", headers=OWNER).status_code == 200
        assert browser.get("/api/zen/profile").json()["companion"] is None
        assert (
            browser.post("/api/companion/snapshot", headers=ext, json=snapshot()).status_code == 403
        )


def test_snapshot_age_replay_credentials_change_and_restart(tmp_path, monkeypatch):
    app, setup = prepared(tmp_path, monkeypatch)
    with TestClient(app) as browser:
        login(browser)
        browser.post("/api/zen/profile/refresh", headers=OWNER)
        workspace = setup.profile_workspace()
        now = datetime.now(UTC).timestamp()
        workspace.clock = lambda: now
        code = browser.post("/api/zen/profile/pairing", headers=OWNER).json()["code"]
        ext = {"Origin": ORIGIN}
        token = browser.post(
            "/api/companion/pair", headers=ext, json={"code": code, "accountId": "42"}
        ).json()["token"]
        ext["Authorization"] = "Bearer " + token
        value = snapshot()
        assert browser.post("/api/companion/snapshot", headers=ext, json=value).status_code == 200
        assert browser.post("/api/companion/snapshot", headers=ext, json=value).status_code == 400
        now += 301
        state = browser.get("/api/zen/profile").json()
        assert state["stale"] and state["companion"]["activeSeeds"]["total"] == 42
        assert (
            browser.post("/api/companion/snapshot", headers=ext, json=snapshot()).status_code == 400
        )
        assert (tmp_path / "profile-cache.json").stat().st_mode & 0o777 == 0o600
        assert token not in (tmp_path / "profile-cache.json").read_text()
        from torrentlinker.profile_workspace import ProfileWorkspace

        restored = ProfileWorkspace(tmp_path, clock=lambda: now)
        restored.bind(setup.settings.indexer.url, "fake-key")
        assert restored.state()["stale"] and restored.state()["paired"]
        # A saved connection works after a server restart without another code.
        fresh = snapshot()
        fresh["capturedAt"] = datetime.fromtimestamp(now, UTC).isoformat()
        assert restored.receive("/api/companion/snapshot", ORIGIN, "Bearer " + token, fresh) == {
            "accepted": True
        }
        restored.disconnect()
        with pytest.raises(PermissionError):
            restored.receive("/api/companion/snapshot", ORIGIN, "Bearer " + token, fresh)
        setup.secrets.update({"indexer-api-key": "new-account-key"})
        state = browser.get("/api/zen/profile").json()
        assert state["api"] is None and state["companion"] is None and not state["paired"]
        assert browser.post("/api/companion/snapshot", headers=ext, json=value).status_code == 403


@pytest.mark.parametrize("origin", ["", "https://c411.org", "https://evil.test", "null"])
def test_companion_refuses_web_pages_and_preflight_cannot_unlock_owner_routes(
    tmp_path, monkeypatch, origin
):
    app, setup = prepared(tmp_path, monkeypatch)
    with TestClient(app) as browser:
        assert (
            browser.post("/api/companion/pair", headers={"Origin": origin}, json={}).status_code
            == 403
        )
        assert browser.options("/api/companion/pair", headers={"Origin": origin}).status_code == 403
        assert (
            browser.post(
                "/api/zen/profile/pairing", headers={"Origin": ORIGIN}, json={}
            ).status_code
            == 403
        )
        preflight = browser.options("/api/companion/pair", headers={"Origin": ORIGIN})
        assert preflight.status_code == 204
        assert preflight.headers["access-control-allow-origin"] == ORIGIN
        assert "access-control-allow-credentials" not in preflight.headers


def test_profile_auth_errors_and_proxy_prefix(tmp_path, monkeypatch):
    app, setup = prepared(tmp_path, monkeypatch)
    setup.secrets.update({"indexer-api-key": ""})
    with TestClient(app, root_path="/torrentlinker") as browser:
        login(browser)
        state = browser.post("/torrentlinker/api/zen/profile/refresh", headers=OWNER).json()
        assert state["error"] == "no-key" and state["api"] is None
        assert (
            browser.post("/torrentlinker/api/zen/profile/pairing", headers=OWNER).status_code == 409
        )
        assert (
            browser.options(
                "/torrentlinker/api/companion/pair", headers={"Origin": ORIGIN}
            ).status_code
            == 204
        )


def test_pairing_expiry_origin_binding_and_unexpected_snapshot_fields(tmp_path, monkeypatch):
    app, setup = prepared(tmp_path, monkeypatch)
    with TestClient(app) as browser:
        login(browser)
        browser.post("/api/zen/profile/refresh", headers=OWNER)
        workspace = setup.profile_workspace()
        now = datetime.now(UTC).timestamp()
        workspace.clock = lambda: now
        code = browser.post("/api/zen/profile/pairing", headers=OWNER).json()["code"]
        now += 601
        ext = {"Origin": ORIGIN}
        assert (
            browser.post(
                "/api/companion/pair", headers=ext, json={"code": code, "accountId": "42"}
            ).status_code
            == 403
        )
        code = browser.post("/api/zen/profile/pairing", headers=OWNER).json()["code"]
        token = browser.post(
            "/api/companion/pair", headers=ext, json={"code": code, "accountId": "42"}
        ).json()["token"]
        ext["Authorization"] = "Bearer " + token
        assert (
            browser.post(
                "/api/companion/snapshot",
                headers={**ext, "Origin": "chrome-extension://" + "b" * 32},
                json=snapshot(),
            ).status_code
            == 403
        )
        value = snapshot()
        value["capturedAt"] = datetime.fromtimestamp(now, UTC).isoformat()
        assert (
            browser.post(
                "/api/companion/snapshot",
                headers=ext,
                json={**value, "cookies": "must-not-be-stored"},
            ).status_code
            == 400
        )
        assert browser.post("/api/companion/snapshot", headers=ext, json=value).status_code == 200
        now += 3601
        assert browser.get("/api/zen/profile").json()["paired"]
        value["capturedAt"] = datetime.fromtimestamp(now, UTC).isoformat()
        assert browser.post("/api/companion/snapshot", headers=ext, json=value).status_code == 200
        # Opening setup and abandoning it must not disconnect a working companion.
        browser.post("/api/zen/profile/pairing", headers=OWNER)
        assert browser.get("/api/zen/profile").json()["paired"]


def test_profile_overview_follows_the_period_and_history(tmp_path, monkeypatch):
    app, setup = prepared(tmp_path, monkeypatch)
    with TestClient(app) as browser:
        login(browser)
        data = browser.post("/api/zen/profile/refresh?days=7", headers=OWNER).json()
        overview = data["overview"]
        assert overview["span"] == 7
        assert overview["header"]["username"] == "Demo account"
        assert overview["header"]["team"] is None
        assert overview["tiles"]["zen"]["value"] == 123.5
        assert overview["period"] is None  # one day of history so far
        assert overview["calendar"][-1]["outcome"] == "api"
        assert browser.get("/api/zen/profile?days=0").json()["overview"]["span"] == 0
        assert browser.get("/api/zen/profile?days=5").status_code == 400


def test_profile_torrent_clients_show_an_unreachable_client_as_unavailable(tmp_path, monkeypatch):
    app, setup = prepared(tmp_path, monkeypatch)
    with TestClient(app) as browser:
        login(browser)
        data = browser.get("/api/zen/profile/torrent-clients").json()
        assert data["clients"] and all(c["count"] is None for c in data["clients"])
        assert all(c["error"] for c in data["clients"])
        assert data["zen"] is None and data["gap"] is None
