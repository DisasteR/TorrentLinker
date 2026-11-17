"""Durable owner-authored community actions, shared by desktop and WebUI.

Only the paired browser executes c411 writes. A transaction reserves each attempt
before dispatch; ambiguous results are never automatically sent a second time.
"""

from __future__ import annotations

import html
import json
import math
import os
import re
import secrets
import sqlite3
import time
import unicodedata
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Any

KINDS = {"comment": "comment_post", "vote": "trust_vote", "chat": "shout_message"}
EDITABLE = {"draft", "queued", "paused", "refused", "cancelled"}
DEFAULTS = {
    "enabled": False,
    "limits": {"comment": 5, "vote": 5, "chat": 5},
    "intervals": {"comment": 60, "vote": 60, "chat": 600},
    "templates": {"comment": "", "chat": ""},
}
MAX_ITEMS = 500


def normalized(text: str) -> str:
    plain = html.unescape(re.sub(r"<[^>]*>", " ", text))
    plain = "".join(c for c in unicodedata.normalize("NFKD", plain) if not unicodedata.combining(c))
    return " ".join(re.findall(r"[^\W_]+", plain.casefold()))


def bounded(value: Any, low: int, high: int) -> int:
    if type(value) is not int or not low <= value <= high:
        raise ValueError(f"Use a whole number from {low} to {high}.")
    return value


def message_library() -> list[dict[str, Any]]:
    path = Path(__file__).parent / "assets" / "community_messages_fr.json"
    return json.loads(path.read_text(encoding="utf-8"))["entries"] if path.exists() else []


