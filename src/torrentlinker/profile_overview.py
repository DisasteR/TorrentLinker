"""The Profile overview's figures, shared by the desktop view and the WebUI tab.

Plain data only: each frontend formats and draws it. Missing data stays None so it
shows as "—" or an empty state, never as a wrong zero.
"""

from __future__ import annotations

from collections.abc import Callable, Iterable, Sequence
from datetime import date, timedelta
from typing import Any, TypeGuard

from torrentlinker.domain import Torrent
from torrentlinker.profile_history import ProfileDay, active_seeds
from torrentlinker.seeding import combined_seeding, indexer_torrents
from torrentlinker.torrent_management import safe_error
from torrentlinker.zen import ZenProfile, compare_classes

PERIODS = (7, 30, 90, 0)  # days; 0 is all history
CALENDAR_DAYS = 26 * 7
PATHS = ("seed", "upload", "specialization", "competitivity")


def known(value: object) -> TypeGuard[int | float]:
    return type(value) in (int, float)


def ratio(uploaded: Any, downloaded: Any) -> float | None:
    """Actual uploaded over actual downloaded; unknown rather than infinite at zero."""
    if not known(uploaded) or not known(downloaded) or downloaded <= 0:
        return None
    return float(uploaded / downloaded)


def difference(a: Any, b: Any) -> float | None:
    return a - b if known(a) and known(b) else None


def header(state: dict[str, Any]) -> dict[str, Any]:
    api = state.get("api") or {}
    slug = api.get("class_slug")
    team = api.get("team_name")
    tag = api.get("team_tag")
    return {
        "initial": (api.get("username") or "?")[:1].upper(),
        "username": api.get("username"),
        "class": slug.replace("_", " ").replace("-", " ").title() if slug else None,
        "level": api.get("level"),
        "team": (f"{team} [{tag}]" if tag else team) if team else None,
        "member_since": (api.get("member_since") or "")[:10] or None,
        "download": {
            "allowed": api.get("download_allowed") if api else None,
            "reason": api.get("blocked_reason"),
        },
        "freeleech": {
            "active": bool(api.get("freeleech")),
            "sources": list(api.get("freeleech_sources") or []),
        },
        "api": {"updated_at": state.get("api_updated_at"), "stale": bool(state.get("api_stale"))},
        "companion": {
            "received_at": state.get("received_at") if state.get("companion") else None,
            "stale": bool(state.get("stale")),
            "paired": bool(state.get("paired")),
        },
    }


def tiles(state: dict[str, Any], figures: dict[str, Any] | None) -> dict[str, dict[str, Any]]:
    """Current values with the period's change and trend line when history allows."""
    api = state.get("api") or {}
    up, down = api.get("uploaded_actual"), api.get("downloaded_actual")
    companion = state.get("companion") or {}
    values = {
        "ratio": ratio(up, down),
        "buffer": difference(up, down),
        "uploaded": up if known(up) else None,
        "downloaded": down if known(down) else None,
        "zen": api.get("balance"),
        "active_seeds": active_seeds(companion),
    }
    series = {
        "ratio": "ratio",
        "buffer": "buffer",
        "uploaded": "uploaded",
        "downloaded": "downloaded",
        "zen": "balance",
        "active_seeds": "active_seeds",
    }
    result = {}
    for key, value in values.items():
        name = series[key]
        trend = [p[name] for p in figures["series"] if known(p[name])] if figures else []
        result[key] = {
            "value": value,
            "change": figures["change"][name] if figures else None,
            "trend": trend if len(trend) > 1 else [],
        }
    return result


