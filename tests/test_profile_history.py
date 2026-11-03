import time
from dataclasses import replace
from datetime import datetime

import pytest

from torrentlinker.profile import ProfileError, UserProfile
from torrentlinker.profile_history import ProfileHistory
from torrentlinker.store import Store

PROFILE = UserProfile(
    "viet",
    "tanker",
    None,
    0.025,
    120.0,
    True,
    None,
    account_id="42",
    level=12,
    total_earned=400,
    total_spent=280,
    uploaded_actual=1000,
    uploaded_credit=100,
    downloaded_actual=500,
    downloaded_credit=50,
    total_xp=3400,
)


class Clock:
    def __init__(self, when: str) -> None:
        self.now = datetime.fromisoformat(when).timestamp()

    def __call__(self) -> float:
        return self.now


@pytest.fixture
def local_time(monkeypatch):
    def use(zone: str) -> None:
        monkeypatch.setenv("TZ", zone)
        time.tzset()

    yield use
    monkeypatch.delenv("TZ", raising=False)
    time.tzset()


def history(tmp_path, when="2026-11-02T10:00:00"):
    clock = Clock(when)
    return ProfileHistory(Store(tmp_path / "state.db"), clock), clock


def test_a_refresh_and_a_snapshot_fill_todays_row(tmp_path):
    h, _ = history(tmp_path)
    h.record_api("account", PROFILE)
    assert h.days("account")[-1].outcome == "api"
    h.record_companion(
        "account", {"activeSeeds": {"total": 412}, "details": {"totalSeedingTimeSec": 86400}}
    )

    (day,) = h.days("account")
    assert day.day == "2026-11-02"
    assert day.outcome == "ok"
    assert day.values["uploaded_actual"] == 1000
    assert day.values["downloaded_credit"] == 50
    assert day.values["balance"] == 120.0
    assert day.values["earned"] == 400 and day.values["spent"] == 280
    assert day.values["level"] == 12 and day.values["xp"] == 3400
    assert day.values["active_seeds"] == 412
    assert day.values["seed_time"] == 86400


def test_later_refreshes_replace_the_days_values(tmp_path):
    h, clock = history(tmp_path)
    h.record_api("account", PROFILE)
    clock.now += 3600
    h.record_api("account", replace(PROFILE, balance=150.0))

    (day,) = h.days("account")
    assert day.values["balance"] == 150.0


def test_a_failure_keeps_the_days_good_values(tmp_path):
    h, clock = history(tmp_path)
    h.record_api("account", PROFILE)
    clock.now += 60
    h.record_failure("account")

    (day,) = h.days("account")
    assert day.values["balance"] == 120.0
    assert day.outcome == "api"
    assert day.failed


def test_a_day_with_only_failures_is_failed_without_values(tmp_path):
    h, _ = history(tmp_path)
    h.record_failure("account")

    (day,) = h.days("account")
    assert day.outcome == "failed"
    assert "balance" not in day.values


def test_a_day_with_only_companion_snapshots_had_no_api_sync(tmp_path):
    h, _ = history(tmp_path)
    h.record_companion("account", {"activeSeeds": {"total": 3}, "details": None})

    (day,) = h.days("account")
    assert day.outcome == "failed"
    assert day.values == {"active_seeds": 3}


def test_each_account_has_its_own_history(tmp_path):
    h, clock = history(tmp_path)
    h.record_api("old", PROFILE)
    clock.now += 86400
    h.record_api("new", replace(PROFILE, balance=5.0))

    assert [d.values["balance"] for d in h.days("new")] == [5.0]
    assert [d.day for d in h.days("old")] == ["2026-11-02"]


def test_days_follow_local_time(tmp_path, local_time):
    local_time("Pacific/Auckland")  # UTC+13 in November
    h, clock = history(tmp_path, "2026-11-02T23:30:00")
    h.record_api("account", PROFILE)
    clock.now += 3600  # 00:30 local, the next day
    h.record_api("account", replace(PROFILE, balance=130.0))

    assert [(d.day, d.values["balance"]) for d in h.days("account")] == [
        ("2026-11-02", 120.0),
        ("2026-11-03", 130.0),
    ]


def test_history_survives_a_restart(tmp_path):
    h, clock = history(tmp_path)
    h.record_api("account", PROFILE)

    reopened = ProfileHistory(Store(tmp_path / "state.db"), clock)

    assert reopened.days("account")[0].values["balance"] == 120.0


def test_the_workspace_records_refreshes_failures_and_starts_over_on_a_new_key(
    tmp_path, monkeypatch
):
    import httpx

    from tests.test_web_profile import ORIGIN, prepared, snapshot

    _, setup = prepared(tmp_path, monkeypatch)
    workspace = setup.profile_workspace()
    workspace.refresh(setup.settings.indexer.url, "fake-key")
    code = workspace.pairing()["code"]
    token = workspace.receive("/api/companion/pair", ORIGIN, "", {"code": code, "accountId": "42"})
    workspace.receive("/api/companion/snapshot", ORIGIN, "Bearer " + token["token"], snapshot())
    (day,) = workspace.history_days()
    assert day.outcome == "ok" and day.values["active_seeds"] == 42

    monkeypatch.setattr(
        "torrentlinker.profile.net.client",
        lambda **kw: httpx.Client(transport=httpx.MockTransport(lambda r: httpx.Response(500))),
    )
    setup.secrets.update({"indexer-api-key": "other-key"})
    workspace = setup.profile_workspace()
    assert workspace.history_days() == []
    with pytest.raises(ProfileError):
        workspace.refresh(setup.settings.indexer.url, "other-key")
    (day,) = workspace.history_days()
    assert day.outcome == "failed" and day.values == {}


