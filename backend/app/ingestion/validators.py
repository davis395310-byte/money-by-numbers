"""Data validation for NFL ingestion (product spec section 15).

Every check returns a list of human-readable error strings; an empty list
means the record is valid. Rejected records are counted and their reasons
stored on the ingestion run — corrupt data is NEVER silently accepted, and
missing values are NEVER fabricated (validators only reject; they never
invent replacements).
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional
from zoneinfo import ZoneInfo

from ..models.core import VALID_TEAM_ABBRS

# nflverse kickoff times are Eastern; chronological checks use the US
# calendar date of the game, not the UTC instant (a night game can fall
# on the next UTC day).
EASTERN = ZoneInfo("America/New_York")

REQUIRED_GAME_FIELDS = (
    "game_id", "season", "week", "game_date", "home_team", "away_team", "status",
)
REQUIRED_STAT_FIELDS = ("game_id", "team")
REQUIRED_TEAM_FIELDS = ("abbr", "name", "city", "conference", "division")


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _as_datetime(value: Any) -> Optional[datetime]:
    if isinstance(value, datetime):
        dt = value
    elif isinstance(value, str):
        try:
            dt = datetime.fromisoformat(value)
        except ValueError:
            return None
    else:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt


def validate_team_record(record: Dict[str, Any]) -> List[str]:
    errors: List[str] = []
    for field in REQUIRED_TEAM_FIELDS:
        if not record.get(field):
            errors.append(f"missing required field: {field}")
    abbr = record.get("abbr")
    if abbr and abbr not in VALID_TEAM_ABBRS:
        errors.append(f"invalid team abbreviation: {abbr!r}")
    if record.get("conference") not in ("AFC", "NFC", None, "") and record.get("conference"):
        errors.append(f"invalid conference: {record.get('conference')!r}")
    return errors


def validate_game_record(
    record: Dict[str, Any], now: Optional[datetime] = None
) -> List[str]:
    """Validate one normalized game record.

    Checks: required fields, team validity, week/season ranges, date
    parseability, score sanity, status/score consistency, and future data
    (a ``final`` game dated in the future is rejected).
    """
    now = now or _utcnow()
    errors: List[str] = []

    for field in REQUIRED_GAME_FIELDS:
        if record.get(field) in (None, ""):
            errors.append(f"missing required field: {field}")

    home, away = record.get("home_team"), record.get("away_team")
    for label, team in (("home_team", home), ("away_team", away)):
        if team and team not in VALID_TEAM_ABBRS:
            errors.append(f"invalid team in {label}: {team!r}")
    if home and away and home == away:
        errors.append("home_team and away_team must differ")

    season = record.get("season")
    if season is not None:
        try:
            season_i = int(season)
            if season_i < 1920 or season_i > now.year + 1:
                errors.append(f"invalid season: {season!r}")
        except (TypeError, ValueError):
            errors.append(f"invalid season: {season!r}")

    week = record.get("week")
    if week is not None:
        try:
            week_i = int(week)
            if week_i < 1 or week_i > 22:
                errors.append(f"invalid week: {week!r}")
        except (TypeError, ValueError):
            errors.append(f"invalid week: {week!r}")

    game_date = _as_datetime(record.get("game_date"))
    if record.get("game_date") not in (None, "") and game_date is None:
        errors.append(f"invalid game_date: {record.get('game_date')!r}")

    status = record.get("status")
    if status not in ("scheduled", "final", None, ""):
        errors.append(f"invalid status: {status!r}")

    home_score, away_score = record.get("home_score"), record.get("away_score")
    for label, score in (("home_score", home_score), ("away_score", away_score)):
        if score is not None:
            if isinstance(score, bool) or not isinstance(score, (int, float)) or int(score) != score:
                errors.append(f"invalid {label}: {score!r} (must be an integer)")
            elif score < 0:
                errors.append(f"invalid {label}: {score!r} (negative)")

    # Status / score consistency.
    if status == "final" and (home_score is None or away_score is None):
        errors.append("final game missing score(s)")
    if status == "scheduled" and (home_score is not None or away_score is not None):
        errors.append("scheduled game already has score(s)")

    # Future data: a final game dated in the future is corrupt by definition.
    if status == "final" and game_date is not None and game_date > now:
        errors.append(f"future data: final game dated {game_date.isoformat()}")

    # Chronological sanity: the date must be within the plausible window for
    # the season. Regular-season weeks 1-18 run September-January, so weeks
    # 17/18 can fall in January of season+1. nflverse numbers Wild Card as
    # week 18 (pre-2021) or week 19 (2021+); all playoff weeks are Jan/Feb
    # of season+1. Compared in Eastern time: the business date of a game is
    # its US date, while the stored instant is UTC.
    if game_date is not None and season is not None and not errors:
        try:
            season_i = int(season)
            week_i = int(week) if week is not None else 1
            ok_years = {season_i, season_i + 1} if week_i <= 18 else {season_i + 1}
            eastern = game_date.astimezone(EASTERN) if game_date.tzinfo else game_date
            if eastern.year not in ok_years:
                errors.append(
                    f"chronological mismatch: season {season_i} week {week_i} "
                    f"dated {eastern.date().isoformat()}"
                )
        except (TypeError, ValueError):
            pass

    return errors


def validate_team_stat_record(
    record: Dict[str, Any], known_game_ids: Optional[set] = None
) -> List[str]:
    errors: List[str] = []
    for field in REQUIRED_STAT_FIELDS:
        if record.get(field) in (None, ""):
            errors.append(f"missing required field: {field}")
    team = record.get("team")
    if team and team not in VALID_TEAM_ABBRS:
        errors.append(f"invalid team: {team!r}")
    if known_game_ids is not None and record.get("game_id") not in known_game_ids:
        errors.append(f"unknown game_id: {record.get('game_id')!r}")
    for numeric_field in ("points", "yards_total", "turnovers"):
        value = record.get(numeric_field)
        if value is not None and value < 0:
            errors.append(f"invalid {numeric_field}: {value!r} (negative)")
    epa = record.get("epa_per_play")
    if epa is not None and not (-15.0 <= epa <= 15.0):
        errors.append(f"implausible epa_per_play: {epa!r}")
    return errors


def is_stale(last_success: Optional[datetime], ttl_hours: float, now: Optional[datetime] = None) -> bool:
    """True when the last successful ingestion is older than the TTL."""
    now = now or _utcnow()
    if last_success is None:
        return True
    return (now - last_success).total_seconds() / 3600.0 > ttl_hours


# ---------------------------------------------------------------------------
# Injury records (Phase 5)
# ---------------------------------------------------------------------------

REQUIRED_INJURY_FIELDS = ("season", "week", "team", "full_name")

#: Official game-status values in the nflverse injuries release. Unknown
#: values are NOT rejected — they are kept verbatim and surfaced as
#: warnings by ``injury_warnings`` (the source occasionally uses values
#: like "Note" for informational entries).
KNOWN_REPORT_STATUSES = {"Out", "Doubtful", "Questionable"}

#: Practice-participation values in the nflverse injuries release.
KNOWN_PRACTICE_STATUSES = {
    "Did Not Participate In Practice",
    "Limited Participation in Practice",
    "Full Participation in Practice",
}


def validate_injury_record(
    record: Dict[str, Any], now: Optional[datetime] = None
) -> List[str]:
    """Validate one normalized injury record.

    Rejects: missing required fields, invalid team, bad season/week, a
    ``date_modified`` in the future (future-data rule — injury reports
    cannot be modified after "now"). Unknown report/practice statuses are
    accepted (see ``injury_warnings``).
    """
    now = now or _utcnow()
    errors: List[str] = []

    for field in REQUIRED_INJURY_FIELDS:
        if record.get(field) in (None, ""):
            errors.append(f"missing required field: {field}")

    team = record.get("team")
    if team and team not in VALID_TEAM_ABBRS:
        errors.append(f"invalid team: {team!r}")

    season = record.get("season")
    if season is not None:
        try:
            season_i = int(season)
            if season_i < 1960 or season_i > now.year + 1:
                errors.append(f"invalid season: {season!r}")
        except (TypeError, ValueError):
            errors.append(f"invalid season: {season!r}")

    week = record.get("week")
    if week is not None:
        try:
            week_i = int(week)
            if week_i < 1 or week_i > 22:
                errors.append(f"invalid week: {week!r}")
        except (TypeError, ValueError):
            errors.append(f"invalid week: {week!r}")

    modified = _as_datetime(record.get("date_modified"))
    if record.get("date_modified") is not None and modified is None:
        errors.append(f"unparseable date_modified: {record.get('date_modified')!r}")
    elif modified is not None and modified > now + timedelta(hours=24):
        errors.append(
            f"date_modified in the future: {record.get('date_modified')!r} (future data rejected)"
        )

    return errors


def injury_warnings(record: Dict[str, Any]) -> List[str]:
    """Non-rejecting warnings for an injury record (unknown statuses)."""
    warnings: List[str] = []
    status = record.get("report_status")
    if status is not None and status not in KNOWN_REPORT_STATUSES:
        warnings.append(f"unknown report_status kept verbatim: {status!r}")
    practice = record.get("practice_status")
    if practice is not None and practice not in KNOWN_PRACTICE_STATUSES:
        warnings.append(f"unknown practice_status kept verbatim: {practice!r}")
    return warnings


# ---------------------------------------------------------------------------
# Weather records (Phase 5)
# ---------------------------------------------------------------------------

REQUIRED_WEATHER_FIELDS = ("game_id", "kickoff", "latitude", "longitude")


def validate_weather_record(record: Dict[str, Any]) -> List[str]:
    """Validate one normalized weather record.

    Rejects: missing required fields, unparseable kickoff, coordinates out
    of range, physically implausible values (temp/wind/precip). Missing
    measurements (None) are accepted — the provider leaves them null
    rather than inventing them.
    """
    errors: List[str] = []

    for field in REQUIRED_WEATHER_FIELDS:
        if record.get(field) in (None, ""):
            errors.append(f"missing required field: {field}")

    if record.get("kickoff") is not None and _as_datetime(record.get("kickoff")) is None:
        errors.append(f"unparseable kickoff: {record.get('kickoff')!r}")

    lat = record.get("latitude")
    if lat is not None:
        try:
            if not (-90.0 <= float(lat) <= 90.0):
                errors.append(f"latitude out of range: {lat!r}")
        except (TypeError, ValueError):
            errors.append(f"invalid latitude: {lat!r}")
    lon = record.get("longitude")
    if lon is not None:
        try:
            if not (-180.0 <= float(lon) <= 180.0):
                errors.append(f"longitude out of range: {lon!r}")
        except (TypeError, ValueError):
            errors.append(f"invalid longitude: {lon!r}")

    temp = record.get("temp_f")
    if temp is not None:
        try:
            if not (-80.0 <= float(temp) <= 130.0):
                errors.append(f"implausible temp_f: {temp!r}")
        except (TypeError, ValueError):
            errors.append(f"invalid temp_f: {temp!r}")
    wind = record.get("wind_mph")
    if wind is not None:
        try:
            if float(wind) < 0 or float(wind) > 250:
                errors.append(f"implausible wind_mph: {wind!r}")
        except (TypeError, ValueError):
            errors.append(f"invalid wind_mph: {wind!r}")
    prob = record.get("precip_prob")
    if prob is not None:
        try:
            if not (0.0 <= float(prob) <= 1.0):
                errors.append(f"precip_prob outside [0,1]: {prob!r}")
        except (TypeError, ValueError):
            errors.append(f"invalid precip_prob: {prob!r}")

    return errors
