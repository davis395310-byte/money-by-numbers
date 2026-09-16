"""Concrete estimators for MONEY BY NUMBERS NFL models.

Wraps whichever gradient-boosting libraries are installed
(XGBoost, LightGBM — scikit-learn's HistGradientBoosting is always
available as a fallback). The module reports honestly what it can use
via :func:`available_estimators`; nothing claims a library that is not
importable.

All estimators follow a small uniform interface:

    est = make_classifier("lightgbm")
    est.fit(X_train, y_train)
    proba = est.predict_proba(X_test)   # P(home win), shape (n,)
    est2 = make_regressor("lightgbm")
    est2.fit(X_train, y_margin)
    margin = est2.predict(X_test)
"""

from __future__ import annotations

from typing import Any, Callable, Optional

import numpy as np

#: Candidate base-learner families, in preference order.
CANDIDATE_LEARNERS: tuple[str, ...] = ("lightgbm", "xgboost", "hgb")


def _try_import(name: str) -> bool:
    try:
        __import__(name)
        return True
    except Exception:
        return False


def available_learners() -> list[str]:
    """Names of gradient-boosting libraries actually importable here."""
    learners = []
    if _try_import("lightgbm"):
        learners.append("lightgbm")
    if _try_import("xgboost"):
        learners.append("xgboost")
    learners.append("hgb")  # sklearn HistGradientBoosting: always available
    return learners


# ---------------------------------------------------------------------------
# Uniform estimator wrappers
# ---------------------------------------------------------------------------

class _BaseWrapper:
    """Uniform fit/predict wrapper around a constructed sklearn-style model."""

    kind: str = "base"   # "classifier" or "regressor"
    learner: str = "base"

    def __init__(self, **params: Any) -> None:
        self.params = params
        self.model: Any = None

    def _construct(self) -> Any:
        raise NotImplementedError

    def _merged(self, defaults: dict) -> dict:
        """Merge constructor defaults with caller overrides (caller wins)."""
        merged = dict(defaults)
        merged.update(self.params)
        return merged

    def fit(self, X: Any, y: Any) -> "_BaseWrapper":
        self.model = self._construct()
        self.model.fit(X, y)
        return self

    def predict_proba(self, X: Any) -> np.ndarray:
        if self.kind != "classifier":
            raise TypeError(f"{self.learner} is not a classifier")
        return np.asarray(self.model.predict_proba(X)[:, 1], dtype=float)

    def predict(self, X: Any) -> np.ndarray:
        return np.asarray(self.model.predict(X), dtype=float)


class LightGBMClassifier(_BaseWrapper):
    kind = "classifier"
    learner = "lightgbm"

    def _construct(self) -> Any:
        import lightgbm as lgb

        return lgb.LGBMClassifier(**self._merged({
            "n_estimators": 400,
            "learning_rate": 0.03,
            "num_leaves": 31,
            "min_child_samples": 40,
            "subsample": 0.8,
            "colsample_bytree": 0.8,
            "reg_lambda": 1.0,
            "random_state": 42,
            "verbose": -1,
        }))


class LightGBMRegressor(_BaseWrapper):
    kind = "regressor"
    learner = "lightgbm"

    def _construct(self) -> Any:
        import lightgbm as lgb

        return lgb.LGBMRegressor(**self._merged({
            "n_estimators": 400,
            "learning_rate": 0.03,
            "num_leaves": 31,
            "min_child_samples": 40,
            "subsample": 0.8,
            "colsample_bytree": 0.8,
            "reg_lambda": 1.0,
            "random_state": 42,
            "verbose": -1,
        }))


class XGBoostClassifier(_BaseWrapper):
    kind = "classifier"
    learner = "xgboost"

    def _construct(self) -> Any:
        import xgboost as xgb

        return xgb.XGBClassifier(**self._merged({
            "n_estimators": 400,
            "learning_rate": 0.03,
            "max_depth": 4,
            "subsample": 0.8,
            "colsample_bytree": 0.8,
            "reg_lambda": 1.0,
            "random_state": 42,
            "eval_metric": "logloss",
        }))


class XGBoostRegressor(_BaseWrapper):
    kind = "regressor"
    learner = "xgboost"

    def _construct(self) -> Any:
        import xgboost as xgb

        return xgb.XGBRegressor(**self._merged({
            "n_estimators": 400,
            "learning_rate": 0.03,
            "max_depth": 4,
            "subsample": 0.8,
            "colsample_bytree": 0.8,
            "reg_lambda": 1.0,
            "random_state": 42,
        }))


class HGBClassifier(_BaseWrapper):
    """scikit-learn HistGradientBoosting fallback (always available)."""

    kind = "classifier"
    learner = "hgb"

    def _construct(self) -> Any:
        from sklearn.ensemble import HistGradientBoostingClassifier

        return HistGradientBoostingClassifier(**self._merged({
            "max_iter": 400,
            "learning_rate": 0.05,
            "max_leaf_nodes": 31,
            "min_samples_leaf": 40,
            "l2_regularization": 1.0,
            "random_state": 42,
        }))


class HGBRegressor(_BaseWrapper):
    kind = "regressor"
    learner = "hgb"

    def _construct(self) -> Any:
        from sklearn.ensemble import HistGradientBoostingRegressor

        return HistGradientBoostingRegressor(**self._merged({
            "max_iter": 400,
            "learning_rate": 0.05,
            "max_leaf_nodes": 31,
            "min_samples_leaf": 40,
            "l2_regularization": 1.0,
            "random_state": 42,
        }))


_CLASSIFIERS: dict[str, Callable[..., _BaseWrapper]] = {
    "lightgbm": LightGBMClassifier,
    "xgboost": XGBoostClassifier,
    "hgb": HGBClassifier,
}

_REGRESSORS: dict[str, Callable[..., _BaseWrapper]] = {
    "lightgbm": LightGBMRegressor,
    "xgboost": XGBoostRegressor,
    "hgb": HGBRegressor,
}


def make_classifier(learner: str, **params: Any) -> _BaseWrapper:
    """Build a classifier wrapper for an available learner.

    Raises:
        ValueError: if the learner is unknown or not installed.
    """
    if learner not in _CLASSIFIERS:
        raise ValueError(f"unknown classifier learner: {learner!r}")
    if learner not in available_learners():
        raise ValueError(f"learner {learner!r} is not installed in this environment")
    return _CLASSIFIERS[learner](**params)


def make_regressor(learner: str, **params: Any) -> _BaseWrapper:
    """Build a regressor wrapper for an available learner."""
    if learner not in _REGRESSORS:
        raise ValueError(f"unknown regressor learner: {learner!r}")
    if learner not in available_learners():
        raise ValueError(f"learner {learner!r} is not installed in this environment")
    return _REGRESSORS[learner](**params)
