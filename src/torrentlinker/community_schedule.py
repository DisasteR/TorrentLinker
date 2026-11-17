"""Publication windows and conservative rolling-day reservations for Community."""

from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

AUTOMATION: dict[str, Any] = {
    "enabled": False,
    "kinds": {"comment": False, "vote": False, "chat": False},
    "start": "09:00",
    "end": "19:00",
    "timezone": "UTC",
    "channels": ["general"],
    "messages": [],
}


def validate_automation(value: Any) -> None:
    if not isinstance(value, dict) or set(value) != set(AUTOMATION):
        raise ValueError("Invalid automatic mode settings.")
    if type(value["enabled"]) is not bool or not isinstance(value["kinds"], dict):
        raise ValueError("Choose automatic actions.")
    if set(value["kinds"]) != set(AUTOMATION["kinds"]) or any(
        type(v) is not bool for v in value["kinds"].values()
    ):
        raise ValueError("Choose automatic actions.")
    try:
        ZoneInfo(value["timezone"])
        for key in ("start", "end"):
            if datetime.strptime(value[key], "%H:%M").strftime("%H:%M") != value[key]:
                raise ValueError()
    except (ValueError, TypeError, KeyError, ZoneInfoNotFoundError):
        raise ValueError("Choose a timezone and valid publication hours.") from None
    if value["start"] >= value["end"]:
        raise ValueError("The publication window must end after it starts.")
    if not isinstance(value["channels"], list) or any(
        c not in ("general", "class") for c in value["channels"]
    ):
        raise ValueError("Choose general or class chat.")
    messages = value["messages"]
    if (
        not isinstance(messages, list)
        or len(messages) > 200
        or any(not isinstance(m, str) or not 5 <= len(m.split()) or len(m) > 500 for m in messages)
    ):
        raise ValueError(
            "Select up to 200 messages, each 5 words or more and at most 500 characters."
        )
    if value["enabled"]:
        if not any(value["kinds"].values()):
            raise ValueError("Enable at least one automatic action.")
        if value["kinds"]["chat"] and (not messages or not value["channels"]):
            raise ValueError("Select messages and a channel for automatic chat.")


def in_window(stamp: float, config: dict[str, Any]) -> float:
    local = datetime.fromtimestamp(stamp, ZoneInfo(config["timezone"]))
    start_h, start_m = map(int, config["start"].split(":"))
    end_h, end_m = map(int, config["end"].split(":"))
    start = local.replace(hour=start_h, minute=start_m, second=0, microsecond=0)
    end = local.replace(hour=end_h, minute=end_m, second=0, microsecond=0)
    if local >= end:
        return (start + timedelta(days=1)).timestamp()
    return max(stamp, start.timestamp())


def next_slot(
    earliest: float,
    reservations: list[float],
    cap: int,
    interval: int,
    window: dict[str, Any] | None = None,
) -> float | None:
    if cap <= 0:
        return None
    stamp = earliest
    for _ in range(len(reservations) * 3 + 10):
        if window:
            stamp = in_window(stamp, window)
        nearby = sorted(t for t in reservations if abs(t - stamp) < interval)
        if nearby:
            stamp = nearby[-1] + interval
            continue
        # No rolling 24-hour window containing this slot may exceed the cap,
        # including reservations after it, not just past sends.
        ordered = sorted([*reservations, stamp])
        index = ordered.index(stamp)
        conflict = None
        for start in range(max(0, index - cap), min(index + 1, len(ordered) - cap)):
            if ordered[start + cap] - ordered[start] < 86400:
                conflict = ordered[start] + 86400
                break
        if conflict is not None:
            stamp = max(stamp + 1, conflict)
            continue
        return stamp
    return None
