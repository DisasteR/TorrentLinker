"""Authenticated media adapters. Playback bytes remain under existing storage roots."""

from __future__ import annotations

from typing import Any, Literal

from fastapi import HTTPException, Query, Request, Response
from fastapi.responses import FileResponse, StreamingResponse
from pydantic import BaseModel, ConfigDict, Field


class MediaClient(BaseModel):
    model_config = ConfigDict(extra="forbid")
    client_id: str = ""


class SessionOpen(MediaClient):
    platform: Literal["web", "desktop"] = "web"


class SessionUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    token: str = Field(max_length=100)
    report: dict[str, Any] | None = None
    action: str = Field(default="", max_length=30)
    media_ids: list[str] | None = Field(default=None, max_length=1000)
    index: int | None = Field(default=None, ge=0, le=1000)
    position: float | None = Field(default=None, ge=0, le=8640000)
    volume: float | None = Field(default=None, ge=0, le=1)
    shuffle: bool | None = None
    repeat: Literal["off", "one", "all"] | None = None
    context: dict[str, Any] | None = None
    command_ack: int | None = Field(default=None, ge=0)
    kind: Literal["audio", "subtitle"] | None = None
    track_id: str | None = Field(default=None, max_length=128)
    source_token: str | None = Field(default=None, max_length=128)


class PlaylistSave(MediaClient):
    id: str = ""
    name: str = Field(min_length=1, max_length=100)
    media_ids: list[str] = Field(max_length=1000)


class Watched(MediaClient):
    watched: bool


class MediaChat(BaseModel):
    token: str = Field(max_length=100)
    conversation_id: str = Field(default="", max_length=64)
    new: bool = False


