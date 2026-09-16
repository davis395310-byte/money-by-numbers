# MONEY BY NUMBERS — ALGORITHM RECONSTRUCTION

Forensic reconstruction of record, 2026-09-09. Sources, in priority order:
(1) `~/workspace/money-by-numbers/` — the actual implementation (primary source);
(2) `~/workspace/nfl-model-research/` (RESEARCH.md, results.json, model.py);
(3) `~/workspace/nfl-week1-backtest/` (model.py, model_meta.json, locked picks).
The old `Bpenergy` GitHub repository is unreachable (HTTP 404, repo page and API,
checked 2026-09-09); no historical commits could be inspected. Nothing in this
document is invented; unverifiable items are labeled **NOT VERIFIED**.

---

## 1. What the system is

Two registered model versions exist (`ml/versioning.py`, `ml/register_model.py`):

| Version | Architecture | Status |
|---|---|---|
| `MNB-NFL-2026.01` | Elo-only: `P(home win) = 1/(1+10^(-(elo_diff+35)/400))` | **CHAMPION / production** (`CHAMPION_MODEL_VERSION`, `backend/app/edges/engine.py:24`) |
| `MNB-NFL-2026.02` | Stacked ensemble: LightGBM + HistGradientBoosting base learners, logistic-regression meta-learner, isotonic calibration | Challenger — measured below the champion |

Champion resolution (`resolve_champion_model`, `engine.py:138-169`): reads the
`models` collection for `champion: true`; falls back to the explicit constant
with `source: "fallback_constant"` — never a silent switch.

A third lineage exists outside the registry: the independent research task
(`~/workspace/nfl-model-research/model.py`) tested five candidates on the same
2016–2025 walk-forward. Its logistic-regression-on-research-factors candidate
measured 63.77% (macro) vs Elo 62.16% — a real, leakage-audited result — but it
has NOT been promoted through the registry and is NOT the production model.

---

## 2. Data flow

```
nflverse schedules parquet (via backend/app/data_providers/nflverse.py, on-disk cache)
   ↓  ml/data_loader.py: load_games() — normalize, parse kickoff (ET→UTC),
      home_win = 1 iff home_score > away_score (TIES ENCODED AS 0),
      margin = home−away, total = home+away, status final/scheduled
   ↓  ml/features.py: build_feature_frame() — ONE chronological pass, kickoff-sorted;
      per-team state (Elo, last-5 lists, streaks, h2h) updated ONLY after finals;
      same-kickoff games computed from a pre-batch snapshot;
      every row carries _data_through (latest contributing kickoff) for audit
   ↓  11 features (see MNB_feature_dictionary.csv). Market lines, injuries, weather
      are DELIBERATELY EXCLUDED from the feature set (features.py:34-35, hooks only)
   ↓  TRAINING (ml/train.py): all final REG games 1999–2025 (features warmed from 1999);
      _fit_fold: last 20% of training fold → isotonic calibrator; stack refit on full fold
   ↓  MODEL: StackingClassifier (LR meta-learner on chronological OOF probs) +
      WeightedAverageRegressor (Ridge, positive, normalized) for margin and total
   ↓  CALIBRATION: IsotonicRegression(out_of_bounds="clip") on held-out block
   ↓  PREDICTION: p = calibrated P(home win); winner = home iff p ≥ 0.5;
      scores from (total ± margin)/2, rounded, clipped ≥ 0
   ↓  MARKET COMPARISON (backend/app/edges/engine.py): implied prob → no-vig →
      edge = model_prob − novig; flag iff edge ≥ 0.03 (inclusive); rank by edge desc
   ↓  CONFIDENCE: HIGH p≥0.68 / MEDIUM p≥0.58 / LOW (from probability only)
   ↓  PICK: locked pre-kickoff into immutable `predictions` ledger (no write routes)
   ↓  RESULT: postgame settlement grades ML/spread/total, pushes, Brier, CLV
```

---

## 3. Exact models used (brief §4)

