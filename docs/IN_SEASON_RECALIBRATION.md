# In-Season Recalibration & Situational Motivation (Phase 2)

Date: 2026-09-10. Research: `~/workspace/nfl-model-research/situational/`.
Production code: `backend/app/situational/`, `pipelines/jobs/recalibrate.py`.
Tests: `tests/test_situational.py` (24 tests).

## 1. Baseline truth: Elo was never static

Elo already updates team strength after every game — the walk-forward
research re-fit nothing mid-season, but the live model moves its ratings
continuously. What was missing:

1. a **situational/motivation layer** (Elo rates full-strength teams; it
   cannot see rest-starters or must-win urgency), and
2. **scheduled formal recalibration** (weekly calibration re-fit, periodic
   factor review with guardrails).

This document covers both. Nothing here touches the champion
`MNB-NFL-2026.01`, the locked Week 1 picks, or the live ledger.

## 2. Stakes classifier

`backend/app/situational/stakes.py` — pure functions, no future data.

Inputs per game: both teams' records (wins/losses/ties, conference) as they
stand *before* kickoff, `season_games` (16 for seasons ≤ 2020, 17 after),
`week`, and `playoff_slots` (7 for 2020+, 6 for 2016–2019).

Clinch/elimination math (conservative sufficient conditions on points,
`pts = wins + 0.5·ties`):

- `max_pts(T) = pts(T) + remaining(T)` — win out; `min_pts(T) = pts(T)`.
- **ELIMINATED**: ≥ `playoff_slots` conference mates already have `pts`
  strictly above `max_pts(T)` — seven teams uncatchable. Sufficient; truly dead.
- **Clinched** (internal): at most `slots − 1` mates have `max_pts` strictly
  above `pts(T)` — at most six teams can finish ahead. Strict `>` means exact
  ties never clinch (tiebreak risk) — conservative by design.
- **DEAD_RUBBER**: seed locked — no tied win% with anyone, every team ranked
  above has `pts > max_pts(T)` (cannot be caught), every team below has
  `max_pts < pts(T)` (cannot catch T). Berth *and* seed fixed: the classic
  rest-starters risk.
- **MUST_WIN**: win-and-in (a win clinches an unclinched berth), lose-and-out
  (a loss eliminates), or week ≥ 15 seeding battle (within 1.0 pts of an
  adjacent seed while unlocked). Hypotheticals perturb only the current game.
- **NORMAL**: everything else.

Priority: ELIMINATED > DEAD_RUBBER > MUST_WIN > NORMAL.

**Documented limitations.** NFL tiebreaks (head-to-head, conference record,
…) are ignored; the strict inequalities keep every label conservative —
fewer non-NORMAL labels, all of them truly earned. A team dead only on
tiebreak subtleties reads NORMAL. Starter-rest status is NOT inferred from
stakes: the adjustment below discounts the team's *rating*, it never claims
to know who sits.

## 3. Measurement: 2016–2025 walk-forward, no leakage

n = 2,639 regular-season games. Elo probabilities are the frozen
walk-forward values (reproduced exactly: favorite accuracy 0.6218 matches
the research Elo to the digit — no implementation bug).

| Bucket | n | actual fav win | Elo-implied | diff | z | verdict |
|---|---|---|---|---|---|---|
| Fav DEAD_RUBBER | 8 | 0.250 | 0.713 | −0.463 | −2.90 | **INCLUDE (shrunk)** |
| Fav ELIMINATED | 85 | 0.506 | 0.633 | −0.127 | −2.43 | excluded (see below) |
| Fav MUST_WIN | 498 | 0.667 | 0.687 | −0.021 | −0.99 | NO EVIDENCE |
| Dog MUST_WIN | 373 | 0.601 | 0.654 | −0.054 | −2.17 | NO EVIDENCE |
| Dog ELIMINATED | 247 | 0.700 | 0.725 | −0.025 | −0.88 | NO EVIDENCE |
| Both NORMAL | 1,988 | 0.614 | 0.676 | — | — | baseline |

Split-half (2016–2020 / 2021–2025) sign stability: DEAD_RUBBER negative in
both halves (−0.179 / −0.748).

### Verdicts

- **DEAD_RUBBER → INCLUDE, heavily shrunk.** Passes the pre-set bar
  (|z| ≥ 2.5, stable sign). The 8 games are independently legible — 6 of 8
  are famous rest-starters games where the favorite demonstrably sat its
  starting QB (2016 W17 DAL, 2020 W17 KC, 2023 W18 BAL, 2024 W18 BUF,
  2024 W18 KC), and the favorites went 2–6 vs 5.7 expected. Mechanism: Elo
  rates the full-strength team; backups play instead. n = 8 is stated
  everywhere the number appears.
