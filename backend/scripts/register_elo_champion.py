#!/usr/bin/env python3
"""Register the Elo lineage (MNB-NFL-2026.01) as the explicit champion model.

Walk-forward (2016-2025, measured in ``ml/artifacts/backtest_2016_2025.json``)
put the in-harness Elo baseline at 62.16% winner accuracy vs 59.47% for the
MNB-NFL-2026.02 ensemble -- so the Elo lineage stays champion. This script
writes ``champion: true`` on MNB-NFL-2026.01 and ``champion: false`` on
MNB-NFL-2026.02 (upserting the 2026.02 doc with its measured stats if it is
not registered yet). All metrics are quoted from the artifact files, never
invented. No improvement claim is made for any model vs the market.

Run from the backend/ directory so ``app`` is importable:

    python3 scripts/register_elo_champion.py --mongo-uri <uri>
    python3 scripts/register_elo_champion.py --mongomock   # in-memory dry run
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import datetime, timezone

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
BACKTEST_PATH = os.path.join(REPO_ROOT, "ml", "artifacts", "backtest_2016_2025.json")
ENSEMBLE_META_PATH = os.path.join(
    REPO_ROOT, "ml", "artifacts", "MNB-NFL-2026.02", "metadata.json"
)

CHAMPION_VERSION = "MNB-NFL-2026.01"
ENSEMBLE_VERSION = "MNB-NFL-2026.02"


def _load_json(path: str) -> dict:
    with open(path, "r", encoding="utf-8") as fh:
        return json.load(fh)


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Register MNB-NFL-2026.01 (Elo lineage) as champion model."
    )
    parser.add_argument("--mongomock", action="store_true",
                        help="use in-memory mongomock instead of MongoDB")
    parser.add_argument("--mongo-uri", default=None,
                        help="MongoDB URI (overrides MONGODB_URI)")
    parser.add_argument("--db", default=None, help="database name (overrides MONGODB_DB)")
    args = parser.parse_args()

    backtest = _load_json(BACKTEST_PATH)["aggregate"]
    ensemble_meta = _load_json(ENSEMBLE_META_PATH)
    wf = ensemble_meta["walk_forward"]

    # Measured numbers, quoted from the artifacts (rounded for storage).
    elo_metrics = {
        "accuracy": round(float(backtest["elo_accuracy"]), 4),      # 0.6216
        "brier": round(float(backtest["elo_brier"]), 4),            # 0.2344
        "n_games": int(backtest["n_games"]),                         # 2639
        "source": "ml/artifacts/backtest_2016_2025.json aggregate "
                  "(in-harness Elo baseline, 2016-2025 walk-forward)",
    }
    ensemble_metrics = {
        "accuracy": round(float(wf["accuracy"]), 4),                # 0.5947
        "brier": round(float(wf["brier"]), 4),                       # 0.2371
        "n_games": int(wf["n_games"]),                               # 2639
        "ats_accuracy": round(float(wf["ats_accuracy"]), 4),         # 0.5066
        "ats_n": int(wf["ats_n"]),                                   # 2574
        "source": "ml/artifacts/MNB-NFL-2026.02/metadata.json walk_forward",
    }

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

    now = datetime.now(timezone.utc)

    champion_doc = {
        "version": CHAMPION_VERSION,
        "description": "margin-aware Elo, K=20, HFA=35",
        "lineage": "elo",
        "training_period": "2016-2025 walk-forward (in-harness baseline)",
        "champion": True,
        "metrics": elo_metrics,
        "notes": (
            "Champion by explicit registration: walk-forward measured 62.16% "
            "winner accuracy vs 59.47% for the MNB-NFL-2026.02 ensemble "
            f"({BACKTEST_PATH}). No improvement claim is made for any model "
            "vs the market."
        ),
        "created_at": now,
    }
    res = db["models"].update_one(
        {"version": CHAMPION_VERSION}, {"$set": champion_doc}, upsert=True
    )
    action = "inserted" if res.upserted_id is not None else "updated"
    print(f"{action} {CHAMPION_VERSION}: champion=true, "
          f"accuracy={elo_metrics['accuracy']}, brier={elo_metrics['brier']}, "
          f"n={elo_metrics['n_games']}")

    # Demote the ensemble and make sure its measured stats are recorded.
    res2 = db["models"].update_one(
        {"version": ENSEMBLE_VERSION},
        {
            "$set": {
                "champion": False,
                "metrics": ensemble_metrics,
                "notes": (
                    "Production ensemble v2 (LightGBM + HGB stacking, isotonic "
                    "calibration). Walk-forward 2016-2025: 59.47% winner "
                    "accuracy vs 62.16% Elo baseline; ATS vs closing lines "
                    "50.7% (n=2574): no demonstrated edge vs the market. "
                    "Not champion."
                ),
            },
            "$setOnInsert": {
                "version": ENSEMBLE_VERSION,
                "description": "calibrated stacking ensemble (LightGBM + HGB)",
                "lineage": "ensemble",
                "training_period": ensemble_meta.get("training_period", ""),
                "created_at": now,
            },
        },
        upsert=True,
    )
    action2 = "inserted" if res2.upserted_id is not None else "updated"
    print(f"{action2} {ENSEMBLE_VERSION}: champion=false, "
          f"accuracy={ensemble_metrics['accuracy']}, brier={ensemble_metrics['brier']}, "
          f"n={ensemble_metrics['n_games']}, ats={ensemble_metrics['ats_accuracy']} "
          f"(n={ensemble_metrics['ats_n']})")

    # Safety: no other doc may carry champion:true.
    stray = db["models"].update_many(
        {"version": {"$nin": [CHAMPION_VERSION]}, "champion": True},
        {"$set": {"champion": False}},
    )
    if stray.modified_count:
        print(f"demoted {stray.modified_count} stray champion:true doc(s)")
    print("done: champion model is MNB-NFL-2026.01 (explicit flag, not a silent switch)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
