"""Generate tipping-point explainers for locked picks and store them.

Reads the immutable ``predictions`` ledger for a season/week, rebuilds
the tipping-point inputs (rest differential from the schedule, market
moneylines from the odds snapshot), and upserts one explainer document
per pick into ``pick_explainers``. The ledger itself is never modified.

Usage:
    cd ~/workspace/money-by-numbers
    MONGODB_URI=mongodb://127.0.0.1:27017/ python3 backend/scripts/generate_explainers.py --season 2026 --week 1
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))
BACKEND_DIR = REPO_ROOT / "backend"
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

from app.config import get_settings  # noqa: E402
from app.database import get_database_name, get_mongo_client  # noqa: E402
from app.picks.explainers import build_explainer  # noqa: E402
from ml.data_loader import load_games  # noqa: E402


def _rest_map(season: int, week: int) -> dict[str, int]:
    slate = load_games(range(season, season + 1), game_types=("REG",))
    slate = slate[slate["week"] == week]
    out: dict[str, int] = {}
    for _, row in slate.iterrows():
        hr = row["home_rest"] if row["home_rest"] is not None else 7
        ar = row["away_rest"] if row["away_rest"] is not None else 7
        out[str(row["game_id"])] = int(hr - ar)
    return out


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--season", type=int, required=True)
    ap.add_argument("--week", type=int, required=True)
    args = ap.parse_args()

    settings = get_settings()
    if not settings.MONGODB_URI:
        raise SystemExit("MONGODB_URI is not configured")
    db = get_mongo_client()[get_database_name()]

    rest = _rest_map(args.season, args.week)
    odds = {o["game_id"]: o for o in db["odds"].find({})}

    preds = list(db["predictions"].find({"season": args.season, "week": args.week}))
    if not preds:
        raise SystemExit(f"no locked predictions for {args.season} week {args.week}")

    written = 0
    for p in preds:
        p.pop("_id", None)
        game_id = p.get("game_id")
        snap = odds.get(game_id or "")
        market = None
        if snap and snap.get("moneyline_home") and snap.get("moneyline_away"):
            market = {
                "moneyline_home": snap["moneyline_home"],
                "moneyline_away": snap["moneyline_away"],
                "snapshot_at": str(snap.get("timestamp")),
            }
        doc = build_explainer(p, rest_diff=rest.get(game_id or "", 0), market=market)
        db["pick_explainers"].update_one(
            {"prediction_id": doc["prediction_id"]}, {"$set": doc}, upsert=True
        )
        written += 1
    print(f"wrote {written} explainers for {args.season} week {args.week}")


if __name__ == "__main__":
    main()