def progression(api: dict[str, Any]) -> dict[str, Any]:
    into, remaining = api.get("xp_into_level"), api.get("xp_to_next_level")
    span = (into or 0) + (remaining or 0) if known(into) and known(remaining) else 0
    paths = []
    for key in PATHS:
        quest = (api.get("quests") or {}).get(key) or {}
        done, total = quest.get("questsCompleted"), quest.get("questsTotal")
        star = quest.get("star")
        paths.append(
            {
                "key": key,
                "completed": done,
                "total": total,
                # A path can have no quests yet and still keep its star.
                "fraction": min(1.0, done / total)
                if known(done) and known(total) and total > 0
                else 0.0,
                "stars": int(star) if isinstance(star, int) else 0,  # bool or count
            }
        )
    tier, level = api.get("grade_tier"), api.get("grade_level")
    return {
        "level": api.get("level"),
        "into": into,
        "to_next": remaining,
        "fraction": into / span if known(into) and span > 0 else 0.0,
        "total_xp": api.get("total_xp"),
        "grade": " ".join(str(v) for v in (tier, level) if v is not None)
        or api.get("grade")
        or None,
        "stars": api.get("stars"),
        "quests_completed": api.get("quests_completed"),
        "quests_total": api.get("quests_total"),
        "tree_bonus": api.get("tree_bonus") if api.get("class_slug") else None,
        "paths": paths,
    }


def accounting(actual: Any, credit: Any, direction: str) -> dict[str, Any] | None:
    """c411's rule: upload total adds credit; download total subtracts it, floor zero.

    `solid` and `extra` are shares of the longer of actual and total. For downloads,
    `extra` is the credit that lowered the total, drawn hatched.
    """
    if not known(actual):
        return None
    credit = credit if known(credit) else 0
    sign = 1 if direction == "uploaded" else -1
    total = max(0, actual + sign * credit)
    base = max(actual, total)
    if base <= 0:
        return {
            "actual": actual,
            "credit": credit,
            "total": total,
            "solid": 0.0,
            "extra": 0.0,
            "hatched": False,
        }
    lowered = total < actual
    return {
        "actual": actual,
        "credit": credit,
        "total": total,
        "solid": (total if lowered else actual) / base,
        "extra": abs(total - actual) / base,
        "hatched": lowered,
    }


def transfers(api: dict[str, Any]) -> dict[str, Any]:
    return {
        direction: accounting(
            api.get(f"{direction}_actual"), api.get(f"{direction}_credit"), direction
        )
        for direction in ("uploaded", "downloaded")
    }


# Profile history ------------------------------------------------------------------


def window(days: Sequence[ProfileDay], span: int, today: date) -> list[ProfileDay]:
    """The days within `span` days before today (0: all of them)."""
    if not span:
        return list(days)
    start = (today - timedelta(days=span)).isoformat()
    return [d for d in days if d.day >= start]


def point(d: ProfileDay) -> dict[str, Any]:
    v = d.values
    up, down = v.get("uploaded_actual"), v.get("downloaded_actual")
    return {
        "day": d.day,
        "uploaded": up,
        "downloaded": down,
        "buffer": difference(up, down),
        "ratio": ratio(up, down),
        "balance": v.get("balance"),
        "spent": v.get("spent"),
        "earned": v.get("earned"),
        "active_seeds": v.get("active_seeds"),
    }


def period_figures(days: Sequence[ProfileDay]) -> dict[str, Any] | None:
    """Change, average per day and measured Zen over the days with API or ledger values.

    Each figure is measured between the first and last days that know it: ledger days
    carry only Zen. None with fewer than two such days: history is still being collected.
    """
    series = [point(d) for d in days if (d.api or d.ledger) and d.values]
    if len(series) < 2:
        return None

    def ends(name: str) -> tuple[dict[str, Any], dict[str, Any], int] | None:
        points = [p for p in series if known(p[name])]
        if len(points) < 2:
            return None
        first, last = points[0], points[-1]
        elapsed = (date.fromisoformat(last["day"]) - date.fromisoformat(first["day"])).days
        return first, last, max(1, elapsed)

    names = ("uploaded", "downloaded", "buffer", "ratio", "balance", "active_seeds")
    spans = {n: ends(n) for n in (*names, "earned")}
    change = {n: difference(s[1][n], s[0][n]) if (s := spans[n]) else None for n in names}
    # Zen measured: what the balance gained plus what was spent meanwhile.
    measured: float | None = None
    if (span := spans["balance"]) and known(span[0]["spent"]) and known(span[1]["spent"]):
        first, last, elapsed = span
        measured = (last["balance"] - first["balance"] + last["spent"] - first["spent"]) / elapsed
    elif span := spans["earned"]:
        measured = (span[1]["earned"] - span[0]["earned"]) / span[2]
    purchases = 0
    for before, after in zip(series, series[1:], strict=False):
        if known(before["spent"]) and known(after["spent"]):
            purchases += after["spent"] > before["spent"]
        elif known(before["balance"]) and known(after["balance"]):
            purchases += after["balance"] < before["balance"]
    first, last = series[0], series[-1]
    return {
        "first": first["day"],
        "last": last["day"],
        "elapsed": max(
            1, (date.fromisoformat(last["day"]) - date.fromisoformat(first["day"])).days
        ),
        "change": change,
        "average": {
            n: value / span[2] if (value := change[n]) is not None and (span := spans[n]) else None
            for n in ("uploaded", "downloaded", "balance")
        },
        "measured_zen": measured,
        "purchases": purchases,
        "series": series,
    }


