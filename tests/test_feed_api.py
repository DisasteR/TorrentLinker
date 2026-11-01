from fastapi.testclient import TestClient

from tests.test_agent_access import call
from tests.test_web_jobs import finished, login
from torrentlinker.web.app import create_app


def test_rss_owner_routes_and_mcp_use_same_subscription_permissions(tmp_path):
    app = create_app(tmp_path, password="test-password-long")
    with TestClient(app) as browser:
        assert browser.get("/api/feeds").status_code == 401
        login(browser)
        from tests.test_feeds import Source

        source = Source()
        app.state.agent_service.feed_control.feeds.indexer = lambda: source
        definition = {"name": "Films", "client_id": "default", "categories": [2030]}
        assert browser.post("/api/feeds/preview", json=definition).is_success
        saved = browser.post("/api/feeds", json={"subscription": definition}).json()
        assert saved["name"] == "Films"
        grant = browser.post("/api/agents", json={"name": "Reader", "clients": ["default"]}).json()
        browser.put("/api/agents", json={"enabled": True})
        result = call(browser, grant["token"], "list_feeds")
        assert result["structuredContent"]["subscriptions"][0]["id"] == saved["id"]
        assert not call(
            browser,
            grant["token"],
            "preview_feed",
            {"subscription": {"name": "Books", "client_id": "default", "categories": [7020]}},
        ).get("isError")
        proposal = call(
            browser,
            grant["token"],
            "process_feed_action",
            {
                "request_id": "rss-create",
                "client_id": "default",
                "operation": "save",
                "subscription": {"name": "Books", "client_id": "default", "categories": [7020]},
            },
        )["structuredContent"]
        assert proposal["requires_owner_approval"]
        assert len(browser.get("/api/feeds").json()) == 1
        approved = browser.post(f"/api/agents/proposals/{proposal['id']}/approve").json()
        assert finished(browser, approved["job_id"])["status"] == "completed"
        assert len(browser.get("/api/feeds").json()) == 2


from tests import test_catalogue_selection  # noqa: E402

catalogue_app = test_catalogue_selection.catalogue_app


def test_app_queue_scheduler_never_contacts_clients_and_restart_keeps_rule(
    catalogue_app, monkeypatch
):
    import time

    from tests.test_feeds import Source, release
    from torrentlinker.download_queue import entries
    from torrentlinker.feed_control import FeedControl
    from torrentlinker.feeds import Subscription

    app, browser, token = catalogue_app
    service = app.state.agent_service
    control = service.feed_control
    source = Source()
    source.rows = [release("b", metadata={"kind": "movie", "tmdbid": "123"})]
    monkeypatch.setattr(control.feeds, "indexer", lambda: source)
    monkeypatch.setattr(service.setup, "indexer", lambda: source)
    folder = next(iter(service.for_client("default").download_destinations()))
    definition = Subscription(
        name="Local only", mode="app_queue", folder=folder, include_current=True
    )
    control.feeds.preview(definition)
    feed = control.save(definition)
    service.for_client("default").feed_observed_at = time.time()

    def forbidden():
        raise AssertionError("app_queue contacted a Torrent Client")

    monkeypatch.setattr(service.setup, "client", forbidden)
    control.tick()
    assert len(entries(service.setup.store)) == 1
    restarted = FeedControl(service)
    restarted.tick()
    assert restarted.feeds.get(feed["id"]).mode == "app_queue"
    assert len(entries(service.setup.store)) == 1