- **ELIMINATED favorite (n=85, z=−2.43) → EXCLUDED.** The raw gap does not
  survive the baseline correction: the model is globally overconfident
  (see §4), and the eliminated-favorite excess over baseline is −0.065,
  z ≈ −1.2 — not significant. Eliminated teams play to roughly Elo
  expectation. There is **no measurable quit/tank effect**.
- **MUST_WIN (either side) → EXCLUDED.** No urgency effect: favorites
  z = −0.99; underdog-must-win z = −2.17 but the bucket performs *better*
  than baseline, and split halves are unstable (+0.004 / −0.095).
- **Dog ELIMINATED → NO EVIDENCE** (z = −0.88): favorites do not gain
  beyond Elo against eliminated underdogs.

## 4. Calibration finding → weekly refit

The NORMAL baseline exposes genuine miscalibration: mean implied 0.6775 vs
actual 0.6218 — the Elo is systematically overconfident. Category effects
were therefore evaluated against this baseline, not from raw gaps.

Response: `pipelines/jobs/recalibrate.py::RecalibrateJob`, scheduled every
**Wednesday** (`recalibrate`, `weekly_days: [2]`, 14:00 UTC, in-season
only). It fits Platt scaling `p_cal = expit(a + b·logit(p_raw))` on the
season's settled ledger picks (walk-forward: past games only) and upserts
`(a, b, n, refit_count)` into the `calibration` collection. The prediction
path applies the stored params. Fewer than 50 settled picks → honest
`SkipJob` (no fit on noise).

## 5. Four-week factor review with guardrails

Every 4th successful refit, the job runs the deeper review:

- Candidate (a, b) fit on all-but-the-last-4-weeks; incumbent = current
  stored (a, b); held-out = last 4 weeks of settled picks.
- **Adopted only if the candidate's held-out Brier beats the incumbent's
  by ≥ 0.002.** Otherwise REJECTED — and on review weeks the incumbent
  stands (the weekly fit is recorded but not adopted).
- Every verdict — accepted, rejected, or no-held-out-data — is recorded in
  `factor_reviews`. Nothing is silent.
- The dead-rubber discount is re-measured and reported on review weeks;
  it changes only with ≥ 8 new tagged games *and* a guardrail pass —
  in practice "unchanged (insufficient new data)".
- **The job never promotes a model.** It snapshots the champion before and
  after and fails loudly if the champion document changed.

## 6. The wired adjustment

`backend/app/situational/adjustment.py` — exactly one adjustment:

- `DEAD_RUBBER_DISCOUNT_ELO = 50.0` — raw MLE ≈ 349 Elo points from the
  n=8 measurement (logit gap 2.01 × 400/ln 10), shrunk with a skeptical
  prior of weight 48 (6× the data mass) at zero: `349 × 8/(8+48) = 50.0`.
- Applied to the DEAD_RUBBER team's Elo *before* the win probability is
  computed; zero shifts reproduce the standard Elo logistic exactly.
- **Every adjusted pick is tagged `"situational-adjustment"`** — the tag,
  team, stakes, Elo shift, and basis travel on the prediction document
  (`adjustments` / `adjustment_detail`) and into API responses via the
  predictions projection. Never silent.
- Master switch `ADJUSTMENT_ENABLED`; all other stakes values shift exactly
  0.0 (tested and excluded — asserted in the test suite).

## 7. What was NOT wired (tested-and-excluded)

- Must-win urgency adjustments (both sides) — no evidence.
- Eliminated-team quit/tank adjustments — no evidence vs baseline.
- Playoff HFA changes, rest/bye, QB experience, rematch, momentum, cold,
  turnover-margin playoff factors — no evidence (see PLAYOFF_SIM_REPORT.md).
- Starter-rest inference — unavailable unless sourced; never inferred.

## 8. Limitations

- Dead-rubber: n = 8. The shrinkage is honest but the true effect remains
  uncertain; the 4-week re-measurement owns future updates.
- Tiebreak-ignorant classifier is conservative; some true dead-rubber /
  eliminated teams read NORMAL (shrinks those samples, never inflates).
- Weekly calibration is fit in-season on the season's own picks —
  walk-forward legitimate (past only), but early-season fits are noisy;
  the 50-pick gate and the 4-week guardrail are the defenses.
