# Richard's Hand-Specified Team-Rating Formula — Honest Backtest 2016–2025

**Date:** 2026-09-14
**Status:** Research-only. Champion `MNB-NFL-2026.01` untouched. Nothing promoted. Nothing replaced.

> **61.83% exclusion note:** the signed forensic verdict of 2026-09-13 found the 61.83%
> figure NOT VERIFIED (zero occurrences in any code, doc, or artifact). It is not
> associated with this formula, appears nowhere in these results, and must never be
> quoted as this formula's accuracy.

## Verdict

- **Straight-up: MATCHES the champion, does not beat it.** Richard's hand-weighted
  formula (V-R) measures **62.26% (1643/2639)** walk-forward — 3 extra correct picks
  versus champion `.01`'s 62.14% (1640/2639), a +0.12pp difference that is pure noise
  (95% CI [60.39%, 64.09%] contains 62.14% comfortably). It trails EPA-V1's 63.21%.
  Worse, it **degrades on the genuine holdout**: 63.67% on 2016–2020 → **60.93%**
  on 2021–2025. There is no evidence here to change anything about the champion.
- **Against the spread: NO EDGE — decisively.** Model side vs the closing line:
  **48.91%** full sample (1259–1315), **48.79% on holdout**, ROI **−6.85% at −110**.
  Below 50%, far below the 52.38% break-even bar, on every split. This formula as
  specified is not a spread-beater.
- **Hand weights vs fitted weights:** V-F (same 23 inputs, logistic-fit weights)
  scores 61.96% SU / 50.04% ATS — statistically indistinguishable from V-R's
  62.26% / 48.91%. Richard's hand-set weights are **not worse** than data-fit
  weights; neither clears any bar.

Richard's instinct — that hard factual team stats should drive the ratings — is
sound: the formula is a legitimate ~62% winner-picker, built exactly as he wrote
it. But ten seasons of evidence say it adds nothing over the Elo champion, and
nothing at all against the line.

## What was tested

The formula was implemented **verbatim** from Richard's spec:

- **OffPower** = 1.10 × (0.18·OffEPA + 0.12·PassEPA + 0.06·RushEPA + 0.10·OffSuccess%
  + 0.08·OffExplosive% + 0.12·PPG + 0.06·YPG + 0.10·RedZoneTD% + 0.08·ThirdDown%
  + 0.04·TOP). QBScore (0.06 weight) has no 2016–2025 historical feed → contributes
  exactly 0 (see limitation §3).
- **DefPower** = 1.10 × (0.18·DefEPA + 0.12·DefPassEPA + 0.06·DefRushEPA
  + 0.10·DefSuccess% + 0.08·ExplosiveAllowed% + 0.12·PAPG + 0.06·YAPG
  + 0.10·RedZoneDef% + 0.08·ThirdDownDef% + 0.04·Sack%). Front7Health (0.06) → 0.
- **SituationalScore** = 0.22·TurnoverDifferential + 0.10·PenaltyDifferential
  + 0.16·HomeFieldAdvantage + 0.10·RestDifferential − 50. Weather, MarketMovement,
  OffensiveLine, SkillPositionHealth (0.42 combined weight) → 0.
- **Overall** = 0.50·OffPower + 0.50·DefPower + 0.12·SituationalScore.
- **Pick:** higher Overall wins. PowerDifference = Overall_home − Overall_away.

Every metric was derived from nflverse play-by-play (2014–2025 parquet, REG only):
EPA splits / success / explosive (≥20-yd pass, ≥10-yd run) from real offensive plays
(pass+run, no kneels/spikes, EPA present); PPG/YPG/PAPG/YAPG from game scores and
gained yards; red-zone TD% on drives reaching the 20; third-down conversion on
3rd-down pass/run plays; TOP from `drive_time_of_possession`; sack% = sacks/QB
dropbacks; turnover differential = takeaways − giveaways per game; penalty
differential = net penalty yards per game; rest = capped-14 days since previous game.

