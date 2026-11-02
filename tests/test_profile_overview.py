from dataclasses import asdict
from datetime import date
from pathlib import Path

import pytest

from tests.test_profile import PROFILE
from torrentlinker.domain import Torrent
from torrentlinker.profile import parse_profile
from torrentlinker.profile_history import ProfileDay
from torrentlinker.profile_overview import (
    accounting,
    calendar,
    header,
    period_figures,
    progression,
    ratio,
    tiles,
    torrent_clients_overview,
)
from torrentlinker.zen import ZenClass, ZenProfile

TODAY = date(2026, 11, 2)
URL = "https://c411.org/api/torznab"


def api(**changes):
    data = asdict(parse_profile({**PROFILE, **changes}))
    data.pop("zen_class")
    return data


def day(when: str, outcome: str = "api", **values) -> ProfileDay:
    return ProfileDay(when, values, api=outcome in {"api", "ok"}, companion=outcome == "ok")


# Header -------------------------------------------------------------------------


def test_header_shows_team_class_and_status():
    data = api(
        team={"name": "Saumon", "tag": "SMN", "role": "member"},
        freeleech={"active": True, "sources": ["campaign"]},
    )
    result = header({"api": data, "api_updated_at": 100.0, "api_stale": False})

    assert result["initial"] == "V"
    assert result["class"] == "Tanker"
    assert result["team"] == "Saumon [SMN]"
    assert result["level"] == 12
    assert result["member_since"] == "2025-01-02"
    assert result["download"] == {"allowed": True, "reason": None}
    assert result["freeleech"] == {"active": True, "sources": ["campaign"]}
    assert result["api"] == {"updated_at": 100.0, "stale": False}


def test_null_class_and_team_are_absent_not_errors():
    result = header({"api": api(team=None, **{"class": None}), "error": None})

    assert result["class"] is None
    assert result["team"] is None


def test_unknown_blocked_reasons_and_sources_are_kept_as_given():
    data = api(
        download={"allowed": False, "blockedReason": "moon_phase"},
        freeleech={"active": True, "sources": ["nebula"]},
    )
    result = header({"api": data})

    assert result["download"] == {"allowed": False, "reason": "moon_phase"}
    assert result["freeleech"]["sources"] == ["nebula"]


def test_stale_companion_is_flagged():
    result = header({"api": api(), "companion": {"x": 1}, "received_at": 50.0, "stale": True})

    assert result["companion"] == {"received_at": 50.0, "stale": True, "paired": False}


# Tiles --------------------------------------------------------------------------


def test_ratio_and_buffer_use_actual_traffic():
    data = api(
        transfer={
            "uploaded": {"actual": 300, "credit": 100, "total": 400},
            "downloaded": {"actual": 100, "credit": 50, "total": 50},
        }
    )
    result = tiles({"api": data}, None)

    assert result["ratio"]["value"] == 3.0
    assert result["buffer"]["value"] == 200
    assert result["uploaded"]["value"] == 300
    assert result["downloaded"]["value"] == 100
    assert result["zen"]["value"] == 123.5


def test_ratio_is_unknown_without_downloads():
    assert ratio(100, 0) is None
    assert ratio(None, 5) is None


def test_active_seeds_need_the_companion():
    assert tiles({"api": api()}, None)["active_seeds"]["value"] is None
    with_companion = {"api": api(), "companion": {"activeSeeds": {"total": 412}}}
    assert tiles(with_companion, None)["active_seeds"]["value"] == 412


def test_tiles_carry_period_change_and_trend():
    rows = [
        day("2026-10-01", uploaded_actual=100, downloaded_actual=50, balance=10),
        day("2026-11-01", uploaded_actual=160, downloaded_actual=60, balance=40),
    ]
    figures = period_figures(rows)
    result = tiles({"api": api()}, figures)

    assert result["uploaded"]["change"] == 60
    assert result["uploaded"]["trend"] == [100, 160]
    assert result["zen"]["change"] == 30
    assert result["buffer"]["change"] == 50