def stamp(when: str) -> float:
    return datetime.fromisoformat(when).timestamp()


def test_the_zen_ledger_fills_earlier_days_backwards_from_todays_balance(tmp_path):
    h, _ = history(tmp_path, "2026-11-03T10:00:00")
    h.record_api("account", replace(PROFILE, balance=930.0, total_earned=2930, total_spent=2000))
    h.record_ledger(
        "account",
        930.0,
        [
            (stamp("2026-11-03T00:05:00"), 150),  # today: already in the balance
            (stamp("2026-11-01T18:00:00"), -500),  # a purchase
            (stamp("2026-11-01T00:05:00"), 140),
            (stamp("2026-10-31T00:05:00"), 130),
        ],
    )

    rows = {d.day: d for d in h.days("account")}
    assert {
        day: (d.values["balance"], d.values["earned"], d.values["spent"]) for day, d in rows.items()
    } == {
        "2026-10-31": (1140.0, 2640, 1500),
        "2026-11-01": (780.0, 2780, 2000),
        "2026-11-02": (780.0, 2780, 2000),
        "2026-11-03": (930.0, 2930, 2000),
    }
    # Replayed days are not syncs: the calendar leaves them blank.
    assert [rows[d].outcome for d in ("2026-10-31", "2026-11-02")] == ["none", "none"]


def test_the_ledger_never_replaces_a_day_the_api_recorded(tmp_path):
    h, clock = history(tmp_path, "2026-11-02T10:00:00")
    h.record_api("account", replace(PROFILE, balance=700.0))
    clock.now = stamp("2026-11-03T10:00:00")
    h.record_api("account", replace(PROFILE, balance=930.0))
    h.record_ledger("account", 930.0, [(stamp("2026-11-03T00:05:00"), 200)])

    assert [(d.day, d.values["balance"], d.outcome) for d in h.days("account")] == [
        ("2026-11-02", 700.0, "api"),
        ("2026-11-03", 930.0, "api"),
    ]


def test_ledger_balances_need_todays_totals_for_earned_and_spent(tmp_path):
    h, _ = history(tmp_path, "2026-11-03T10:00:00")
    h.record_ledger(
        "account",
        930.0,
        [(stamp("2026-11-03T00:05:00"), 150), (stamp("2026-11-02T00:05:00"), 140)],
    )

    (day,) = h.days("account")
    assert day.day == "2026-11-02" and day.values == {"balance": 780.0}


def test_a_snapshot_with_the_zen_ledger_fills_earlier_days(tmp_path, monkeypatch):
    from datetime import UTC, timedelta

    from tests.test_web_profile import ORIGIN, prepared, snapshot

    _, setup = prepared(tmp_path, monkeypatch)
    workspace = setup.profile_workspace()
    workspace.refresh(setup.settings.indexer.url, "fake-key")
    code = workspace.pairing()["code"]
    token = workspace.receive("/api/companion/pair", ORIGIN, "", {"code": code, "accountId": "42"})
    yesterday = datetime.now(UTC) - timedelta(days=1)
    body = {
        **snapshot(),
        "zenLedger": [
            {"createdAt": datetime.now(UTC).isoformat(), "delta": 23},
            {"createdAt": yesterday.isoformat(), "delta": 50},
        ],
    }
    workspace.receive("/api/companion/snapshot", ORIGIN, "Bearer " + token["token"], body)

    first = workspace.history_days()[0]
    assert first.ledger and first.values["balance"] == 123 - 23
    assert "zenLedger" not in workspace.state()["companion"]


@pytest.mark.parametrize(
    "ledger",
    [
        "not a list",
        [{"createdAt": "yesterday", "delta": 5}],
        [{"createdAt": "2026-11-02T00:00:00Z", "delta": "5"}],
        [{"createdAt": "2026-11-02T00:00:00Z", "delta": 5, "reason": "x"}],
        [{"createdAt": "2026-11-02T00:00:00Z", "delta": 5}] * 501,
    ],
)
def test_a_malformed_zen_ledger_rejects_the_snapshot(tmp_path, monkeypatch, ledger):
    from tests.test_web_profile import ORIGIN, prepared, snapshot

    _, setup = prepared(tmp_path, monkeypatch)
    workspace = setup.profile_workspace()
    workspace.refresh(setup.settings.indexer.url, "fake-key")
    code = workspace.pairing()["code"]
    token = workspace.receive("/api/companion/pair", ORIGIN, "", {"code": code, "accountId": "42"})
    with pytest.raises(ValueError):
        workspace.receive(
            "/api/companion/snapshot",
            ORIGIN,
            "Bearer " + token["token"],
            {**snapshot(), "zenLedger": ledger},
        )
