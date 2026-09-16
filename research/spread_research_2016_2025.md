# Spread Research 2016–2025: Can the model's predicted margins beat the closing spread?

**Date:** 2026-09-13
**Status:** RESEARCH ONLY — nothing here is promoted, and no production model, ledger entry, or locked prediction was modified.
**Verdict: NO EDGE. The model does not beat the closing spread.**

## 1. What was tested

Whether the model's predicted margins, graded against the closing spread line, produce an against-the-spread (ATS) edge over the 2016–2025 regular seasons (2,639 games). Break-even at standard −110 odds is **52.38%** — anything below that loses money.

## 2. Data and join coverage

| Source | Detail |
|---|---|
| Model predictions | `~/workspace/mnb_audit/pergame_predictions_working.csv` — 2,639 rows. **Model identity: MNB-NFL-2026.02 (ensemble).** This is NOT the champion MNB-NFL-2026.01 (Elo); no per-game file exists for .01, so this research measures the .02 ensemble's margins. Straight-up accuracy in this file: 1570/2639 = **59.49%** (matches the verified .02 figure). |
| Spreads | nflverse schedules parquet `spread_line`, read from the repo's own on-disk cache (`~/.cache/money-by-numbers/nflverse/`). nflverse documents `spread_line` as **the closing spread line** (source: Pro-Football-Reference). Sign convention: positive = home team favored by that many points. |
| Join | On `game_id`. **Matched 2,639 / 2,639 (100%). Zero missing, zero mismatched** — the CSV's `spread` column is byte-identical to the cached nflverse bytes. No imputation was performed. |
| Cross-check | `backtest_2016_2025_ORIGINAL.json` aggregate: `ats_accuracy = 0.5066`, `ats_n = 2574` — consistent with the recomputation below. |

## 3. Method

- Model predicted margin = `predicted_home_score − predicted_away_score` (the integer scores in the file, per the task spec).
- Actual margin = `actual_home_score − actual_away_score`.
- Model's side: bet home −line if `(pred_margin − spread_line) > 0`, else bet away +line.
- Home covers iff `(actual_margin − spread_line) > 0`; a push (`actual_margin == spread_line`) is graded separately and excluded from win % and ROI (stake returned).
- ROI assumes flat −110: risk $1.10 to win $1.00 → ROI = (W − 1.1·L) / (W+L).
- **Selection-bias guard:** any threshold/rule is selected on 2016–2020 (train) and evaluated on 2021–2025 (holdout).

## 4. Results

### 4a. Naive rule: model's predicted winner ATS vs closing line

| Metric | Value |
|---|---|
| Wins / Losses / Pushes | 1,297 / 1,277 / 65 |
| ATS win % (excl. pushes) | **50.39%** |
| ROI at −110 | **−4.18%** per $1 risked |

