# MONEY BY NUMBERS — MODEL AUDIT

Leakage, bugs, validation, and methodology audit. 2026-09-09.
Model under audit: `MNB-NFL-2026.02` (challenger) vs `MNB-NFL-2026.01` (champion, Elo).

---

## 1. Leakage audit — PASS

Three-layer enforcement, all verified in code and by execution:

1. **Structural** (`ml/leakage.py::check_feature_frame`): every feature row
   carries `_data_through` (latest contributing kickoff); the harness requires
   `_data_through < kickoff` for every row. `run_walk_forward` calls
   `assert_no_leakage` before any metric is recorded (`ml/backtest.py:145`).
2. **Fold cutoff** (`ml/backtest.py:158-165`): per fold, max training kickoff
   must be strictly before min test kickoff — raises otherwise.
3. **Behavioral** (`check_truncation_invariance`): rebuilding features on
   data truncated at a cutoff yields byte-identical rows at/before the cutoff.
   Exercised by `tests/test_ml.py::test_truncation_invariance_on_real_data` and
   `test_poison_future_game_does_not_change_past_features` — both PASS.

Additional structural protections: per-team state updates only after finals;
same-kickoff games computed from a pre-batch snapshot (`ml/features.py:160-238`).
Market lines are never features (`features.py:34-35`). **No leakage found.
The backtest is presented as valid.**

**Research lineage leakage audit** (2026-09-09, `~/workspace/nfl-model-research/`):
`leakage_audit(df_eval, m)` — **PASS**: "leakage audit passed on 300 sampled
games (max source kickoff < target kickoff everywhere)" over 2,639 eval games,
13,934 team-games, 17/17 pbp seasons (log: `~/workspace/mnb_audit/research_leakage.log`).
`exact_value_audit` — **NOT RUN**: crashed with `ValueError: assignment
destination is read-only` at `model.py:595` (numpy 2.x read-only-array
incompatibility in the audit code itself, not a leakage finding). No PASS/FAIL
can be claimed from it; the source was not modified.

## 2. Bug findings

| # | Location | Finding | Severity | Effect |
|---|---|---|---|---|
| 1 | `ml/data_loader.py:125-127` | Ties encoded as home loss (`home_score > away_score`) | Low | ~8–10 ties in 2016–2025 each counted as a miss; accuracy understated ~0.1–0.3pp |
| 2 | `ml/backtest.py:232-258` | Comment says "pooled"; accuracy/Brier/MAE are macro-averaged | Low | Reported 59.47% vs pooled 59.49%; Elo 62.16% vs 62.14%. Both figures now reported |
| 3 | `ml/backtest.py:208`, `ml/train.py:243` | `proba >= 0.5` tiebreak favors home team | Negligible | Exact-0.5 probabilities are rare; documented |
| 4 | `settle.py` vs `routers/odds.py` | Two live CLV definitions (prob/point-based vs raw American cents) | Medium | Ambiguity in what the frontend reports — NOT VERIFIED which is displayed |
| 5 | `ml/features.py` (absent) | No offseason Elo mean-reversion (538 regresses to ~1505) | Info | Modeling choice, not a bug; noted |
| 6 | `ml/train.py:7-8` vs `:44,:96` | Docstring says final model trains on 2016–2025; code trains 1999–2025 | Low | Training window is the code value (1999–2025); `metadata.json` records `"training_period": "1999-2025 regular seasons"` correctly |

No duplicate games, timezone errors, team-mapping errors, odds-conversion
errors, or train/test contamination were found. The exact no-vig example was
verified: −150/+130 → 0.5798 (not the rough 0.5882 that appeared in earlier
conversation).

## 3. Methodology validation

| Check | Result |
|---|---|
| Walk-forward chronological protocol | PASS — one fold/season, train < Y, no shuffling |
| Leakage harness gates results | PASS — raises before metrics on violation |
| Calibration on held-out block | PASS — last 20% of training fold, never test data |
| Ensemble weights learned, not hand-set | PASS — LR coefficients / Ridge(normalized) |
| Market lines excluded from features | PASS — explicit exclusion + grep-clean models.py |
| Champion not silently replaced | PASS — explicit registry + fallback constant |
| Losing picks preserved | PASS — predictions ledger has no write routes; settlement writes separate collection |
| 61.83% reproducible | **FAIL — NOT VERIFIED** (zero occurrences in any artifact; GitHub repo 404) |
| XGBoost/CatBoost used | **NOT VERIFIED / contradicted** — artifact lists lightgbm+hgb only; no CatBoost code exists |

## 3b. Full backtest reproduction — VERIFIED (PASS)

On 2026-09-09 the documented command `python3 -m ml.run_backtest` was re-run
end-to-end: EXIT=0. The regenerated `ml/artifacts/backtest_2016_2025.json` is
**byte-identical** to the original artifact (md5
`454d3767c33b334fb87749ee5f90dc41`; 0 field mismatches across all 14 aggregate
fields and all 10 folds; the original is secured at
`~/workspace/mnb_audit/backtest_2016_2025_ORIGINAL.json`; run log at
`~/workspace/mnb_audit/backtest_rerun2.log`). Because it is byte-identical, the
regeneration altered no historical result.

**Verdict mapping:** the existing implementation and its 2016–2025 backtest
reproduce exactly — the artifact's numbers are VERIFIED as reproducible. This is
independent of the 61.83% claim, which remains NOT VERIFIED (see §5).

## 4. Statistical context

- Ensemble pooled accuracy 59.49% (1570/2639). 95% Wilson interval ≈ [57.6%, 61.4%].
- Elo pooled 62.14% (1640/2639), 95% CI ≈ [60.3%, 64.0%]. The ensemble
  underperforms the baseline by 2.65pp pooled — outside noise, a real deficit.
- ATS 50.66% (1304/2574): 95% CI ≈ [48.7%, 52.6%] — includes 50%; no evidence
  of edge vs the spread, and no vig-adjusted profit is demonstrated.
- Research logistic candidate 63.77% vs Elo 62.16%: +1.6pp on n=2639
  (SE of the difference ≈ 0.9pp) — suggestive, not definitive; calibration gain
  (Brier 0.2344 → 0.2229) is the more robust improvement.

## 5. What was NOT verifiable

- The 61.83% figure in its entirety (numerator, denominator, model, sample).
- Any "Big Pick Energy"-era implementation (GitHub repo unreachable).
- Which CLV definition the frontend displays.
- Feature importance (SHAP/gain/permutation): no such code exists.
- Moneyline ROI: no valid historical odds feed at audit time — not computed,
  not zero-filled.
- Business metrics (clicks, conversions, revenue): no production traffic;
  correctly absent, not fabricated.