def media_routes(app, agent, assistant):
    media = agent.media

    def client(value):
        return value or agent.setup.settings.default_client_id

    @app.get("/api/media")
    def listing(
        client_id: str = "",
        kind: str = "",
        q: str = "",
        view: str = "all",
        genre: str = "",
        year: str = "",
        language: str = "",
        availability: str = "",
        album_id: str = "",
        artist: str = "",
        offset: int = Query(0, ge=0),
        limit: int = Query(100, ge=1, le=200),
        adult: bool = False,
        anime: bool | None = None,
        oav: bool | None = None,
    ):
        return media.listing(
            client(client_id),
            kind=kind,
            q=q,
            view=view,
            genre=genre,
            year=year,
            language=language,
            availability=availability,
            offset=offset,
            limit=limit,
            album_id=album_id,
            artist=artist,
            adult=adult,
            anime=anime,
            oav=oav,
        )

    @app.get("/api/media/home")
    def home(
        client_id: str = "",
        limit: int = Query(20, ge=1, le=50),
        scope: str = Query("home", pattern="^(home|films|series|anime|adult)$"),
    ):
        return media.home_rows(client(client_id), kind="video", limit=limit, scope=scope)

    @app.post("/api/media/scan", status_code=202)
    def scan(body: MediaClient):
        target = client(body.client_id)
        media.setup(target)
        return {
            "id": agent.jobs.submit(
                "media-scan",
                lambda stop, progress: media.scan(target, stop, progress),
                client_id=target,
            )
        }

    @app.get("/api/media/items/{identifier}")
    def detail(
        identifier: str,
        client_id: str = "",
        episodes_offset: int = Query(0, ge=0),
        releases_offset: int = Query(0, ge=0),
        limit: int = Query(200, ge=1, le=200),
    ):
        return media.detail(
            client(client_id),
            identifier,
            episodes_offset=episodes_offset,
            releases_offset=releases_offset,
            limit=limit,
        )

    @app.api_route("/api/media/items/{identifier}/stream", methods=["GET", "HEAD"])
    def stream(identifier: str, request: Request, client_id: str = ""):
        try:
            path = media.resolve(client(client_id), identifier)
        except (ValueError, OSError) as error:
            raise HTTPException(
                409, "Media is unavailable or unfinished. Refresh its details."
            ) from error
        # Starlette validates Range and serves 206/416 without loading the file into memory.
        import mimetypes

        return FileResponse(
            path,
            media_type=mimetypes.guess_type(path.name)[0] or "application/octet-stream",
            headers={"Accept-Ranges": "bytes"},
        )

    @app.post("/api/media/items/{identifier}/preview")
    def create_preview(identifier: str, body: MediaClient):
        return media.create_preview(client(body.client_id), identifier)

    @app.delete("/api/media/previews/{preview_id}")
    def close_preview(preview_id: str, client_id: str = ""):
        return media.close_preview(client(client_id), preview_id)

    @app.api_route("/api/media/previews/{preview_id}/stream", methods=["GET", "HEAD"])
    def preview_stream(preview_id: str, request: Request, client_id: str = ""):
        import mimetypes
        import re

        try:
            path, size = media.preview_source(client(client_id), preview_id)
        except (ValueError, OSError) as error:
            raise HTTPException(
                409, str(error) if isinstance(error, ValueError) else "Preview source unavailable."
            ) from error
        start, end, status = 0, size - 1, 200
        requested = request.headers.get("range", "")
        if requested:
            match = re.fullmatch(r"bytes=(\d*)-(\d*)", requested)
            if not match or not any(match.groups()):
                return Response(status_code=416, headers={"Content-Range": f"bytes */{size}"})
            if match[1]:
                start = int(match[1])
                end = min(int(match[2]), size - 1) if match[2] else size - 1
            else:
                start = max(0, size - int(match[2]))
            if start > end or start >= size:
                return Response(status_code=416, headers={"Content-Range": f"bytes */{size}"})
            status = 206
        headers = {
            "Accept-Ranges": "bytes",
            "Content-Length": str(end - start + 1),
            "X-Media-Preview": "verified-fixed-prefix",
        }
        if status == 206:
            headers["Content-Range"] = f"bytes {start}-{end}/{size}"
        if request.method == "HEAD":
            return Response(
                status_code=status,
                headers=headers,
                media_type=mimetypes.guess_type(
                    path.stem if path.suffix.lower() == ".!qb" else path.name
                )[0]
                or "application/octet-stream",
            )

        def content():
            with path.open("rb") as source:
                source.seek(start)
                remaining = end - start + 1
                while remaining:
                    block = source.read(min(65536, remaining))
                    if not block:
                        break
                    remaining -= len(block)
                    yield block

        return StreamingResponse(
            content(),
            status_code=status,
            headers=headers,
            media_type=mimetypes.guess_type(
                path.stem if path.suffix.lower() == ".!qb" else path.name
            )[0]
            or "application/octet-stream",
        )

    @app.get("/api/media/items/{identifier}/artwork")
    def artwork(identifier: str, client_id: str = ""):
        try:
            path = media.artwork(client(client_id), identifier)
            if path is None:
                raise ValueError("Artwork unavailable.")
            return FileResponse(path)
        except (OSError, ValueError) as error:
            raise HTTPException(404, "Artwork unavailable.") from error

    @app.get("/api/media/items/{identifier}/subtitles/{subtitle_id}")
    def subtitles(identifier: str, subtitle_id: str, client_id: str = ""):
        try:
            data, mime = media.subtitle(client(client_id), identifier, subtitle_id)
            return Response(data, media_type=mime)
        except (OSError, ValueError) as error:
            raise HTTPException(404, "Subtitle unavailable.") from error

    @app.post("/api/media/items/{identifier}/watched")
    def watched(identifier: str, body: Watched):
        return media.watched(client(body.client_id), identifier, body.watched)

    @app.get("/api/media/playlists")
    def playlists(client_id: str = ""):
        return media.playlists(client(client_id))

    @app.post("/api/media/playlists")
    def save_playlist(body: PlaylistSave):
        return media.save_playlist(client(body.client_id), body.name, body.media_ids, body.id)

    @app.delete("/api/media/playlists/{identifier}")
    def delete_playlist(identifier: str, client_id: str = ""):
        return media.delete_playlist(client(client_id), identifier)

    @app.post("/api/media/sessions")
    def open_session(body: SessionOpen):
        return media.open_session(client(body.client_id), body.platform)

    @app.post("/api/media/sessions/{identifier}")
    def session(identifier: str, body: SessionUpdate):
        return media.session(identifier, **body.model_dump(exclude_none=True))

    @app.post("/api/media/sessions/{identifier}/chat")
    def chat(identifier: str, body: MediaChat):
        return assistant.media_chat(identifier, body.token, body.conversation_id, new=body.new)
