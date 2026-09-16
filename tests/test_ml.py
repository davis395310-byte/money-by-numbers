"""Phase 3 ML tests: feature correctness, no-leakage, calibration,
versioning, prediction schema, and estimator smoke tests.

Every test runs for real here — no mocked training, no invented
numbers. Slow integration tests (full backtest) live in
``ml/run_backtest.py``, not in this file.
"""

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import numpy as np
import pandas as pd
import pytest

from ml import calibration as cal
from ml import versioning
from ml.data_loader import load_games
from ml.ensemble import StackingClassifier
from ml.features import (
    FEATURE_NAMES,
    REQUIRED_JOIN_KEYS,
    build_feature_frame,
    elo_expected,
    elo_update,
    elo_win_probability,
    list_feature_names,
)
from ml.leakage import KickoffLeakageHarness, assert_no_leakage
from ml.models import available_learners, make_classifier, make_regressor

UTC = "UTC"


# ---------------------------------------------------------------------------
# Elo engine
# ---------------------------------------------------------------------------

def test_elo_expected_known_value():
    # diff=0 -> 0.5; diff=400 -> 10/11.
    assert elo_expected(0.0) == pytest.approx(0.5)
    assert elo_expected(400.0) == pytest.approx(10 / 11, rel=1e-6)


def test_elo_update_zero_sum_and_no_hfa_leak():
    nh, na = elo_update(1500.0, 1500.0, 4.0)
    assert nh + na == pytest.approx(3000.0)  # zero-sum
    assert nh == pytest.approx(1505.53, abs=0.05)
    assert na == pytest.approx(1494.47, abs=0.05)
    # Many home games must not inflate ratings by HFA each time.
    h, a = 1500.0, 1500.0
    for _ in range(8):
        h, a = elo_update(h, a, 3.0)
    assert h < 1600.0, f"HFA leaked into stored ratings: {h}"
    # Tie changes nothing.
    assert elo_update(1500.0, 1500.0, 0.0) == (1500.0, 1500.0)


def test_elo_win_probability_adds_hfa():
    assert elo_win_probability(0.0) == pytest.approx(elo_expected(35.0))
    assert elo_win_probability(0.0, home=False) == pytest.approx(0.5)


# ---------------------------------------------------------------------------
# Feature schema + correctness on synthetic games
# ---------------------------------------------------------------------------

def _synthetic_games() -> pd.DataFrame:
    rows = [
        {
            "game_id": "2020_01_KC_HOU", "season": 2020, "week": 1,
            "game_type": "REG",
            "kickoff": pd.Timestamp("2020-09-10 20:20", tz="America/New_York").tz_convert(UTC),
            "home_team": "KC", "away_team": "HOU",
            "home_score": 34, "away_score": 20, "status": "final",
            "overtime": 0, "location": "Home", "div_game": 1, "roof": "dome",
            "home_rest": 200, "away_rest": 200,
            "spread_line": -9.5, "total_line": 54.5,
        },
        {
            "game_id": "2020_02_HOU_KC", "season": 2020, "week": 2,
            "game_type": "REG",
            "kickoff": pd.Timestamp("2020-09-20 13:00", tz="America/New_York").tz_convert(UTC),
            "home_team": "HOU", "away_team": "KC",
            "home_score": 20, "away_score": 24, "status": "final",
            "overtime": 0, "location": "Home", "div_game": 0, "roof": "outdoors",
            "home_rest": 10, "away_rest": 10,
            "spread_line": 3.0, "total_line": 48.0,
        },
    ]
    df = pd.DataFrame(rows)
    df["home_win"] = (df["home_score"] > df["away_score"]).astype("Int64")
    df["margin"] = df["home_score"] - df["away_score"]
    df["total"] = df["home_score"] + df["away_score"]
    return df


