# "Perfect 6" weekly analysis — 2016–2025

Descriptive statistics on taking, for each regular-season week, the model's 6
highest-confidence winner picks and grading them against actual outcomes.

## Methods

- Source: `~/workspace/mnb_audit/pergame_predictions_working.csv` (2,639 games,
  2016–2025, regular season only).
- `model_probability` is the model's home-team win probability (verified:
  predicted winner = home iff p > 0.5 in 2,632/2,639 rows; the 7 exceptions are
  p = 0.5 exactly, all in 2024). Pick confidence = max(p, 1 − p). The `correct`
  column was independently verified against predicted vs actual winner (0 mismatches).
- Games grouped by (season, week) → 175 weeks. Every week has 13–16 games, so no
  week was dropped. Within each week, games ranked by confidence, top 6 graded.
- Model identity caveat: this file is **MNB-NFL-2026.02** (ensemble, 59.49% SU),
  not champion MNB-NFL-2026.01 — no per-game .01 capture exists. Results describe
  the ensemble's confidence ranking.

## Results

- Weeks analyzed: **175**. Weeks dropped: **0**.
- Perfect (6-for-6) weeks: **17 of 175 = 9.71%**.
- Mean hits per week: **4.01**. Median: **4**. Weeks with 0 hits: **0**.

### Weekly hits distribution (observed)

| Hits | Weeks | % |
|------|------:|------:|
| 6/6 | 17 | 9.71% |
| 5/6 | 43 | 24.57% |
| 4/6 | 61 | 34.86% |
| 3/6 | 37 | 21.14% |
| 2/6 | 13 | 7.43% |
| 1/6 | 4 | 2.29% |
| 0/6 | 0 | 0.00% |

The observed distribution matches an independent-binomial expectation with
p = 0.6686 almost exactly (expected 6/6: 15.6 weeks; observed: 17). Upsets do
not cluster — a miss on one high-confidence pick does not predict misses on the
others.

### Calibration

- 1,050 top-6 picks (6 × 175 weeks): mean stated confidence **67.84%**, actual
  hit rate **66.86%** — well calibrated (within ~1 point).
- Top-6 hit rate (66.86%) exceeds the model's overall hit rate (59.49%), as
  expected: the confidence ranking is doing real work.

### Season-by-season breakdown

| Season | Weeks | 6/6 weeks | Mean hits/week |
|--------|------:|----------:|---------------:|
| 2016 | 17 | 3 | 4.18 |
| 2017 | 17 | 1 | 3.82 |
| 2018 | 17 | 2 | 4.29 |
| 2019 | 17 | 1 | 3.71 |
| 2020 | 17 | 1 | 3.82 |
| 2021 | 18 | 1 | 4.17 |
| 2022 | 18 | 3 | 4.33 |
| 2023 | 18 | 1 | 3.67 |
| 2024 | 18 | 2 | 4.06 |
| 2025 | 18 | 2 | 4.06 |

Best seasons: 2022 (3 perfect weeks, 4.33 mean hits), 2016 (3 perfect weeks,
4.18 mean hits). Weakest: 2023 (1 perfect week, 3.67 mean hits).

### The 17 perfect weeks

2016 W12, W14, W17 · 2017 W8 · 2018 W5, W9 · 2019 W9 · 2020 W12 · 2021 W5 ·
2022 W11, W13, W17 · 2023 W17 · 2024 W6, W13 · 2025 W2, W8.

Mean confidence of the top-6 on perfect weeks ranged 0.615–0.826 — no confidence
threshold separates perfect weeks from the rest.

## Notes

- Descriptive statistics only. Six-leg sweeps are rare events (~1 week in 10 at
  the observed hit rate); this is not a betting system and no payouts, odds, or
  profitability were computed or implied.
- 15 rows carry degenerate probabilities (0.0 or 1.0, all 2017); with
  confidence 1.0 they can enter the top 6 and several were misses — retained as
  recorded, not corrected.
- Nothing was modified: no production models, ledger, or locked picks touched.

*Analysis run 2026-09-13.*
