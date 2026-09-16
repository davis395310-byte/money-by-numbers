"""NFL ingestion pipeline (product spec sections 13-15).

``run_ingestion`` pulls teams, games, and per-team game stats from a
provider, validates every record, de-duplicates, upserts into MongoDB, and
writes an ``ingestion_runs`` document recording source, timestamps, and
records_received / records_accepted / records_rejected plus all errors.

Nothing is ever silently dropped: every rejected record increments the
rejected counter and appends its reason to the run's error list (capped for
storage). Missing values stay NULL in the database.
"""

from __future__ import annotations

import logging
import uuid
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Tuple

from ..data_providers.base import NFLDataProvider
from . import validators

log = logging.getLogger("mnb.ingestion")

MAX_STORED_ERRORS = 200


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _upsert(collection: Any, filter_: Dict[str, Any], document: Dict[str, Any]) -> None:
    collection.update_one(filter_, {"$set": document}, upsert=True)


def _clean_stat_document(record: Dict[str, Any], now: datetime) -> Dict[str, Any]:
    """Project a normalized stat dict onto the stored game_stats shape."""
    return {
        "game_id": record["game_id"],
        "team": record["team"],
        "season": record.get("season"),
        "week": record.get("week"),
        "points": record.get("points"),
        "yards_total": record.get("yards_total"),
        "passing_yards": record.get("passing_yards"),
        "rushing_yards": record.get("rushing_yards"),
        "turnovers": record.get("turnovers"),
        "third_down_conv": record.get("third_down_conv"),
        "third_down_att": record.get("third_down_att"),
        "red_zone_td": record.get("red_zone_td"),
        "red_zone_att": record.get("red_zone_att"),
        "epa_total": record.get("epa_total"),
        "epa_per_play": record.get("epa_per_play"),
        "success_rate": record.get("success_rate"),
        "ingested_at": now,
    }


def _clean_game_document(record: Dict[str, Any], now: datetime) -> Dict[str, Any]:
    return {
        "game_id": record["game_id"],
        "season": record["season"],
        "week": record["week"],
        "home_team": record["home_team"],
        "away_team": record["away_team"],
        "game_date": record["game_date"],
        "status": record["status"],
        "home_score": record["home_score"],
        "away_score": record["away_score"],
        "venue": record.get("venue"),
        "roof": record.get("roof"),
        "overtime": record.get("overtime"),
        "ingested_at": now,
    }


def _dedup_key_game(record: Dict[str, Any]) -> Tuple:
    return (
        record.get("game_id"),
        record.get("season"),
        record.get("week"),
        record.get("home_team"),
        record.get("away_team"),
    )


def ingest_teams(db: Any, provider: NFLDataProvider, now: datetime) -> Dict[str, Any]:
    """Upsert the 32 teams. Returns received/accepted/rejected + errors."""
    result = provider.fetch_teams()
    received = len(result.records)
    accepted = 0
    rejected = 0
    errors: List[str] = list(result.errors)
    for record in result.records:
        record_errors = validators.validate_team_record(record)
        if record_errors:
            rejected += 1
            errors.append(f"team {record.get('abbr')}: " + "; ".join(record_errors))
            continue
        _upsert(db["teams"], {"abbr": record["abbr"]}, {**record, "ingested_at": now})
        accepted += 1
    return {"received": received, "accepted": accepted, "rejected": rejected, "errors": errors}


def _bump(per_season: Dict[Any, Dict[str, int]], season: Any, field: str) -> None:
    bucket = per_season.setdefault(season if season is not None else "unknown",
                                   {"received": 0, "accepted": 0, "rejected": 0})
    bucket[field] += 1


