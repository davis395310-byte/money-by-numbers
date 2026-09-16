#!/usr/bin/env python3
"""Run the NFL ingestion pipeline (nflverse -> MongoDB).

Examples:
    # Dry-run against an in-memory database (no Mongo needed):
    python scripts/ingest_nfl.py --seasons 2016-2025 --mongomock

    # Against a real MongoDB (URI from MONGODB_URI or --mongo-uri):
    python scripts/ingest_nfl.py --seasons 2024-2025

Run from the backend/ directory so ``app`` is importable.
"""

from __future__ import annotations

import argparse
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


def parse_seasons(text: str) -> list[int]:
    text = text.strip()
    if "-" in text:
        start, end = text.split("-", 1)
        return list(range(int(start), int(end) + 1))
    return [int(s) for s in text.split(",") if s.strip()]


def main() -> int:
    parser = argparse.ArgumentParser(description="Ingest NFL data from nflverse into MongoDB.")
    parser.add_argument("--seasons", default="2016-2025", help="'2016-2025' or '2024,2025'")
    parser.add_argument("--mongomock", action="store_true", help="use in-memory mongomock instead of MongoDB")
    parser.add_argument("--mongo-uri", default=None, help="MongoDB URI (overrides MONGODB_URI)")
    parser.add_argument("--db", default=None, help="database name (overrides MONGODB_DB)")
    args = parser.parse_args()

    seasons = parse_seasons(args.seasons)
    print(f"seasons: {seasons}")

    from app.data_providers import NflverseProvider
    from app.database import ensure_indexes
    from app.ingestion import run_ingestion

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
    provider = NflverseProvider()
    run_doc = run_ingestion(db, provider, seasons)

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
