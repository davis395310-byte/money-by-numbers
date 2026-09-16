"""Data provider interfaces (product spec section 14).

Every NFL/odds/injury/weather provider in Money By Numbers implements the
``NFLDataProvider`` contract below (or a sibling contract for non-NFL data),
so providers can be swapped without touching ingestion, validation, or the
API. The base class bakes in the cross-cutting concerns from the spec:

- authentication (``requires_auth`` flag + ``auth_configured()``)
- rate-limit handling (``RateLimiter``: minimum interval between requests)
- retry logic (``retry_with_backoff``: exponential backoff, capped)
- caching (``DiskCache``: on-disk cache with TTL per payload)
- error handling (errors are collected on ``ProviderResult.errors`` and in
  ``provider.last_errors`` — never raised past the provider boundary unless
  the caller opts into strict mode)
- timestamp + freshness tracking (``fetched_at`` on every result,
  ``is_fresh(ttl)`` on the provider)

Concrete providers must NEVER fabricate records: a missing value stays
``None`` and is reported in ``errors``/warnings, never invented.
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import time
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import datetime, timezone
from functools import wraps
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

log = logging.getLogger("mnb.providers")


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


# ---------------------------------------------------------------------------
# Result / error containers
# ---------------------------------------------------------------------------


@dataclass
class ProviderResult:
    """What a provider fetch returns.

    ``records`` is a list of plain dicts in the provider's *normalized*
    shape (see each concrete provider's docstring). ``errors`` holds
    non-fatal problems encountered (e.g. rows skipped); an empty record list
    with errors means the fetch failed honestly, not that the world is empty.
    """

    source: str
    records: List[Dict[str, Any]] = field(default_factory=list)
    fetched_at: datetime = field(default_factory=utcnow)
    cache_hit: bool = False
    errors: List[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not self.errors or bool(self.records)


# ---------------------------------------------------------------------------
# Rate limiting
# ---------------------------------------------------------------------------


class RateLimiter:
    """Enforces a minimum interval between outbound requests."""

    def __init__(self, min_interval_seconds: float = 1.0) -> None:
        self.min_interval = max(0.0, min_interval_seconds)
        self._last_call: float = 0.0

    def wait(self) -> None:
        if self.min_interval <= 0:
            return
        elapsed = time.monotonic() - self._last_call
        if elapsed < self.min_interval:
            time.sleep(self.min_interval - elapsed)
        self._last_call = time.monotonic()


# ---------------------------------------------------------------------------
# Retry with exponential backoff
# ---------------------------------------------------------------------------


def retry_with_backoff(
    tries: int = 3,
    base_delay: float = 1.0,
    max_delay: float = 30.0,
    retryable: Callable[[BaseException], bool] | None = None,
) -> Callable:
    """Decorator: retry the wrapped callable with exponential backoff.

    All exceptions are treated as retryable unless ``retryable`` says
    otherwise. After ``tries`` attempts the last exception is re-raised so
    the caller can record it honestly.
    """

    def decorator(fn: Callable) -> Callable:
        @wraps(fn)
        def wrapper(*args: Any, **kwargs: Any) -> Any:
            attempt = 0
            while True:
                try:
                    return fn(*args, **kwargs)
                except Exception as exc:  # noqa: BLE001 - backoff then re-raise
                    attempt += 1
                    if retryable is not None and not retryable(exc):
                        raise
                    if attempt >= tries:
                        raise
                    delay = min(max_delay, base_delay * (2 ** (attempt - 1)))
                    log.warning(
                        "%s attempt %d/%d failed (%s); retrying in %.1fs",
                        fn.__name__, attempt, tries, exc, delay,
                    )
                    time.sleep(delay)

        return wrapper

    return decorator


# ---------------------------------------------------------------------------
# On-disk cache with TTL
# ---------------------------------------------------------------------------


class DiskCache:
    """Content-addressed on-disk cache with per-entry TTL.

    Entries are keyed by an arbitrary string (usually the URL + params).
    Stored as raw bytes plus a small JSON sidecar with the write timestamp,
    so TTL can be evaluated without reading the payload.
    """

    def __init__(self, directory: str | Path, default_ttl_hours: float = 24.0) -> None:
        self.directory = Path(directory)
        self.directory.mkdir(parents=True, exist_ok=True)
        self.default_ttl_hours = default_ttl_hours

    def _paths(self, key: str) -> tuple[Path, Path]:
        digest = hashlib.sha256(key.encode("utf-8")).hexdigest()
        return self.directory / f"{digest}.bin", self.directory / f"{digest}.json"

    def get(self, key: str, ttl_hours: float | None = None) -> Optional[bytes]:
        data_path, meta_path = self._paths(key)
        if not data_path.exists() or not meta_path.exists():
            return None
        ttl = self.default_ttl_hours if ttl_hours is None else ttl_hours
        try:
            meta = json.loads(meta_path.read_text(encoding="utf-8"))
            written = datetime.fromisoformat(meta["written_at"])
            age_hours = (utcnow() - written).total_seconds() / 3600.0
            if age_hours > ttl:
                return None
            return data_path.read_bytes()
        except Exception as exc:  # noqa: BLE001 - corrupt cache = miss
            log.warning("cache read failed for %s: %s", key, exc)
            return None

    def put(self, key: str, payload: bytes) -> None:
        data_path, meta_path = self._paths(key)
        data_path.write_bytes(payload)
        meta_path.write_text(
            json.dumps({"written_at": utcnow().isoformat()}), encoding="utf-8"
        )

    def clear(self) -> int:
        removed = 0
        for path in self.directory.glob("*.bin"):
            meta = path.with_suffix(".json")
            path.unlink(missing_ok=True)
            meta.unlink(missing_ok=True)
            removed += 1
        return removed


def default_cache_dir() -> Path:
    base = os.environ.get("MNB_CACHE_DIR") or os.path.join(
        os.path.expanduser("~"), ".cache", "money-by-numbers"
    )
    return Path(base)


# ---------------------------------------------------------------------------
# Provider contract
# ---------------------------------------------------------------------------


class NFLDataProvider(ABC):
    """Contract for NFL data providers (spec section 14).

    Normalized shapes (all datetimes timezone-aware UTC unless noted):

    Team dict:   {abbr, name, city, conference, division}
    Game dict:   {game_id, season, week, game_date (datetime), home_team,
                  away_team, venue|None, status ("scheduled"|"final"),
                  home_score|None, away_score|None, overtime|None}
    Team-game-stat dict: {game_id, season, week, team, points,
                  plays|None, yards_total|None, passing_yards|None,
                  rushing_yards|None, turnovers|None,
                  third_down_conv|None, third_down_att|None,
                  red_zone_td|None, red_zone_att|None,
                  epa_total|None, epa_per_play|None, success_rate|None}
    """

    name: str = "base"
    requires_auth: bool = False

    def __init__(
        self,
        cache: Optional[DiskCache] = None,
        rate_limiter: Optional[RateLimiter] = None,
    ) -> None:
        self.cache = cache or DiskCache(default_cache_dir() / self.name)
        self.rate_limiter = rate_limiter or RateLimiter(min_interval_seconds=1.0)
        self.last_errors: List[str] = []
        self._last_fetched_at: Optional[datetime] = None

    # -- identity / auth ----------------------------------------------------
    def auth_configured(self) -> bool:
        """True when the provider can authenticate (or needs no auth)."""
        return not self.requires_auth

    # -- freshness ----------------------------------------------------------
    @property
    def last_fetched_at(self) -> Optional[datetime]:
        return self._last_fetched_at

    def is_fresh(self, ttl_hours: float = 24.0) -> bool:
        if self._last_fetched_at is None:
            return False
        age = (utcnow() - self._last_fetched_at).total_seconds() / 3600.0
        return age <= ttl_hours

    # -- fetches ------------------------------------------------------------
    @abstractmethod
    def fetch_teams(self) -> ProviderResult:
        ...

    @abstractmethod
    def fetch_games(self, seasons: List[int]) -> ProviderResult:
        ...

    @abstractmethod
    def fetch_team_game_stats(self, seasons: List[int]) -> ProviderResult:
        ...

    # -- helpers ------------------------------------------------------------
    def _record_success(self) -> None:
        self._last_fetched_at = utcnow()
        self.last_errors = []

    def _record_errors(self, errors: List[str]) -> None:
        self.last_errors = list(errors)