## A structural finding about the formula (not a bug — documented as specified)

The spec's preferred normalization is the z-score, and the task required it, so
every metric entered as a direction-corrected z-score (higher z = better; PA/G,
YA/G, explosive-allowed, EPA-allowed, success-allowed, RZ-TD%-allowed,
3rd-down%-allowed, net penalties inverted). Under z-scoring, three of the
formula's headline constants are **inert for picks**:

- The **1.10** multipliers are pure scale — absorbed by the probability mapping,
  irrelevant to rankings.
- The **−50** situational centering is a constant added to both teams — it
  cancels exactly in PowerDifference.
- The **0.12** situational weight and **0.16** HFA weight survive only as small
  constants/variation (~5–10% of total PowerDifference variance).

What the test genuinely evaluated — the substance — is Richard's **relative
weighting of the metrics** (0.18 EPA vs 0.04 TOP, the 50/50 offense/defense
split, turnover differential at 0.22, etc.). That is the part that matters, and
it is what the numbers below judge. Had the 0–100 min-max normalization been
used instead, the constants would bite differently, but rankings would be
similar; z-score was followed per the task spec.

## Walk-forward protocol (same discipline as the EPA study)

- Predict season S (2016–2025) training on seasons < S only. 2014–2015 = burn-in
  and training data, never evaluated.
- STRICT as-of: every rolling metric uses only games with game_date strictly
  before the predicted game (season-to-date; prior full season as fallback for
  early-season games). League mean/std for z-scoring use per-team season
  aggregates from seasons < S only.
- Holdout declared in advance: train 2016–2020, holdout 2021–2025, evaluated once.
  No tuning of any kind was performed — weights are Richard's (V-R) or a single
  per-fold logistic fit (V-F, C=1.0, fixed a priori).
- Win-probability mapping: p(home) = expit(b·PowerDiff), one parameter b fit per
  fold by MLE (V-R); standard logistic on 23 home−away diffs (V-F). Projected
  margin = slope·linear-score, through-origin OLS per fold, for ATS grading.
- 10 ties → counted as misses (champion convention). 65 ATS pushes excluded.

## Leakage audit — PASS

- As-of audit over all **2,639 evaluated games: 0 violations** — every game's
  `data_through` (latest contributing kickoff) is strictly before its kickoff.
- Rolling construction is shifted-cumsum by design (prior games only); fallback
  uses the prior full season, never the current one.

## Results — straight-up (bars: champion 62.14%, EPA-V1 63.21%)