(The file's `ats_result` column, computed from unrounded continuous margins, reads 50.66%; the two agree on all but 93 rows where rounding the predicted scores to integers flips the graded side. Both figures are far below 52.38%.)

### 4b. Gap analysis: |model_margin − market_spread| buckets

Take the model's side vs the line, bucketed by disagreement size.

**Train 2016–2020 (n=1,248 non-push):**

| |gap| bucket | W | L | P | Win % | ROI |
|---|---|---|---|---|---|---|
| 0–1 | 72 | 82 | 2 | 46.75% | −11.82% |
| 1–2 | 82 | 88 | 6 | 48.24% | −8.71% |
| 2–3 | 92 | 76 | 5 | 54.76% | +5.00% |
| 3–5 | 152 | 167 | 6 | 47.65% | −9.94% |
| 5+ | 218 | 219 | 13 | 49.89% | −5.24% |

**Holdout 2021–2025 (n=1,326 non-push):**

| |gap| bucket | W | L | P | Win % | ROI |
|---|---|---|---|---|---|---|
| 0–1 | 101 | 102 | 4 | 49.75% | −5.52% |
| 1–2 | 109 | 102 | 6 | 51.66% | −1.52% |
| 2–3 | 106 | 98 | 5 | 51.96% | −0.88% |
| 3–5 | 128 | 163 | 13 | 43.99% | −17.63% |
| 5+ | 237 | 180 | 5 | 56.83% | +9.35% |

The "best" bucket flips between halves (train: 2–3 pts at 54.76%; holdout: 5+ pts at 56.83%, which was 49.89% on train). That is noise, not signal.

### 4c. Threshold-rule selection (train) → holdout evaluation

| Rule |gap| ≥ t | Train win % | Holdout win % |
|---|---|---|
| t = 1.0 | 49.73% | — |
| t = 1.5 | 50.15% (best on train, n=1,003) | — |
| t = 2.0 | 50.00% | 51.64% |
| t = 2.5 | 49.70% | — |
| t = 3.0 | 48.94% | 51.55% |
| t = 4.0 | 49.83% | — |
| t = 5.0 | 49.89% | — |

**No threshold clears 52.38% on the train half at all**, so no rule can even be honestly selected. The two carry-forward thresholds (|gap| ≥ 2.0, ≥ 3.0) both fail on holdout.

### 4d. Season-by-season, naive rule

| Season | W | L | P | Win % | ROI |
|---|---|---|---|---|---|
| 2016 | 130 | 121 | 5 | 51.79% | −1.24% |
| 2017 | 122 | 126 | 8 | 49.19% | −6.69% |
| 2018 | 131 | 116 | 9 | 53.04% | +1.38% |
| 2019 | 110 | 136 | 10 | 44.72% | −16.10% |
| 2020 | 123 | 133 | 0 | 48.05% | −9.10% |
| 2021 | 127 | 141 | 4 | 47.39% | −10.49% |
| 2022 | 131 | 130 | 10 | 50.19% | −4.60% |
| 2023 | 145 | 113 | 14 | 56.20% | +8.02% |
| 2024 | 138 | 130 | 4 | 51.49% | −1.87% |
| 2025 | 140 | 131 | 1 | 51.66% | −1.51% |

Best single seasons (2018: 53.04%, 2023: 56.20%) are isolated; surrounded by losing seasons. No year-to-year consistency.

### 4e. Extra checks

- HIGH confidence & |gap| ≥ 3 (all years): 105–116–6, **47.51%**, ROI −10.23% — worse than average.
- Naive rule by half: train 49.36% (ROI −6.35%), holdout 51.36% (ROI −2.15%).

## 5. Verdict

**No edge.** The model's predicted margins do not beat the closing spread:

- Naive ATS: 50.39%, ROI −4.18%.
- Nothing clears the 52.38% break-even on the holdout half under any rule, bucket, or threshold.
- Apparent bright spots (holdout |gap| ≥ 5 at 56.83%; 2023 at 56.20%) fail the selection-bias guard — they do not replicate on the train half or across seasons.

**Product implication:** the board must continue to present "our predicted margin" as model output. It must NOT be framed as ATS picks, "model vs Vegas" winners, or anything implying the model beats the number. The 50.66% verified ATS baseline stands: essentially a coin flip against the spread.

## 6. Data limitations

- Per-game predictions exist only for **MNB-NFL-2026.02**; the champion **MNB-NFL-2026.01** (Elo, 62.14% SU) was not re-measured here. If a per-game .01 capture is ever produced, this research should be re-run on it — but there is no reason to expect a different ATS conclusion without a spread-specific model.
- ATS was graded from the file's **integer rounded predicted scores**; 93 rows (3.5%) grade differently than from the continuous margins used in the original backtest (50.39% vs 50.66%). Immaterial to the verdict.
- `spread_line` is nflverse's closing consensus (PFR-sourced), not a specific sportsbook's number; real-world lines vary by book and time.
- ROI assumes flat −110 on every play; no line shopping, no half-point analysis, no key-number adjustments.
- 65 pushes (2.5%) excluded from win % and ROI (stake returned) — standard treatment.