# Progression --------------------------------------------------------------------


def test_progression_reads_basis_points_and_keeps_unknown_grades():
    data = api(
        **{
            "class": {
                **PROFILE["class"],
                "treeBonusBp": 250,
                "progress": {
                    "questsCompleted": 31,
                    "questsTotal": 52,
                    "stars": 3,
                    "grade": {"tier": "platine", "level": 2},
                },
            }
        }
    )
    result = progression(data)

    assert result["tree_bonus"] == pytest.approx(0.025)
    assert result["grade"] == "platine 2"
    assert result["level"] == 12
    assert result["fraction"] == pytest.approx(0.2)
    assert result["total_xp"] == 3400


def test_a_path_with_no_quests_keeps_its_star_and_an_empty_bar():
    data = api(
        **{
            "class": {
                **PROFILE["class"],
                "voies": {"upload": {"star": True, "questsCompleted": 0, "questsTotal": 0}},
            }
        }
    )
    paths = {p["key"]: p for p in progression(data)["paths"]}

    assert list(paths) == ["seed", "upload", "specialization", "competitivity"]
    assert paths["upload"] == {
        "key": "upload",
        "completed": 0,
        "total": 0,
        "fraction": 0.0,
        "stars": 1,
    }
    assert paths["seed"]["completed"] is None and paths["seed"]["fraction"] == 0.0


def test_tree_bonus_is_unknown_without_a_class():
    assert progression(api(**{"class": None}))["tree_bonus"] is None


# Transfer accounting ------------------------------------------------------------


def test_upload_total_adds_credit():
    result = accounting(1000, 200, "uploaded")

    assert result["total"] == 1200
    assert result["solid"] == pytest.approx(1000 / 1200)
    assert result["extra"] == pytest.approx(200 / 1200)
    assert not result["hatched"]


def test_download_total_subtracts_credit_drawn_hatched():
    result = accounting(1000, 300, "downloaded")

    assert result["total"] == 700
    assert result["solid"] == pytest.approx(0.7)
    assert result["extra"] == pytest.approx(0.3)
    assert result["hatched"]


def test_download_credit_larger_than_traffic_floors_at_zero():
    result = accounting(100, 500, "downloaded")

    assert result["total"] == 0
    assert result["solid"] == 0
    assert result["extra"] == pytest.approx(1.0)


def test_accounting_without_data_is_unknown():
    assert accounting(None, 5, "uploaded") is None
    assert accounting(0, 0, "downloaded")["total"] == 0


# Period maths -------------------------------------------------------------------


def test_period_needs_two_days():
    assert period_figures([]) is None
    assert period_figures([day("2026-11-01", balance=5)]) is None


def test_period_change_average_and_measured_zen_with_purchases():
    rows = [
        day("2026-10-23", uploaded_actual=1000, downloaded_actual=100, balance=500, spent=0),
        day("2026-10-28", uploaded_actual=1600, downloaded_actual=150, balance=200, spent=1000),
        day("2026-11-02", uploaded_actual=2000, downloaded_actual=200, balance=700, spent=1000),
    ]
    result = period_figures(rows)

    assert result["first"] == "2026-10-23" and result["last"] == "2026-11-02"
    assert result["elapsed"] == 10
    assert result["change"]["uploaded"] == 1000
    assert result["change"]["downloaded"] == 100
    assert result["change"]["balance"] == 200
    assert result["change"]["ratio"] == pytest.approx(10 - 10)
    assert result["average"]["uploaded"] == 100
    # Balance rose 200 while 1000 was spent: 1200 earned over 10 days.
    assert result["measured_zen"] == pytest.approx(120)
    assert result["purchases"] == 1


def test_measured_zen_falls_back_on_balance_drops_without_spent():
    rows = [
        day("2026-10-30", balance=100),
        day("2026-10-31", balance=50),
        day("2026-11-01", balance=120),
    ]
    result = period_figures(rows)

    assert result["measured_zen"] is None
    assert result["purchases"] == 1


