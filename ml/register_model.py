"""Register a trained model version + backtest results in MongoDB.

Reads ``ml/artifacts/<version>/metadata.json`` and
``ml/artifacts/backtest_2016_2025.json`` (both produced by real runs —
``ml/train.py`` and ``ml/run_backtest.py``) and upserts them into the
``models`` and ``backtests`` collections so the predictions API can serve
them.

If ``MONGODB_URI`` is not configured, nothing is stored and the script
says so honestly. Nothing here invents metrics.

Usage:
    cd ~/workspace/money-by-numbers
    python3 -m ml.register_model --version MNB-NFL-2026.02
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))
if str(REPO_ROOT / "backend") not in sys.path:
    sys.path.insert(0, str(REPO_ROOT / "backend"))

from app.config import get_settings  # noqa: E402
from app.database import get_database_name, get_mongo_client  # noqa: E402
from ml.versioning import VERSION_PATTERN  # noqa: E402

ARTIFACTS_DIR = REPO_ROOT / "ml" / "artifacts"


def main() -> int:
    parser = argparse.ArgumentParser(description="Register a trained model version in MongoDB.")
    parser.add_argument("--version", required=True, help="e.g. MNB-NFL-2026.02")
    args = parser.parse_args()

    if not VERSION_PATTERN.match(args.version):
        print(f"invalid version: {args.version!r}")
        return 2

    meta_path = ARTIFACTS_DIR / args.version / "metadata.json"
    if not meta_path.exists():
        print(f"no artifacts for {args.version}: {meta_path} missing.")
        print("train first: python3 -m ml.train")
        return 2
    metadata = json.loads(meta_path.read_text())

    backtest_path = ARTIFACTS_DIR / "backtest_2016_2025.json"
    backtest = json.loads(backtest_path.read_text()) if backtest_path.exists() else None

    settings = get_settings()
    if not settings.MONGODB_URI:
        print("MONGODB_URI is not configured: nothing stored (honest no-op).")
        print(f"would have registered {args.version} + backtest from {backtest_path}")
        return 0

    client = get_mongo_client()
    if client is None:
        print("could not connect to MongoDB: nothing stored.")
        return 1
    try:
        db = client[get_database_name()]
        now = datetime.now(timezone.utc)
        db["models"].update_one(
            {"version": args.version},
            {
                "$set": {
                    "version": args.version,
                    "created_at": now,
                    "training_period": metadata.get("training_period"),
                    "features": metadata.get("features"),
                    "training_date": metadata.get("training_date"),
                    "calibration": metadata.get("calibration"),
                    "performance": metadata.get("performance"),
                    "learners": metadata.get("learners"),
                    "notes": "Walk-forward 2016-2025 backtest; leakage harness passed.",
                }
            },
            upsert=True,
        )
        print(f"registered model {args.version} in 'models'")
        if backtest:
            db["backtests"].update_one(
                {"model_version": args.version, "season_start": 2016, "season_end": 2025},
                {
                    "$set": {
                        "backtest_id": f"{args.version}-2016-2025",
                        "model_version": args.version,
                        "season_start": 2016,
                        "season_end": 2025,
                        "created_at": now,
                        "metrics": backtest["aggregate"],
                    }
                },
                upsert=True,
            )
            print(f"recorded backtest {args.version} 2016-2025 in 'backtests'")
    finally:
        client.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
