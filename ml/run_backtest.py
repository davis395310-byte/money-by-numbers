"""Run the 2016-2025 walk-forward backtest and save results.

Usage:
    cd ~/workspace/money-by-numbers
    python3 -m ml.run_backtest

Writes ml/artifacts/backtest_2016_2025.json. Raises on any leakage
violation (no results are recorded unless the harness passes).
"""

from __future__ import annotations

import json
import sys
from dataclasses import asdict
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from ml.backtest import run_walk_forward  # noqa: E402
from ml.data_loader import load_games  # noqa: E402
from ml.features import build_feature_frame  # noqa: E402
from ml.models import available_learners  # noqa: E402

ARTIFACTS_DIR = REPO_ROOT / "ml" / "artifacts"


def main() -> None:
    learners = [l for l in ("lightgbm", "xgboost", "hgb") if l in available_learners()]
    if not learners:
        learners = ["hgb"]
    print(f"learners: {learners}", flush=True)

    print("loading games...", flush=True)
    games = load_games(range(1999, 2026), game_types=("REG",))
    print(f"games: {len(games)}", flush=True)

    print("building features...", flush=True)
    frame = build_feature_frame(games)
    print(f"feature rows: {len(frame)}", flush=True)

    def progress(season: int, i: int, n: int) -> None:
        print(f"fold {i + 1}/{n}: season {season}", flush=True)

    result = run_walk_forward(frame, games, learners, progress=progress)

    payload = {
        "learners": result.learners,
        "feature_names": result.feature_names,
        "aggregate": result.aggregate,
        "folds": [asdict(f) for f in result.folds],
    }
    ARTIFACTS_DIR.mkdir(parents=True, exist_ok=True)
    out = ARTIFACTS_DIR / "backtest_2016_2025.json"
    with open(out, "w") as fh:
        json.dump(payload, fh, indent=2)
    print(f"saved -> {out}", flush=True)

    agg = result.aggregate
    print(f"\naccuracy: {agg['accuracy']:.4f}  (elo baseline: {agg['elo_accuracy']:.4f})", flush=True)
    print(f"brier:    {agg['brier']:.4f}  (elo baseline: {agg['elo_brier']:.4f})", flush=True)
    print(f"margin MAE: {agg['margin_mae']:.2f} | total MAE: {agg['total_mae']:.2f}", flush=True)
    print(f"ATS: {agg['ats_accuracy']} (n={agg['ats_n']}) | O/U: {agg['ou_accuracy']} (n={agg['ou_n']})", flush=True)


if __name__ == "__main__":
    main()
