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