def calendar(days: Iterable[ProfileDay], today: date) -> list[dict[str, str]]:
    """One cell per day for 26 weeks, oldest first; "none" when nothing was tried."""
    outcomes = {d.day: d.outcome for d in days}
    cells = []
    for back in range(CALENDAR_DAYS - 1, -1, -1):
        when = (today - timedelta(days=back)).isoformat()
        cells.append({"day": when, "outcome": outcomes.get(when, "none")})
    return cells


def overview(
    state: dict[str, Any], days: Sequence[ProfileDay], span: int, today: date
) -> dict[str, Any]:
    """Everything the Profile page shows except the Torrent Clients' figures."""
    api = state.get("api") or {}
    figures = period_figures(window(days, span, today))
    companion = state.get("companion") or {}
    details = companion.get("details") or {}
    return {
        "span": span,
        "header": header(state),
        "tiles": tiles(state, figures),
        "progression": progression(api),
        "zen": {
            "balance": api.get("balance"),
            "earned": api.get("total_earned"),
            "spent": api.get("total_spent"),
        },
        "transfers": transfers(api),
        "companion": {
            "active_seeds": active_seeds(companion),
            "validated_uploads": details.get("validatedUploadsCount"),
            "seed_time": details.get("totalSeedingTimeSec"),
        }
        if companion
        else None,
        "period": figures,
        "calendar": calendar(days, today),
    }


# Torrent Clients ------------------------------------------------------------------


def torrent_clients_overview(
    profile: ZenProfile,
    targets: Iterable[tuple[str, Callable[[], list[Torrent]]]],
    indexer_url: str,
    active_seeds: int | None,
    uploads: int = 0,
) -> dict[str, Any]:
    """Seeding per named Torrent Client against c411, and today's Zen Estimate.

    A client that cannot be reached is unavailable, not zero. Only torrents for the
    configured indexer count. The gap needs the companion's active seeds.
    """
    rows: list[dict[str, Any]] = []
    observed: list[Torrent] = []
    for name, fetch in targets:
        try:
            torrents = fetch()
        except Exception as error:  # each client fails on its own
            rows.append({"name": name, "count": None, "size": None, "error": safe_error(error)})
            continue
        seeding = [t for t in indexer_torrents(torrents, indexer_url) if t.is_seeding]
        observed.extend(torrents)
        rows.append(
            {
                "name": name,
                "count": len(seeding),
                "size": sum(t.total_size for t in seeding),
                "error": None,
            }
        )
    reachable = any(r["error"] is None for r in rows)
    if not reachable:
        return {"clients": rows, "unique": None, "gap": None, "zen": None}
    view = combined_seeding(profile, observed, indexer_url, uploads)
    unique = view.count
    top = max((r for r in view.rows if r.zen is not None), key=lambda r: r.zen or 0, default=None)
    days = sorted(
        compare_classes(profile, view.active, uploads).items(), key=lambda item: -item[1].total
    )
    classes = [
        {"id": cls.value, "daily": d.total, "current": cls is profile.zen_class} for cls, d in days
    ]
    return {
        "clients": rows,
        "unique": unique,
        "gap": active_seeds - unique if known(active_seeds) else None,
        "zen": {
            "daily": view.day.total,
            "count": unique,
            "classes": classes,
            "top": {"name": top.torrent.name, "zen": top.zen} if top else None,
        },
    }