def test_automatic_dispatch_respects_budget_and_schedule_after_restart(catalogue_app, monkeypatch):
    from tests.test_feeds import Source, release
    from torrentlinker.download_queue import entries
    from torrentlinker.feed_control import FeedControl
    from torrentlinker.feeds import Subscription

    app, browser, token = catalogue_app
    service = app.state.agent_service
    control = service.feed_control
    source = Source()
    source.rows = [
        release("a", metadata={"kind": "movie", "tmdbid": "456"}),
        release("b", metadata={"kind": "movie", "tmdbid": "123"}),
    ]
    monkeypatch.setattr(service.setup, "indexer", lambda: source)
    monkeypatch.setattr(control.feeds, "indexer", lambda: source)
    folder = next(iter(service.for_client("default").download_destinations()))
    definition = Subscription(
        name="Budgeted",
        mode="automatic_download",
        folder=folder,
        include_current=True,
        daily_bytes=0,
    )
    refused = browser.post("/api/feeds", json={"subscription": definition.model_dump()})
    assert refused.status_code != 200
    control.feeds.preview(definition)
    saved = control.save(definition, approved=True)
    feed = control.feeds.get(saved["id"])
    control.tick()
    assert entries(service.setup.store)[0].status == "queued"
    assert (
        "daily download budget"
        in next(row for row in control.feeds.decisions(feed.id) if row["infohash"] == "b" * 40)[
            "reason"
        ]
    )
    restarted = FeedControl(service)
    assert "daily download budget" in restarted.dispatch(feed)[0]["reason"]
    blocked_hours = feed.model_copy(update={"download_hours": []})
    control.save(blocked_hours, approved=True)
    assert (
        restarted.dispatch(blocked_hours)[0]["reason"] == "Outside configured UTC download hours."
    )
    assert entries(service.setup.store)[0].status == "queued"


def test_new_subscription_requires_matching_preview_before_save(tmp_path):
    app = create_app(tmp_path, password="test-password-long")
    with TestClient(app) as browser:
        login(browser)
        response = browser.post("/api/feeds", json={"subscription": {"name": "Unpreviewed"}})
        assert response.status_code != 200
        assert browser.get("/api/feeds").json() == []


def test_expired_budget_attempt_renews_and_pause_delete_revoke_authority(
    catalogue_app, monkeypatch
):
    import time

    import pytest

    from tests.test_feeds import Source, release
    from torrentlinker.feeds import Subscription

    app, browser, token = catalogue_app
    service = app.state.agent_service
    control = service.feed_control
    source = Source()
    source.rows = [
        release("a", metadata={"kind": "movie", "tmdbid": "456"}),
        release("b", metadata={"kind": "movie", "tmdbid": "123"}),
    ]
    monkeypatch.setattr(service.setup, "indexer", lambda: source)
    monkeypatch.setattr(control.feeds, "indexer", lambda: source)
    folder = next(iter(service.for_client("default").download_destinations()))
    definition = Subscription(
        name="Renewable",
        mode="automatic_download",
        folder=folder,
        include_current=True,
        daily_bytes=0,
    )
    control.feeds.preview(definition)
    saved = control.save(definition, approved=True)
    feed = control.feeds.get(saved["id"])
    control.tick()
    now = time.time()
    with monkeypatch.context() as clock:
        clock.setattr(time, "time", lambda: now + 1000)
        assert "daily download budget" in control.dispatch(feed)[0]["reason"]
    proposals = service.access.db.execute("SELECT status FROM proposals").fetchall()
    assert [p[0] for p in proposals] == ["rejected", "pending"]
    actor = control.feeds.db.execute(
        "SELECT actor FROM actors WHERE feed=?", (feed.id,)
    ).fetchone()[0]
    control.set_paused(feed.id, True)
    assert control.dispatch(feed)[0]["status"] == "stopped"
    with pytest.raises(PermissionError):
        service.access.grant(actor)
    with pytest.raises(ValueError):
        control.check_download_authority(actor, "default", folder)
    control.set_paused(feed.id, False)
    assert service.access.grant(actor).daily_bytes == 0
    control.delete(feed.id)
    assert control.dispatch(feed)[0]["status"] == "stopped"
    with pytest.raises(PermissionError):
        service.access.grant(actor)


