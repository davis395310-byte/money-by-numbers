# EPA / On-Paper Team-Strength Research 2016–2025

**Date:** 2026-09-13
**Status:** Research-only. Champion `MNB-NFL-2026.01` untouched. Nothing promoted.

## Verdict

- **Straight-up: PROMISING BUT UNPROVEN.** EPA team-strength features measure
  **63.21% (1668/2639)** walk-forward, +1.07pp above champion `MNB-NFL-2026.01`
  (62.14%), and hold at **63.28% on the genuine 2021–2025 holdout**. But the
  95% confidence interval ([61.37%, 65.05%]) still contains 62.14% — the lift
  is inside noise. **Do not promote on this evidence.**
- **Against the spread: NO EDGE.** Model side vs the closing line: **51.36%**
  full sample, **50.30% on holdout**, ROI **−3.97% at −110**. Below the 52.38%
  break-even bar on every split. Plainly no edge.

## Question

Richard asked whether the algorithm should be "progressive" and "take into
consideration how each team is doing on paper" — i.e., rolling team-strength
features from play-by-play (EPA), updated as the season unfolds, instead of
relying on score-based Elo alone. This tests exactly that.

## Data

- nflverse play-by-play parquet, seasons 2014–2025, regular season only.
  2014–2015 = burn-in (feature history + training data only, never evaluated).
- Evaluation sample: **2,639 regular-season games, 2016–2025** — the exact
  verified sample (2022 has 271 games; the canceled Bills–Bengals game).
- `spread_line` = nflverse closing spread (PFR-sourced), positive = home
  favored. Coverage 100% (2,639/2,639).