def ingest_games(
    db: Any,
    provider: NFLDataProvider,
    seasons: List[int],
    now: datetime,
) -> Tuple[Dict[str, Any], set]:
    """Fetch, validate, dedupe, and upsert games.

    Returns (summary, accepted_game_ids) — the id set lets the stats stage
    reject orphan stat rows. Summary includes a per-season breakdown.
    """
    result = provider.fetch_games(seasons)
    received = len(result.records)
    accepted = 0
    rejected = 0
    errors: List[str] = list(result.errors)
    accepted_game_ids: set = set()
    per_season: Dict[Any, Dict[str, int]] = {}

    seen_in_batch: set = set()
    for record in result.records:
        season = record.get("season")
        _bump(per_season, season, "received")
        record_errors = validators.validate_game_record(record, now=now)
        key = _dedup_key_game(record)
        if key in seen_in_batch:
            record_errors.append("duplicate game in provider batch")
        if record_errors:
            rejected += 1
            _bump(per_season, season, "rejected")
            errors.append(f"game {record.get('game_id')}: " + "; ".join(record_errors))
            continue
        seen_in_batch.add(key)
        _upsert(db["games"], {"game_id": record["game_id"]}, _clean_game_document(record, now))
        accepted += 1
        _bump(per_season, season, "accepted")
        accepted_game_ids.add(record["game_id"])

    # Also count games already present (idempotent re-runs must not inflate).
    summary = {
        "received": received,
        "accepted": accepted,
        "rejected": rejected,
        "errors": errors,
        "per_season": per_season,
    }
    return summary, accepted_game_ids


def ingest_team_stats(
    db: Any,
    provider: NFLDataProvider,
    seasons: List[int],
    known_game_ids: set,
    now: datetime,
) -> Dict[str, Any]:
    result = provider.fetch_team_game_stats(seasons)
    received = len(result.records)
    accepted = 0
    rejected = 0
    errors: List[str] = list(result.errors)
    per_season: Dict[Any, Dict[str, int]] = {}
    seen_in_batch: set = set()
    for record in result.records:
        season = record.get("season")
        _bump(per_season, season, "received")
        record_errors = validators.validate_team_stat_record(record, known_game_ids or None)
        key = (record.get("game_id"), record.get("team"))
        if key in seen_in_batch:
            record_errors.append("duplicate team-game stat row in provider batch")
        if record_errors:
            rejected += 1
            _bump(per_season, season, "rejected")
            errors.append(
                f"stat {record.get('game_id')}/{record.get('team')}: " + "; ".join(record_errors)
            )
            continue
        seen_in_batch.add(key)
        _upsert(
            db["game_stats"],
            {"game_id": record["game_id"], "team": record["team"]},
            _clean_stat_document(record, now),
        )
        accepted += 1
        _bump(per_season, season, "accepted")
    return {
        "received": received,
        "accepted": accepted,
        "rejected": rejected,
        "errors": errors,
        "per_season": per_season,
    }


def run_ingestion(
    db: Any,
    provider: NFLDataProvider,
    seasons: List[int],
    run_id: Optional[str] = None,
) -> Dict[str, Any]:
    """Run a full ingestion pass and record it in ``ingestion_runs``."""
    run_id = run_id or uuid.uuid4().hex
    started_at = _utcnow()
    log.info("ingestion run %s started (source=%s seasons=%s)", run_id, provider.name, seasons)

    team_summary = ingest_teams(db, provider, started_at)
    game_summary, accepted_game_ids = ingest_games(db, provider, seasons, started_at)
    stat_summary = ingest_team_stats(db, provider, seasons, accepted_game_ids, started_at)

    finished_at = _utcnow()
    all_errors: List[str] = (
        team_summary["errors"] + game_summary["errors"] + stat_summary["errors"]
    )
    total_rejected = team_summary["rejected"] + game_summary["rejected"] + stat_summary["rejected"]
    status = "success" if not all_errors else ("partial" if (team_summary["accepted"] or game_summary["accepted"]) else "failed")

    # Merge per-season breakdowns (string keys: MongoDB field names).
    per_season: Dict[str, Dict[str, Any]] = {}
    for dataset, summary in (("games", game_summary), ("game_stats", stat_summary)):
        for season, counts in summary.get("per_season", {}).items():
            bucket = per_season.setdefault(str(season), {})
            bucket[dataset] = counts

    run_doc = {
        "run_id": run_id,
        "source": provider.name,
        "seasons": seasons,
        "started_at": started_at,
        "finished_at": finished_at,
        "status": status,
        "records_received": {
            "teams": team_summary["received"],
            "games": game_summary["received"],
            "game_stats": stat_summary["received"],
        },
        "records_accepted": {
            "teams": team_summary["accepted"],
            "games": game_summary["accepted"],
            "game_stats": stat_summary["accepted"],
        },
        "records_rejected": {
            "teams": team_summary["rejected"],
            "games": game_summary["rejected"],
            "game_stats": stat_summary["rejected"],
        },
        "total_rejected": total_rejected,
        "per_season": per_season,
        "errors": all_errors[:MAX_STORED_ERRORS],
        "errors_truncated": len(all_errors) > MAX_STORED_ERRORS,
    }
    db["ingestion_runs"].insert_one(run_doc)
    log.info(
        "ingestion run %s finished: status=%s games %d/%d accepted, stats %d/%d accepted, %d errors",
        run_id, status,
        game_summary["accepted"], game_summary["received"],
        stat_summary["accepted"], stat_summary["received"],
        len(all_errors),
    )
    return run_doc