def test_mcp_inbox_and_marks_do_not_cross_overlapping_client_permissions(
    catalogue_app, monkeypatch
):
    from tests.test_feeds import Source, release
    from torrentlinker.feeds import Subscription

    app, browser, token = catalogue_app
    service = app.state.agent_service
    other = browser.post(
        "/api/clients",
        json={"name": "Other", "client": "qbittorrent", "host": "https://other.test"},
    ).json()["client_id"]
    source = Source()
    source.rows = [release("b")]
    feeds = service.feed_control.feeds
    monkeypatch.setattr(feeds, "indexer", lambda: source)
    permitted = feeds.save(Subscription(name="Permitted", client_id="default"))
    hidden = feeds.save(Subscription(name="Hidden", client_id=other))
    feeds.refresh(permitted.id)
    feeds.refresh(hidden.id)
    feeds.mark(["b" * 40], feed_id=hidden.id, action="unread")
    feeds.mark(["b" * 40], feed_id=hidden.id, action="save")
    response = call(browser, token, "feed_inbox")["structuredContent"]["rows"][0]
    assert response["feeds"] == [permitted.id]
    assert response["unread"] is False
    assert response["saved"] is False
    proposal = call(
        browser,
        token,
        "process_feed_action",
        {
            "request_id": "mark-local",
            "client_id": "default",
            "operation": "mark",
            "hashes": ["b" * 40],
            "mark": "read",
        },
    )["structuredContent"]
    approved = browser.post(f"/api/agents/proposals/{proposal['id']}/approve").json()
    assert finished(browser, approved["job_id"])["status"] == "completed"
    assert feeds.inbox(hidden.id)[0]["unread"] is True


def test_client_work_dedup_uses_persisted_identity_without_inbox_membership(
    catalogue_app, monkeypatch
):
    from tests.test_feeds import Source, release
    from torrentlinker.download_queue import entries
    from torrentlinker.feeds import Subscription

    app, browser, token = catalogue_app
    control = app.state.agent_service.feed_control
    source = Source()
    source.rows = [release("b", metadata={"kind": "movie", "tmdbid": "123"})]
    monkeypatch.setattr(control.feeds, "indexer", lambda: source)
    control.feeds.observe_client_release(release("a", metadata={"kind": "movie", "tmdbid": "123"}))
    folder = next(iter(control.service.for_client("default").download_destinations()))
    definition = Subscription(
        name="Another release", mode="app_queue", folder=folder, include_current=True
    )
    control.feeds.preview(definition)
    saved = control.save(definition)
    control.feeds.refresh(saved["id"])
    result = control.process(saved["id"], refresh_clients=True)
    assert result["rows"][0]["status"] == "duplicate_work"
    assert entries(control.service.setup.store) == []


def test_pending_automatic_entry_rechecks_edited_hard_requirements(catalogue_app, monkeypatch):
    import pytest

    from tests.test_feeds import Source, release
    from torrentlinker.feeds import Preferences, Subscription

    app, browser, token = catalogue_app
    service = app.state.agent_service
    control = service.feed_control
    source = Source()
    source.rows = [
        release("a", metadata={"kind": "movie", "tmdbid": "456"}),
        release("b", metadata={"kind": "movie", "tmdbid": "123"}),
    ]
    monkeypatch.setattr(service.setup, "indexer", lambda: source)
    monkeypatch.setattr(control.feeds, "indexer", lambda: source)
    folder = next(iter(service.for_client("default").download_destinations()))
    definition = Subscription(
        name="Edited", mode="automatic_download", folder=folder, include_current=True, daily_bytes=0
    )
    control.feeds.preview(definition)
    saved = control.save(definition, approved=True)
    control.tick()
    feed = control.feeds.get(saved["id"])
    control.save(feed.model_copy(update={"preferences": Preferences(min_size=2000)}), approved=True)
    assert "size outside required range" in control.dispatch(feed)[0]["reason"]
    actor = control.feeds.db.execute(
        "SELECT actor FROM actors WHERE feed=?", (feed.id,)
    ).fetchone()[0]
    with pytest.raises(ValueError, match="size outside required range"):
        control.check_download_authority(actor, "default", folder, "b" * 40)