def test_feature_schema_contract():
    assert list_feature_names() == FEATURE_NAMES
    assert len(FEATURE_NAMES) == 11
    frame = build_feature_frame(_synthetic_games())
    for key in REQUIRED_JOIN_KEYS:
        assert key in frame.columns
    for name in FEATURE_NAMES:
        assert name in frame.columns
    assert "_data_through" in frame.columns


def test_feature_values_hand_checked():
    frame = build_feature_frame(_synthetic_games()).set_index("game_id")
    g1 = frame.loc["2020_01_KC_HOU"]
    # First game ever: neutral priors.
    assert g1["elo_diff"] == pytest.approx(0.0)
    assert g1["off_form_diff"] == pytest.approx(0.0)
    assert g1["streak_diff"] == 0
    assert g1["div_game"] == 1
    assert g1["dome"] == 1
    assert g1["neutral"] == 0
    assert g1["rest_diff"] == 0
    g2 = frame.loc["2020_02_HOU_KC"]
    # KC won G1 34-20 -> KC elo ~1511.25, HOU ~1488.75; G2 home=HOU.
    assert g2["elo_diff"] == pytest.approx(1488.75 - 1511.25, abs=0.6)
    # Streaks: HOU -1, KC +1 -> diff -2.
    assert g2["streak_diff"] == -2
    # Head-to-head: G1 past home=KC margin +14; current home=HOU -> -14.
    assert g2["h2h_margin_avg"] == pytest.approx(-14.0)
    # Form: HOU scored 20, KC scored 34 -> -14.
    assert g2["off_form_diff"] == pytest.approx(-14.0)
    assert g2["dome"] == 0
    assert g2["div_game"] == 0


def test_same_kickoff_games_do_not_see_each_other():
    df = _synthetic_games()
    # Force identical kickoffs: neither game may use the other's result.
    df["kickoff"] = pd.Timestamp("2020-09-10 20:20", tz=UTC)
    frame = build_feature_frame(df).set_index("game_id")
    assert frame.loc["2020_01_KC_HOU", "elo_diff"] == pytest.approx(0.0)
    assert frame.loc["2020_02_HOU_KC", "elo_diff"] == pytest.approx(0.0)
    assert frame.loc["2020_02_HOU_KC", "streak_diff"] == 0


# ---------------------------------------------------------------------------
# Leakage harness
# ---------------------------------------------------------------------------

def test_check_game_flags_future_as_of():
    h = KickoffLeakageHarness()
    kickoff = pd.Timestamp("2020-09-10 20:20", tz=UTC).to_pydatetime()
    ok = h.check_game("g1", kickoff, {"elo": (1500.0, pd.Timestamp("2020-09-01", tz=UTC).to_pydatetime())})
    assert ok == []
    bad = h.check_game("g1", kickoff, {"elo": (1500.0, pd.Timestamp("2020-09-11", tz=UTC).to_pydatetime())})
    assert len(bad) == 1 and bad[0].feature == "elo"
    with pytest.raises(AssertionError):
        assert_no_leakage(bad)


def test_frame_audit_passes_on_real_data():
    games = load_games(range(1999, 2001), game_types=("REG",))
    frame = build_feature_frame(games)
    violations = KickoffLeakageHarness().check_feature_frame(frame)
    assert violations == []
    assert_no_leakage(violations)  # must not raise


def test_truncation_invariance_on_real_data():
    games = load_games(range(1999, 2001), game_types=("REG",))
    cutoff = pd.Timestamp("2000-01-01", tz=UTC)
    violations = KickoffLeakageHarness().check_truncation_invariance(
        build_feature_frame, games, cutoff, FEATURE_NAMES
    )
    assert violations == []


