"""MONEY BY NUMBERS — ml package.

Machine learning layer for the NFL prediction pipeline.

Modules:
    features    Feature-group definitions (Elo team strength, recent form, matchup,
                situational, injuries, weather, market). Constants only; no values.
    models      Estimator interface (fit / predict_proba / predict_score) for the
                planned XGBoost / LightGBM / CatBoost ensemble.
    ensemble    Weighted-average / stacking combination interface.
    calibration Calibration stub (Brier score, log-loss, calibration curves).
    versioning  Model version format ``MNB-NFL-YYYY.NN`` and metadata dataclass.
    leakage     Leakage-test harness: a historical prediction may only use
                information available before kickoff.

STATUS: Phase 1 scaffolding. No training data, no trained models, no
performance numbers. Anything that computes raises NotImplementedError.
Fabricated statistics are never acceptable here.
"""

from ml.versioning import ModelVersionMetadata, make_version

__all__ = ["ModelVersionMetadata", "make_version"]