**Base learners actually used** (per `ml/artifacts/backtest_2016_2025.json`
`"learners": ["lightgbm", "hgb"]`):

- **LightGBM** (`lightgbm.LGBMClassifier/LGBMRegressor`; `ml/models.py:88-135`):
  n_estimators=400, learning_rate=0.03, num_leaves=31, min_child_samples=40,
  subsample=0.8, colsample_bytree=0.8, reg_lambda=1.0, random_state=42, verbose=-1.
- **HistGradientBoosting** (sklearn; `ml/models.py:169-204`): max_iter=400,
  learning_rate=0.05, max_leaf_nodes=31, min_samples_leaf=40,
  l2_regularization=1.0, random_state=42.

**Ensemble** (`ml/ensemble.py`): stacking — `LogisticRegression(max_iter=1000`,
all else sklearn defaults) on out-of-fold probabilities from
`TimeSeriesSplit(n_splits=3)` (chronological, never shuffled). Meta-weights are
learned coefficients (can be negative; 2016 fold: lightgbm 0.9401, hgb −0.1840).
Margin/total: `Ridge(alpha=1.0, positive=True)` weights normalized to sum to 1.

**Calibration** (`ml/calibration.py`): isotonic regression, fit on the last 20%
(chronological) of each training fold; predict clipped to [0,1].

**NOT used in the measured model, despite existing as code:** XGBoost wrappers
exist (`ml/models.py:138-166`) but were not in the recorded learner list.
**CatBoost, Random Forest, neural networks: no code exists — NOT VERIFIED as
ever used.** The brief's warning was correct: do not assume all three GBM
libraries were used; only LightGBM + HGB were.

**Target variable** (`ml/data_loader.py:125-129`, `ml/backtest.py:137`):
`home_win` ∈ {1, 0}; 1 iff home_score > away_score; ties → 0; NaN for non-finals
(dropped). Winner decision: predict home iff calibrated p ≥ 0.5.

**Preprocessing:** none beyond the feature engineering itself — no
normalization, standardization, scaling, log transforms, one-hot encodings,
imputation (missing → neutral defaults: 0.0 / 7 days rest), outlier removal,
feature selection, or dimensionality reduction. All 11 features are numeric.

**Validation:** walk-forward, one fold per season 2016–2025; train = all final
REG games with season < Y (features computed from 1999); no random splits, no
k-fold shuffling; no per-week or per-game retraining in the backtest. The final
production model trains once on 1999–2025.

---

## 4. Input inventory (brief §§6–8, 14)

All 11 features are listed individually in `MNB_feature_dictionary.csv` with
Source / Raw Value / Transformation / Final Model Value / Used By. Summary:

| # | Feature | Window/transform |
|---|---|---|
| 1 | elo_diff | Full-history margin-aware Elo (K=20, scale=400, HFA=35 in expectation only), updated post-final |
| 2 | off_form_diff | Simple mean, last ≤5 finals |
| 3 | def_form_diff | Simple mean, last ≤5 finals (away−home orientation) |
| 4 | net_form_diff | Simple mean, last ≤5 finals |
| 5 | streak_diff | Signed counter (+1/−1, reset to 0 on tie) |
| 6 | rest_diff | (home_rest‖7) − (away_rest‖7), days |
| 7 | div_game | Binary |
| 8 | dome | Binary (roof ∈ {dome, closed}) |
| 9 | neutral | Binary (location = "Neutral") |
| 10 | h2h_margin_avg | Simple mean, last ≤3 meetings, current-home perspective |
| 11 | week | Integer 1–18, untransformed |

All rolling windows are **simple averages** — no exponential weighting, no
custom weights. Team features cover scoring form, streaks, rest, and Elo;
**no** yards/play, EPA, success rate, turnovers, sacks, third-down, or red-zone
features exist in the production model (those belong to the separate research
candidate). **No matchup features** (e.g. Team A pass offense vs Team B pass
defense) are computed. **No QB model, no injury model, no weather model** in the
trained model — Phase 5 built data providers and documented hooks
(`injury_context_features`, `weather_context_features` return neutral defaults
and MUST NOT be fed to a model). **Market data is never a model input**
(`features.py:34-35`); closing lines serve only as the ATS benchmark.

