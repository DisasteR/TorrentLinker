"""Films and series sent to the Torrent Client can download in order, to play early."""

from pathlib import Path

from fastapi.testclient import TestClient

from tests.test_deluge_unit import FakeWebUi
from tests.test_deluge_unit import api_for as deluge_for
from tests.test_media_api import login, media_app
from tests.test_transmission_unit import FakeRpc
from tests.test_transmission_unit import api_for as transmission_for
from tests.torrents import make_torrent
from torrentlinker.config import Settings
from torrentlinker.paths import MappedQbit, PathMapper, is_video
from torrentlinker.qbit import QbitApi


def film(tmp_path: Path) -> bytes:
    root = tmp_path / "Film.2026"
    root.mkdir()
    (root / "Film.2026.mkv").write_bytes(b"v" * 4000)
    (root / "Film.2026.nfo").write_text("notes")
    return make_torrent(root)


def book(tmp_path: Path) -> bytes:
    (tmp_path / "book.epub").write_bytes(b"x" * 1000)
    return make_torrent(tmp_path / "book.epub")


class Inner:
    """A Torrent Client recording how each download was asked for."""

    supports_category_tags = False

    def __init__(self) -> None:
        self.calls: list[dict] = []

    def download(self, data: bytes, save_path: str, tags: list[str], **options) -> str:
        self.calls.append(options)
        return "hash"


def test_only_videos_download_in_order_and_only_when_turned_on(tmp_path: Path) -> None:
    video, other = film(tmp_path), book(tmp_path)
    assert is_video(video) and not is_video(other)
    inner = Inner()
    on = MappedQbit(inner, PathMapper([]), sequential_video=True)  # type: ignore[arg-type]
    on.download(video, "/dl", [])
    on.download(other, "/dl", [])
    MappedQbit(inner, PathMapper([])).download(video, "/dl", [])  # type: ignore[arg-type]
    assert inner.calls == [{"sequential": True}, {}, {}]


class Recording:
    def __init__(self) -> None:
        self.added: list[dict] = []

    def torrents_add(self, **kwargs: object) -> str:
        self.added.append(kwargs)
        return "Ok."

    def torrents_info(self, torrent_hashes: str) -> list[object]:
        return [object()]


def test_qbittorrent_gets_sequential_and_first_last_pieces(tmp_path: Path) -> None:
    api = QbitApi("http://127.0.0.1:1", "", "")
    api._client = recording = Recording()  # type: ignore[assignment]
    api.download(film(tmp_path), "/dl", [], sequential=True)
    api.download(book(tmp_path), "/dl", [])
    first, second = recording.added
    assert first["is_sequential_download"] and first["is_first_last_piece_priority"]
    assert "is_sequential_download" not in second


def test_deluge_gets_sequential_and_first_last_pieces(tmp_path: Path) -> None:
    web = FakeWebUi()
    api = deluge_for(web)
    h = api.download(film(tmp_path), "/dl", [], sequential=True)
    options = web.torrents[h]["options"]
    assert options["sequential_download"] and options["prioritize_first_last_pieces"]
    other = api.download(book(tmp_path), "/dl", [])
    assert "sequential_download" not in web.torrents[other]["options"]


def test_transmission_gets_sequential_download(tmp_path: Path, monkeypatch) -> None:
    rpc = FakeRpc()
    api = transmission_for(rpc)
    sent: list[dict] = []
    call = api._call
    monkeypatch.setattr(api, "_call", lambda method, **kw: sent.append(kw) or call(method, **kw))
    api.download(film(tmp_path), "/dl", [], sequential=True)
    api.download(book(tmp_path), "/dl", [])
    adds = [kw for kw in sent if "metainfo" in kw]
    assert adds[0]["sequential_download"] is True
    assert "sequential_download" not in adds[1]


def test_the_choice_is_saved_and_off_by_default(tmp_path: Path) -> None:
    path = tmp_path / "settings.json"
    settings = Settings()
    assert settings.sequential_video is False
    settings.sequential_video = True
    settings.save(path)
    assert Settings.load(path).sequential_video is True


def test_the_web_ui_turns_it_on_for_every_client(tmp_path: Path) -> None:
    app, _root = media_app(tmp_path)
    with TestClient(app) as browser:
        login(browser)
        assert browser.get("/api/setup").json()["sequential_video"] is False
        response = browser.post("/api/setup/sequential-video", json={"enabled": True})
        assert response.is_success, response.text
        assert browser.get("/api/setup").json()["sequential_video"] is True
    assert Settings.load(tmp_path / "config/settings.json").sequential_video is True
