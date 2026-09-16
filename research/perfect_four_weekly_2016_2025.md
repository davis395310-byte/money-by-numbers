# "Perfect 4" Weekly Analysis — 2016–2025

**Date:** 2026-09-13
**Question:** For each regular-season week, how often do the model's 4 highest-confidence winner picks ALL hit?
**Data:** `~/workspace/mnb_audit/pergame_predictions_working.csv` — 2,639 games, 2016–2025 regular seasons.
**Model:** **MNB-NFL-2026.02** (ensemble), not champion .01 — no per-game .01 capture exists. Results describe .02's behavior, not .01's.
**Status:** DESCRIPTIVE ONLY. Not a betting system. No parlay payouts, odds, or profitability computed or implied.

---

## Method

1. Grouped 2,639 games into 175 weeks by (season, week).
2. Within each week, ranked games by confidence = max(model_probability, 1 − model_probability), where `model_probability` is the verified home-win probability. Took the top 4.
3. Graded each pick against `actual_winner` via the independently verified `correct` column (re-checked: `correct` == (predicted_winner == actual_winner) on all 2,639 rows — zero mismatches).

**Sanity checks:** every week had 13–16 games; zero weeks dropped. 15 rows (all 2017) carry degenerate probabilities of exactly 0.0/1.0; retained as recorded, same as the sibling Perfect-6 study.

---

## Headline results

| Metric | Value |
|---|---|
| Weeks analyzed | **175** |
| Perfect 4-for-4 weeks | **35 → 20.00%** |
| Mean hits / week | **2.686** |
| Stated mean confidence (top-4 picks, n=700) | **70.36%** |
| Actual hit rate (top-4 picks) | **67.14%** |
| Calibration verdict | Well calibrated (slightly conservative-to-mild overconfidence, ~3 pts) |
| Top-4 hit rate vs model overall | 67.14% vs 59.49% — high-confidence picks meaningfully outperform the average pick |

**Full weekly distribution:**

| Hits (of 4) | Weeks | % | Binomial-expected (p=0.6714) |
|---|---|---|---|
| 4 | 35 | 20.00% | 35.6 (20.32%) |
| 3 | 69 | 39.43% | 69.6 (39.78%) |
| 2 | 54 | 30.86% | 51.1 (29.20%) |
| 1 | 15 | 8.57% | 16.7 (9.53%) |
| 0 | 2 | 1.14% | 2.0 (1.17%) |

The observed distribution matches an independent-binomial expectation (p = 0.6714) almost exactly — upsets do not cluster; weekly outcomes behave like independent coin-weighted trials at the model's stated confidence.

The two 0-for-4 weeks in the sample were **2017 Week 6** and **2023 Week 7** — the model's highest-confidence picks went cold twice in ten years (1.14% of weeks).

---

## Season-by-season breakdown

| Season | Weeks | 4-for-4 weeks | Mean hits/week |
|---|---|---|---|
| 2016 | 17 | 4 | 2.824 |
| 2017 | 17 | 4 | 2.588 |
| 2018 | 17 | 4 | 2.765 |
| 2019 | 17 | 2 | 2.706 |
| 2020 | 17 | 2 | 2.706 |
| 2021 | 18 | 5 | 2.833 |
| 2022 | 18 | 5 | 2.833 |
| 2023 | 18 | 2 | 2.278 |
| 2024 | 18 | 3 | 2.611 |
| 2025 | 18 | 4 | 2.722 |

Best: **2021 and 2022** (5 perfect weeks each, 2.833 mean hits). Worst: **2023** (2 perfect weeks, 2.278 mean hits) — also the weakest season in the sibling Perfect-6 study.

---

## Plain-language reading

About **1 week in 5**, the model's four most confident picks all won. Roughly 60% of weeks produced 3-or-4 hits; total whiffs were almost nonexistent. The confidence numbers are honest: the model said ~70% and hit ~67%. But a 20% sweep rate is not a system — it is the arithmetic of four ~67% events landing together, and it arrives with no predictability about *which* weeks sweep.

---

## Caveats

- Predictions are MNB-NFL-2026.02 (ensemble, 59.49% overall), not champion MNB-NFL-2026.01 (62.14%) — no per-game .01 file exists.
- 15 degenerate 0.0/1.0 probabilities (all 2017) retained as recorded.
- Spreads, totals, and ATS outcomes are out of scope here; this study grades straight-up winners only.
- Nothing was modified: no production models, ledger, or locked picks touched.