---

## 5. Team strength / Elo (brief §9)

Full specification in `MNB_mathematical_specification.md` §1. Recap:
start 1500.0; K=20; scale=400; HFA=35 Elo points applied to the EXPECTATION
only (stored ratings stay home-neutral); margin multiplier
(|margin|+3)^0.8/(7.5+0.006·|diff|); zero-sum updates; ties change nothing;
season reset/regression toward mean: **none implemented** (NOT VERIFIED as ever
existing — no offseason regression code found in `ml/features.py`).

---

## 6. Market-data timing (brief §15) and timestamp audit (brief §16)

- Market data (spread_line/total_line from nflverse, documented as closing
  lines from PFR) enters ONLY at grading time (`ml/backtest.py:12`,
  `_ats_pick_correct`). It is knowable at kickoff, so grading against it is
  legitimate; it never enters the feature frame.
- Every feature row carries `_data_through` (latest contributing kickoff); the
  harness enforces `_data_through < kickoff` for every row. The backtest
  additionally asserts max training kickoff < min test kickoff per fold
  (`ml/backtest.py:158-165`).
- Odds snapshots for edges/CLV: pre-kickoff snapshots only; "closing" =
  latest snapshot at or before kickoff (`settle.py:173-187`).

---

## 7. Leakage audit (brief §17)

`ml/leakage.py::KickoffLeakageHarness` enforces three checks: per-game
as-of < kickoff; structural `_data_through < kickoff` on the frame;
truncation invariance (rebuilding on truncated data yields byte-identical rows
at/before the cutoff). `assert_no_leakage` raises before any metric is recorded;
`run_walk_forward` calls it first (`ml/backtest.py:145`). Tests
`tests/test_ml.py::test_frame_audit_passes_on_real_data`,
`test_truncation_invariance_on_real_data`,
`test_poison_future_game_does_not_change_past_features` exercise it.
**Result: PASS** (reproduction run; see backtest report). No leakage found; the
backtest is presented as valid.

---

## 8. Bug audit (brief §47)

1. **Tie encoding** (`ml/data_loader.py:125-127`): `home_win = (home_score >
   away_score)` encodes NFL ties as 0 (home loss). ~8–10 ties occurred in
   2016–2025; each counted as a model miss regardless of probability. Small
   downward bias on accuracy (~0.1–0.3pp). Documented, not silently fixed.
2. **Macro vs pooled aggregation** (`ml/backtest.py:232-258`): the comment says
   "pooled" but accuracy/Brier/log-loss/MAEs are macro-averaged (mean of
   seasonal values); only ATS/O-U are truly pooled. Reported 59.47% is the
   macro mean; the pooled figure is 59.49% (1570/2639). Elo: reported 62.16%
   macro; pooled 62.14% (1640/2639). No conclusion changes.
3. **Probability tie at 0.5** (`ml/backtest.py:208`, `ml/train.py:243`):
   `proba >= 0.5` predicts the home team on an exact 0.5 — a home-favoring
   tiebreak. Rare; documented.
4. **Two CLV definitions** coexist: `settle.py:196-237` (probability/line-point
   differences) vs `routers/odds.py:250-314` (raw American-price cents). Which
   the frontend reports is NOT VERIFIED.
5. **rest_diff default**: missing rest → 7 days for both teams (neutral).
   Documented; a genuine unknown, not an error per se.
6. **No offseason Elo regression** (no mean-reversion code found). 538's model
   regresses toward 1505 each offseason; this implementation does not. Noted
   as a modeling choice, not a bug.

No duplicate games, timezone errors (kickoffs parsed ET→UTC, `data_loader.py`),
team-mapping errors, odds-conversion errors, or train/test split errors were
found.

---

## 9. Human-readable example (brief §44)