class CommunityActions:
    def __init__(self, directory: Path, clock: Callable[[], float] = time.time):
        self.path = directory / "community.sqlite3"
        self.clock = clock
        directory.mkdir(parents=True, exist_ok=True)
        fd = os.open(self.path, os.O_CREAT | os.O_RDWR, 0o600)
        os.close(fd)
        with self._db() as db:
            db.executescript("""
                CREATE TABLE IF NOT EXISTS accounts (
                    account TEXT PRIMARY KEY, settings TEXT NOT NULL,
                    snapshot TEXT NOT NULL DEFAULT '{}', synced REAL NOT NULL DEFAULT 0,
                    retry_at REAL NOT NULL DEFAULT 0, page INTEGER NOT NULL DEFAULT 1,
                    refresh INTEGER NOT NULL DEFAULT 0);
                CREATE TABLE IF NOT EXISTS actions (
                    id TEXT PRIMARY KEY, account TEXT NOT NULL, kind TEXT NOT NULL,
                    subject TEXT NOT NULL, payload TEXT NOT NULL, state TEXT NOT NULL,
                    due REAL NOT NULL, created REAL NOT NULL, position REAL NOT NULL,
                    revision INTEGER NOT NULL DEFAULT 1, attempt TEXT, started REAL,
                    posted REAL, remote_id TEXT, note TEXT NOT NULL DEFAULT '',
                    reward TEXT NOT NULL DEFAULT 'unknown');
                CREATE INDEX IF NOT EXISTS action_account ON actions(account, state, due);
                CREATE TABLE IF NOT EXISTS candidates (
                    account TEXT NOT NULL, hash TEXT NOT NULL, title TEXT NOT NULL,
                    seen REAL NOT NULL, PRIMARY KEY(account,hash));
            """)

    @contextmanager
    def _db(self) -> Iterator[sqlite3.Connection]:
        db = sqlite3.connect(self.path, timeout=10)
        db.row_factory = sqlite3.Row
        try:
            db.execute("BEGIN IMMEDIATE")
            yield db
            db.commit()
        except BaseException:
            db.rollback()
            raise
        finally:
            db.close()

    def _account(self, db: sqlite3.Connection, account: str) -> sqlite3.Row:
        if not account or len(account) > 160:
            raise ValueError("Refresh the c411 profile first.")
        db.execute(
            "INSERT OR IGNORE INTO accounts(account,settings) VALUES (?,?)",
            (account, json.dumps(DEFAULTS)),
        )
        row = db.execute("SELECT * FROM accounts WHERE account=?", (account,)).fetchone()
        assert row is not None
        return row

    def _expire(self, db: sqlite3.Connection, account: str) -> None:
        db.execute(
            """UPDATE actions SET state='uncertain', note='No receipt. Check c411; no retry.'
                      WHERE account=? AND state='sending' AND started<?""",
            (account, self.clock() - 180),
        )

    @staticmethod
    def _item(row: sqlite3.Row) -> dict[str, Any]:
        result = dict(row)
        result.pop("account")
        result.update(json.loads(result.pop("payload")))
        if result["reward"] == "pending_24h" and result["posted"]:
            result["reward_due"] = result["posted"] + 86400
        return result

    def state(self, account: str) -> dict[str, Any]:
        with self._db() as db:
            row = self._account(db, account)
            self._expire(db, account)
            items = [
                self._item(r)
                for r in db.execute(
                    "SELECT * FROM actions WHERE account=? ORDER BY position,created", (account,)
                )
            ]
            snapshot = json.loads(row["snapshot"])
            settings = json.loads(row["settings"])
            quotas = {}
            for kind in KINDS:
                used = sum(
                    1
                    for i in items
                    if i["kind"] == kind
                    and i["state"] in ("sending", "uncertain", "posted")
                    and (i["started"] or 0) > self.clock() - 86400
                )
                rule = snapshot.get("rules", {}).get(kind, {})
                quotas[kind] = {
                    "local_used": used,
                    "site_used": rule.get("today"),
                    "limit": settings["limits"][kind],
                }
            for item in items:
                if item["state"] != "queued":
                    continue
                kind = item["kind"]
                rule = snapshot.get("rules", {}).get(kind, {})
                cap = min(settings["limits"][kind], rule.get("limit", 0))
                recent = sorted(
                    i["started"]
                    for i in items
                    if i["kind"] == kind
                    and i["state"] in ("sending", "uncertain", "posted")
                    and (i["started"] or 0) > self.clock() - 86400
                )
                next_at = max(item["due"], row["retry_at"])
                if recent:
                    next_at = max(
                        next_at,
                        recent[-1] + max(settings["intervals"][kind], rule.get("cooldown", 0)),
                    )
                if cap and len(recent) >= cap:
                    next_at = max(next_at, recent[-cap] + 86400)
                item["next_at"] = next_at
                item["wait_reason"] = (
                    "Queue paused"
                    if not settings["enabled"]
                    else "Action disabled"
                    if not settings["limits"][kind]
                    else "Waiting for fresh site limits"
                    if self.clock() - row["synced"] > 90
                    else "Site permission required"
                    if rule.get("locked", True)
                    else "Site daily limit reached"
                    if rule.get("today", 0) >= cap
                    else ""
                )
            return {
                "account_id": account,
                "settings": settings,
                "items": items,
                "candidates": [
                    dict(r)
                    for r in db.execute(
                        "SELECT hash,title,seen FROM candidates WHERE account=? "
                        "ORDER BY seen DESC,title LIMIT 500",
                        (account,),
                    )
                ],
                "channels": snapshot.get("channels", []),
                "quotas": quotas,
                "synced_at": row["synced"],
                "retry_at": row["retry_at"],
                "page": row["page"],
                "refresh_pending": bool(row["refresh"]),
                "now": self.clock(),
            }

    def settings(self, account: str, values: dict[str, Any]) -> None:
        if set(values) != set(DEFAULTS) or type(values["enabled"]) is not bool:
            raise ValueError("Invalid community settings.")
        for field in ("limits", "intervals"):
            if not isinstance(values[field], dict) or set(values[field]) != set(KINDS):
                raise ValueError("Set a separate limit and interval for each action.")
            for value in values[field].values():
                bounded(value, 0 if field == "limits" else 30, 5 if field == "limits" else 86400)
        if not isinstance(values["templates"], dict) or set(values["templates"]) != {
            "comment",
            "chat",
        }:
            raise ValueError("Invalid templates.")
        if any(not isinstance(t, str) or len(t) > 500 for t in values["templates"].values()):
            raise ValueError("Templates must contain at most 500 characters.")
        with self._db() as db:
            self._account(db, account)
            db.execute(
                "UPDATE accounts SET settings=? WHERE account=?", (json.dumps(values), account)
            )

    def refresh(self, account: str, page: int = 1) -> None:
        bounded(page, 1, 100000)
        with self._db() as db:
            self._account(db, account)
            db.execute("UPDATE accounts SET page=?,refresh=1 WHERE account=?", (page, account))

    def demand(self, account: str) -> dict[str, Any]:
        with self._db() as db:
            row = self._account(db, account)
            settings = json.loads(row["settings"])
            return {
                "collect": bool(row["refresh"]) or settings["enabled"],
                "page": row["page"],
                "retry_at": row["retry_at"],
            }

    def observe(self, account: str, data: dict[str, Any]) -> None:
        # Data returned by the extension is deliberately much narrower than c411's bodies.
        if set(data) != {"rules", "channels", "candidates", "page"}:
            raise ValueError("Invalid community snapshot.")
        rules = data["rules"]
        if not isinstance(rules, dict) or set(rules) != set(KINDS):
            raise ValueError("Missing action rules.")
        clean_rules = {}
        for kind, rule in rules.items():
            if not isinstance(rule, dict):
                raise ValueError("Invalid action rule.")
            clean_rules[kind] = {
                "today": bounded(rule.get("today"), 0, 1000000),
                "limit": bounded(rule.get("limit"), 0, 5),
                "cooldown": bounded(rule.get("cooldown"), 0, 86400),
                "locked": rule.get("locked") is not False,
            }
        channels = data["channels"]
        candidates = data["candidates"]
        if not isinstance(channels, list) or len(channels) > 20:
            raise ValueError("Invalid channels.")
        clean_channels = []
        for c in channels:
            if not isinstance(c, dict) or c.get("kind") not in ("general", "class"):
                raise ValueError("Only general and class channels are supported.")
            clean_channels.append(
                {
                    "id": str(bounded(c.get("id"), 1, 2147483647)),
                    "name": str(c.get("name", ""))[:160],
                    "kind": c["kind"],
                }
            )
        if not isinstance(candidates, list) or len(candidates) > 50:
            raise ValueError("Read at most 50 seeds at a time.")
        clean_candidates = []
        for c in candidates:
            if not isinstance(c, dict) or not re.fullmatch(r"[a-f0-9]{40}", c.get("hash", "")):
                raise ValueError("Invalid torrent hash.")
            clean_candidates.append(
                (account, c["hash"], str(c.get("title", ""))[:300], self.clock())
            )
        page = bounded(data["page"], 1, 100000)
        with self._db() as db:
            row = self._account(db, account)
            db.execute(
                "UPDATE accounts SET snapshot=?,synced=?,refresh=? WHERE account=?",
                (
                    json.dumps({"rules": clean_rules, "channels": clean_channels}),
                    self.clock(),
                    int(page != row["page"]),
                    account,
                ),
            )
            db.executemany(
                "INSERT INTO candidates VALUES (?,?,?,?) ON CONFLICT(account,hash) "
                "DO UPDATE SET title=excluded.title,seen=excluded.seen",
                clean_candidates,
            )

    def save(self, account: str, data: dict[str, Any]) -> str:
        kind = data.get("kind")
        if kind not in KINDS:
            raise ValueError("Choose a comment, vote or chat message.")
        subject = data.get("subject", "")
        if not isinstance(subject, str) or not re.fullmatch(
            r"[1-9][0-9]{0,9}" if kind == "chat" else r"[a-fA-F0-9]{40}", subject
        ):
            raise ValueError("Choose a channel or enter a torrent's 40-character info hash.")
        subject = subject.lower()
        content = data.get("content", "")
        vote = data.get("vote", "")
        if not isinstance(content, str) or len(content) > 500:
            raise ValueError("Use at most 500 characters.")
        content = content.strip()
        if kind != "vote" and len(content) < 3:
            raise ValueError("Write the message before saving.")
        if kind == "chat" and len(content.split()) < 5:
            raise ValueError("Chat messages require at least five words.")
        if kind == "vote" and (
            vote not in ("approve", "disapprove") or (vote == "disapprove" and len(content) < 5)
        ):
            raise ValueError(
                "Choose your vote; disapproval needs a reason of at least five characters."
            )
        due = data.get("due", self.clock())
        if type(due) not in (int, float) or not math.isfinite(due) or not 0 <= due <= 32503680000:
            raise ValueError("Invalid scheduled time.")
        identifier = data.get("id") or secrets.token_hex(16)
        if not isinstance(identifier, str) or not re.fullmatch(r"[a-f0-9]{32}", identifier):
            raise ValueError("Invalid draft ID.")
        with self._db() as db:
            self._account(db, account)
            old = db.execute(
                "SELECT * FROM actions WHERE id=? AND account=?", (identifier, account)
            ).fetchone()
            if data.get("id") and old is None:
                raise ValueError("Draft not found.")
            if old and (old["state"] not in EDITABLE or data.get("revision") != old["revision"]):
                raise ValueError("Draft changed or already sent. Reload it before editing.")
            for other in db.execute(
                "SELECT * FROM actions WHERE account=? AND id<>? AND state<>'cancelled'",
                (account, identifier),
            ):
                if other["kind"] == kind and (
                    (kind != "chat" and other["subject"] == subject)
                    or (
                        kind == "chat"
                        and normalized(json.loads(other["payload"])["content"])
                        == normalized(content)
                    )
                ):
                    raise ValueError(
                        "This torrent action or chat message is already in the journal."
                    )
            payload = json.dumps(
                {
                    "content": content,
                    "vote": vote if kind == "vote" else "",
                    "title": str(data.get("title", subject))[:300],
                }
            )
            if old:
                # Editing always returns the item to draft, requiring Queue again.
                db.execute(
                    "UPDATE actions SET kind=?,subject=?,payload=?,due=?,state='draft',"
                    "revision=revision+1,note='' WHERE id=?",
                    (kind, subject, payload, due, identifier),
                )
            else:
                count = db.execute(
                    "SELECT count(*) FROM actions WHERE account=? AND state IN "
                    "('draft','queued','paused','sending')",
                    (account,),
                ).fetchone()[0]
                if count >= MAX_ITEMS:
                    raise ValueError("The waitlist is full. Cancel or finish older drafts first.")
                db.execute(
                    "INSERT INTO actions(id,account,kind,subject,payload,state,"
                    "due,created,position) "
                    "VALUES (?,?,?,?,?,'draft',?,?,?)",
                    (identifier, account, kind, subject, payload, due, self.clock(), self.clock()),
                )
        return identifier

    def control(self, account: str, identifier: str, action: str, revision: int) -> None:
        with self._db() as db:
            row = db.execute(
                "SELECT * FROM actions WHERE id=? AND account=?", (identifier, account)
            ).fetchone()
            if row is None or row["revision"] != revision:
                raise ValueError("Item changed. Reload the waitlist.")
            if row["state"] not in EDITABLE:
                raise ValueError("Already sending or sent. Check c411 for uncertain results.")
            states = {"queue": "queued", "pause": "paused", "cancel": "cancelled"}
            if action == "queue":
                for other in db.execute(
                    "SELECT * FROM actions WHERE account=? AND kind=? "
                    "AND id<>? AND state<>'cancelled'",
                    (account, row["kind"], identifier),
                ):
                    same = (
                        other["subject"] == row["subject"]
                        if row["kind"] != "chat"
                        else normalized(json.loads(other["payload"])["content"])
                        == normalized(json.loads(row["payload"])["content"])
                    )
                    if same:
                        raise ValueError("This action is already in the journal.")
            if action in states:
                db.execute(
                    "UPDATE actions SET state=?,revision=revision+1 WHERE id=?",
                    (states[action], identifier),
                )
            elif action == "first":
                first = db.execute(
                    "SELECT min(position) FROM actions WHERE account=?", (account,)
                ).fetchone()[0]
                db.execute(
                    "UPDATE actions SET position=?,revision=revision+1 WHERE id=?",
                    (first - 1, identifier),
                )
            else:
                raise ValueError("Unknown queue action.")

    def claim(self, account: str) -> dict[str, Any] | None:
        now = self.clock()
        with self._db() as db:
            row = self._account(db, account)
            self._expire(db, account)
            settings = json.loads(row["settings"])
            if not settings["enabled"] or now < row["retry_at"] or now - row["synced"] > 90:
                return None
            if db.execute(
                "SELECT 1 FROM actions WHERE account=? AND state='sending'", (account,)
            ).fetchone():
                return None
            rules = json.loads(row["snapshot"]).get("rules", {})
            for job in db.execute(
                "SELECT * FROM actions WHERE account=? AND state='queued' AND due<=? "
                "ORDER BY position,created",
                (account, now),
            ).fetchall():
                kind = job["kind"]
                rule = rules.get(kind)
                if not rule or rule["locked"]:
                    continue
                cap = min(settings["limits"][kind], rule["limit"])
                recent = db.execute(
                    "SELECT started FROM actions WHERE account=? AND kind=? "
                    "AND state IN ('sending','uncertain','posted') AND started>? "
                    "ORDER BY started",
                    (account, kind, now - 86400),
                ).fetchall()
                interval = max(30, settings["intervals"][kind], rule["cooldown"])
                # A rolling window is deliberately conservative until c411 publishes its reset zone.
                if (
                    len(recent) >= cap
                    or rule["today"] + sum(r["started"] >= row["synced"] for r in recent) >= cap
                ):
                    continue
                if recent and now < recent[-1]["started"] + interval:
                    continue
                attempt = secrets.token_hex(16)
                db.execute(
                    "UPDATE actions SET state='sending',attempt=?,started=?,revision=revision+1 "
                    "WHERE id=?",
                    (attempt, now, job["id"]),
                )
                return {
                    "id": job["id"],
                    "attempt": attempt,
                    "accountId": account,
                    "kind": kind,
                    "subject": job["subject"],
                    "limit": cap,
                    "expires": now + 90,
                    **json.loads(job["payload"]),
                }
        return None

    def receipt(self, account: str, data: dict[str, Any]) -> None:
        result = data.get("outcome")
        if result not in ("posted", "refused", "uncertain", "deferred"):
            raise ValueError("Invalid action receipt.")
        with self._db() as db:
            row = db.execute(
                "SELECT * FROM actions WHERE account=? AND id=? AND attempt=?",
                (account, data.get("id"), data.get("attempt")),
            ).fetchone()
            if row is None:
                raise ValueError("Unknown posting attempt.")
            if row["state"] not in ("sending", "uncertain"):
                return  # Receipt acknowledgement retry, never another send.
            remote = data.get("remoteId")
            if result == "posted" and (not isinstance(remote, str) or not 0 < len(remote) <= 160):
                result = "uncertain"
            note = data.get("reason", "")
            # Never accept arbitrary site errors, HTML, credentials or traceback text.
            allowed = {
                "account",
                "auth",
                "permission",
                "duplicate",
                "quota",
                "cooldown",
                "network",
                "schema",
                "expired",
                "token",
                "unavailable",
                "receipt",
                "rate",
                "channel",
            }
            note = note if note in allowed else ""
            wait = data.get("retryAfter", 60)
            wait = wait if type(wait) is int and 0 <= wait <= 86400 else 60
            if result == "deferred":
                db.execute(
                    "UPDATE accounts SET retry_at=max(retry_at,?) WHERE account=?",
                    (self.clock() + max(30, wait), account),
                )
            if note in ("auth", "account", "token"):
                settings = json.loads(self._account(db, account)["settings"])
                settings["enabled"] = False
                db.execute(
                    "UPDATE accounts SET settings=? WHERE account=?",
                    (json.dumps(settings), account),
                )
            db.execute(
                "UPDATE actions SET state=?,posted=?,remote_id=?,note=?,reward=? WHERE id=?",
                (
                    "queued" if result == "deferred" else result,
                    self.clock() if result == "posted" else None,
                    remote if result == "posted" else None,
                    note,
                    "pending_24h" if result == "posted" and row["kind"] == "comment" else "unknown",
                    row["id"],
                ),
            )
