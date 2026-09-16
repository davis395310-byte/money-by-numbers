#!/usr/bin/env python3
"""Run the odds ingestion pipeline (The Odds API v4 -> MongoDB).

Each accepted snapshot is INSERTED into the `odds` collection — repeated
snapshots for the same game build line-movement history (never overwritten;
indexed on (game_id, timestamp)). A credit ledger in `odds_credit_usage`
tracks the free-tier budget (500 credits/month); the script refuses to pull
when the budget check fails.

Examples:
    # Live pull against a real MongoDB (needs ODDS_API_KEY + budget):
    python scripts/ingest_odds.py

    # Live pull against an in-memory database (nothing persists):
    python scripts/ingest_odds.py --mongomock

    # Parser exercise only: no key, no network, no database writes:
    python scripts/ingest_odds.py --dry-run-no-key

Run from the backend/ directory so ``app`` is importable.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import uuid
from datetime import datetime, timezone

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

FIXTURE_PATH = os.path.join(
    os.path.dirname(os.path.abspath(__file__)), "fixtures", "theoddsapi_sample.json"
)
MAX_STORED_ERRORS = 200


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _clean_odds_document(record: dict, now: datetime) -> dict:
    return {
        "game_id": record["game_id"],
        "timestamp": record["timestamp"],
        "sportsbook": record["sportsbook"],
        "season": record.get("season"),
        "week": record.get("week"),
        "spread": record.get("spread"),
        "moneyline_home": record.get("moneyline_home"),
        "moneyline_away": record.get("moneyline_away"),
        "total": record.get("total"),
        "source": "theoddsapi",
        "ingested_at": now,
    }


def _write_run_doc(
    db,
    run_id: str,
    started_at: datetime,
    finished_at: datetime,
    status: str,
    received: int,
    accepted: int,
    rejected: int,
    errors: list,
) -> dict:
    run_doc = {
        "run_id": run_id,
        "source": "theoddsapi",
        "started_at": started_at,
        "finished_at": finished_at,
        "status": status,
        "records_received": received,
        "records_accepted": accepted,
        "records_rejected": rejected,
        "errors": errors[:MAX_STORED_ERRORS],
        "errors_truncated": len(errors) > MAX_STORED_ERRORS,
    }
    db["ingestion_runs"].insert_one(run_doc)
    return run_doc


def run_live_ingest(args) -> int:
    from app.config import get_settings
    from app.data_providers import TheOddsApiProvider, check_budget
    from app.database import ensure_indexes
    from app.models.core import OddsSnapshot

    settings = get_settings()
    if not (settings.ODDS_API_KEY and settings.ODDS_API_KEY.strip()):
        print(
            "Refusing to run: ODDS_API_KEY is not set. Live odds ingestion "
            "needs a The Odds API key (free tier: 500 credits/month). "
            "Set ODDS_API_KEY in your environment or .env, or run with "
            "--dry-run-no-key to exercise the parser against the synthetic "
            "fixture only."
        )
        return 2

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

    started_at = _utcnow()
    run_id = uuid.uuid4().hex

    # Budget check BEFORE any network call: a False budget means no pull.
    ok, reason = check_budget(db, min_required=args.min_credits)
    if not ok and not args.allow_unknown_budget:
        print(f"Refusing to pull odds: {reason}")
        _write_run_doc(
            db, run_id, started_at, _utcnow(), "skipped", 0, 0, 0, [reason]
        )
        return 2
    if not ok:
        print(f"WARNING: budget check failed but --allow-unknown-budget given: {reason}")
    else:
        print(f"budget: {reason}")

    provider = TheOddsApiProvider()
    result = provider.fetch_odds(db=db)
    provider.record_credit_usage(db)

    received = len(result.records)
    accepted = 0
    rejected = 0
    errors = list(result.errors)
    now = _utcnow()
    for record in result.records:
        try:
            snap = OddsSnapshot(**record)
        except Exception as exc:  # noqa: BLE001 - invalid record rejected, never stored
            rejected += 1
            errors.append(
                f"snapshot rejected ({record.get('sportsbook')}/"
                f"{record.get('game_id')}): {exc}"
            )
            continue
        # Insert each snapshot: repeated pulls for the same game build
        # line-movement history. (game_id, timestamp) index supports this.
        db["odds"].insert_one(_clean_odds_document(snap.model_dump(), now))
        accepted += 1

    finished_at = _utcnow()
    status = "success" if not errors else ("partial" if accepted else "failed")
    run_doc = _write_run_doc(
        db, run_id, started_at, finished_at, status,
        received, accepted, rejected, errors,
    )
    print(json.dumps(
        {
            "run_id": run_doc["run_id"],
            "status": run_doc["status"],
            "records_received": received,
            "records_accepted": accepted,
            "records_rejected": rejected,
            "error_count": len(errors),
            "first_errors": errors[:5],
            "credits_remaining": provider.last_credit_remaining,
            "credits_used": provider.last_credit_used,
        },
        indent=2,
        default=str,
    ))
    return 0 if status in ("success", "partial") else 1


def run_dry_run() -> int:
    """Exercise parsing + validation against the synthetic fixture only.

    No API key, no network, no database writes. The fixture numbers are
    made up for parser testing and are never persisted or presented as real.
    """
    from app.data_providers import TheOddsApiProvider
    from app.models.core import OddsSnapshot

    with open(FIXTURE_PATH, encoding="utf-8") as fh:
        fixture = json.load(fh)
    events = fixture["events"]
    records, errors = TheOddsApiProvider.parse_odds_payload(events, db=None)
    accepted = 0
    rejected = 0
    validation_errors = []
    for record in records:
        try:
            OddsSnapshot(**record)
            accepted += 1
        except Exception as exc:  # noqa: BLE001
            rejected += 1
            validation_errors.append(str(exc))
    print(json.dumps(
        {
            "mode": "dry-run-no-key",
            "fixture": FIXTURE_PATH,
            "events_in_fixture": len(events),
            "records_parsed": len(records),
            "records_accepted": accepted,
            "records_rejected": rejected,
            "parse_errors": errors,
            "validation_errors": validation_errors,
            "sample_record": records[0] if records else None,
        },
        indent=2,
        default=str,
    ))
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Ingest live NFL odds from The Odds API v4 into MongoDB. "
            "Refuses to run without ODDS_API_KEY (exit 2) unless "
            "--dry-run-no-key is given. Enforces the free-tier credit "
            "budget (500 credits/month) before pulling."
        )
    )
    parser.add_argument("--mongomock", action="store_true",
                        help="use in-memory mongomock instead of MongoDB")
    parser.add_argument("--mongo-uri", default=None,
                        help="MongoDB URI (overrides MONGODB_URI)")
    parser.add_argument("--db", default=None,
                        help="database name (overrides MONGODB_DB)")
    parser.add_argument("--dry-run-no-key", action="store_true",
                        help="parse the synthetic fixture only: no key, no network, no writes")
    parser.add_argument("--min-credits", type=int, default=1,
                        help="minimum remaining credits required to pull (default: 1)")
    parser.add_argument("--allow-unknown-budget", action="store_true",
                        help="pull even when the credit ledger has no current-month "
                             "balance (e.g. brand-new key). The ledger is seeded "
                             "from the response headers of this run.")
    args = parser.parse_args()

    if args.dry_run_no_key:
        return run_dry_run()
    return run_live_ingest(args)


if __name__ == "__main__":
    raise SystemExit(main())
