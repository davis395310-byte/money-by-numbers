"""nflverse provider — free NFL data, no authentication required.

Sources (nflverse-data GitHub releases, updated nightly during the season):
- schedules/scores: .../releases/download/schedules/games.parquet
- play-by-play (EPA etc.): .../releases/download/pbp/play_by_play_{season}.parquet

No API key, no account. If nflverse is unreachable, fetches fail honestly
with recorded errors; nothing is synthesized.

Normalization notes:
- Legacy team abbreviations are mapped to canonical ones:
  LA/STL -> LAR (Rams), SD -> LAC (Chargers), OAK -> LV (Raiders).
- ``game_id`` is the nflverse game id (e.g. ``2024_01_KC_BAL``); the unique
  compound index on (season, week, home_team, away_team) is a second
  duplicate guard.
- A game is ``final`` when both scores are present, ``scheduled`` otherwise.
- Missing values (venue, overtime, per-play stats) stay ``None`` — they are
  never invented.
"""

from __future__ import annotations

import io
import logging
import subprocess
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional
from zoneinfo import ZoneInfo

import pandas as pd

from .base import DiskCache, NFLDataProvider, ProviderResult, RateLimiter, retry_with_backoff, utcnow
from .teams_data import TEAMS

log = logging.getLogger("mnb.providers.nflverse")

SCHEDULES_URL = (
    "https://github.com/nflverse/nflverse-data/releases/download/schedules/games.parquet"
)
PBP_URL_TEMPLATE = (
    "https://github.com/nflverse/nflverse-data/releases/download/pbp/"
    "play_by_play_{season}.parquet"
)

# Legacy abbreviations -> canonical (matches app.models.core.VALID_TEAM_ABBRS).
TEAM_ABBR_MAP = {
    "LA": "LAR",
    "STL": "LAR",
    "SD": "LAC",
    "OAK": "LV",
}

# Play types that count as offensive plays for yardage/EPA aggregates.
OFFENSIVE_PLAY_TYPES = {"pass", "run"}


def normalize_team_abbr(abbr: Any) -> Optional[str]:
    if abbr is None or (isinstance(abbr, float) and pd.isna(abbr)):
        return None
    abbr = str(abbr).strip().upper()
    return TEAM_ABBR_MAP.get(abbr, abbr)


def _opt_int(value: Any) -> Optional[int]:
    if value is None or pd.isna(value):
        return None
    return int(value)


def _opt_float(value: Any) -> Optional[float]:
    if value is None or pd.isna(value):
        return None
    return float(value)


def _opt_str(value: Any) -> Optional[str]:
    if value is None or pd.isna(value):
        return None
    text = str(value).strip()
    return text or None


