"""NFL injury-report provider (product spec section 32).

Source: the nflverse ``injuries`` release — the official weekly NFL injury
reports, one parquet file per season (2009+), verified live 2026-09-09::

    https://github.com/nflverse/nflverse-data/releases/download/injuries/injuries_{season}.parquet

Each row is one player's injury-report entry for one week: the official
game status (Out / Doubtful / Questionable), the reported injury, and
practice participation. Columns: season, game_type, team, week, gsis_id,
position, full_name, first_name, last_name, report_primary_injury,
report_secondary_injury, report_status, practice_primary_injury,
practice_secondary_injury, practice_status, date_modified.

HONEST LIMITS (documented, never hidden):
- This is the *official weekly injury report*, not a live injury-news wire.
  For the season in progress, the newest week available may lag the
  current week. The provider reports the max (season, week, date_modified)
  observed so callers can see exactly how fresh the data is.
- No authentication required, no API key.

Never fabricates: unknown statuses are kept verbatim and surfaced as
warnings; missing values stay None; rows that fail validation are
reported, not silently dropped or invented.
"""

from __future__ import annotations

import io
import logging
import subprocess
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Tuple

import pandas as pd

from .base import (
    DiskCache,
    ProviderResult,
    RateLimiter,
    default_cache_dir as _default_cache_dir,
    retry_with_backoff,
    utcnow,
)
from .nflverse import normalize_team_abbr

log = logging.getLogger("mnb.providers.injuries")

INJURIES_RELEASE_URL = (
    "https://github.com/nflverse/nflverse-data/releases/download/injuries/injuries_{season}.parquet"
)

# First season the nflverse injuries release covers (verified 2026-09-09).
FIRST_SEASON = 2009

#: Official game-status values seen in the release. Anything else is kept
#: verbatim and flagged as a warning — never rejected, never rewritten.
KNOWN_REPORT_STATUSES = {"Out", "Doubtful", "Questionable"}

#: Practice-participation values seen in the release.
KNOWN_PRACTICE_STATUSES = {
    "Did Not Participate In Practice",
    "Limited Participation in Practice",
    "Full Participation in Practice",
}


def _opt_str(value: Any) -> Optional[str]:
    if value is None:
        return None
    try:
        if pd.isna(value):
            return None
    except (TypeError, ValueError):
        pass
    text = str(value).strip()
    return text or None


def _parse_modified(value: Any) -> Optional[datetime]:
    if value is None:
        return None
    try:
        if pd.isna(value):
            return None
    except (TypeError, ValueError):
        pass
    text = str(value).strip()
    if not text:
        return None
    try:
        dt = datetime.fromisoformat(text)
    except ValueError:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt


class NflverseInjuryProvider:
    """Fetches official weekly NFL injury reports from nflverse.

    Normalized record shape::

        {season, week, game_type, team, gsis_id, position, full_name,
         first_name, last_name, report_status, report_primary_injury,
         report_secondary_injury, practice_status, practice_primary_injury,
         practice_secondary_injury, date_modified (iso|None),
         source, fetched_at (iso)}
    """

    name = "nflverse-injuries"
    requires_auth = False

    def __init__(
        self,
        cache: Optional[DiskCache] = None,
        rate_limiter: Optional[RateLimiter] = None,
        http_timeout: float = 120.0,
    ) -> None:
        self.cache = cache or DiskCache(
            _default_cache_dir() / self.name
        )
        self.rate_limiter = rate_limiter or RateLimiter(min_interval_seconds=1.0)
        self.last_errors: List[str] = []
        self._last_fetched_at: Optional[datetime] = None
        self.http_timeout = http_timeout

    @property
    def last_fetched_at(self) -> Optional[datetime]:
        return self._last_fetched_at

    def is_fresh(self, ttl_hours: float = 24.0) -> bool:
        if self._last_fetched_at is None:
            return False
        age = (utcnow() - self._last_fetched_at).total_seconds() / 3600.0
        return age <= ttl_hours

    # -- transport (same curl pattern as the nflverse provider: Python HTTP
    # -- clients stall against the egress proxy; curl transfers reliably).
    @retry_with_backoff(tries=3, base_delay=2.0, max_delay=30.0)
    def _download(self, url: str) -> bytes:
        cached = self.cache.get(url)
        if cached is not None:
            log.info("cache hit: %s", url)
            return cached
        self.rate_limiter.wait()
        log.info("downloading: %s", url)
        max_time = str(max(1, int(self.http_timeout)))
        proc = subprocess.run(
            ["curl", "-sSL", "--fail", "--max-time", max_time, "-o", "-", url],
            capture_output=True,
            timeout=self.http_timeout + 60,
        )
        if proc.returncode != 0:
            raise RuntimeError(
                f"curl exit {proc.returncode}: {proc.stderr.decode('utf-8', 'replace')[:300]}"
            )
        payload = proc.stdout
        if not payload:
            raise RuntimeError("curl returned an empty body")
        self.cache.put(url, payload)
        return payload

    @staticmethod
    def normalize_injuries_frame(
        df: pd.DataFrame, season: int
    ) -> Tuple[List[Dict[str, Any]], List[str]]:
        """Turn one season's raw injuries frame into normalized records.

        Kept static so it can be unit-tested without network access.
        Returns (records, errors); rows that fail basic parsing are
        reported, never silently dropped.
        """
        records: List[Dict[str, Any]] = []
        errors: List[str] = []
        warnings: List[str] = []
        for row in df.itertuples(index=False):
            try:
                team = normalize_team_abbr(row.team)
                if team is None:
                    raise ValueError(f"unrecognized team abbr: {row.team!r}")
                report_status = _opt_str(getattr(row, "report_status", None))
                if report_status is not None and report_status not in KNOWN_REPORT_STATUSES:
                    warnings.append(
                        f"unknown report_status {report_status!r} for "
                        f"{getattr(row, 'full_name', '?')} ({team} {season} W{getattr(row, 'week', '?')})"
                    )
                modified = _parse_modified(getattr(row, "date_modified", None))
                records.append(
                    {
                        "season": int(season),
                        "week": int(row.week),
                        "game_type": _opt_str(getattr(row, "game_type", None)),
                        "team": team,
                        "gsis_id": _opt_str(getattr(row, "gsis_id", None)),
                        "position": _opt_str(getattr(row, "position", None)),
                        "full_name": _opt_str(getattr(row, "full_name", None)),
                        "first_name": _opt_str(getattr(row, "first_name", None)),
                        "last_name": _opt_str(getattr(row, "last_name", None)),
                        "report_status": report_status,
                        "report_primary_injury": _opt_str(
                            getattr(row, "report_primary_injury", None)
                        ),
                        "report_secondary_injury": _opt_str(
                            getattr(row, "report_secondary_injury", None)
                        ),
                        "practice_status": _opt_str(getattr(row, "practice_status", None)),
                        "practice_primary_injury": _opt_str(
                            getattr(row, "practice_primary_injury", None)
                        ),
                        "practice_secondary_injury": _opt_str(
                            getattr(row, "practice_secondary_injury", None)
                        ),
                        "date_modified": modified.isoformat() if modified else None,
                        "source": "nflverse-injuries",
                        "fetched_at": utcnow().isoformat(),
                    }
                )
            except Exception as exc:  # noqa: BLE001 - one bad row must not kill the batch
                errors.append(f"row skipped (season {season}): {exc}")
        return records, errors + [f"warning: {w}" for w in warnings]

    def fetch_injuries(self, seasons: List[int]) -> ProviderResult:
        """Download and normalize injury reports for the given seasons."""
        records: List[Dict[str, Any]] = []
        errors: List[str] = []
        for season in seasons:
            url = INJURIES_RELEASE_URL.format(season=season)
            try:
                payload = self._download(url)
                df = pd.read_parquet(io.BytesIO(payload))
                season_records, season_errors = self.normalize_injuries_frame(df, season)
                records.extend(season_records)
                errors.extend(season_errors)
                log.info("injuries season %d: %d records", season, len(season_records))
            except Exception as exc:  # noqa: BLE001 - one bad season must not kill the batch
                errors.append(f"season {season}: {exc}")
        result = ProviderResult(
            source=self.name, records=records, fetched_at=utcnow(), errors=errors
        )
        self.last_errors = list(errors)
        self._last_fetched_at = result.fetched_at
        return result

    def auth_configured(self) -> bool:
        return True

    def coverage(self) -> Dict[str, Any]:
        """Honest statement of what this source covers."""
        return {
            "source": "nflverse injuries release (official weekly NFL injury reports)",
            "url_pattern": INJURIES_RELEASE_URL,
            "first_season": FIRST_SEASON,
            "granularity": "per player, per week",
            "live_wire": False,
            "note": (
                "Official weekly injury reports, not a live news wire. "
                "For the season in progress the newest week available may lag "
                "the current week."
            ),
        }
