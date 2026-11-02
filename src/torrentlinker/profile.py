"""The user's c411 profile: Zen Balance, Class and tree bonus (GET /api/user/profile).

Uses the same key as the Indexer, sent as `Authorization: Bearer`; the key needs the
`profile:read` scope. c411 allows 30 requests a minute and refreshes the data at most
every 30 seconds, so a profile younger than that is served from memory.
"""

from __future__ import annotations

import threading
import time
from collections.abc import Callable
from dataclasses import dataclass, field, replace
from enum import StrEnum
from typing import Any
from urllib.parse import urlsplit

import httpx

from torrentlinker import net
from torrentlinker.zen import ZenClass, ZenProfile

PROFILE_PATH = "/api/user/profile"
FRESH_FOR = 30.0  # seconds: c411 does not refresh the profile more often


class ProfileProblem(StrEnum):
    NO_KEY = "no-key"
    UNAUTHORIZED = "unauthorized"  # 401: missing or invalid key
    MISSING_SCOPE = "missing-scope"  # 403: the key lacks profile:read
    RATE_LIMITED = "rate-limited"  # 429
    FAILED = "failed"


class ProfileError(Exception):
    def __init__(self, problem: ProfileProblem, detail: str = "") -> None:
        super().__init__(detail or problem.value)
        self.problem = problem


@dataclass(frozen=True)
class UserProfile:
    username: str
    class_slug: str | None
    zen_class: ZenClass | None  # None when the user has no Class or it is unknown to us
    tree_bonus: float  # e.g. 0.05 for +5 %
    balance: float  # Zen Balance
    download_allowed: bool
    blocked_reason: str | None
    account_id: str = ""
    member_since: str | None = None
    level: int | None = None
    xp_into_level: int | None = None
    xp_to_next_level: int | None = None
    quests_completed: int | None = None
    quests_total: int | None = None
    stars: int | None = None
    total_earned: float | None = None
    total_spent: float | None = None
    uploaded_actual: int | None = None
    uploaded_credit: int | None = None
    uploaded_total: int | None = None
    downloaded_actual: int | None = None
    downloaded_credit: int | None = None
    downloaded_total: int | None = None
    freeleech: bool | None = None
    grade: str = ""
    quests: dict[str, dict[str, Any]] = field(default_factory=dict)
    team_name: str | None = None  # None outside a team
    team_tag: str | None = None
    team_role: str | None = None
    grade_tier: str | None = None  # bronze, argent or or; unknown tiers kept as given
    grade_level: int | None = None
    freeleech_sources: list[str] = field(default_factory=list)  # personal, campaign, window…
    total_xp: int | None = None

    def apply(self, profile: ZenProfile) -> ZenProfile:
        """The Zen Profile with the Class and tree bonus read from c411."""
        if self.zen_class is None:
            return profile
        return replace(profile, zen_class=self.zen_class, tree_bonus=self.tree_bonus)


def class_from_slug(slug: str | None) -> ZenClass | None:
    """c411 does not document its Class slugs: recognise them by what they contain."""
    s = (slug or "").lower()
    if "tanker" in s:
        return ZenClass.TANKER
    if "keeper" in s or "swarm" in s:
        return ZenClass.SWARM_KEEPER
    if "reupload" in s:
        return ZenClass.REUPLOADER
    return None


def source_name(source: Any) -> str:
    """A freeleech source as given; c411 has not shown a populated list yet."""
    if isinstance(source, dict):
        source = source.get("source") or source.get("type") or source.get("name") or ""
    return str(source)


def parse_profile(data: dict[str, Any]) -> UserProfile:
    cls = data.get("class") or {}
    slug = cls.get("effectiveSlug") or cls.get("slug")
    zen = data.get("zen") or {}
    download = data.get("download") or {}
    level = data.get("level") or {}
    progress = cls.get("progress") or {}
    transfer = data.get("transfer") or {}
    uploaded = transfer.get("uploaded") or {}
    downloaded = transfer.get("downloaded") or {}
    grade = progress.get("grade") or {}
    team = data.get("team") or {}
    freeleech = data.get("freeleech") or {}
    return UserProfile(
        username=str(data.get("username", "")),
        class_slug=slug,
        zen_class=class_from_slug(slug),
        tree_bonus=int(cls.get("treeBonusBp") or 0) / 10000,
        balance=float(zen.get("balance") or 0),
        download_allowed=bool(download.get("allowed", True)),
        blocked_reason=download.get("blockedReason"),
        account_id=str(data.get("id") or ""),
        member_since=data.get("memberSince"),
        level=level.get("level"),
        xp_into_level=level.get("xpIntoLevel"),
        xp_to_next_level=level.get("xpToNextLevel"),
        quests_completed=progress.get("questsCompleted"),
        quests_total=progress.get("questsTotal"),
        stars=progress.get("stars"),
        total_earned=zen.get("totalEarned"),
        total_spent=zen.get("totalSpent"),
        uploaded_actual=uploaded.get("actual"),
        uploaded_credit=uploaded.get("credit"),
        uploaded_total=uploaded.get("total"),
        downloaded_actual=downloaded.get("actual"),
        downloaded_credit=downloaded.get("credit"),
        downloaded_total=downloaded.get("total"),
        freeleech=freeleech.get("active"),
        grade=" ".join(str(v) for v in (grade.get("tier"), grade.get("level")) if v is not None),
        quests={
            name: {key: value.get(key) for key in ("star", "questsCompleted", "questsTotal")}
            for name, value in (cls.get("voies") or {}).items()
            if name in {"seed", "upload", "specialization", "competitivity"}
            and isinstance(value, dict)
        },
        team_name=team.get("name"),
        team_tag=team.get("tag"),
        team_role=team.get("role"),
        grade_tier=grade.get("tier"),
        grade_level=grade.get("level"),
        freeleech_sources=[source_name(s) for s in freeleech.get("sources") or []],
        total_xp=level.get("xp"),
    )


def profile_url(indexer_url: str) -> str:
    parts = urlsplit(indexer_url)
    return f"{parts.scheme}://{parts.netloc}{PROFILE_PATH}"


class ProfileClient:
    def __init__(
        self,
        indexer_url: str,
        api_key: str,
        http: httpx.Client | None = None,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self.url = profile_url(indexer_url)
        self.api_key = api_key
        self._http = http or net.client(timeout=30, follow_redirects=True)
        self._clock = clock
        self._lock = threading.Lock()
        self._last: tuple[float, UserProfile] | None = None

    def fetch(self) -> UserProfile:
        if not self.api_key:
            raise ProfileError(ProfileProblem.NO_KEY)
        with self._lock:
            if self._last and self._clock() - self._last[0] < FRESH_FOR:
                return self._last[1]
            try:
                response = self._http.get(
                    self.url, headers={"Authorization": f"Bearer {self.api_key}"}
                )
            except httpx.HTTPError as e:
                raise ProfileError(ProfileProblem.FAILED, str(e)) from e
            problem = {
                401: ProfileProblem.UNAUTHORIZED,
                403: ProfileProblem.MISSING_SCOPE,
                429: ProfileProblem.RATE_LIMITED,
            }.get(response.status_code)
            if problem:
                raise ProfileError(problem)
            if response.status_code != 200:
                raise ProfileError(ProfileProblem.FAILED, f"HTTP {response.status_code}")
            try:
                profile = parse_profile(response.json())
            except (ValueError, TypeError, AttributeError) as e:
                raise ProfileError(ProfileProblem.FAILED, str(e)) from e
            self._last = (self._clock(), profile)
            return profile