class NflverseProvider(NFLDataProvider):
    """Free nflverse parquet provider. ``requires_auth`` is False."""

    name = "nflverse"
    requires_auth = False

    def __init__(
        self,
        cache: Optional[DiskCache] = None,
        rate_limiter: Optional[RateLimiter] = None,
        http_timeout: float = 120.0,
    ) -> None:
        super().__init__(cache=cache, rate_limiter=rate_limiter)
        self.http_timeout = http_timeout

    # -- transport ----------------------------------------------------------
    @retry_with_backoff(tries=3, base_delay=2.0, max_delay=30.0)
    def _download(self, url: str) -> bytes:
        """Download a URL with rate limiting, caching, and retry.

        Uses the ``curl`` binary via subprocess: in this runtime, Python
        HTTP clients (httpx/urllib) stall against the egress proxy while
        curl transfers reliably. ``curl`` is therefore a system dependency
        of this provider (standard in the deployment images; see
        docs/deployment.md).
        """
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

    def _read_parquet(self, url: str, columns: Optional[List[str]] = None) -> pd.DataFrame:
        payload = self._download(url)
        return pd.read_parquet(io.BytesIO(payload), columns=columns)

    # -- teams --------------------------------------------------------------
    def fetch_teams(self) -> ProviderResult:
        self._record_success()
        return ProviderResult(
            source=self.name,
            records=[dict(t) for t in TEAMS],
            fetched_at=utcnow(),
        )

    # -- games --------------------------------------------------------------
    @staticmethod
    def normalize_games_frame(df: pd.DataFrame, seasons: List[int]) -> List[Dict[str, Any]]:
        """Turn the raw schedules parquet frame into normalized game dicts.

        Kept static so it can be unit-tested without network access.
        """
        wanted = set(seasons)
        df = df[df["season"].isin(wanted)].copy()
        records: List[Dict[str, Any]] = []
        errors: List[str] = []
        for row in df.itertuples(index=False):
            try:
                home = normalize_team_abbr(row.home_team)
                away = normalize_team_abbr(row.away_team)
                home_score = _opt_int(row.home_score)
                away_score = _opt_int(row.away_score)
                status = "final" if (home_score is not None and away_score is not None) else "scheduled"
                game_date = NflverseProvider._parse_gameday(row)
                records.append(
                    {
                        "game_id": str(row.game_id),
                        "season": int(row.season),
                        "week": int(row.week),
                        "game_date": game_date,
                        "home_team": home,
                        "away_team": away,
                        "venue": _opt_str(getattr(row, "stadium", None)),
                        "roof": _opt_str(getattr(row, "roof", None)),
                        "status": status,
                        "home_score": home_score,
                        "away_score": away_score,
                        "overtime": _opt_int(getattr(row, "overtime", None)),
                    }
                )
            except Exception as exc:  # noqa: BLE001 - one bad row must not kill the batch
                errors.append(f"row skipped ({getattr(row, 'game_id', '?')}): {exc}")
        return records, errors

    @staticmethod
    def _parse_gameday(row: Any) -> Optional[datetime]:
        gameday = getattr(row, "gameday", None)
        if gameday is None or pd.isna(gameday):
            return None
        gametime = getattr(row, "gametime", None)
        text = str(gameday)
        if gametime is not None and not pd.isna(gametime) and str(gametime).strip():
            text = f"{text} {gametime}"
            fmt = "%Y-%m-%d %H:%M"
        else:
            fmt = "%Y-%m-%d"
        try:
            naive = datetime.strptime(text.strip(), fmt)
        except ValueError:
            # Fall back to date-only parse.
            naive = datetime.strptime(str(gameday).strip()[:10], "%Y-%m-%d")
        # nflverse documents gametime in Eastern Time (America/New_York).
        # Attach the source timezone instead of mislabeling the wall time
        # as UTC: the stored instant is then the true kickoff moment, and
        # drivers (pymongo/mongomock) persist it as UTC.
        return naive.replace(tzinfo=ZoneInfo("America/New_York"))

    def fetch_games(self, seasons: List[int]) -> ProviderResult:
        try:
            df = self._read_parquet(SCHEDULES_URL)
        except Exception as exc:  # noqa: BLE001 - honest failure
            self._record_errors([f"schedules download failed: {exc}"])
            return ProviderResult(source=self.name, errors=[f"schedules download failed: {exc}"])
        records, errors = self.normalize_games_frame(df, seasons)
        self._record_success() if records else self._record_errors(errors)
        if errors:
            self.last_errors = errors
        return ProviderResult(source=self.name, records=records, errors=errors)

    # -- team game stats ------------------------------------------------------
    @staticmethod
    def aggregate_team_stats(
        pbp: pd.DataFrame, schedules: pd.DataFrame, season: int
    ) -> List[Dict[str, Any]]:
        """Aggregate per-team per-game stats from a season of play-by-play.

        Static for unit testing. ``schedules`` supplies the final points
        (authoritative score), pbp supplies yardage/EPA/turnover detail.
        """
        errors: List[str] = []
        sched = schedules[schedules["season"] == season].copy()
        score_lookup: Dict[str, Dict[str, Optional[int]]] = {}
        for row in sched.itertuples(index=False):
            gid = str(row.game_id)
            score_lookup[gid] = {
                normalize_team_abbr(row.home_team): _opt_int(row.home_score),
                normalize_team_abbr(row.away_team): _opt_int(row.away_score),
            }

        pbp = pbp.copy()
        pbp["posteam_norm"] = pbp["posteam"].map(normalize_team_abbr)
        offense = pbp[pbp["play_type"].isin(OFFENSIVE_PLAY_TYPES) & pbp["posteam_norm"].notna()]

        records: List[Dict[str, Any]] = []
        grouped = pbp.groupby(["game_id", "posteam_norm"], dropna=True)
        off_grouped = offense.groupby(["game_id", "posteam_norm"], dropna=True)
        off_keys = set(off_grouped.groups.keys())
        for (game_id, team), plays in grouped:
            game_id = str(game_id)
            key = (plays["game_id"].iloc[0], plays["posteam_norm"].iloc[0])
            off = off_grouped.get_group(key) if key in off_keys else offense.iloc[0:0]
            points = score_lookup.get(game_id, {}).get(team)
            # Red-zone trips: distinct offensive *drives* reaching the red
            # zone (yardline_100 <= 20). A trip is a drive, not a play —
            # counting plays would inflate attempts. Zero trips is real
            # data (0); only a missing drive column yields None.
            if len(off) and "drive" in off.columns:
                rz_by_drive = off[off["yardline_100"] <= 20].dropna(
                    subset=["drive"]
                ).groupby("drive")
                red_zone_att = _opt_int(len(rz_by_drive))
                red_zone_td = _opt_int(
                    sum(1 for _, drv in rz_by_drive if (drv["touchdown"].fillna(0) == 1).any())
                )
            else:
                red_zone_att = None
                red_zone_td = None
            records.append(
                {
                    "game_id": game_id,
                    "season": season,
                    "week": _opt_int(plays["week"].iloc[0]) if "week" in plays.columns else None,
                    "team": team,
                    "points": points,
                    "plays": int(len(off)) or None,
                    "yards_total": _opt_float(off["yards_gained"].sum()) if len(off) else None,
                    "passing_yards": _opt_float(off["passing_yards"].sum()) if len(off) else None,
                    "rushing_yards": _opt_float(off["rushing_yards"].sum()) if len(off) else None,
                    "turnovers": int(plays["interception"].fillna(0).sum() + plays["fumble_lost"].fillna(0).sum()),
                    "third_down_conv": _opt_int(off["third_down_converted"].fillna(0).sum()) if len(off) else None,
                    "third_down_att": _opt_int(
                        off["third_down_converted"].fillna(0).sum() + off["third_down_failed"].fillna(0).sum()
                    ) if len(off) else None,
                    "red_zone_td": red_zone_td,
                    "red_zone_att": red_zone_att,
                    "epa_total": _opt_float(off["epa"].sum()) if len(off) else None,
                    "epa_per_play": _opt_float(off["epa"].mean()) if len(off) else None,
                    "success_rate": _opt_float(off["success"].mean()) if len(off) else None,
                }
            )
        return records, errors

    def fetch_team_game_stats(self, seasons: List[int]) -> ProviderResult:
        all_records: List[Dict[str, Any]] = []
        errors: List[str] = []
        try:
            schedules = self._read_parquet(
                SCHEDULES_URL,
                columns=["game_id", "season", "home_team", "away_team", "home_score", "away_score"],
            )
        except Exception as exc:  # noqa: BLE001
            msg = f"schedules download failed (needed for points): {exc}"
            self._record_errors([msg])
            return ProviderResult(source=self.name, errors=[msg])

        for season in seasons:
            url = PBP_URL_TEMPLATE.format(season=season)
            try:
                pbp = self._read_parquet(
                    url,
                    columns=[
                        "game_id", "season", "week", "posteam", "drive", "play_type", "epa",
                        "success", "yards_gained", "passing_yards", "rushing_yards",
                        "third_down_converted", "third_down_failed", "touchdown",
                        "interception", "fumble_lost", "yardline_100",
                    ],
                )
            except Exception as exc:  # noqa: BLE001 - one season failing must not kill the rest
                errors.append(f"pbp {season} download failed: {exc}")
                continue
            records, season_errors = self.aggregate_team_stats(pbp, schedules, season)
            all_records.extend(records)
            errors.extend(season_errors)

        if all_records:
            self._record_success()
        if errors:
            self.last_errors = errors
        return ProviderResult(source=self.name, records=all_records, errors=errors)
