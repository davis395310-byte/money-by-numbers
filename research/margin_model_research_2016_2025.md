# Margin-Model Research 2016–2025: Can a purpose-built model beat the closing spread?

**Date:** 2026-09-13
**Status:** RESEARCH ONLY — no production model was registered, promoted, or modified. The champion MNB-NFL-2026.01, the ledger, and all locked picks are untouched.
**Verdict: NO. No purpose-built margin model beats the closing line.**

## 1. Question

The champion predicts *winners* at 62.14%, but its margins go ~50.4% ATS — no edge. The prior spread study (spread_research_2016_2025.md) confirmed no exploitable gap using the winner-model's margins. This study asks whether models **trained specifically to predict point margins** can do what the winner-model cannot: predict margins more sharply than the closing spread, covering >52.38% at −110.

## 2. Data and coverage

| Source | Detail |
|---|---|
| Games | nflverse schedules cache (`~/.cache/money-by-numbers/nflverse/`), REG games, 1999–2025. `spread_line` = closing line (PFR-sourced). Sign convention verified empirically: **positive = home favored** (home win rate 67.2% when > 0, 34.3% when < 0). Home covers iff `(actual_margin − spread_line) > 0`; push iff equal (2.46% of games). |
| Warmup | 1999–2015 (4,328 games) — used only to build sequential features (Elo, rolling form). Never evaluated. |
| Evaluation | 2016–2025, **2,639 / 2,639 games, zero missing, zero imputed**. No null spreads, scores, or rest values. |
| Cross-check | The `spread_line` column is byte-identical to the prior study's join. |

## 3. Features (all strictly pre-kickoff — no leakage)

Computed in a single chronological pass over 1999–2025; every feature for game *g* uses only games kicked off before *g*. State (Elo, rolling histories) is updated with actual results only *after* each game is processed.

| Feature | Definition |
|---|---|
| `elo_diff` | Home Elo − away Elo. Sequential from 1999, all teams init 1500, K=20, HFA=55 in the win-expectancy update. Intercept absorbs average HFA in the regression. |
| `roll_margin8` | (Home mean own scoring margin, last 8) − (away, last 8). Expanding mean when < 8 games played; 0.0 if none. |
| `roll_pf8` / `roll_pa8` | Same construction for points scored / points allowed. |
| `rest_diff` | `home_rest − away_rest` (days, nflverse). |
| `div_game` | nflverse divisional-game flag (public schedule knowledge). |
| `week_f` | Week number (1–18). |

Target: `actual_margin` = home_score − away_score.

## 4. Models

| ID | Model | Features | Notes |
|---|---|---|---|
| M0 | Market baseline | `spread_line` only | No training. The bar to beat. Cannot bet itself (pred == line always). |
| M1 | Ridge(α=1.0) | `elo_diff` | Pure Elo→margin mapping. |
| M2 | Ridge(α=1.0) | all 7 features | Train-only standardization. |
| M3 | HistGradientBoosting(max_iter=300, lr=0.05, depth=3, seed=42) | all 7 features | Fixed hyperparameters — no tuning on evaluation data. |
| M4 | Ridge(α=1.0) | all 7 + `spread_line` | **MARKET-INFORMED.** Labeled; cannot count as "beating Vegas". Included to measure how much the line itself adds. |

