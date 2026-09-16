"""Calibration utilities for MONEY BY NUMBERS predictions.

Every stored prediction must carry a *calibrated* probability: raw
gradient-boosting outputs are systematically miscalibrated, so the
pipeline fits an isotonic-regression calibrator on held-out data
(chronologically after the training block — never on the training rows
themselves) before any prediction is persisted or served.

Metrics:
    - Brier score: mean squared error of probabilities vs outcomes.
    - Log-loss: cross-entropy of probabilities vs outcomes.
    - Calibration curve: predicted-probability bins vs observed event
      rates, exposing systematic over/under-confidence.
"""

from __future__ import annotations

import numpy as np


def brier_score(y_true: np.ndarray, y_prob: np.ndarray) -> float:
    """Mean squared error of predicted probabilities vs binary outcomes."""
    y_true = np.asarray(y_true, dtype=float)
    y_prob = np.asarray(y_prob, dtype=float)
    return float(np.mean((y_prob - y_true) ** 2))


def log_loss(y_true: np.ndarray, y_prob: np.ndarray, eps: float = 1e-12) -> float:
    """Cross-entropy of predicted probabilities vs binary outcomes."""
    y_true = np.asarray(y_true, dtype=float)
    y_prob = np.clip(np.asarray(y_prob, dtype=float), eps, 1.0 - eps)
    return float(-np.mean(y_true * np.log(y_prob) + (1.0 - y_true) * np.log(1.0 - y_prob)))


def calibration_curve(
    y_true: np.ndarray, y_prob: np.ndarray, n_bins: int = 10
) -> list[dict[str, float]]:
    """Predicted-probability bins vs observed event rates.

    Returns a list of ``{"bin_center", "predicted", "observed", "count"}``
    dicts; empty bins are omitted. A perfectly calibrated model has
    ``predicted ≈ observed`` in every bin.
    """
    y_true = np.asarray(y_true, dtype=float)
    y_prob = np.asarray(y_prob, dtype=float)
    edges = np.linspace(0.0, 1.0, n_bins + 1)
    curve = []
    for lo, hi in zip(edges[:-1], edges[1:]):
        if hi == 1.0:
            mask = (y_prob >= lo) & (y_prob <= hi)
        else:
            mask = (y_prob >= lo) & (y_prob < hi)
        count = int(mask.sum())
        if count == 0:
            continue
        curve.append(
            {
                "bin_center": float((lo + hi) / 2),
                "predicted": float(y_prob[mask].mean()),
                "observed": float(y_true[mask].mean()),
                "count": count,
            }
        )
    return curve


class IsotonicCalibrator:
    """Isotonic-regression probability calibrator.

    Fit on held-out (post-training, pre-evaluation) predictions only.
    ``predict`` clips to [0, 1]; monotonicity is guaranteed by isotonic
    regression.
    """

    def __init__(self) -> None:
        self._model = None

    def fit(self, y_true: np.ndarray, y_prob: np.ndarray) -> "IsotonicCalibrator":
        from sklearn.isotonic import IsotonicRegression

        self._model = IsotonicRegression(out_of_bounds="clip", y_min=0.0, y_max=1.0)
        self._model.fit(np.asarray(y_prob, dtype=float), np.asarray(y_true, dtype=float))
        return self

    def predict(self, y_prob: np.ndarray) -> np.ndarray:
        if self._model is None:
            raise RuntimeError("IsotonicCalibrator.predict called before fit")
        return np.clip(
            np.asarray(self._model.predict(np.asarray(y_prob, dtype=float)), dtype=float),
            0.0,
            1.0,
        )