def test_pending_download_rechecks_alternative_work_added_to_client(catalogue_app, monkeypatch):
    from pathlib import Path
    from types import SimpleNamespace

    from tests.test_feeds import Source, release
    from torrentlinker.domain import Torrent
    from torrentlinker.feeds import Subscription

    app, browser, token = catalogue_app
    service = app.state.agent_service
    control = service.feed_control
    source = Source()
    source.rows = [
        release("a", metadata={"kind": "movie", "tmdbid": "456"}),
        release("b", metadata={"kind": "movie", "tmdbid": "123"}),
    ]
    monkeypatch.setattr(service.setup, "indexer", lambda: source)
    monkeypatch.setattr(control.feeds, "indexer", lambda: source)
    folder = next(iter(service.for_client("default").download_destinations()))
    definition = Subscription(
        name="Late alternative",
        mode="automatic_download",
        folder=folder,
        include_current=True,
        daily_bytes=0,
    )
    control.feeds.preview(definition)
    saved = control.save(definition, approved=True)
    control.tick()
    control.feeds.observe_client_release(release("c", metadata={"kind": "movie", "tmdbid": "123"}))
    client = SimpleNamespace(
        torrents=lambda **_: [Torrent("c" * 40, "Alternative", Path("/downloads"))]
    )
    monkeypatch.setattr(service.setup, "client", lambda: client)
    control.inventory(refresh=True)
    result = control.dispatch(control.feeds.get(saved["id"]))
    assert "work is already in a Torrent Client" in result[0]["reason"]


def test_mcp_recovers_owned_snapshot_after_feed_deletion_but_not_other_connections(
    catalogue_app, monkeypatch
):
    from tests.test_feeds import Source, release
    from torrentlinker.feeds import Subscription

    app, browser, token = catalogue_app
    service = app.state.agent_service
    source = Source()
    source.rows = [release("b", title="Saved review")]
    monkeypatch.setattr(service.feed_control.feeds, "indexer", lambda: source)
    feed = service.feed_control.feeds.save(Subscription(name="Later deleted"))
    service.feed_control.feeds.refresh(feed.id)
    saved = call(browser, token, "select_feed_items", {"hashes": ["b" * 40], "name": "My review"})[
        "structuredContent"
    ]
    service.feed_control.delete(feed.id)
    recovered = call(browser, token, "get_feed_selections", {"selection_id": saved["id"]})[
        "structuredContent"
    ]
    assert recovered["rows"][0]["title"] == "Saved review"
    assert recovered["subscription_ids"] == [feed.id]
    other = browser.post(
        "/api/agents", json={"name": "Other connection", "clients": ["default"]}
    ).json()
    denied = call(browser, other["token"], "get_feed_selections", {"selection_id": saved["id"]})
    assert denied["structuredContent"].get("error")


def test_mcp_exposes_subscription_fields_and_preferences(tmp_path):
    import asyncio

    app = create_app(tmp_path, password="test-password-long")
    tools = asyncio.run(app.state.assistant.mcp.list_tools())
    for name in ("preview_feed", "process_feed_action"):
        schema = next(tool.input_schema for tool in tools if tool.name == name)
        definitions = schema.get("$defs", {})
        subscription = definitions["Subscription"]["properties"]
        assert "categories" in subscription and "interval_minutes" in subscription
        assert subscription["mode"]["enum"] == ["review_only", "app_queue", "automatic_download"]
        preferences = definitions["Preferences"]["properties"]
        assert {"max_size", "preferred", "priorities", "min_seeders"} <= preferences.keys()


def test_inbox_marks_releases_already_in_clients_without_resolving_works(tmp_path, monkeypatch):
    import time
    from pathlib import Path

    from torrentlinker import feed_control
    from torrentlinker.domain import Torrent

    app = create_app(tmp_path, password="test-password-long")
    with TestClient(app):
        control = app.state.agent_service.feed_control
        target = app.state.agent_service.for_client("default")
        target.observed = [Torrent("A" * 40, "Seeding", Path("/downloads"))]
        target.connection_error = ""
        target.feed_observed_at = time.time()
        monkeypatch.setattr(
            control.feeds, "inbox", lambda **_: [{"infohash": "a" * 40}, {"infohash": "b" * 40}]
        )

        def no_identity(*_):
            raise AssertionError("The inbox must not resolve every client torrent's work")

        monkeypatch.setattr(feed_control, "work_identity", no_identity)
        rows = control.inbox()

    assert [row["in_clients"] for row in rows] == [["default"], []]
    assert rows[0]["client_inventory_unknown"] == []
    assert not rows[1]["queued"]
