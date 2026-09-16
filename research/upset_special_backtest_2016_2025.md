# Weekly Upset Special — 2016–2025 Walk-Forward Backtest

**Status:** RESEARCH ONLY. Did not touch the champion registry (MNB-NFL-2026.01),
locked 2026 picks, or any existing ledger. SU-only study; no ATS, no ROI.

**Runner:** `research/upset_special_run.py`
**Picks list:** `research/upset_special_picks.csv` (172 rows, one per special)
**Date run:** 2026-09-14

## Exact rule tested

For each regular-season week (2016–2025):

1. Consider the games where the **champion model** (MNB-NFL-2026.01, pure Elo
   family) picks the team that is the **underdog by the closing line**.
2. The week's **Upset Special** is the ONE such pick with the **highest model
   win probability for the picked team**.
3. If a week has no underdog pick, there is no Upset Special that week.
   (Deterministic tie-break for equal probabilities: earliest kickoff, then
   game_id. No exact ties occurred in the sample.)

## Model pipeline (verified champion replication)

- `games = load_games(range(1999, 2026), game_types=("REG",))`
- `frame = build_feature_frame(games)` — point-in-time Elo walk-forward
  (ratings start 1500, margin-aware 538-style zero-sum updates, K=20,
  scale 400, 35-Elo home-field expectation adjustment; no future information)
- Per 2016–2025 game: `p_home = elo_win_probability(elo_diff)`; **pick home
  iff `p_home >= 0.5`** (exactly the audited harness grading rule)
- Model win probability for the picked team = `p_home` if home picked,
  else `1 - p_home`
- Actual winner from `home_score`/`away_score`; `home_win = home_score >
  away_score`; correct iff `(pick_home == home_win)` — ties grade exactly
  like the audited harness (a tie counts as a miss for a home pick, and as a
  hit for an away pick, since `home_win` is False)

**Replication sanity gate:** the runner first reproduces the verified
champion record before applying the rule. Result: **1640/2639 = 62.14%**,
per-season correct counts 154, 167, 162, 161, 160, 172, 160, 157, 180, 167 —
**PASS, exact match** to the audited champion backtest. The rule was then
applied to this identical 2,639-game sample. No leakage: the feature frame is
point-in-time by construction, and closing lines are pre-kickoff information.

## Spread-line sign convention (verified empirically)

`spread_line` is the nflverse closing consensus, home-team perspective:

| Convention | Meaning |
|---|---|
| `spread_line > 0` | HOME team favored (giving points) |
| `spread_line < 0` | HOME team underdog (receiving points) |
| `spread_line == 0` | Pick'em — no underdog |

Verified against three known games (not taken on documentation alone):

- **2024-09-05 BAL @ KC** — KC home, favored by ~3 → `spread_line = +3.0` ✓
- **2024-11-24 KC @ CAR** — CAR home, KC road favorite by ~11 → `spread_line = -11.0` ✓
- **2016-09-11 TB @ ATL** — ATL home, favored by 2.5 → `spread_line = +2.5` ✓

**Underdog pick** = the model picked the team receiving points:
`(pick_home AND spread_line < 0) OR (not pick_home AND spread_line > 0)`.
Pick'ems (4 games) are excluded — neither team is an underdog. No null
`spread_line` values exist in the 2,639-game sample.

## Results

### Headline

| Metric | Value |
|---|---|
| Upset Specials selected | **172 of 175** regular-season weeks (3 weeks had no underdog pick) |
| SU record | **67–105** |
| Hit rate | **38.95%** |
| 95% Wilson CI | **[31.98%, 46.41%]** |
| Avg model win probability on these picks | **69.88%** (min 50.3%, max 89.7%) |
| Calibration gap | **−30.9pp** (model said ~70%, hit ~39%) |
| Ties among specials | 0 |

Note on week count: the sample contains **175** distinct regular-season weeks
(17 weeks × 5 seasons 2016–2020, plus 18 weeks × 5 seasons 2021–2025), not 180.
172/175 = 98.3% of weeks produced a special.

### Per-season breakdown

| Season | Specials | Record | Hit rate | Avg model prob |
|---|---|---|---|---|
| 2016 | 17 | 4–13 | 23.5% | 69.8% |
| 2017 | 17 | 8–9 | 47.1% | 73.0% |
| 2018 | 16 | 5–11 | 31.2% | 67.8% |
| 2019 | 17 | 6–11 | 35.3% | 70.6% |
| 2020 | 17 | 5–12 | 29.4% | 68.6% |
| 2021 | 18 | 7–11 | 38.9% | 69.0% |
| 2022 | 18 | 10–8 | 55.6% | 70.8% |
| 2023 | 18 | 6–12 | 33.3% | 70.2% |
| 2024 | 18 | 10–8 | 55.6% | 72.3% |
| 2025 | 16 | 6–10 | 37.5% | 66.2% |

Only 2 of 10 seasons finished above .500; 8 of 10 were losing seasons.

### Calibration of the specials

| Model prob bucket | n | Observed hit rate | Avg stated prob |
|---|---|---|---|
| 50–55% | 7 | 28.6% | 53.3% |
| 55–60% | 13 | 15.4% | 57.6% |
| 60–65% | 32 | 43.8% | 62.8% |
| 65–90% | 120 | 40.8% | 74.1% |

Severely miscalibrated in every bucket: the model's confidence numbers on
market underdogs are not to be trusted at face value. When the model says a
market underdog wins ~74% of the time, it actually wins ~41%.

### Context (same 2,639-game sample, same grading rule)

| Group | Record | Hit rate | 95% Wilson CI |
|---|---|---|---|
| ALL model underdog picks (637) | 259–378 | 40.66% | [36.91%, 44.52%] |
| ALL model favorite picks (1,998) | 1377–621 | 68.92% | — |
| Base rate: underdog wins outright (2,635 lined, non-pick'em games) | 880–1755 | 33.40% | [31.62%, 35.22%] |
| Base rate, strict outright (ties excluded from wins) | 876/2635 | 33.24% | — |

The weekly-"best" selection (38.95%) does **not** beat simply taking all of
the model's underdog picks (40.66%) — the CIs overlap almost entirely and the
point estimate is lower. The selection step adds nothing.

The model's underdog picks do beat the naive underdog base rate (40.7% vs
33.4%), which is expected: these are spots where a 62%-accurate model
disagrees with the market, so some real signal survives. But "better than
blindly betting underdogs" is a long way from a credible weekly feature.

## What was NOT measured

- **Moneyline ROI: DATA UNAVAILABLE.** There is no valid historical
  moneyline odds feed in this workspace, so no profit/loss, ROI, or
  units-won figure can be computed honestly. None is presented.
- No ATS evaluation was performed (SU only, per the research brief).
- No live or forward-looking claim is made; this is a historical
  description of 2016–2025.

## Verdict

**Not a credible weekly feature.** Ten seasons of walk-forward evidence say
the "Upset Special" — the model's most-confident market-underdog pick each
week — went **67–105 (38.95%)**, with the entire 95% confidence interval
**[32.0%, 46.4%] below 50%**. Eight of ten seasons lost. The weekly
cherry-pick underperforms even the model's full set of underdog picks
(40.7%), so the selection adds no value, and the model's stated confidence
(~70% average) overstates reality by ~31 points — publishing those
probabilities beside a 39%-hitting feature would actively mislead. The model
is genuinely good at picking favorites (68.9%); asking it to headline
underdogs each week plays to its weakest suit. Do not ship this as a product
feature on this evidence. If revisited, it would need a fundamentally
different selection rule with its own pre-registered walk-forward test —
not a tweak tuned on these results.
