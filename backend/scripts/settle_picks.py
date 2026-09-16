#!/usr/bin/env python3
"""Settle locked picks against final scores (Phase 6).

Reads locked picks from the ``predictions`` collection, grades every pick
whose game is final, and writes results to ``pick_results``. Idempotent:
re-running re-grades to identical values and never double-counts.

Examples:
    # Dependency-free smoke run (in-memory; nothing persists):
    python scripts/settle_picks.py --mongomock

    # Against a real MongoDB (URI from MONGODB_URI or --mongo-uri):
    python scripts/settle_picks.py --season 2026 --week 1

Run from the backend/ directory so ``app`` is importable.
"""

from __future__ import annotations

import argparse
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Settle locked NFL picks against final scores."
    )
    parser.add_argument("--season", type=int, default=None, help="limit to season")
    parser.add_argument("--week", type=int, default=None, help="limit to week")
    parser.add_argument("--mongomock", action="store_true",
                        help="use in-memory mongomock instead of MongoDB")
    parser.add_argument("--mongo-uri", default=None,
                        help="MongoDB URI (overrides MONGODB_URI)")
    parser.add_argument("--db", default=None, help="database name (overrides MONGODB_DB)")
    args = parser.parse_args()

    if args.mongo_uri:
        os.environ["MONGODB_URI"] = args.mongo_uri
    if args.db:
        os.environ["MONGODB_DB"] = args.db

    from app.database import ensure_indexes, get_database_name, get_mongo_client
    from app.picks import run_settlement

    if args.mongomock:
        import mongomock

        db = mongomock.MongoClient()["money_by_numbers"]
    else:
        client = get_mongo_client()
        if client is None:
            print("ERROR: MONGODB_URI is not configured.", file=sys.stderr)
            return 2
        db = client[get_database_name()]

    ensure_indexes(db)
    summary = run_settlement(db, season=args.season, week=args.week)
    # datetimes are not JSON-serializable; stringify the summary for display.
    print(json.dumps(summary, default=str, indent=2))
    return 0 if summary["error_count"] == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
