import httpx
import pytest

from torrentlinker.profile import (
    ProfileClient,
    ProfileError,
    ProfileProblem,
    class_from_slug,
)
from torrentlinker.zen import ZenClass, ZenProfile

PROFILE = {
    "id": 42,
    "username": "viet",
    "avatarUrl": None,
    "memberSince": "2025-01-02T00:00:00Z",
    "level": {"level": 12, "xp": 3400, "xpIntoLevel": 200, "xpToNextLevel": 800},
    "class": {
        "slug": "tanker",
        "effectiveSlug": "tanker",
        "foundation": "seed",
        "treeBonusBp": 300,
        "progress": {
            "questsCompleted": 4,
            "questsTotal": 10,
            "stars": 2,
            "grade": {"tier": "bronze", "level": 1},
        },
        "voies": {"seed": {"star": 1, "questsCompleted": 3, "questsTotal": 5}},
    },
    "team": None,
    "zen": {"balance": 123.5, "totalEarned": 400, "totalSpent": 276.5},
    "transfer": {
        "uploaded": {"actual": 10, "credit": 0, "total": 10},
        "downloaded": {"actual": 5, "credit": 0, "total": 5},
    },
    "download": {"allowed": True, "blockedReason": None},
    "freeleech": {"active": False, "sources": []},
    "silverLeech": False,
    "leechBonuses": [],
}


class Clock:
    now = 100.0

    def __call__(self) -> float:
        return self.now


def client(handler, clock: Clock | None = None) -> ProfileClient:  # type: ignore[no-untyped-def]
    return ProfileClient(
        "https://c411.example/api/torznab",
        "K",
        http=httpx.Client(transport=httpx.MockTransport(handler)),
        clock=clock or Clock(),
    )


def test_profile_is_read_with_the_indexer_key_as_a_bearer_token() -> None:
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(200, json=PROFILE)

    profile = client(handler).fetch()

    assert str(seen[0].url) == "https://c411.example/api/user/profile"
    assert seen[0].headers["Authorization"] == "Bearer K"
    assert "apikey" not in str(seen[0].url)
    assert profile.balance == 123.5
    assert profile.zen_class is ZenClass.TANKER
    assert profile.tree_bonus == pytest.approx(0.03)
    assert profile.download_allowed


def test_profile_retains_account_progression_and_transfer_totals() -> None:
    profile = client(lambda r: httpx.Response(200, json=PROFILE)).fetch()

    assert profile.account_id == "42"
    assert profile.member_since == "2025-01-02T00:00:00Z"
    assert profile.level == 12
    assert profile.xp_into_level == 200
    assert profile.xp_to_next_level == 800
    assert (profile.quests_completed, profile.quests_total) == (4, 10)
    assert profile.total_earned == 400
    assert profile.total_spent == 276.5
    assert profile.uploaded_actual == 10
    assert profile.downloaded_total == 5
    assert profile.freeleech is False


def test_profile_keeps_team_grade_freeleech_sources_and_total_xp() -> None:
    data = {
        **PROFILE,
        "team": {"name": "Saumon", "slug": "saumon", "tag": "SMN", "role": "member"},
        "freeleech": {"active": True, "sources": ["campaign", {"type": "lunar"}]},
    }
    profile = client(lambda r: httpx.Response(200, json=data)).fetch()

    assert (profile.team_name, profile.team_tag, profile.team_role) == ("Saumon", "SMN", "member")
    assert (profile.grade_tier, profile.grade_level) == ("bronze", 1)
    assert profile.freeleech_sources == ["campaign", "lunar"]
    assert profile.total_xp == 3400


def test_null_team_and_class_are_not_errors() -> None:
    data = {**PROFILE, "team": None, "class": None}
    profile = client(lambda r: httpx.Response(200, json=data)).fetch()

    assert profile.team_name is None and profile.team_tag is None and profile.team_role is None
    assert profile.class_slug is None
    assert profile.grade_tier is None and profile.grade_level is None
    assert profile.quests == {}


def test_class_and_tree_bonus_fill_the_zen_profile_keeping_manual_fields() -> None:
    profile = client(lambda r: httpx.Response(200, json=PROFILE)).fetch()

    filled = profile.apply(ZenProfile(ZenClass.SWARM_KEEPER, 0, seed_hours=12, torrent_cap=50))

    assert filled == ZenProfile(ZenClass.TANKER, 0.03, 12, 50)


def test_a_user_without_class_keeps_the_manual_zen_profile() -> None:
    data = {**PROFILE, "class": None}
    profile = client(lambda r: httpx.Response(200, json=data)).fetch()
    manual = ZenProfile(ZenClass.SWARM_KEEPER)

    assert profile.zen_class is None
    assert profile.apply(manual) == manual


def test_blocked_downloads_are_reported() -> None:
    data = {**PROFILE, "download": {"allowed": False, "blockedReason": "insufficient_balance"}}
    profile = client(lambda r: httpx.Response(200, json=data)).fetch()

    assert not profile.download_allowed
    assert profile.blocked_reason == "insufficient_balance"


@pytest.mark.parametrize(
    ("status", "problem"),
    [
        (401, ProfileProblem.UNAUTHORIZED),
        (403, ProfileProblem.MISSING_SCOPE),
        (429, ProfileProblem.RATE_LIMITED),
        (500, ProfileProblem.FAILED),
    ],
)
def test_errors_say_what_went_wrong(status: int, problem: ProfileProblem) -> None:
    with pytest.raises(ProfileError) as e:
        client(lambda r: httpx.Response(status)).fetch()

    assert e.value.problem is problem


def test_without_a_key_nothing_is_requested() -> None:
    seen: list[httpx.Request] = []
    c = ProfileClient(
        "https://c411.example/api/torznab",
        "",
        http=httpx.Client(transport=httpx.MockTransport(lambda r: seen.append(r))),  # type: ignore[arg-type,func-returns-value]
    )

    with pytest.raises(ProfileError) as e:
        c.fetch()

    assert e.value.problem is ProfileProblem.NO_KEY
    assert seen == []


def test_a_profile_younger_than_30_seconds_is_not_requested_again() -> None:
    clock, seen = Clock(), []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(200, json=PROFILE)

    c = client(handler, clock)
    c.fetch()
    clock.now += 10
    c.fetch()
    assert len(seen) == 1
    clock.now += 25
    c.fetch()
    assert len(seen) == 2


def test_class_slugs_are_recognised_loosely() -> None:
    assert class_from_slug("Swarm-Keeper") is ZenClass.SWARM_KEEPER
    assert class_from_slug("swarm_keeper") is ZenClass.SWARM_KEEPER
    assert class_from_slug("reuploader") is ZenClass.REUPLOADER
    assert class_from_slug("TANKER") is ZenClass.TANKER
    assert class_from_slug("mystery") is None
    assert class_from_slug(None) is None