def test_period_ignores_days_without_api_values():
    rows = [
        day("2026-10-30", balance=100),
        day("2026-10-31", "failed"),
        day("2026-11-01", balance=120),
    ]
    result = period_figures(rows)

    assert [p["day"] for p in result["series"]] == ["2026-10-30", "2026-11-01"]


def test_window_keeps_the_chosen_days():
    from torrentlinker.profile_overview import window

    rows = [day(f"2026-10-{d:02d}", balance=d) for d in range(1, 32)] + [
        day("2026-11-01", balance=32),
        day("2026-11-02", balance=33),
    ]

    assert window(rows, 7, TODAY)[0].day == "2026-10-26"
    assert len(window(rows, 0, TODAY)) == len(rows)


# Calendar -----------------------------------------------------------------------


def test_calendar_covers_26_weeks_with_a_state_per_day():
    rows = [day("2026-11-02", "ok"), day("2026-11-01", "failed"), day("2026-10-30", "api")]
    cells = calendar(rows, TODAY)

    assert len(cells) == 182
    assert cells[-1] == {"day": "2026-11-02", "outcome": "ok"}
    assert cells[-2]["outcome"] == "failed"
    assert cells[-3]["outcome"] == "none"
    assert cells[-4]["outcome"] == "api"
    assert cells[0]["day"] == "2026-05-05"


# Seeding, tracker vs clients ----------------------------------------------------


def torrent(hash, *, seeding=True, tracker="https://tracker.c411.org/announce", size=10):
    return Torrent(
        hash,
        hash,
        Path("/data"),
        state="uploading" if seeding else "pausedUP",
        tracker=tracker,
        size=size,
        swarm_seeders=5,
    )


def failing():
    raise ConnectionError("refused by https://box.example/secret")


def test_each_client_counts_its_seeding_indexer_torrents():
    targets = [
        ("seedbox", lambda: [torrent("a"), torrent("b", size=5), torrent("c", seeding=False)]),
        ("NAS", lambda: [torrent("b", size=5), torrent("d", tracker="https://other.org/a")]),
    ]
    result = torrent_clients_overview(ZenProfile(ZenClass.TANKER), targets, URL, active_seeds=4)

    assert result["clients"] == [
        {"name": "seedbox", "count": 2, "size": 15, "error": None},
        {"name": "NAS", "count": 1, "size": 5, "error": None},
    ]
    # a and b are seeded (b twice); c411 sees 4.
    assert result["unique"] == 2
    assert result["gap"] == 2


def test_an_unreachable_client_is_unavailable_not_zero():
    targets = [("seedbox", lambda: [torrent("a")]), ("NAS", failing)]
    result = torrent_clients_overview(ZenProfile(), targets, URL, active_seeds=None)

    nas = result["clients"][1]
    assert nas["count"] is None and nas["size"] is None
    assert nas["error"] and "secret" not in nas["error"]
    assert result["gap"] is None  # no companion


def test_zen_panel_estimates_from_reachable_clients():
    targets = [("seedbox", lambda: [torrent("a", size=4_000_000_000)])]
    result = torrent_clients_overview(ZenProfile(ZenClass.TANKER, seed_hours=24), targets, URL, 1)

    zen = result["zen"]
    assert zen["daily"] > 0
    assert zen["top"]["name"] == "a"
    assert [c["current"] for c in zen["classes"]].count(True) == 1
    assert zen["classes"][0]["daily"] >= zen["classes"][-1]["daily"]


def test_zen_panel_is_empty_without_a_reachable_client():
    assert torrent_clients_overview(ZenProfile(), [("NAS", failing)], URL, 3)["zen"] is None
    assert torrent_clients_overview(ZenProfile(), [], URL, 3)["zen"] is None
    assert torrent_clients_overview(ZenProfile(), [], URL, 3)["gap"] is None
