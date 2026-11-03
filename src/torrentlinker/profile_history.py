"""Profile history: one row per local day per account, for change over time.

Each row keeps the day's last successful API values, the companion's last active
seeds and seed time, and whether the day saw an API success, a companion snapshot
or a failure. A failure never overwrites the day's good values. Days before any
sync can be replayed from c411's Zen ledger; those never replace an API day. The account is the
profile cache's binding, so changing the indexer or its key starts a new history.
History never changes planning inputs.
"""

from __future__ import annotations

import time
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import date, timedelta
from typing import Any

from torrentlinker.profile import UserProfile
from torrentlinker.store import Store

API_VALUES = {
    "uploaded_actual": "uploaded_actual",
    "uploaded_credit": "uploaded_credit",
    "downloaded_actual": "downloaded_actual",
    "downloaded_credit": "downloaded_credit",
    "balance": "balance",
    "earned": "total_earned",
    "spent": "total_spent",
    "level": "level",
    "xp": "total_xp",
}


@dataclass(frozen=True)
class ProfileDay:
    day: str  # local date, YYYY-MM-DD
    values: dict[str, Any] = field(default_factory=dict)
    api: bool = False
    companion: bool = False
    failed: bool = False
    ledger: bool = False  # Zen values replayed from c411's ledger, not synced

    @property
    def outcome(self) -> str:
        """ok (API and companion), api (API only), failed (no API success that day,
        even with companion snapshots: the API sync is what the day is judged by) or
        none (only replayed from the ledger: nothing was tried)."""
        if self.api:
            return "ok" if self.companion else "api"
        if self.ledger and not (self.companion or self.failed):
            return "none"
        return "failed"


def active_seeds(snapshot: dict[str, Any] | None) -> int | None:
    """Active seeds on c411 from a companion snapshot, None without one."""
    return ((snapshot or {}).get("activeSeeds") or {}).get("total")


def local_day(timestamp: float) -> str:
    return date.fromtimestamp(timestamp).isoformat()


class ProfileHistory:
    def __init__(self, store: Store, clock: Callable[[], float] = time.time) -> None:
        self.store = store
        self.clock = clock

    def _update(self, account: str, change: Callable[[dict[str, Any]], None]) -> None:
        day = local_day(self.clock())
        row = self.store.profile_day(account, day) or {"values": {}}
        change(row)
        self.store.save_profile_day(account, day, row)

    def record_api(self, account: str, profile: UserProfile) -> None:
        def change(row: dict[str, Any]) -> None:
            row["values"].update({key: getattr(profile, name) for key, name in API_VALUES.items()})
            row["api"] = True

        self._update(account, change)

    def record_companion(self, account: str, snapshot: dict[str, Any]) -> None:
        def change(row: dict[str, Any]) -> None:
            seeds = active_seeds(snapshot)
            seed_time = (snapshot.get("details") or {}).get("totalSeedingTimeSec")
            if seeds is not None:
                row["values"]["active_seeds"] = seeds
            if seed_time is not None:
                row["values"]["seed_time"] = seed_time
            row["companion"] = True

        self._update(account, change)

    def record_failure(self, account: str) -> None:
        self._update(account, lambda row: row.update(failed=True))

    def record_ledger(
        self, account: str, balance: float, entries: list[tuple[float, float]]
    ) -> None:
        """Fill each day from the oldest entry to yesterday with its closing Zen.

        `balance` is the current balance and `entries` (time, delta) every ledger
        entry since the oldest one, so walking back undoes them day by day. Earned
        and spent need today's API totals to walk back from.
        """
        now = self.clock()
        today = local_day(now)
        entries = sorted((e for e in entries if e[0] <= now), reverse=True)
        if not entries:
            return
        totals = self.store.profile_day(account, today) or {}
        current = (totals.get("values") or {}) if totals.get("api") else {}
        values: dict[str, float] = {"balance": balance}
        values.update({k: current[k] for k in ("earned", "spent") if current.get(k) is not None})
        oldest = local_day(entries[-1][0])
        day = date.fromisoformat(today)
        index = 0
        while (day := day - timedelta(days=1)).isoformat() >= oldest:
            # Undo everything after this day's close.
            while index < len(entries) and local_day(entries[index][0]) > day.isoformat():
                delta = entries[index][1]
                values["balance"] -= delta
                name = "earned" if delta > 0 else "spent"
                if name in values:
                    values[name] -= abs(delta)
                index += 1
            row = self.store.profile_day(account, day.isoformat()) or {"values": {}}
            if row.get("api"):
                continue
            row["values"].update(values)
            row["ledger"] = True
            self.store.save_profile_day(account, day.isoformat(), row)

    def days(self, account: str) -> list[ProfileDay]:
        return [
            ProfileDay(
                day,
                dict(row.get("values") or {}),
                bool(row.get("api")),
                bool(row.get("companion")),
                bool(row.get("failed")),
                bool(row.get("ledger")),
            )
            for day, row in self.store.profile_days(account)
        ]
