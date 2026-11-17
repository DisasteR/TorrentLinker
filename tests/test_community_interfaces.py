import os
from copy import deepcopy
from pathlib import Path

from fastapi.testclient import TestClient
from PySide6.QtCore import QDateTime

from tests.test_community import H, observation
from tests.test_web_jobs import login
from tests.test_web_profile import ORIGIN, OWNER, prepared
from torrentlinker.community import DEFAULTS
from torrentlinker.gui.community_view import CommunityView


def ready(tmp_path, monkeypatch):
    app, setup = prepared(tmp_path, monkeypatch)
    w = setup.profile_workspace()
    w.refresh(setup.settings.indexer.url, "fake-key")
    w.community.observe("42", observation())
    return app, setup, w


def test_native_editor_settings_pause_and_suggestions(qtbot, tmp_path, monkeypatch):
    _, _, w = ready(tmp_path, monkeypatch)
    view = CommunityView(w)
    qtbot.addWidget(view)
    view.resize(1100, 900)
    view.show()
    view.subject.setText(H)
    view.content.setPlainText("Merci pour ce partage bien utile")
    view.due.setDateTime(QDateTime.currentDateTime().addSecs(3600))
    view.save_draft()
    assert view.table.rowCount() == 1
    view.table.selectRow(0)
    view.control("queue")
    assert w.community_state()["items"][0]["state"] == "queued"
    view.control("edit")
    assert w.community_state()["items"][0]["state"] == "paused"
    view.content.setPlainText("Le commentaire a été modifié")
    view.save_draft()
    assert w.community_state()["items"][0]["state"] == "draft"
    view.limits["comment"].setValue(2)
    view.intervals["chat"].setValue(1200)
    view.templates["comment"].setPlainText("Un modèle de commentaire personnalisé")
    view.enabled.setChecked(True)
    view.save_settings()
    assert w.community_state()["settings"]["limits"]["comment"] == 2
    assert w.community_state()["settings"]["intervals"]["chat"] == 1200
    view.toggle_queue()
    assert not w.community_state()["settings"]["enabled"]
    view.kind.setCurrentIndex(view.kind.findData("chat"))
    view.suggestions.setCurrentIndex(1)
    view.choose_suggestion()
    assert len(view.content.toPlainText().split()) >= 5
    # Background refresh never replaces text the owner is typing.
    view.content.setPlainText("Un brouillon encore en cours de rédaction")
    view.refresh()
    assert view.content.toPlainText() == "Un brouillon encore en cours de rédaction"
    target = Path(os.getenv("TL_COMMUNITY_SCREENSHOTS", str(tmp_path)))
    target.mkdir(parents=True, exist_ok=True)
    assert view.grab().save(str(target / "tl-community-desktop.png"))


def test_web_owner_and_extension_protocol_share_journal(tmp_path, monkeypatch):
    app, setup, w = ready(tmp_path, monkeypatch)
    with TestClient(app) as c:
        assert c.get("/api/community").status_code == 401
        login(c)
        assert (
            c.post(
                "/api/community/settings", headers={"X-TorrentLinker": ""}, json=DEFAULTS
            ).status_code
            == 403
        )
        assert (
            c.post(
                "/api/community/save",
                headers=OWNER,
                json={
                    "kind": "comment",
                    "subject": H,
                    "content": "Merci pour ce partage bien utile",
                },
            ).status_code
            == 200
        )
        item = c.get("/api/community").json()["items"][0]
        assert w.community_state()["items"][0]["id"] == item["id"]
        c.post(
            "/api/community/control",
            headers=OWNER,
            json={
                "id": item["id"],
                "revision": item["revision"],
                "action": "queue",
            },
        ).raise_for_status()
        c.post(
            "/api/community/settings", headers=OWNER, json={**deepcopy(DEFAULTS), "enabled": True}
        ).raise_for_status()
        ext = {"Origin": ORIGIN}
        code = w.pairing()["code"]
        token = c.post(
            "/api/companion/pair", headers=ext, json={"code": code, "accountId": "42"}
        ).json()["token"]
        ext["Authorization"] = "Bearer " + token
        assert (
            c.post(
                "/api/companion/actions-claim", headers=ext, json={"accountId": "other"}
            ).status_code
            == 403
        )
        response = c.post("/api/companion/actions-claim", headers=ext, json={"accountId": "42"})
        command = response.json()["command"]
        assert command["content"] == "Merci pour ce partage bien utile"
        assert (
            c.post("/api/companion/actions-claim", headers=ext, json={"accountId": "42"}).json()[
                "command"
            ]
            is None
        )
        body = {**command, "outcome": "posted", "remoteId": "123"}
        assert c.post("/api/companion/actions-receipt", headers=ext, json=body).status_code == 200
        assert c.post("/api/companion/actions-receipt", headers=ext, json=body).status_code == 200
        assert c.get("/api/community").json()["items"][0]["state"] == "posted"
        assert len(c.get("/api/community/messages").json()["entries"]) >= 180
        w.bind(setup.settings.indexer.url, "rotated-key")
        w.refresh(setup.settings.indexer.url, "rotated-key")
        assert w.community_state()["items"][0]["state"] == "posted"
        assert not w.community_state()["paired"]