| Variant | Full 2016–25 | 95% CI | Train 2016–20 | Holdout 2021–25 | Brier |
|---|---|---|---|---|---|
| **V-R (Richard's weights)** | **62.26%** (1643/2639) | [60.39%, 64.09%] | 63.67% (815/1280) | **60.93%** (828/1359) | 0.2351 |
| V-F (fitted weights) | 61.96% (1635/2639) | [60.09%, 63.79%] | 62.50% (800/1280) | 61.44% (835/1359) | 0.2320 |
| Champion MNB-NFL-2026.01 | 62.14% (1640/2639) | — | — | — | (0.2344*) |
| EPA-V1 (research) | 63.21% (1668/2639) | — | 63.12% | 63.28% | 0.2237 |

\* Elo-baseline Brier from 2026-09-09 research; .01's own Brier is not in the verified record.

Per-season V-R: 2016 57.8% · 2017 64.5% · 2018 64.1% · 2019 65.2% · 2020 66.8% ·
2021 58.8% · 2022 58.3% · 2023 58.5% · 2024 65.8% · 2025 63.2%.
Per-season V-F: 2016 60.5% · 2017 65.2% · 2018 62.1% · 2019 60.2% · 2020 64.5% ·
2021 59.6% · 2022 60.9% · 2023 60.3% · 2024 65.8% · 2025 60.7%.

V-R and V-F agree on 75.4% of picks. The +0.12pp V-R edge over the champion is
three games in ten seasons — noise. The train→holdout decay (63.67% → 60.93%)
is the wrong direction for a promotion case.

**Calibration (V-R, full sample, stated → actual by decile):**
0.313→0.367 · 0.394→0.352 · 0.432→0.485 · 0.463→0.439 · 0.491→0.492 ·
0.515→0.555 · 0.543→0.663 · 0.574→0.659 · 0.612→0.686 · 0.693→0.746.
Roughly diagonal but **underconfident in the upper-middle deciles** — the
single-parameter logistic compresses PowerDifference too much. Calibration is
acceptable, not sharp.

## Results — ATS (bar: 52.38% on holdout; pushes excluded; ROI at −110)

Model side vs closing `spread_line` (home −line iff pred_margin > line, else away
+line; nflverse closing consensus, 100% coverage):

| Split | V-R ATS% | V-R W–L | V-R ROI | V-F ATS% | V-F W–L | V-F ROI |
|---|---|---|---|---|---|---|
| Full 2016–25 | **48.91%** | 1259–1315 | −6.62% | 50.04% | 1288–1286 | −4.47% |
| Train 2016–20 | 49.04% | 612–636 | −6.38% | 50.56% | 631–617 | −3.47% |
| **Holdout 2021–25** | **48.79%** | 647–679 | **−6.85%** | 49.55% | 657–669 | −5.41% |

65 pushes excluded throughout. V-R is below 50% on all three splits — its
margin projections (single slope on PowerDifference) are systematically worse
than the line at pricing favorites. **No ATS edge. Nothing spread-facing changes.**

## Limitations (read before citing any number above)

1. **Six of 30 inputs were neutral in this backtest** — QBScore, Front7Health,
   OffensiveLine, SkillPositionHealth, Weather, MarketMovement have no reliable
   2016–2025 historical feed, so their contributions were exactly 0. The test
   evaluates the formula **minus those six inputs**. Do not describe these
   results as testing the "complete" formula.
2. Normalization choice (z-score, per task spec) renders the 1.10 multipliers and
   −50 centering inert for picks (documented above). A 0–100 min-max build would
   be a different implementation, not tested here.
3. `spread_line` is nflverse's closing consensus, not a specific book's number.
4. No neutral-site handling; London/international games carry full HFA.
5. 2014–2015 training rows use 2014–2015-combined normalization params (training
   only, never evaluated; evaluation folds are strictly prior-season).
6. Playoff games excluded throughout (REG only). 2020 COVID season included.
7. Margin mapping is a single through-origin slope — deliberately minimal.

## Recommendation

1. **Do not change the champion.** V-R matches .01 on winners (62.26% vs 62.14%,
   noise) and is decisively worse against the spread. There is no promotion case.
2. **Track 2 stays in research.** The ATS result (48.79% holdout) is a clean
   negative; nothing about Best Bets, public claims, or product changes.
3. If Richard wants to pursue the formula further, the only untested part is the
   six neutral inputs — QB quality, line play, health, weather. Those need real
   historical feeds (injury reports, weather archives) that do not currently
   exist in the pipeline. Note the irony the data already hints at: the parts of
   the formula most likely to be priced into the line (market movement, QB news)
   are exactly the parts that couldn't be backtested.

## Artifacts

- Runner (reproducible): [richard_formula_run.py](sandbox://workspace/money-by-numbers/research/richard_formula_run.py)
- Per-game predictions: `~/workspace/epa_pbp/richard_formula_predictions.csv`
  (2,639 rows: PowerDiff, p_vr, p_vf, pred margins, actuals, spread_line, data_through)
- Summary JSON: `~/workspace/epa_pbp/richard_formula_summary.json`
- This report: [richard_formula_backtest_2016_2025.md](sandbox://workspace/money-by-numbers/research/richard_formula_backtest_2016_2025.md)