def test_poison_future_game_does_not_change_past_features():
    games = load_games(range(1999, 2000), game_types=("REG",))
    frame = build_feature_frame(games)
    fake = games.iloc[[0]].copy()
    fake["game_id"] = "2099_01_FAKE"
    fake["season"] = 2099
    fake["kickoff"] = pd.Timestamp("2099-09-01", tz=UTC)
    fake["home_team"] = "KC"
    fake["away_team"] = "BUF"
    fake["home_score"] = 100
    fake["away_score"] = 0
    poisoned = pd.concat([games, fake], ignore_index=True)
    frame2 = build_feature_frame(poisoned)
    m1 = frame.set_index("game_id")[FEATURE_NAMES].sort_index().fillna(-99999).to_numpy()
    m2 = (
        frame2[frame2["game_id"].isin(frame["game_id"])]
        .set_index("game_id")[FEATURE_NAMES]
        .sort_index()
        .fillna(-99999)
        .to_numpy()
    )
    assert (m1 == m2).all()


# ---------------------------------------------------------------------------
# Calibration
# ---------------------------------------------------------------------------

def test_brier_and_logloss_known_values():
    assert cal.brier_score(np.array([1, 0]), np.array([1.0, 0.0])) == pytest.approx(0.0)
    assert cal.brier_score(np.array([1, 0]), np.array([0.5, 0.5])) == pytest.approx(0.25)
    assert cal.log_loss(np.array([1]), np.array([0.9])) == pytest.approx(0.1053605, rel=1e-4)


def test_calibration_curve_bins():
    y_true = np.array([1, 1, 0, 0])
    y_prob = np.array([0.9, 0.8, 0.2, 0.1])
    curve = cal.calibration_curve(y_true, y_prob, n_bins=2)
    assert len(curve) == 2
    high = curve[1]
    assert high["observed"] == pytest.approx(1.0)
    assert high["predicted"] == pytest.approx(0.85)


def test_isotonic_calibrator_monotonic_and_bounded():
    rng = np.random.RandomState(0)
    y_prob = rng.uniform(0.05, 0.95, 400)
    y_true = (rng.uniform(0, 1, 400) < y_prob * 0.7).astype(int)
    iso = cal.IsotonicCalibrator().fit(y_true, y_prob)
    grid = np.linspace(0, 1, 51)
    out = iso.predict(grid)
    assert out.min() >= 0.0 and out.max() <= 1.0
    assert np.all(np.diff(out) >= -1e-9)  # monotone non-decreasing


# ---------------------------------------------------------------------------
# Versioning
# ---------------------------------------------------------------------------

def test_version_format():
    v = versioning.make_version(2, year=2026)
    assert v == "MNB-NFL-2026.02"
    meta = versioning.ModelVersionMetadata(version=v)
    assert meta.version == "MNB-NFL-2026.02"
    with pytest.raises(ValueError):
        versioning.ModelVersionMetadata(version="v1-bad")


# ---------------------------------------------------------------------------
# Prediction storage schema
# ---------------------------------------------------------------------------

def test_prediction_schema_accepts_and_rejects():
    from backend.app.models.predictions import Prediction

    good = Prediction(
        prediction_id="p1",
        game_id="2026_01_NE_SEA",
        created_at="2026-09-09T12:00:00Z",
        model_version="MNB-NFL-2026.02",
        training_cutoff="2025 season (pre-kickoff Week 1 2026)",
        season=2026,
        week=1,
        home_team="SEA",
        away_team="NE",
        model_probability=0.7153,
        predicted_winner="SEA",
        predicted_home_score=26,
        predicted_away_score=21,
        confidence="high",
    )
    assert good.model_version == "MNB-NFL-2026.02"
    assert good.correct is None  # unresolved until the game is final
    with pytest.raises(Exception):
        Prediction(
            prediction_id="p2",
            game_id="2026_01_NE_SEA",
            created_at="2026-09-09T12:00:00Z",
            model_version="MNB-NFL-2026.02",
            training_cutoff="2025 season",
            model_probability=1.5,  # outside [0, 1]: must be rejected
            predicted_winner="SEA",
            confidence="high",
        )
    with pytest.raises(Exception):
        Prediction(  # bad version format: must be rejected
            prediction_id="p3",
            game_id="2026_01_NE_SEA",
            created_at="2026-09-09T12:00:00Z",
            model_version="v2",
            training_cutoff="2025 season",
            model_probability=0.6,
            predicted_winner="SEA",
            confidence="high",
        )
    with pytest.raises(Exception):
        Prediction(  # missing training_cutoff: must be rejected
            prediction_id="p4",
            game_id="2026_01_NE_SEA",
            created_at="2026-09-09T12:00:00Z",
            model_version="MNB-NFL-2026.02",
            model_probability=0.6,
            predicted_winner="SEA",
            confidence="high",
        )


