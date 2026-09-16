# MONEY BY NUMBERS — MATHEMATICAL MODEL SPECIFICATION

Forensic reconstruction of record, 2026-09-09. Every formula below was copied
from the implementation; each carries its code location. Anything that could
not be confirmed from source is labeled **NOT VERIFIED**. Nothing here is
invented.

Model under audit: `MNB-NFL-2026.02` (stacked ensemble) and the champion
baseline `MNB-NFL-2026.01` (Elo-only). Repository: `~/workspace/money-by-numbers`.

---

## 1. Elo rating system (team strength)

Constants — `ml/features.py:52-55`:
- `ELO_START = 1500.0`
- `ELO_K = 20.0`
- `ELO_SCALE = 400.0`
- `ELO_HFA = 35.0` (fit by log-loss grid search on 2016+ pre-game diffs; `model_meta.json` confirms `hfa_elo: 35.0`, `k_factor: 20.0`)

### 1a. Expected score — `elo_expected`, `ml/features.py:58-60`

    E(diff) = 1 / (1 + 10^(-diff / 400))

`diff` is the Elo advantage of the team in question, in Elo points. Output in [0,1].

### 1b. Margin-of-victory multiplier — `elo_mov_multiplier`, `ml/features.py:63-65`

    mult(margin, diff) = (|margin| + 3)^0.8 / (7.5 + 0.006 * |diff|)

538-style autocorrelation correction: blowouts by heavy favorites move ratings
less than equally large upsets. `margin` is the home-team point margin.

### 1c. Rating update — `elo_update`, `ml/features.py:68-91`

    diff      = (home_elo - away_elo) + 35        # HFA shifts EXPECTATION only
    expected  = E(diff)                            # P(home win) incl. HFA
    k         = 20 * mult(margin, diff)

    If margin > 0 (home won):
        home_elo' = home_elo + k * (1 - expected)
        away_elo' = away_elo - k * (1 - expected)
    If margin < 0 (home lost):
        home_elo' = home_elo - k * expected
        away_elo' = away_elo + k * expected
    If margin = 0 (tie): ratings unchanged.

Zero-sum: points gained by one team are lost by the other. Stored ratings stay
home-neutral (HFA never leaks into the ratings themselves). Ratings update only
AFTER a game is final, and games sharing an identical kickoff are all computed
from a pre-batch snapshot — `ml/features.py:150-238`.

### 1d. Win probability from Elo — `elo_win_probability`, `ml/features.py:94-96`

    P_home_win = E(elo_diff + 35)

where `elo_diff = home_elo - away_elo` with no HFA baked in. This is the entire
`MNB-NFL-2026.01` champion model: one number in, one probability out.

### Worked example
Home Elo 1600, away Elo 1550 → elo_diff = 50.
P_home = 1/(1+10^(-85/400)) = 1/(1+10^(-0.2125)) = 1/(1+0.6131) = **0.6199**.
After a 10-point home win: diff=85, expected=0.6199,
mult = 13^0.8/(7.5+0.006*85) = 7.783/8.01 = 0.9717, k = 20*0.9717 = 19.43;
home_elo' = 1600 + 19.43*(1-0.6199) = 1600 + 7.39 = **1607.39**;
away_elo' = 1550 - 7.39 = **1542.61**.

---

## 2. Rolling / form features — `ml/features.py:135-147, 190-210`

For a team with chronological list `v` of a quantity over final games only:

    avg_last(v, n) = mean(v[-n:])   if v non-empty
                   = 0.0            if v empty (neutral prior, never invented)

All windows are simple (unweighted) means — no exponential weighting, no
custom weights:

- `off_form_diff` = avg_last(home_pts_for, 5) − avg_last(away_pts_for, 5)
- `def_form_diff` = avg_last(away_pts_against, 5) − avg_last(home_pts_against, 5)
  (positive = home defense better)
- `net_form_diff` = avg_last(home_margins, 5) − avg_last(away_margins, 5)
- `streak_diff`   = home_streak − away_streak, where streak is a signed counter
  (+1 per win, −1 per loss, **reset to 0 on a tie**) — `ml/features.py:230`
- `rest_diff`     = (home_rest or 7) − (away_rest or 7), in days
- `h2h_margin_avg` = mean of last ≤3 meetings' margins converted to the
  current home team's perspective (negated when the home team was away);
  0.0 if the pair never met
