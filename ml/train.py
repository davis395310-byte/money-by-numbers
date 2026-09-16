"""Train the production MONEY BY NUMBERS model and persist artifacts.

Trains on all final regular-season games 2016–2025 (features warmed up
from 1999 so Elo and rolling form are mature), gates on the leakage
harness, and writes:

    ml/artifacts/<version>/
        stack.pkl          fitted StackingClassifier (win probability)
        calibrator.pkl     fitted IsotonicCalibrator
        margin_reg.pkl     fitted WeightedAverageRegressor (home margin)
        total_reg.pkl      fitted WeightedAverageRegressor (total points)
        metadata.json      ModelVersionMetadata + walk-forward metrics
        features.json      feature schema (FEATURE_NAMES)

The registered version is ``MNB-NFL-2026.02`` (``2026.01`` was the
preliminary Elo-only v1 used for the locked Week 1 predictions).

Usage:
    cd ~/workspace/money-by-numbers
    python3 -m ml.train            # trains + persists artifacts
"""

from __future__ import annotations

import json
import pickle
import sys
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
import pandas as pd

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from ml.backtest import _fit_fold  # noqa: E402
from ml.calibration import brier_score, log_loss  # noqa: E402
from ml.data_loader import load_games  # noqa: E402
from ml.features import FEATURE_NAMES, build_feature_frame, list_feature_names  # noqa: E402
from ml.leakage import KickoffLeakageHarness, assert_no_leakage  # noqa: E402
from ml.models import available_learners  # noqa: E402
from ml.versioning import ModelVersionMetadata, make_version  # noqa: E402

ARTIFACTS_DIR = REPO_ROOT / "ml" / "artifacts"
#: Final model trains on every final regular-season game before 2026 —
#: the same "all prior data" protocol the walk-forward backtest validates.
TRAIN_SEASONS = range(1999, 2026)
WARMUP_START = 1999
VERSION = make_version(2, year=2026)  # MNB-NFL-2026.02


def train_final_model(
    version: str = VERSION,
    artifacts_dir: Path = ARTIFACTS_DIR,
    walk_forward_metrics: dict | None = None,
) -> tuple[dict[str, object], Path]:
    """Train on 2016–2025 finals and persist versioned artifacts.

    Returns (fitted_bundle, artifact_dir). Raises AssertionError on any
    leakage-harness violation.
    """
    learners = [l for l in ("lightgbm", "xgboost", "hgb") if l in available_learners()]

    games = load_games(range(WARMUP_START, 2026), game_types=("REG",))
    frame = build_feature_frame(games)

    harness = KickoffLeakageHarness()
    assert_no_leakage(harness.check_feature_frame(frame))

    labels = games.set_index("game_id")
    frame = frame.copy()
    frame["y_win"] = frame["game_id"].map(labels["home_win"]).astype(float)
    frame["y_margin"] = frame["game_id"].map(labels["margin"]).astype(float)
    frame["y_total"] = frame["game_id"].map(labels["total"]).astype(float)
    train = frame[
        frame["season"].isin(set(TRAIN_SEASONS)) & frame["y_win"].notna()
    ].reset_index(drop=True)

    X = train[FEATURE_NAMES].to_numpy(dtype=float)
    fitted = _fit_fold(
        X,
        train["y_win"].to_numpy(),
        train["y_margin"].to_numpy(),
        train["y_total"].to_numpy(),
        learners,
    )

    # In-sample (training) diagnostics — for metadata only, NOT a claim
    # about generalization (that comes from the walk-forward backtest).
    raw = fitted["stack"].predict_proba(X)
    cal = fitted["calibrator"].predict(raw)
    train_metrics = {
        "n_games": int(len(train)),
        "seasons": [int(TRAIN_SEASONS.start), int(TRAIN_SEASONS.stop) - 1],
        "train_brier_raw": brier_score(train["y_win"].to_numpy(), raw),
        "train_brier_calibrated": brier_score(train["y_win"].to_numpy(), cal),
        "train_logloss_calibrated": log_loss(train["y_win"].to_numpy(), cal),
        "train_accuracy": float(np.mean((cal >= 0.5) == train["y_win"].to_numpy())),
    }

    out_dir = Path(artifacts_dir) / version
    out_dir.mkdir(parents=True, exist_ok=True)
    bundle = {
        "stack": fitted["stack"],
        "calibrator": fitted["calibrator"],
        "margin_reg": fitted["margin_reg"],
        "total_reg": fitted["total_reg"],
    }
    for name, obj in bundle.items():
        with open(out_dir / f"{name}.pkl", "wb") as fh:
            pickle.dump(obj, fh)

    metadata = ModelVersionMetadata(
        version=version,
        training_period=f"{TRAIN_SEASONS.start}-{TRAIN_SEASONS.stop - 1} regular seasons",
        features=list_feature_names(),
        training_date=datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        calibration={
            "train_brier_calibrated": train_metrics["train_brier_calibrated"],
            "train_logloss_calibrated": train_metrics["train_logloss_calibrated"],
        },
        performance={
            "train_accuracy": train_metrics["train_accuracy"],
            **({"walk_forward_" + k: v for k, v in (walk_forward_metrics or {}).items()}),
        },
    )
    with open(out_dir / "metadata.json", "w") as fh:
        json.dump(
            {
                "version": metadata.version,
                "training_period": metadata.training_period,
                "features": metadata.features,
                "training_date": metadata.training_date,
                "calibration": metadata.calibration,
                "performance": metadata.performance,
                "learners": learners,
                "leakage_harness": "passed",
            },
            fh,
            indent=2,
        )
    with open(out_dir / "features.json", "w") as fh:
        json.dump({"feature_names": list_feature_names()}, fh, indent=2)

    return bundle, out_dir


def predict_scheduled(bundle: dict[str, object], frame: pd.DataFrame) -> pd.DataFrame:
    """Predict scheduled games from a feature frame.

    Returns game_id, p_home_win (calibrated), pred_margin, pred_total,
    pred_home_score, pred_away_score. Inputs must already satisfy the
    kickoff rule (callers build the frame chronologically).
    """
    X = frame[FEATURE_NAMES].to_numpy(dtype=float)
    proba = bundle["calibrator"].predict(bundle["stack"].predict_proba(X))
    margin = bundle["margin_reg"].predict(X)
    total = bundle["total_reg"].predict(X)
    home_score = np.clip(np.round((total + margin) / 2), 0, None).astype(int)
    away_score = np.clip(np.round((total - margin) / 2), 0, None).astype(int)
    return pd.DataFrame(
        {
            "game_id": frame["game_id"],
            "p_home_win": proba,
            "pred_margin": margin,
            "pred_total": total,
            "pred_home_score": home_score,
            "pred_away_score": away_score,
            "predicted_winner": np.where(
                proba >= 0.5, frame["home_team"], frame["away_team"]
            ),
        }
    )


if __name__ == "__main__":
    bundle, out_dir = train_final_model()
    print(f"trained {VERSION}; artifacts -> {out_dir}")
    print(f"learners: {bundle['stack'].learners}")
    print(f"meta weights: {bundle['stack'].meta_weights}")