- 10 ties in sample → counted as **misses** (same denominator convention as
  the champion's 1640/2639). 65 ATS pushes → excluded from ATS% and ROI.

## Features ("on paper" team strength)

Real offensive plays only: `play_type` in (pass, run), no kneels/spikes,
EPA present. Sacks kept as pass plays. For each team, rolling windows over
**strictly prior** regular-season games (game_date < game date):

| Window | Meaning |
|---|---|
| season-to-date | prior games this season |
| last-5 | last 5 games (cross-season) |
| last-16 | last 16 games (cross-season) |

Per window, shrunk toward fixed 2014–2015 league priors
(shrinkage = (sum + k·prior)/(n + k), k = 400/200 plays, fixed a priori):

- offensive EPA/play, defensive EPA/play allowed
- offensive/defensive success rate
- passing EPA/play and rushing EPA/play (offense + allowed)
- giveaway rate (offense), takeaway rate (defense)

Game features = home − away diffs (sign-oriented so positive favors home),
13 total: 6 EPA diffs (std/l5/l16 × off/def), success-rate diffs, pass/rush
splits, defensive pass/rush-allowed diffs, net turnover-margin diff.
Intercept absorbs home-field advantage.

Two variants tested:
- **V1 (EPA-only):** the 13 EPA features.
- **V2 (EPA+Elo):** 13 EPA features + walk-forward `elo_diff`
  (K=20 / scale=400 / HFA=35 engine from `ml/features.py`, REG games from
  2014, chronological).

## Walk-forward protocol (no tuning on holdout)

- To predict season S (2016–2025): train on seasons **< S only**.
- SU: logistic regression (C=1.0, fixed a priori).
- Margin (for ATS grading): ridge regression (alpha=1.0, fixed a priori) on
  the V2 feature set.
- Holdout split declared in advance: train 2016–2020, holdout 2021–2025.
  Hyperparameters, windows, and shrinkage were fixed before any run; the
  holdout was evaluated exactly once per final spec.

## Leakage audit — PASS

1. **As-of audit:** repo `KickoffLeakageHarness.check_feature_frame` over all
   3,151 game rows (2014–2025): **0 violations**. Every row's `_data_through`
   (latest contributing kickoff) is strictly before its kickoff.
2. **Truncation invariance:** full pipeline rebuilt on seasons ≤ 2020 only;
   all features, Elo diffs, and predictions for seasons ≤ 2020 are
   **bit-identical** to the full run. Any future-peeking would have failed
   this check.
3. **Hand verification:** 2016 Week 1 CAR@DEN — DEN's shrunk last-16
   offensive EPA recomputed by hand from 2015 play-by-play = **−0.0566**,
   CAR = **+0.0535**, diff **−0.1101** = stored feature to 4 decimals.
   `_data_through` 2016-01-03 < kickoff 2016-09-08. ✓
4. Training discipline asserted in code: season S trains on seasons < S only.

## Results — straight-up (bar: champion 62.14%)

| Variant | Full 2016–25 | Train 2016–20 | Holdout 2021–25 | Brier |
|---|---|---|---|---|
| V1 EPA-only | **63.21%** (1668/2639) | 63.12% (808/1280) | **63.28%** (860/1359) | 0.2237 |
| V2 EPA+Elo | 62.94% (1661/2639) | 63.52% (813/1280) | 62.40% (848/1359) | 0.2235 |
| Champion .01 | 62.14% (1640/2639) | — | — | (0.2344 Elo baseline*) |

\* Brier 0.2344 is the reported Elo-baseline figure from 2026-09-09 research;
.01's own Brier is not in the verified record.

- V1 beats the champion by **+1.07pp** on the full walk-forward and
  **+1.14pp on the genuine holdout** — numerically the best SU number
  measured to date.
- **But:** 95% CI on the full sample is [61.37%, 65.05%]; 62.14% lies
  inside it. The lift is consistent (V1 ≥ 62.1% in 7 of 10 seasons) yet
  statistically indistinguishable from noise. This is evidence worth
  following, not a promotion case.
- Adding Elo (V2) did **not** help: V2 trails V1 on full and holdout.
  EPA already contains what Elo was contributing.
- Per-season V1: 2016 60.2% · 2017 65.2% · 2018 65.2% · 2019 60.5% ·
  2020 65.6% · 2021 62.1% · 2022 64.6% · 2023 60.3% · 2024 67.3% ·
  2025 62.9%.

**Calibration (V2, full sample):** monotone and roughly diagonal —
stated vs actual by decile: 23.2/30.7 · 35.0/34.1 · 42.4/42.8 · 48.2/48.1 ·
53.4/48.5 · 58.7/53.2 · 63.9/65.9 · 69.1/67.1 · 75.4/73.1 · 84.2/81.1.
Mild overconfidence at the top end, acceptable.

## Results — ATS (bar: 52.38% on holdout)

Model side vs closing `spread_line` (home −line if pred_margin > line,
else away +line; pushes excluded; ROI at −110):

| Split | ATS% | W–L (decisive) | Pushes | ROI @ −110 |
|---|---|---|---|---|
| Full 2016–25 | 51.36% | 1322–1252 | 65 | −1.95% |
| Train 2016–20 | 52.48% | 655–593 | 32 | +0.20% |
| **Holdout 2021–25** | **50.30%** | 667–659 | 33 | **−3.97%** |

Below break-even on all three splits. The train split's 52.48% does not
survive contact with the holdout — exactly the overfitting pattern the
holdout exists to catch. **No ATS edge. Nothing spread-facing changes.**

## Limitations

- `spread_line` is nflverse's closing consensus, not a specific book's number;
  real-world lines vary by book and time.
- No neutral-site handling (a few London/international games per year carry
  the full HFA intercept).
- V2's Elo orders same-date games by game_id (approximation); V2 is not a
  replica of champion .01 (no dead-rubber discount).
- Shrinkage priors fixed from 2014–2015; windows (5/16), k values, C=1.0,
  alpha=1.0 all fixed a priori — no tuning was done at all.
- Playoff games excluded throughout (features and evaluation, REG only).
- 2020 COVID season included with no exclusion.

## Recommendation

1. **Do not promote.** The SU lift is real-but-unproven; the 2026 live
   season is the natural fresh out-of-sample test — score V1's picks against
   .01's locked board week by week.
2. **Track 2 stays in research.** The spread result is a clean NO EDGE;
   nothing about Best Bets, public ATS claims, or product changes.
3. If the 2026 live test confirms the lift, the promotion path is a champion
   challenger run through the formal model registry — never a silent swap.

## Artifacts

- Runner (reproducible): `~/workspace/money-by-numbers/research/epa_team_strength_run.py`
- Per-game predictions: `~/workspace/epa_pbp/epa_predictions.csv`
  (2,639 rows; columns include `p_home_v1`, `p_home_v2`, `pred_margin`,
  `_data_through`, `spread_line`, actuals)
- Summary JSON: `~/workspace/epa_pbp/epa_summary.json`
- Raw play-by-play: `~/workspace/epa_pbp/play_by_play_2014..2025.parquet`
  (moved from /tmp after the sandbox wiped `/tmp/epa_pbp` mid-task)
