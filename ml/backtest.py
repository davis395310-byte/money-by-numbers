"""Walk-forward backtest for MONEY BY NUMBERS (spec sections 70-73).

Protocol: for each season Y in 2016..2025, train on all final games with
season < Y and predict season Y. Features are point-in-time by
construction (see ``ml.features``); the backtest additionally audits that
no training row has a kickoff at or after the earliest test kickoff.

The leakage harness gates the results: any violation raises before any
metric is recorded.

ATS evaluation uses nflverse ``spread_line`` / ``total_line``, documented
by nflverse as the *closing* lines (source: Pro-Football-Reference) —
knowable at kickoff, hence legitimate for grading. Lines are NEVER model
inputs.

An Elo-only baseline (the validated v1 parameterization) runs in the
same harness for an apples-to-apples comparison. No improvement claim is
made unless the numbers prove it.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

import numpy as np
import pandas as pd

from .calibration import IsotonicCalibrator, brier_score, calibration_curve, log_loss
from .ensemble import StackingClassifier, WeightedAverageRegressor
from .features import ELO_HFA, FEATURE_NAMES, elo_win_probability
from .leakage import KickoffLeakageHarness, assert_no_leakage


@dataclass
class FoldResult:
    season: int
    n_games: int
    # Model metrics
    accuracy: float
    brier: float
    logloss: float
    margin_mae: float
    total_mae: float
    ats_accuracy: float | None
    ats_n: int
    ou_accuracy: float | None
    ou_n: int
    calibration: list[dict[str, float]]
    # Elo baseline metrics
    elo_accuracy: float
    elo_brier: float
    elo_ats_accuracy: float | None
    meta_weights: dict[str, float]


@dataclass
class BacktestResult:
    folds: list[FoldResult] = field(default_factory=list)
    aggregate: dict[str, Any] = field(default_factory=dict)
    learners: list[str] = field(default_factory=list)
    feature_names: list[str] = field(default_factory=list)
    leakage_violations: int = 0


def _ats_pick_correct(pred_margin: float, spread_line: float, actual_margin: float) -> bool | None:
    """Grade an ATS pick. Returns None on a push.

    nflverse convention: ``spread_line`` > 0 means the HOME team was
    favored by that many points. The home team covers iff its actual
    margin exceeds the line: ``actual_margin - spread_line > 0``.
    """
    pick_home_covers = (pred_margin - spread_line) > 0
    edge = actual_margin - spread_line
    if edge == 0:
        return None
    return pick_home_covers == (edge > 0)


def _ou_pick_correct(pred_total: float, total_line: float, actual_total: float) -> bool | None:
    pick_over = pred_total > total_line
    if actual_total == total_line:
        return None
    return pick_over == (actual_total > total_line)


def _fit_fold(
    X_train: np.ndarray,
    y_win: np.ndarray,
    y_margin: np.ndarray,
    y_total: np.ndarray,
    learners: list[str],
    calib_frac: float = 0.2,
) -> dict[str, Any]:
    """Fit classifier stack + regressors + calibrator on one training fold.

    Chronological split: the last ``calib_frac`` of the training fold is
    held out for the isotonic calibrator; the stack is then refit on the
    full fold.
    """
    n = len(X_train)
    cut = int(n * (1.0 - calib_frac))
    X_fit, y_fit = X_train[:cut], y_win[:cut]
    X_cal, y_cal = X_train[cut:], y_win[cut:]

    stack = StackingClassifier(learners=list(learners)).fit(X_fit, y_fit)
    cal_proba = stack.predict_proba(X_cal)
    calibrator = IsotonicCalibrator().fit(y_cal, cal_proba)

    stack_full = StackingClassifier(learners=list(learners)).fit(X_train, y_win)
    margin_reg = WeightedAverageRegressor(learners=list(learners)).fit(X_train, y_margin)
    total_reg = WeightedAverageRegressor(learners=list(learners)).fit(X_train, y_total)
    return {
        "stack": stack_full,
        "calibrator": calibrator,
        "margin_reg": margin_reg,
        "total_reg": total_reg,
        "meta_weights": stack_full.meta_weights,
    }


def _elo_baseline_proba(elo_diff: np.ndarray) -> np.ndarray:
    return np.array([elo_win_probability(d) for d in elo_diff])


def run_walk_forward(
    frame: pd.DataFrame,
    games: pd.DataFrame,
    learners: list[str],
    seasons: range = range(2016, 2026),
    progress: Any = None,
) -> BacktestResult:
    """Run the walk-forward backtest.

    Args:
        frame: Feature frame from ``ml.features.build_feature_frame``
            (must include ``_data_through`` and ``kickoff``).
        games: Normalized games with ``home_win``, ``margin``, ``total``,
            ``spread_line``, ``total_line``, ``kickoff``, ``season``.
        learners: Base-learner names, e.g. ``["lightgbm", "xgboost"]``.
        seasons: Test seasons, one fold each.
        progress: Optional callable(season, fold_index, n_folds).

    Returns:
        BacktestResult with per-fold metrics and aggregates. Raises
        AssertionError if the leakage harness finds any violation.
    """
    harness = KickoffLeakageHarness()
    assert_no_leakage(harness.check_feature_frame(frame))

    # Join labels onto the feature rows.
    labels = games.set_index("game_id")
    frame = frame.copy()
    frame["y_win"] = frame["game_id"].map(labels["home_win"]).astype(float)
    frame["y_margin"] = frame["game_id"].map(labels["margin"]).astype(float)
    frame["y_total"] = frame["game_id"].map(labels["total"]).astype(float)
    frame["spread_line"] = frame["game_id"].map(labels["spread_line"])
    frame["total_line"] = frame["game_id"].map(labels["total_line"])
    frame = frame.dropna(subset=["y_win"]).reset_index(drop=True)

    X_all = frame[FEATURE_NAMES].to_numpy(dtype=float)

    result = BacktestResult(learners=list(learners), feature_names=list(FEATURE_NAMES))
    seasons = list(seasons)
    for fi, season in enumerate(seasons):
        if progress:
            progress(season, fi, len(seasons))
        test_mask = frame["season"].to_numpy() == season
        train_mask = frame["season"].to_numpy() < season
        assert train_mask.sum() > 0, f"no training data for fold {season}"
        assert test_mask.sum() > 0, f"no test games for fold {season}"

        # Fold cutoff audit: no training row may reach the test window.
        train_max_ko = pd.Timestamp(frame.loc[train_mask, "kickoff"].max())
        test_min_ko = pd.Timestamp(frame.loc[test_mask, "kickoff"].min())
        if train_max_ko >= test_min_ko:
            raise AssertionError(
                f"fold {season}: training data reaches {train_max_ko} >= "
                f"first test kickoff {test_min_ko}"
            )

        X_train, X_test = X_all[train_mask], X_all[test_mask]
        y_win = frame.loc[train_mask, "y_win"].to_numpy()
        y_margin = frame.loc[train_mask, "y_margin"].to_numpy()
        y_total = frame.loc[train_mask, "y_total"].to_numpy()
        t_win = frame.loc[test_mask, "y_win"].to_numpy()
        t_margin = frame.loc[test_mask, "y_margin"].to_numpy()
        t_total = frame.loc[test_mask, "y_total"].to_numpy()
        t_spread = frame.loc[test_mask, "spread_line"].to_numpy(dtype=float)
        t_total_line = frame.loc[test_mask, "total_line"].to_numpy(dtype=float)

        fitted = _fit_fold(X_train, y_win, y_margin, y_total, learners)
        raw_proba = fitted["stack"].predict_proba(X_test)
        proba = fitted["calibrator"].predict(raw_proba)
        pred_margin = fitted["margin_reg"].predict(X_test)
        pred_total = fitted["total_reg"].predict(X_test)

        # Elo baseline in the same harness (per-fold margin slope for ATS).
        elo_diff_test = X_test[:, FEATURE_NAMES.index("elo_diff")]
        elo_proba = _elo_baseline_proba(elo_diff_test)
        elo_adj = elo_diff_test + ELO_HFA
        X_tr_elo = X_train[:, FEATURE_NAMES.index("elo_diff")] + ELO_HFA
        slope = float(np.polyfit(X_tr_elo, y_margin, 1)[0])
        elo_pred_margin = slope * elo_adj

        ats = [
            _ats_pick_correct(pm, sl, am)
            for pm, sl, am in zip(pred_margin, t_spread, t_margin)
            if not (pd.isna(sl) or pd.isna(pm))
        ]
        ats = [a for a in ats if a is not None]
        elo_ats = [
            _ats_pick_correct(pm, sl, am)
            for pm, sl, am in zip(elo_pred_margin, t_spread, t_margin)
            if not (pd.isna(sl) or pd.isna(pm))
        ]
        elo_ats = [a for a in elo_ats if a is not None]
        ou = [
            _ou_pick_correct(pt, tl, at)
            for pt, tl, at in zip(pred_total, t_total_line, t_total)
            if not (pd.isna(tl) or pd.isna(pt))
        ]
        ou = [o for o in ou if o is not None]

        result.folds.append(
            FoldResult(
                season=season,
                n_games=int(test_mask.sum()),
                accuracy=float(np.mean((proba >= 0.5) == t_win)),
                brier=brier_score(t_win, proba),
                logloss=log_loss(t_win, proba),
                margin_mae=float(np.mean(np.abs(pred_margin - t_margin))),
                total_mae=float(np.mean(np.abs(pred_total - t_total))),
                ats_accuracy=float(np.mean(ats)) if ats else None,
                ats_n=len(ats),
                ou_accuracy=float(np.mean(ou)) if ou else None,
                ou_n=len(ou),
                calibration=calibration_curve(t_win, proba),
                elo_accuracy=float(np.mean((elo_proba >= 0.5) == t_win)),
                elo_brier=brier_score(t_win, elo_proba),
                elo_ats_accuracy=float(np.mean(elo_ats)) if elo_ats else None,
                meta_weights=fitted["meta_weights"],
            )
        )

    # Aggregate across folds (pooled).
    acc = np.mean([f.accuracy for f in result.folds])
    elo_acc = np.mean([f.elo_accuracy for f in result.folds])
    result.aggregate = {
        "seasons": [f.season for f in result.folds],
        "n_games": sum(f.n_games for f in result.folds),
        "accuracy": acc,
        "brier": float(np.mean([f.brier for f in result.folds])),
        "logloss": float(np.mean([f.logloss for f in result.folds])),
        "margin_mae": float(np.mean([f.margin_mae for f in result.folds])),
        "total_mae": float(np.mean([f.total_mae for f in result.folds])),
        "ats_accuracy": (
            float(np.sum([f.ats_accuracy * f.ats_n for f in result.folds])
                  / np.sum([f.ats_n for f in result.folds]))
            if any(f.ats_n for f in result.folds) else None
        ),
        "ats_n": sum(f.ats_n for f in result.folds),
        "ou_accuracy": (
            float(np.sum([f.ou_accuracy * f.ou_n for f in result.folds])
                  / np.sum([f.ou_n for f in result.folds]))
            if any(f.ou_n for f in result.folds) else None
        ),
        "ou_n": sum(f.ou_n for f in result.folds),
        "elo_accuracy": elo_acc,
        "elo_brier": float(np.mean([f.elo_brier for f in result.folds])),
        "accuracy_vs_elo_baseline": acc - elo_acc,
    }
    return result