- `div_game`, `dome`, `neutral` ∈ {0,1} as documented in the feature dictionary
- `week` = season week number (integer, untransformed)

### Worked example
Home team scored [24, 31, 17, 28, 20] in its last 5 finals → avg 24.0.
Away team scored [21, 14, 28, 17, 24] → avg 20.8.
`off_form_diff` = 24.0 − 20.8 = **3.2**.

---

## 3. Base learners — `ml/models.py`

Uniform wrapper interface: `fit(X, y)`, `predict_proba(X)` → P(home win).

### 3a. LightGBM (`LightGBMClassifier` / `LightGBMRegressor`, `ml/models.py:88-135`)

    LGBMClassifier / LGBMRegressor(
        n_estimators=400, learning_rate=0.03, num_leaves=31,
        min_child_samples=40, subsample=0.8, colsample_bytree=0.8,
        reg_lambda=1.0, random_state=42, verbose=-1)

### 3b. HistGradientBoosting (`HGBClassifier` / `HGBRegressor`, `ml/models.py:169-204`)

scikit-learn, always available as fallback:

    HistGradientBoostingClassifier / Regressor(
        max_iter=400, learning_rate=0.05, max_leaf_nodes=31,
        min_samples_leaf=40, l2_regularization=1.0, random_state=42)

### 3c. XGBoost — NOT USED in the measured backtest
`XGBoostClassifier`/`XGBoostRegressor` wrappers exist (`ml/models.py:138-166`)
with (n_estimators=400, learning_rate=0.03, max_depth=4, subsample=0.8,
colsample_bytree=0.8, reg_lambda=1.0, random_state=42, eval_metric="logloss"),
but the recorded artifact `ml/artifacts/backtest_2016_2025.json` lists
`"learners": ["lightgbm", "hgb"]` only. **CatBoost does not exist anywhere in
the codebase.** Any claim that the historical model used XGBoost or CatBoost is
NOT VERIFIED and contradicted by the artifact for the measured run.

---

## 4. Stacking ensemble — `ml/ensemble.py`

### 4a. Out-of-fold predictions — `_oof_predictions`, `ml/ensemble.py:38-56`
`sklearn.model_selection.TimeSeriesSplit(n_splits=3)` — chronological, never
shuffled. Each base learner is fit on each train block and predicts its
validation block, producing an (n_games × n_learners) OOF probability matrix.

### 4b. Meta-learner — `StackingClassifier.fit`, `ml/ensemble.py:70-80`
`sklearn.linear_model.LogisticRegression(max_iter=1000)` (all other
hyperparameters at sklearn defaults: L2, C=1.0) fit on the OOF probabilities.
Base learners are then refit on the FULL training fold. Final probability:

    P_final = σ(w_0 + w_1·p_lgbm + w_2·p_hgb)

where σ is the logistic function and the weights are the fitted LR
coefficients. **Weights are learned, never hand-set.** Because they are raw
logistic-regression coefficients (not constrained to be positive or sum to 1),
individual weights CAN be negative — e.g. the recorded 2016 fold meta-weights
were `{lightgbm: 0.9401, hgb: -0.1840}` (`backtest_2016_2025.json`).

### 4c. Margin/total regressors — `WeightedAverageRegressor`, `ml/ensemble.py:83-106`
`sklearn.linear_model.Ridge(alpha=1.0, positive=True)` on OOF margin (or total)
predictions; coefficients normalized to sum to 1:

    w_i = ridge_coef_i / Σ ridge_coef_j   (all ≥ 0 by the positive=True constraint)
    pred = Σ w_i · base_pred_i

---

## 5. Calibration — `ml/calibration.py`

`IsotonicCalibrator`: `sklearn.isotonic.IsotonicRegression(out_of_bounds="clip",
y_min=0.0, y_max=1.0)`. Fit on the **last 20% of each training fold**
(chronological holdout — never on training rows, never on the test season);
`predict` clips to [0,1]. Every served/stored prediction probability is the
calibrated value. — `ml/backtest.py:96-118`, `ml/calibration.py:70-104`.