**Walk-forward protocol:** for each season S in 2016–2025, train on all games with season < S (1999–S−1), predict season S. No future information anywhere.
**Selection-bias guard:** the single best pure candidate (M1–M3) is chosen on 2016–2020 and reported on the 2021–2025 holdout.
**ATS grading:** bet home iff `pred_margin > spread_line`, else bet away (ties → away, matching the prior study's convention). Pushes (actual == line) excluded; ROI at flat −110 = (W − 1.1·L)/(W+L). Break-even = **52.38%**.

## 5. Results

### 5a. Margin accuracy (MAE, lower is better) — the market wins every season

| Season | Market | M1 Elo | M2 Ridge | M3 GBM | M4 line-aware |
|---|---|---|---|---|---|
| 2016 | **9.02** | 9.56 | 9.31 | 9.27 | 9.02 |
| 2017 | **10.09** | 11.08 | 10.75 | 10.60 | 10.11 |
| 2018 | **9.98** | 10.83 | 10.28 | 10.29 | 10.04 |
| 2019 | **10.21** | 10.86 | 10.58 | 10.95 | 10.18 |
| 2020 | **9.83** | 10.44 | 10.30 | 10.60 | 9.86 |
| 2021 | **10.78** | 11.32 | 11.01 | 10.92 | 10.71 |
| 2022 | **8.74** | 9.69 | 9.18 | 9.34 | 8.81 |
| 2023 | **9.90** | 10.63 | 10.31 | 10.46 | 9.90 |
| 2024 | **9.61** | 10.40 | 10.19 | 10.19 | 9.60 |
| 2025 | **9.72** | 10.48 | 10.33 | 10.37 | 9.80 |
| **Aggregate** | **9.79** | 10.53 | 10.22 | 10.30 | 9.80 |

The closing line predicts the final margin better than every pure model, in **10 out of 10 seasons**, by 0.4–0.7 points of MAE. The line-aware model (M4) merely ties the market (9.80 vs 9.79) — it learns to hug the line, which is exactly why it doesn't count. Mean pred-vs-actual correlation: market n/a, M2 0.37, M3 0.36, M1 0.31, M4 0.44.

### 5b. ATS results — nothing clears break-even

**Aggregate 2016–2025:**

| Model | W–L–P | Win % | ROI @ −110 |
|---|---|---|---|
| M1 Elo | 1297–1277–65 | 50.39% | −4.18% |
| M2 Ridge | 1300–1274–65 | 50.51% | −3.94% |
| M3 GBM | 1323–1251–65 | 51.40% | −2.06% |
| M4 line-aware | 1336–1238–65 | 51.90% | −1.00% |

**Selection-bias guard (pure models only):**

| Model | Train 2016–2020 | Holdout 2021–2025 |
|---|---|---|
| M1 Elo | 51.60% (−1.63%) | 49.25% (−6.58%) |
| M2 Ridge | 50.80% (−3.32%) | 50.23% (−4.52%) |
| **M3 GBM (selected)** | **52.08% (−0.63%)** | **50.75% (−3.42%)** |

The best train-half model (M3, 52.08% — itself *below* break-even, so no rule could even be honestly selected) falls to 50.75% on holdout. M4 reaches 52.49% on holdout (+0.23% ROI) but is market-informed and excluded by protocol — and is within noise of break-even regardless.

**M3 per-season ATS (selected candidate):** 2016: 56.18% · 2017: 55.24% · 2018: 50.61% · 2019: 48.37% · 2020: 50.00% · 2021: 52.24% · 2022: 48.28% · 2023: 50.00% · 2024: 51.12% · 2025: 52.03%. No consistency; the early "good" years do not persist.

**M3 holdout gap buckets** (|pred − line|): 0–1: 51.50% · 1–2: 50.99% · 2–3: 50.68% · 3–5: 47.17% · 5+: 54.10% (145–123, +3.62% ROI). The 5+ bucket is a post-hoc slice, not a train-selected rule, n=268, and only ~0.6 standard errors above break-even — noise, not signal.

## 6. Cross-checks

- **Tie-breaking:** this study bets home iff `pred > line` else away (ties → away), reproducing the prior study's convention exactly: re-grading the .02 file's integer margins under this rule returns the prior study's 1297/1277/65.
- **Coincidence note:** M1 (Elo-only ridge) independently grades 1297–1277–65 — identical counts to the prior .02 figure, though the models agree on the side in only 66.7% of games (880 disagreements). Both are ~50.4% coin flips; identical aggregates from different models are a curiosity, not a finding. The M1 pipeline was re-graded independently and confirmed.
- The prior study's 50.66% continuous-margin ATS figure for .02 and this study's pure-model results (50.4–51.4%) are mutually consistent: no specification of these features beats the line.

## 7. Limitations

- Features are schedules-only: no play-by-play/EPA, weather, injuries, or QB-specific adjustments. EPA-based features are the most plausible upgrade and were not tested (pbp warmup data for 2014–2015 could not be verified this session; cached pbp covers 2016+ only).
- Hyperparameters fixed a priori; no systematic tuning was attempted (deliberately, to avoid selection bias — but a tuned model is untested).
- `spread_line` is nflverse's closing consensus, not a specific book's number; real lines vary by book and time. ROI assumes flat −110, no line shopping, no key numbers.
- These are new research models, not MNB-NFL-2026.01 or .02; nothing here affects production.

## 8. Verdict

**No edge — from either direction.** The winner-model's margins don't beat the line (prior study: 50.39%), and purpose-built margin models don't either (this study: best 51.40% aggregate; train-selected model 50.75% on holdout). The structural reason is visible in 5a: the closing line is a strictly better margin predictor than any pure model tested, in all ten seasons. Beating Vegas requires information or modeling the market systematically lacks; Elo + recent form + rest is not it.

**Product implication:** the two-track direction stands. Track 1 (winner-picker, 62.14% SU, public) is unaffected. Track 2 (beat the spread) remains in research — nothing goes public as ATS product until a model clears 52.38% on a genuine holdout. This study does not clear it.

## 9. Reproducibility

- Features: `/tmp/margin_research/features.parquet` (built by `prep.py`; 6,967 games 1999–2025)
- Modeling: `/tmp/margin_research/model.py` → `results.json`
- Aggregations and gap analysis: inline scripts (see session log)
- Source cache: `~/.cache/money-by-numbers/nflverse/` (schedules 1999–2026)
