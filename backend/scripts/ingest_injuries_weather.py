#!/usr/bin/env python3
"""Run the injuries + weather ingestion pipeline.

Injuries: nflverse ``injuries`` release (official weekly NFL injury reports,
2009+, no API key needed).
Weather: Open-Meteo (free, no API key) kickoff-hour conditions at the venue.

Examples:
    # Dry-run against an in-memory database (no Mongo needed):
    python scripts/ingest_injuries_weather.py --seasons 2024-2025 --weeks 1-18 --mongomock

    # Against a real MongoDB (URI from MONGODB_URI or --mongo-uri):
    python scripts/ingest_injuries_weather.py --seasons 2025 --weeks 1

Games come from the ``games`` collection when it has data; otherwise they
are fetched from the nflverse provider for the requested seasons/weeks.

Run from the backend/ directory so ``app`` is importable.
"""

from __future__ import annotations

import argparse
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


def parse_range(text: str) -> list[int]:
    text = text.strip()
    if "-" in text:
        start, end = text.split("-", 1)
        return list(range(int(start), int(end) + 1))
    return [int(s) for s in text.split(",") if s.strip()]


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Ingest NFL injuries + game weather into MongoDB."
    )
    parser.add_argument("--seasons", default="2025", help="'2024-2025' or '2025'")
    parser.add_argument("--weeks", default="1-22", help="'1-18' or '1,2,3'")
    parser.add_argument("--mongomock", action="store_true",
                        help="use in-memory mongomock instead of MongoDB")
    parser.add_argument("--mongo-uri", default=None,
                        help="MongoDB URI (overrides MONGODB_URI)")
    parser.add_argument("--db", default=None, help="database name (overrides MONGODB_DB)")
    parser.add_argument("--max-games", type=int, default=32,
                        help="cap on weather games per run (default 32)")
    args = parser.parse_args()

    seasons = parse_range(args.seasons)
    weeks = set(parse_range(args.weeks))

    from app.data_providers import NflverseInjuryProvider, OpenMeteoWeatherProvider
    from app.data_providers.nflverse import NflverseProvider
    from app.database import ensure_indexes
    from app.ingestion.pipeline import run_context_ingestion

    if args.mongomock:
        import mongomock

        db = mongomock.MongoClient()["money_by_numbers"]
        print("database: mongomock (in-memory)")
    else:
        from pymongo import MongoClient

        uri = args.mongo_uri or os.environ.get("MONGODB_URI")
        if not uri:
            print("ERROR: no MongoDB URI. Set MONGODB_URI, pass --mongo-uri, or use --mongomock.")
            return 2
        db_name = args.db or os.environ.get("MONGODB_DB") or "money_by_numbers"
        db = MongoClient(uri, serverSelectionTimeoutMS=5000)[db_name]
        print(f"database: {db_name}")

    ensure_indexes(db)

    # Games for weather: prefer the games collection, fall back to the
    # nflverse provider so the script works on a fresh database.
    games: list[dict] = []
    try:
        stored = list(
            db["games"].find(
                {"season": {"$in": seasons}, "week": {"$in": sorted(weeks)}},
                {"_id": 0, "game_id": 1, "venue": 1, "game_date": 1, "roof": 1,
                 "home_team": 1, "away_team": 1, "season": 1, "week": 1},
            )
        )
        for doc in stored:
            games.append(
                {
                    "game_id": doc.get("game_id"),
                    "venue": doc.get("venue"),
                    "kickoff": doc.get("game_date"),
                    "roof": doc.get("roof"),
                }
            )
    except Exception as exc:  # noqa: BLE001 - fall back to provider
        print(f"games collection read failed ({exc}); using nflverse provider")

    if not games:
        print("games collection empty; fetching schedule from nflverse provider")
        nflverse = NflverseProvider()
        result = nflverse.fetch_games(seasons)
        for record in result.records:
            if record.get("week") not in weeks:
                continue
            games.append(
                {
                    "game_id": record.get("game_id"),
                    "venue": record.get("venue"),
                    "kickoff": record.get("game_date"),
                    "roof": record.get("roof"),
                }
            )
        for err in result.errors[:5]:
            print(f"provider warning: {err[:150]}")

    games = games[: args.max_games]
    print(f"seasons: {seasons} weeks: {sorted(weeks)} games for weather: {len(games)}")

    injury_provider = NflverseInjuryProvider()
    weather_provider = OpenMeteoWeatherProvider()
    run_doc = run_context_ingestion(
        db, injury_provider, weather_provider, seasons, games
    )

    print(json.dumps(
        {
            "run_id": run_doc["run_id"],
            "status": run_doc["status"],
            "records_received": run_doc["records_received"],
            "records_accepted": run_doc["records_accepted"],
            "records_rejected": run_doc["records_rejected"],
            "per_season": run_doc.get("per_season", {}),
            "error_count": len(run_doc["errors"]),
            "first_errors": run_doc["errors"][:5],
        },
        indent=2,
        default=str,
    ))
    return 0 if run_doc["status"] in ("success", "partial") else 1


if __name__ == "__main__":
    raise SystemExit(main())