Metrics (`ml/calibration.py:30-68`):

    Brier(y, p)    = mean((p − y)²)
    LogLoss(y, p)  = −mean(y·ln(p) + (1−y)·ln(1−p)),  p clipped to [1e-12, 1−1e-12]
    calibration_curve: 10 equal-width bins on [0,1]; per bin {bin_center,
        mean predicted p, observed event rate, count}; empty bins omitted.

---

## 6. Prediction outputs — `ml/train.py:predict_scheduled`, `ml/train.py:220-247`

For a game with calibrated P(home win) = p, predicted home margin m (points),
predicted total t (points):

    predicted_winner   = home_team  if p ≥ 0.5 else away_team
    pred_home_score    = clip(round((t + m)/2), 0, None)   → int
    pred_away_score    = clip(round((t − m)/2), 0, None)   → int

Target encoding (`ml/data_loader.py:125-129`): `home_win = 1` if
home_score > away_score else `0` — **a tied game is encoded as 0 (home loss)**.
Non-final games carry `home_win = NaN` and are dropped from training/eval
(`ml/backtest.py:137`).

---

## 7. Walk-forward protocol — `ml/backtest.py:run_walk_forward`, `ml/backtest.py:139-237`

For each season Y ∈ {2016…2025}: train on all final regular-season games with
season < Y (features warmed up from 1999), predict season Y. Per fold:

1. Leakage harness `assert_no_leakage` on the feature frame — raises before
   any metric is recorded (`ml/backtest.py:145`).
2. Fold cutoff audit: max training kickoff must be < min test kickoff
   (`ml/backtest.py:158-165`).
3. Fit stack + calibrator + margin/total regressors (`_fit_fold`,
   `ml/backtest.py:96-118`); calibrate test probabilities; grade.

Per-fold winner accuracy = `mean((proba ≥ 0.5) == home_win)`.

**Aggregation (AUDIT FINDING — code vs comment mismatch):** the comment at
`ml/backtest.py:232` says "Aggregate across folds (pooled)", but accuracy,
Brier, log-loss, margin/total MAE, and the Elo equivalents are computed as
`np.mean` of the per-fold values — a **macro-average** (mean of seasonal
accuracies), NOT pooled correct/total. ATS and O/U accuracies ARE genuinely
pooled (weighted by n). Consequences:

| Metric | Reported (macro) | Corrected (pooled) |
|---|---|---|
| Ensemble winner accuracy (n=2639) | 59.47% | **59.49%** (1570/2639) |
| Elo winner accuracy (n=2639) | 62.16% | **62.14%** (1640/2639) |
| ATS accuracy (n=2574) | 50.66% | 50.66% (1304/2574, genuinely pooled) |
| O/U accuracy (n=2618) | 49.39% | 49.39% (1293/2618, genuinely pooled) |

The deltas are ≤0.02pp and change no conclusion, but the macro-vs-pooled
distinction is recorded here per the brief's no-silent-alteration rule.

---

## 8. Betting math — `backend/app/edges/engine.py`, `backend/app/picks/settle.py`

### 8a. American odds → implied probability — `american_to_implied`, `engine.py:34-50`
    a < 0:  P = −a / (−a + 100)
    a > 0:  P = 100 / (a + 100)
Returns None for None/0/unparsable — never invented. (Duplicated at
`settle.py:69-70`.)

### 8b. Vig removal — `no_vig_probabilities`, `engine.py:71-82`
    p_a = imp_a / (imp_a + imp_b)
Returns (None, None) if either side missing. Worked: −150/+130 →
imp = 0.6000/0.4348, overround 1.0348, no-vig favorite = **0.5798**
(`engine.py:74,80`).

### 8c. Edge — `edge_vs_market`, `engine.py:89-99`
    edge = model_prob − novig_implied_prob
Plain probability difference. Flagged when `edge ≥ threshold`
(**inclusive** — `engine.py:201`: `if edge is None or edge < threshold: return None`;
verified: 0.03 flagged, 0.0299999 not). Default `DEFAULT_EDGE_THRESHOLD = 0.03`
(`engine.py:27`); `/api/edges` exposes `min_edge` default 0.03
(`routers/odds.py:172`). Rows sorted descending by edge (`routers/odds.py:233`).

### 8d. Expected value — `expected_value`, `engine.py:104-118`
    EV = p × decimal_odds − 1        (unit stake)