# ---------------------------------------------------------------------------
# Backtest grading helpers
# ---------------------------------------------------------------------------

def test_ats_grading_sign_convention():
    from ml.backtest import _ats_pick_correct, _ou_pick_correct

    # spread_line > 0 => home favored. Home favored by 7, wins by 10: covers.
    assert _ats_pick_correct(10.0, 7.0, 10.0) is True
    # Home favored by 7, wins by 3: does NOT cover.
    assert _ats_pick_correct(10.0, 7.0, 3.0) is False
    # Push: margin exactly equals the line -> excluded.
    assert _ats_pick_correct(7.0, 7.0, 7.0) is None
    # spread_line < 0 => away favored. Home getting 3, loses by 1: covers.
    assert _ats_pick_correct(0.0, -3.0, -1.0) is True
    # Home getting 3, loses by 10: does not cover.
    assert _ats_pick_correct(0.0, -3.0, -10.0) is False
    # Totals: over/under vs the line.
    assert _ou_pick_correct(50.0, 47.5, 51.0) is True
    assert _ou_pick_correct(40.0, 47.5, 51.0) is False
    assert _ou_pick_correct(47.5, 47.5, 47.5) is None


# ---------------------------------------------------------------------------
# Predictions API: honest empty states (no MongoDB in test env)
# ---------------------------------------------------------------------------

def test_predictions_endpoints_honest_empty_states():
    from fastapi.testclient import TestClient

    from app.main import app

    client = TestClient(app)
    body = client.get("/api/predictions").json()
    assert body["status"] == "no_predictions"
    assert body["predictions"] == []
    body = client.get("/api/predictions?season=2026&week=1").json()
    assert body["status"] == "no_predictions"
    body = client.get("/api/predictions/models").json()
    assert body["status"] == "no_models"
    assert body["models"] == []
    body = client.get("/api/predictions/backtests").json()
    assert body["status"] == "no_backtests"
    assert body["backtests"] == []


# ---------------------------------------------------------------------------
# Estimators / ensemble smoke tests (tiny, fast)
# ---------------------------------------------------------------------------

def _tiny_data(n: int = 60, seed: int = 0):
    rng = np.random.RandomState(seed)
    X = rng.normal(size=(n, 4))
    y = (X[:, 0] + 0.5 * X[:, 1] + rng.normal(scale=0.5, size=n) > 0).astype(int)
    return X, y


def test_available_learners_always_includes_hgb():
    assert "hgb" in available_learners()


def test_classifier_and_regressor_fit_predict():
    X, y = _tiny_data()
    clf = make_classifier("hgb", max_iter=10)
    clf.fit(X, y)
    proba = clf.predict_proba(X)
    assert proba.shape == (len(X),)
    assert np.all((proba >= 0) & (proba <= 1))
    reg = make_regressor("hgb", max_iter=10)
    reg.fit(X, y.astype(float))
    pred = reg.predict(X)
    assert pred.shape == (len(X),)


def test_stacking_classifier_end_to_end():
    X, y = _tiny_data(n=120)
    stack = StackingClassifier(learners=["hgb"]).fit(X, y)
    proba = stack.predict_proba(X)
    assert proba.shape == (len(X),)
    assert np.all((proba >= 0) & (proba <= 1))
    assert set(stack.meta_weights.keys()) == {"hgb"}