# ---------------------------------------------------------------------------
# Phase 5: injuries + weather
# ---------------------------------------------------------------------------


def _clean_injury_document(record: Dict[str, Any], now: datetime) -> Dict[str, Any]:
    return {
        "season": record.get("season"),
        "week": record.get("week"),
        "game_type": record.get("game_type"),
        "team": record.get("team"),
        "gsis_id": record.get("gsis_id"),
        "position": record.get("position"),
        "full_name": record.get("full_name"),
        "first_name": record.get("first_name"),
        "last_name": record.get("last_name"),
        "report_status": record.get("report_status"),
        "report_primary_injury": record.get("report_primary_injury"),
        "report_secondary_injury": record.get("report_secondary_injury"),
        "practice_status": record.get("practice_status"),
        "practice_primary_injury": record.get("practice_primary_injury"),
        "practice_secondary_injury": record.get("practice_secondary_injury"),
        "date_modified": record.get("date_modified"),
        "source": record.get("source", "nflverse-injuries"),
        "ingested_at": now,
    }


def _clean_weather_document(record: Dict[str, Any], now: datetime) -> Dict[str, Any]:
    return {
        "game_id": record.get("game_id"),
        "venue": record.get("venue"),
        "venue_matched": record.get("venue_matched"),
        "latitude": record.get("latitude"),
        "longitude": record.get("longitude"),
        "kickoff": record.get("kickoff"),
        "temp_f": record.get("temp_f"),
        "wind_mph": record.get("wind_mph"),
        "wind_gust_mph": record.get("wind_gust_mph"),
        "precip_prob": record.get("precip_prob"),
        "precip_in": record.get("precip_in"),
        "weathercode": record.get("weathercode"),
        "conditions": record.get("conditions"),
        "dome": record.get("dome"),
        "source": record.get("source", "open-meteo"),
        "ingested_at": now,
    }


def ingest_injuries(
    db: Any,
    provider: Any,
    seasons: List[int],
    now: datetime,
) -> Dict[str, Any]:
    """Fetch, validate, dedupe, and upsert injury reports.

    Dedup key: (season, week, gsis_id) — one row per player per week.
    Unknown statuses are accepted with warnings (never rewritten).
    """
    result = provider.fetch_injuries(seasons)
    received = len(result.records)
    accepted = 0
    rejected = 0
    errors: List[str] = list(result.errors)
    per_season: Dict[Any, Dict[str, int]] = {}
    seen_in_batch: set = set()

    for record in result.records:
        season = record.get("season")
        _bump(per_season, season, "received")
        record_errors = validators.validate_injury_record(record, now=now)
        key = (record.get("season"), record.get("week"), record.get("gsis_id"))
        if key in seen_in_batch:
            record_errors.append("duplicate injury row in provider batch")
        if record_errors:
            rejected += 1
            _bump(per_season, season, "rejected")
            errors.append(
                f"injury {record.get('full_name')}/{record.get('team')}: "
                + "; ".join(record_errors)
            )
            continue
        for warning in validators.injury_warnings(record):
            errors.append(
                f"injury {record.get('full_name')}/{record.get('team')}: warning: {warning}"
            )
        seen_in_batch.add(key)
        _upsert(
            db["injuries"],
            {
                "season": record.get("season"),
                "week": record.get("week"),
                "gsis_id": record.get("gsis_id"),
            },
            _clean_injury_document(record, now),
        )
        accepted += 1
        _bump(per_season, season, "accepted")

    return {
        "received": received,
        "accepted": accepted,
        "rejected": rejected,
        "errors": errors,
        "per_season": per_season,
    }