Stored on edge rows (`engine.py:216`) but NOT used for filtering or ranking.
Worked: EV(0.70 @ −150) = 0.70×1.6667−1 = **+0.1667**;
EV(0.45 @ +130) = 0.45×2.30−1 = **+0.0350**.

### 8e. Confidence tiers — `confidence_tier`, `engine.py:121-135`
`CONFIDENCE_HIGH = 0.68`, `CONFIDENCE_MEDIUM = 0.58` (`engine.py:30-31`).
**HIGH if p ≥ 0.68; MEDIUM if p ≥ 0.58; else LOW.** Derived from the model
probability ONLY, not from edge. Missing/invalid → LOW. (Ledger duplicates the
same tiers at `ledger.py:88-90`.)

### 8f. Decimal odds — `american_to_decimal`, `engine.py:53-68`
    a < 0: 1 + 100/(−a)      a > 0: 1 + a/100

### 8g. Settlement grading — `backend/app/picks/settle.py`
- Moneyline (`grade_pick`, `settle.py:128-139`): predicted_winner ==
  actual_winner → win; tie game → **push**; else loss. Brier recorded for
  moneyline only: (p−1)² win / (p−0)² loss / None on push (`settle.py:35,132-138`).
- Spread (`settle.py:140-157`): line quoted HOME-perspective.
  cover = (home−away) + line for home side; (away−home) − line for away.
  >0 win, =0 **push**, <0 loss. Missing line → "ungraded".
- Total (`settle.py:158-170`): over wins iff total > line (push on equality);
  under wins iff total < line (push on equality); missing line → "ungraded".
- Idempotent via unique index + upsert on prediction_id (`settle.py:264-269`);
  the `predictions` ledger is never modified (`settle.py:12-13`).
- CLV (`compute_clv`, `settle.py:196-237`): pick-time line/price vs the latest
  odds snapshot **at or before kickoff**. Spread/total: clv_points =
  closing_line − line_at_pick; moneyline: clv_prob_points =
  implied_close − implied_pick (raw implied, no vig removal). Favorable =
  line moved toward the picked side. All None if either side missing.
  NOTE: `routers/odds.py:250-314` computes a DIFFERENT moneyline CLV
  (clv_cents = pick_price − close_price on raw American prices) — two live
  definitions exist; which the frontend reports is NOT VERIFIED.

---

## 9. ATS grading (backtest) — `_ats_pick_correct`, `ml/backtest.py:77-88`
nflverse `spread_line` (closing line, PFR source — knowable at kickoff, hence
legitimate for grading; NEVER a model input — `features.py:34-35`):
spread_line > 0 ⇒ home favored. Pick home covers iff
`(pred_margin − spread_line) > 0`; actual covers iff
`(actual_margin − spread_line) > 0`. Equality on the actual side = push
(excluded from ATS n). Same push-exclusion for totals (`_ou_pick_correct`,
`ml/backtest.py:91-95`).

---

## 10. Model registry — `ml/versioning.py`, `ml/register_model.py`
- `MNB-NFL-2026.01`: Elo-only (this spec §1d). **Champion/production.**
- `MNB-NFL-2026.02`: stacked ensemble (this spec §§2-6). Challenger; measured
  59.47% (macro) vs Elo 62.16% (macro) — did NOT beat the champion.
- `make_version(major, year)` → `MNB-NFL-<year>.<major:02d>`.

---

## 11. NOT VERIFIED items
- The old `Bpenergy` GitHub repository is unreachable (HTTP 404 on both the
  repo page and the API as of 2026-09-09); no historical commits could be
  inspected. Any "Big Pick Energy"-era implementation detail is NOT VERIFIED.
- "61.83% accuracy": appears NOWHERE in code, docs, artifacts, JSONs, CSVs, or
  logs (workspace-wide grep, excluding vendored venv packages). It exists only
  as a conversational reference ("the earlier approximate benchmark"). Its
  numerator, denominator, model, and sample are all NOT VERIFIED.
- Whether the frontend's CLV display uses `settle.py` or `routers/odds.py`
  semantics: NOT VERIFIED.
- SHAP/gain/permutation feature importance: no such code exists in the
  implementation; NOT VERIFIED as ever computed.
