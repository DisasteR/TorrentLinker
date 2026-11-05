"""Persistent, exact release presentations with bounded read-only enrichment."""

from __future__ import annotations

import html
import json
import re
from dataclasses import asdict
from uuid import uuid4

from torrentlinker.assistant.images import image_url
from torrentlinker.catalogue_sync import SUBCATS
from torrentlinker.torrent_details import parse_details, preview_html, web_url
from torrentlinker.zen import ZenClass, ZenProfile, ZenTorrent, note, torrent_zen


def kind_of(row):
    category = row.get("category") or 0
    native = row.get("provenance", {}).get("search", {}).get("category")
    if isinstance(native, int) and native < 0:
        native = next((root for root, children in SUBCATS.items() if -native in children), None)
    if native in {5, 6}:
        return "game"
    if category == 8010:
        return "model3d"
    if category == 7030:
        return "comic"
    if category == 3030 or 7000 <= category < 8000:
        return "ebook"
    for lower, upper, kind in (
        (1000, 2000, "game"),
        (2000, 3000, "movie"),
        (3000, 4000, "music"),
        (4000, 5000, "software"),
        (5000, 6000, "series"),
        (6000, 7000, "adult"),
    ):
        if lower <= category < upper:
            return kind
    return "other"


class Presentations:
    def __init__(self, service):
        self.service = service
        self.access = service.access
        self.research_for_actor = lambda actor: None
        with self.access.lock, self.access.db:
            self.access.db.executescript(
                "CREATE TABLE IF NOT EXISTS selection_presentations (id TEXT PRIMARY KEY, "
                "actor TEXT, client TEXT, body TEXT);"
                "CREATE TABLE IF NOT EXISTS selection_presentation_items (presentation TEXT, "
                "item TEXT, body TEXT, PRIMARY KEY(presentation,item));"
            )

    def record(self, actor, identifier):
        with self.access.lock:
            row = self.access.db.execute(
                "SELECT * FROM selection_presentations WHERE id=?", (identifier,)
            ).fetchone()
            if row is None or (actor is not None and actor != row["actor"]):
                raise ValueError("Presentation not found for this connection.")
            self.service.target(row["actor"], row["client"])
            return row["actor"], row["client"], json.loads(row["body"])

    def create(
        self,
        actor,
        request_id,
        list_id="",
        expected_revision=0,
        selection_id="",
        enrichment_id="",
        offset=0,
        limit=12,
        research=False,
        title="",
        enrichment=False,
        max_rows=None,
    ):
        if sum(bool(s) for s in (list_id, selection_id, enrichment_id)) != 1:
            raise ValueError("Supply exactly one list revision, selection_id or enrichment_id.")
        if research and self.research_for_actor(actor) is None:
            raise ValueError("External metadata research is not enabled for this conversation.")
        intent = dict(
            tool="enrich_selection" if enrichment else "present_selection",
            list_id=list_id,
            revision=expected_revision,
            selection_id=selection_id,
            enrichment_id=enrichment_id,
            offset=offset,
            limit=limit,
            research=research,
            title=title,
        )
        if max_rows is not None:
            intent["max_rows"] = max_rows

        def work():
            if enrichment_id:
                _, client, original = self.record(actor, enrichment_id)
                if not original["enrichment"]:
                    raise ValueError("Use an enrichment_id returned by enrich_selection.")
                body = {**original, "enrichment": False, "title": title or original["title"]}
            else:
                if list_id:
                    if expected_revision < 1:
                        raise ValueError("Supply the exact saved list revision.")
                    lists = self.service.torrent_lists
                    client, label, rows = lists.snapshot(actor, list_id, expected_revision)
                else:
                    client, saved = self.service.selection(actor, selection_id, kind=None)
                    rows = [
                        {
                            **r,
                            "provenance": {
                                **r.get("provenance", {}),
                                "indexer_url": r.get("indexer_url")
                                or r.get("provenance", {}).get("indexer_url")
                                or saved.get("indexer_url", ""),
                            },
                        }
                        for r in saved["rows"]
                    ]
                    label = "Selection"
                if max_rows is not None and len(rows) > max_rows:
                    raise ValueError(
                        f"Deduplication requires at most {max_rows} candidate releases."
                    )
                setup = self.service.target(actor, client).setup
                body = dict(
                    rows=rows,
                    title=title or label,
                    list_id=list_id,
                    revision=expected_revision,
                    research=research,
                    enrichment=enrichment,
                    zen_profile=asdict(setup.settings.zen),
                )
            identifier = uuid4().hex
            self.access.db.execute(
                "INSERT INTO selection_presentations VALUES (?,?,?,?)",
                (identifier, actor, client, json.dumps(body)),
            )
            if enrichment_id:
                self.access.db.execute(
                    "INSERT INTO selection_presentation_items SELECT ?,item,body FROM "
                    "selection_presentation_items WHERE presentation=?",
                    (identifier, enrichment_id),
                )
            return {"id": identifier}

        saved = self.service.torrent_lists.write_request(actor, request_id, intent, work)
        page = self.page(actor, saved["id"], offset, limit)
        if enrichment:
            page["enrichment_id"] = page.pop("presentation_id")
        return page

    @staticmethod
    def basic(row, item_id):
        kind = kind_of(row)
        return dict(
            item_id=item_id,
            title=row.get("title", ""),
            kind=kind,
            category=row.get("native_category_label") or str(row.get("category") or ""),
            category_id=row.get("category"),
            size_bytes=row.get("size") if (row.get("size") or 0) > 0 else None,
            seeders=row.get("seeders")
            if row.get("seeders") is not None and row["seeders"] >= 0
            else None,
            image_url="",
            subtitle="",
            note="",
            source_url=web_url(row.get("website", "")),
            metadata_status="missing",
            artwork_status="missing",
            content_title="",
            content_identity=None,
            facts={},
            description_html="",
            description="",
            provenance=[],
        )

    def item(self, actor, identifier, item_id):
        owner, client, body = self.record(actor, identifier)
        if not re.fullmatch(r"0|[1-9][0-9]{0,4}", item_id) or int(item_id) >= len(body["rows"]):
            raise ValueError("Presentation item not found.")
        with self.access.lock:
            cached = self.access.db.execute(
                "SELECT body FROM selection_presentation_items WHERE presentation=? AND item=?",
                (identifier, item_id),
            ).fetchone()
        if cached:
            item = json.loads(cached[0])
            if body.get("presentation_type") == "upload_drafts":
                from torrentlinker.agent.upload_presentations import current_state

                return current_state(item, self.service.target(owner, client).setup.store)
            return item
        row = body["rows"][int(item_id)]
        result = self.basic(row, item_id)
        setup = self.service.target(owner, client).setup
        category = setup.settings.indexer_categories.get(row.get("category"))
        if category and not row.get("native_category_label"):
            result["category"] = category.name
        profile_data = body.get("zen_profile", {})
        profile = ZenProfile(**{**profile_data, "zen_class": ZenClass(profile_data["zen_class"])})
        assumptions = (
            f"Standalone estimate; class={profile.zen_class}; seed_days=0; "
            f"seed_hours/day={profile.seed_hours}; saved swarm +1; configured tree/badge "
            "bonuses; no current-client portfolio observation; not guaranteed payout."
        )
        result.update(torrent_note=None, estimated_zen_per_day=None, zen_assumptions=assumptions)
        if result["size_bytes"] is not None and result["seeders"] is not None:
            prospective = ZenTorrent(
                result["size_bytes"], result["seeders"] + 1, 0.0, row.get("category")
            )
            result["torrent_note"] = note(profile.zen_class, prospective)
            if profile.zen_class.paid_on_seeding:
                result["estimated_zen_per_day"] = torrent_zen(profile, prospective)
        try:
            indexer = setup.indexer()
            original_source = row.get("indexer_url") or row.get("provenance", {}).get("indexer_url")
            if not original_source or indexer.url != original_source:
                raise ValueError("Indexer changed; frozen release metadata was not fetched.")
            if not getattr(indexer, "is_c411", False):
                raise ValueError("Detailed metadata is unavailable for this Indexer.")
            identity = row.get("hash", "")
            if not re.fullmatch(r"[0-9a-fA-F]{40}", identity):
                raise ValueError("Release identity is unavailable.")
            payload = indexer.torrent_json(identity)
            observed = payload.get("infoHash") or payload.get("infohash")
            if observed and str(observed).lower() != identity.lower():
                raise ValueError("Indexer returned a different release identity.")
            if not isinstance(payload, dict):
                raise ValueError("Invalid Indexer details.")
            details = parse_details(payload)
            description = details.description[:20000]
            markup, _ = preview_html(description)
            markup = re.sub(r"<img\b[^>]*>", "", markup)
            result.update(
                description_html=markup,
                description=html.unescape(re.sub(r"<[^>]+>", " ", markup)),
                description_truncated=len(details.description) > 20000,
                source_url=f"https://c411.org/torrents/{identity.lower()}",
            )
            metadata = payload.get("metadata") or {}
            if not isinstance(metadata, dict):
                raise ValueError("Invalid Indexer metadata.")
            tmdb = metadata.get("tmdbData") or {}
            if not isinstance(tmdb, dict):
                raise ValueError("Invalid Indexer metadata.")
            content_id = tmdb.get("id") or tmdb.get("tmdbId")
            canonical = tmdb.get("title") or tmdb.get("name")
            if result["kind"] in {"movie", "series"} and content_id and canonical:
                result.update(
                    content_identity={"provider": "tmdb", "id": str(content_id)},
                    content_title=str(canonical)[:500],
                    metadata_status="verified",
                )
                for field in ("releaseDate", "firstAirDate", "genres", "runtime"):
                    if field in tmdb:
                        result["facts"][field] = str(tmdb[field])[:500]
            slots = {
                "software": ("iconUrl", "coverUrl"),
                "model3d": ("previewUrl",),
                "other": (),
            }.get(result["kind"], ("coverUrl", "posterUrl"))
            artwork = details.poster if result["kind"] in {"movie", "series"} else ""
            for slot in slots:
                artwork = artwork or metadata.get(slot) or payload.get(slot) or ""
            try:
                result["image_url"] = str(artwork) if artwork and image_url(str(artwork)) else ""
            except ValueError:
                result["image_url"] = ""
            if result["image_url"]:
                result["artwork_status"] = "release_metadata"
            result["provenance"] = [
                {"source": "c411", "release_hash": identity, "url": result["source_url"]}
            ]
            if body["research"]:
                self._research(owner, result)
        except Exception:
            result["note"] = "Metadata unavailable; exact saved release facts are shown."
        # Refreshes may race; the first complete immutable observation wins.
        with self.access.lock, self.access.db:
            self.access.db.execute(
                "INSERT OR IGNORE INTO selection_presentation_items VALUES (?,?,?)",
                (identifier, item_id, json.dumps(result)),
            )
            result = json.loads(
                self.access.db.execute(
                    "SELECT body FROM selection_presentation_items WHERE presentation=? AND item=?",
                    (identifier, item_id),
                ).fetchone()[0]
            )
        return result

    def _research(self, actor, item):
        research = self.research_for_actor(actor)
        if research is None:
            return
        identity = item["content_identity"]
        if not identity or item["kind"] not in {"movie", "series"}:
            return
        kind = "tv" if item["kind"] == "series" else "movie"
        response = research.lookup_metadata(item["content_title"], kind)
        matches = [s for s in response.get("sources", []) if str(s.get("id")) == identity["id"]]
        if len(matches) != 1:
            item["metadata_status"] = "ambiguous" if len(matches) > 1 else "missing"
            return
        source = matches[0]
        item["provenance"].append({"source": "tmdb", "url": web_url(source.get("url", ""))})
        if not item["image_url"] and source.get("image"):
            item["image_url"] = source["image"] if image_url(source["image"]) else ""
            item["artwork_status"] = "verified"

    def page(self, actor, identifier, offset=0, limit=12):
        if offset < 0 or not 1 <= limit <= 20:
            raise ValueError("Use offset >= 0 and page limit from 1 to 20.")
        _, client, body = self.record(actor, identifier)
        rows = body["rows"]
        items = [
            self.item(actor, identifier, str(i))
            for i in range(offset, min(len(rows), offset + limit))
        ]
        # Keep full descriptions out of model receipts and page responses.
        items = [
            {
                k: v
                for k, v in item.items()
                if k
                not in {
                    "description",
                    "description_html",
                    "description_images",
                    "nfo",
                    "files",
                    "upload_form",
                    "upload_review",
                }
            }
            for item in items
        ]
        end = offset + len(items)
        return dict(
            presentation_id=identifier,
            presentation_type=body.get("presentation_type", "selection"),
            title=body["title"],
            list_id=body["list_id"],
            revision=body["revision"],
            client_id=client,
            count=len(rows),
            items=items,
            offset=offset,
            has_more=end < len(rows),
            next_offset=end if end < len(rows) else None,
            untrusted_source=True,
        )
