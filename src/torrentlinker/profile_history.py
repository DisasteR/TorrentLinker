"""Profile history: one row per local day per account, for change over time.

Each row keeps the day's last successful API values, the companion's last active
seeds and seed time, and whether the day saw an API success, a companion snapshot
or a failure. A failure never overwrites the day's good values. The account is the
profile cache's binding, so changing the indexer or its key starts a new history.
History never changes planning inputs.
"""

from __future__ import annotations

import time
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import date
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

    @property
    def outcome(self) -> str:
        """ok (API and companion), api (API only) or failed (no API success that day,
        even with companion snapshots: the API sync is what the day is judged by)."""
        if self.api:
            return "ok" if self.companion else "api"
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

    def days(self, account: str) -> list[ProfileDay]:
        return [
            ProfileDay(
                day,
                dict(row.get("values") or {}),
                bool(row.get("api")),
                bool(row.get("companion")),
                bool(row.get("failed")),
            )
            for day, row in self.store.profile_days(account)
        ]