def ingest_weather(
    db: Any,
    provider: Any,
    games: List[Dict[str, Any]],
    now: datetime,
) -> Dict[str, Any]:
    """Fetch kickoff-hour weather for games and upsert into ``weather``.

    ``games``: dicts with game_id, venue, kickoff/game_date, roof.
    Dedup key: game_id (one weather record per game; re-runs refresh it).
    """
    result = provider.fetch_game_weather(games)
    received = len(result.records)
    accepted = 0
    rejected = 0
    errors: List[str] = list(result.errors)
    per_season: Dict[Any, Dict[str, int]] = {}

    for record in result.records:
        record_errors = validators.validate_weather_record(record)
        if record_errors:
            rejected += 1
            errors.append(
                f"weather {record.get('game_id')}: " + "; ".join(record_errors)
            )
            continue
        _upsert(
            db["weather"],
            {"game_id": record.get("game_id")},
            _clean_weather_document(record, now),
        )
        accepted += 1

    return {
        "received": received,
        "accepted": accepted,
        "rejected": rejected,
        "errors": errors,
        "per_season": per_season,
    }


def run_context_ingestion(
    db: Any,
    injury_provider: Any,
    weather_provider: Any,
    seasons: List[int],
    games: List[Dict[str, Any]],
    run_id: Optional[str] = None,
) -> Dict[str, Any]:
    """Run injuries + weather ingestion and record it in ``ingestion_runs``."""
    run_id = run_id or uuid.uuid4().hex
    started_at = _utcnow()
    log.info(
        "context ingestion run %s started (injuries=%s weather=%s seasons=%s games=%d)",
        run_id, injury_provider.name, weather_provider.name, seasons, len(games),
    )

    injury_summary = ingest_injuries(db, injury_provider, seasons, started_at)
    weather_summary = ingest_weather(db, weather_provider, games, started_at)

    finished_at = _utcnow()
    all_errors: List[str] = injury_summary["errors"] + weather_summary["errors"]
    total_rejected = injury_summary["rejected"] + weather_summary["rejected"]
    status = (
        "success"
        if not all_errors
        else ("partial" if (injury_summary["accepted"] or weather_summary["accepted"]) else "failed")
    )

    per_season: Dict[str, Dict[str, Any]] = {}
    for dataset, summary in (("injuries", injury_summary),):
        for season, counts in summary.get("per_season", {}).items():
            bucket = per_season.setdefault(str(season), {})
            bucket[dataset] = counts

    run_doc = {
        "run_id": run_id,
        "source": f"{injury_provider.name}+{weather_provider.name}",
        "seasons": seasons,
        "started_at": started_at,
        "finished_at": finished_at,
        "status": status,
        "records_received": {
            "injuries": injury_summary["received"],
            "weather": weather_summary["received"],
        },
        "records_accepted": {
            "injuries": injury_summary["accepted"],
            "weather": weather_summary["accepted"],
        },
        "records_rejected": {
            "injuries": injury_summary["rejected"],
            "weather": weather_summary["rejected"],
        },
        "total_rejected": total_rejected,
        "per_season": per_season,
        "errors": all_errors[:MAX_STORED_ERRORS],
        "errors_truncated": len(all_errors) > MAX_STORED_ERRORS,
    }
    db["ingestion_runs"].insert_one(run_doc)
    log.info(
        "context ingestion run %s finished: status=%s injuries %d/%d, weather %d/%d, %d errors",
        run_id, status,
        injury_summary["accepted"], injury_summary["received"],
        weather_summary["accepted"], weather_summary["received"],
        len(all_errors),
    )
    return run_doc