Game: 2024 regular season, Kansas City at Buffalo (hypothetical illustration
structure — values below are computed from the real pipeline formulas; see
note).

Because per-game predictions from the historical walk-forward were not
persisted (the artifact stores fold aggregates only), a single historical
game's exact intermediate values are NOT VERIFIED from stored records. The
mechanics for any game are:

1. **Inputs**: the two teams' Elo ratings and last-5 scoring lists as of the
   previous kickoff; rest days; dome/div/neutral flags; last-3 h2h margins.
2. **Features**: the 11 values per §4 (e.g. elo_diff = KC_elo − BUF_elo).
3. **Base learners**: LightGBM → p₁; HGB → p₂ (raw P(home win)).
4. **Ensemble**: p_raw = σ(w₀ + w₁·p₁ + w₂·p₂), w = fitted LR coefficients.
5. **Calibration**: p = IsotonicRegression(p_raw), clipped [0,1].
6. **Scores**: margin m and total t from the Ridge-weighted regressors;
   home_score = round((t+m)/2), away_score = round((t−m)/2).
7. **Market**: closing moneyline → implied → no-vig (e.g. −150/+130 → 0.5798).
8. **Edge** = p − 0.5798; flagged iff ≥ 0.03. **Confidence** from p alone
   (≥0.68 HIGH, ≥0.58 MEDIUM).
9. **Pick** locked pre-kickoff; graded postgame; Brier and CLV recorded.

A full game-level table for all 2,639 games is in
`MNB_2016_2025_game_predictions.csv` (reconstructed from a re-run of the
walk-forward; per-game probabilities regenerated by the identical pipeline —
see the backtest report for the reproduction note).

---

## 10. Code-to-formula mapping (brief §46)

| Calculation | File | Function | Lines |
|---|---|---|---|
| Elo expected score | ml/features.py | elo_expected | 58–60 |
| Elo MoV multiplier | ml/features.py | elo_mov_multiplier | 63–65 |
| Elo update | ml/features.py | elo_update | 68–91 |
| Elo win probability | ml/features.py | elo_win_probability | 94–96 |
| Feature frame build | ml/features.py | build_feature_frame | 150–238 |
| Rolling mean helper | ml/features.py | _avg_last | 135–139 |
| Base learner wrappers | ml/models.py | LightGBM/HGB/XGBoost classes | 88–204 |
| OOF predictions | ml/ensemble.py | _oof_predictions | 38–56 |
| Stacking fit/predict | ml/ensemble.py | StackingClassifier | 58–80 |
| Ridge-weighted regressor | ml/ensemble.py | WeightedAverageRegressor | 83–106 |
| Isotonic calibration | ml/calibration.py | IsotonicCalibrator | 70–104 |
| Brier / log-loss | ml/calibration.py | brier_score / log_loss | 30–53 |
| Walk-forward | ml/backtest.py | run_walk_forward | 139–237 |
| Fold fitting | ml/backtest.py | _fit_fold | 96–118 |
| ATS grading | ml/backtest.py | _ats_pick_correct | 77–88 |
| Leakage harness | ml/leakage.py | KickoffLeakageHarness | 60–196 |
| Game loading | ml/data_loader.py | load_games | 73–131 |
| American→implied | backend/app/edges/engine.py | american_to_implied | 34–50 |
| No-vig | backend/app/edges/engine.py | no_vig_probabilities | 71–82 |
| Edge | backend/app/edges/engine.py | edge_vs_market | 89–99 |
| EV | backend/app/edges/engine.py | expected_value | 104–118 |
| Confidence | backend/app/edges/engine.py | confidence_tier | 121–135 |
| Champion resolution | backend/app/edges/engine.py | resolve_champion_model | 138–169 |
| Settlement grading | backend/app/picks/settle.py | grade_pick | 128–170 |
| CLV (settlement) | backend/app/picks/settle.py | compute_clv | 196–237 |
| Pick locking | backend/app/picks/ledger.py | lock_pick | — |
