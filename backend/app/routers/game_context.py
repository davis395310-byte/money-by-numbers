"""Game context endpoints (Phase 5, spec sections 31-35).

- ``GET /api/games`` — games from the ``games`` collection (query: season,
  week, upcoming), with venue/kickoff/roof for context display.
- ``GET /api/games/{game_id}/context`` — injury report + weather for one
  game, joined from the ``injuries`` and ``weather`` collections.

Every empty state is honest: no database -> "no_context"; no injury rows
for the teams/week -> injuries "unavailable" (never an empty list
presented as "no injuries"); no weather row -> weather "unavailable".
Injury data is the official weekly NFL injury report (nflverse release),
not a live news wire — the response says so.
"""

from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Query

from ..config import get_settings
from ..database import get_database_name, get_mongo_client

router = APIRouter(tags=["games", "context"])

_PUBLIC_GAME_FIELDS = {
    "_id": 0,
    "game_id": 1,
    "season": 1,
    "week": 1,
    "game_date": 1,
    "home_team": 1,
    "away_team": 1,
    "venue": 1,
    "roof": 1,
    "status": 1,
}

_PUBLIC_INJURY_FIELDS = {
    "_id": 0,
    "season": 1,
    "week": 1,
    "team": 1,
    "position": 1,
    "full_name": 1,
    "report_status": 1,
    "report_primary_injury": 1,
    "practice_status": 1,
    "date_modified": 1,
}

_PUBLIC_WEATHER_FIELDS = {
    "_id": 0,
    "game_id": 1,
    "venue": 1,
    "kickoff": 1,
    "temp_f": 1,
    "wind_mph": 1,
    "wind_gust_mph": 1,
    "precip_prob": 1,
    "precip_in": 1,
    "conditions": 1,
    "dome": 1,
    "source": 1,
    "fetched_at": 1,
}


def _db() -> Any | None:
    if not get_settings().MONGODB_URI:
        return None
    try:
        client = get_mongo_client()
    except Exception:
        return None
    if client is None:
        return None
    return client[get_database_name()]


def _serialize(doc: Dict[str, Any]) -> Dict[str, Any]:
    out: Dict[str, Any] = {}
    for key, value in doc.items():
        if key == "_id":
            continue
        out[key] = value.isoformat() if hasattr(value, "isoformat") else value
    return out


def _summarize_injuries(records: List[Dict[str, Any]]) -> Dict[str, Any]:
    counts = {"Out": 0, "Doubtful": 0, "Questionable": 0, "Other": 0}
    for record in records:
        status = record.get("report_status")
        if status in counts:
            counts[status] += 1
        else:
            counts["Other"] += 1
    return counts


@router.get("/api/games")
def list_games(
    season: Optional[int] = Query(default=None, ge=1920),
    week: Optional[int] = Query(default=None, ge=1, le=22),
    upcoming: bool = Query(default=False),
    limit: int = Query(default=32, ge=1, le=200),
) -> Dict[str, Any]:
    """Games with venue/kickoff/roof for context display."""
    db = _db()
    if db is None:
        return {"status": "no_games", "reason": "database not configured", "games": []}
    query: Dict[str, Any] = {}
    if season is not None:
        query["season"] = season
    if week is not None:
        query["week"] = week
    if upcoming:
        query["game_date"] = {
            "$gte": datetime.now(timezone.utc).replace(tzinfo=None)
        }
        query["status"] = {"$ne": "final"}
    try:
        docs = list(
            db["games"].find(query, _PUBLIC_GAME_FIELDS).sort("game_date", 1).limit(limit)
        )
    except Exception as exc:  # noqa: BLE001 - honest failure, not a crash
        return {"status": "error", "reason": str(exc)[:200], "games": []}
    if not docs:
        return {"status": "no_games", "reason": "no games stored yet", "games": []}
    return {"status": "ok", "games": [_serialize(d) for d in docs]}


@router.get("/api/games/{game_id}/context")
def game_context(game_id: str) -> Dict[str, Any]:
    """Injury report + weather for one game."""
    db = _db()
    if db is None:
        return {
            "status": "no_context",
            "reason": "database not configured",
            "game_id": game_id,
        }
    try:
        game = db["games"].find_one({"game_id": game_id}, _PUBLIC_GAME_FIELDS)
    except Exception as exc:  # noqa: BLE001
        return {"status": "error", "reason": str(exc)[:200], "game_id": game_id}
    if not game:
        return {
            "status": "no_context",
            "reason": f"unknown game_id: {game_id}",
            "game_id": game_id,
        }

    # Injuries: both teams' latest report rows for this game week.
    injuries_section: Dict[str, Any] = {"status": "unavailable", "records": []}
    try:
        teams = [game.get("home_team"), game.get("away_team")]
        injury_docs = list(
            db["injuries"].find(
                {
                    "season": game.get("season"),
                    "week": game.get("week"),
                    "team": {"$in": [t for t in teams if t]},
                },
                _PUBLIC_INJURY_FIELDS,
            ).sort("full_name", 1)
        )
        if injury_docs:
            injuries_section = {
                "status": "ok",
                "records": [_serialize(d) for d in injury_docs],
                "summary": _summarize_injuries(injury_docs),
                "note": (
                    "Official weekly NFL injury report (nflverse release) — "
                    "not a live news wire."
                ),
            }
        else:
            injuries_section = {
                "status": "unavailable",
                "reason": "no injury report rows for these teams/week",
                "records": [],
            }
    except Exception as exc:  # noqa: BLE001
        injuries_section = {"status": "error", "reason": str(exc)[:200], "records": []}

    # Weather: the stored kickoff-hour record for this game.
    weather_section: Dict[str, Any] = {"status": "unavailable"}
    try:
        weather_doc = db["weather"].find_one({"game_id": game_id}, _PUBLIC_WEATHER_FIELDS)
        if weather_doc:
            weather_section = {"status": "ok", "record": _serialize(weather_doc)}
        else:
            weather_section = {
                "status": "unavailable",
                "reason": "no weather record for this game",
            }
    except Exception as exc:  # noqa: BLE001
        weather_section = {"status": "error", "reason": str(exc)[:200]}

    return {
        "status": "ok",
        "game_id": game_id,
        "game": _serialize(game),
        "injuries": injuries_section,
        "weather": weather_section,
    }
