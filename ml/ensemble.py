"""Ensemble combination for MONEY BY NUMBERS.

Two strategies (per product spec):

    - stacking: a logistic-regression meta-learner trained on
      out-of-fold base-learner probabilities (chronological folds —
      never shuffled, to respect the kickoff rule).
    - weighted_average: convex combination of base-learner outputs with
      weights from a ridge regression on out-of-fold predictions
      (used for the margin/total regressors).

Weights are learned, never hand-set. The meta-learners are fit on
out-of-fold predictions and the base learners are then refit on the full
training fold, so no row trains both a base learner and the meta-learner
on its own target.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Sequence

import numpy as np

from .models import _BaseWrapper, make_classifier, make_regressor


def _oof_predictions(
    learners: Sequence[str],
    kind: str,
    X: np.ndarray,
    y: np.ndarray,
    n_splits: int = 3,
) -> np.ndarray:
    """Out-of-fold predictions, chronological splits (TimeSeriesSplit)."""
    from sklearn.model_selection import TimeSeriesSplit

    tss = TimeSeriesSplit(n_splits=n_splits)
    oof = np.zeros((len(X), len(learners)))
    for train_idx, val_idx in tss.split(X):
        for j, name in enumerate(learners):
            est = make_classifier(name) if kind == "classifier" else make_regressor(name)
            est.fit(X[train_idx], y[train_idx])
            oof[val_idx, j] = (
                est.predict_proba(X[val_idx]) if kind == "classifier" else est.predict(X[val_idx])
            )
    return oof


@dataclass
class StackingClassifier:
    """Stacked ensemble of calibrated base classifiers.

    ``predict_proba`` returns P(home win).
    """

    learners: list[str]
    estimators: list[_BaseWrapper] = field(default_factory=list)
    meta: Any = None  # fitted LogisticRegression

    def fit(self, X: np.ndarray, y: np.ndarray) -> "StackingClassifier":
        from sklearn.linear_model import LogisticRegression

        oof = _oof_predictions(self.learners, "classifier", X, y)
        # Meta-learner on OOF probabilities. Rows with all-NaN OOF
        # (the first fold's training block) carry no signal for the
        # meta-learner; TimeSeriesSplit guarantees every row is in
        # exactly one validation fold, so there are no NaNs here.
        self.meta = LogisticRegression(max_iter=1000)
        self.meta.fit(oof, y)
        self.estimators = [make_classifier(name).fit(X, y) for name in self.learners]
        return self

    def predict_proba(self, X: np.ndarray) -> np.ndarray:
        base = np.column_stack([e.predict_proba(X) for e in self.estimators])
        return np.asarray(self.meta.predict_proba(base)[:, 1], dtype=float)

    @property
    def meta_weights(self) -> dict[str, float]:
        coef = self.meta.coef_[0]
        return {name: float(c) for name, c in zip(self.learners, coef)}


@dataclass
class WeightedAverageRegressor:
    """Ridge-weighted average of base regressors (convex-ish weights)."""

    learners: list[str]
    estimators: list[_BaseWrapper] = field(default_factory=list)
    weights: np.ndarray = field(default_factory=lambda: np.array([]))

    def fit(self, X: np.ndarray, y: np.ndarray) -> "WeightedAverageRegressor":
        from sklearn.linear_model import Ridge

        oof = _oof_predictions(self.learners, "regressor", X, y)
        ridge = Ridge(alpha=1.0, positive=True)
        ridge.fit(oof, y)
        w = np.asarray(ridge.coef_, dtype=float)
        total = w.sum()
        self.weights = w / total if total > 0 else np.full_like(w, 1.0 / len(w))
        self.estimators = [make_regressor(name).fit(X, y) for name in self.learners]
        return self

    def predict(self, X: np.ndarray) -> np.ndarray:
        base = np.column_stack([e.predict(X) for e in self.estimators])
        return np.asarray(base @ self.weights, dtype=float)
