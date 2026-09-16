# Matchup-History / Rivalry Research (Phase 3)

Date: 2026-09-10. Research: `~/workspace/nfl-model-research/rivalry/`
(`rivalry_research.py`, `rivalry_results.json`, `rivalry_games.parquet`).
No production code changed — nothing cleared the bar for wiring.

## Protocol

- n = 2,639 regular-season games, 2016–2025, walk-forward.
- Elo: exact MNB-NFL-2026.01 replication (K=20, HFA=35, MOV multiplier),
  1999–2015 burn-in. Every feature strictly trailing — H2H records, streaks,
  and rematch flags use only games already played.
- Division games flagged from nflverse `div_game` (36.4% of games).
- Predeclared: **INCLUDE** = |z| ≥ 2.5 with stable sign in 2016–2020 /
  2021–2025 halves; **WEAK** = |z| ≥ 1.5; else **NO EVIDENCE**.
- Incremental value tested two ways: (a) bucket actual-vs-Elo-implied with
  split halves, and (b) multivariable logistic regressions that include
  `logit(p)` so coefficients are *beyond Elo* by construction. A quadratic
  miscalibration control was run wherever a WEAK signal appeared.

## Results

| Test | n | z (incremental) | Verdict |
|---|---|---|---|
| T1b underdog overperformance, division games (`fav_win ~ logit(p_fav) + is_div`) | 960 div | −0.76 | NO EVIDENCE |
| T2 HFA attenuation, division games (`home_win ~ logit(p_home) + is_div`) | 2,639 | −0.13 | NO EVIDENCE |
| T3b H2H streak ≥3 (`fav_win ~ logit(p_fav) + streak_fav`) | 711 | −2.13 | WEAK → excluded |
| T4 trailing last-5 H2H edge (`home_win ~ logit(p_home) + h2h_edge`) | 2,639 | −1.71 | WEAK → excluded |
| T5 same-season rematch, first-game loser in game 2 | 480 | +0.06 | NO EVIDENCE |
| T6 rematch split: first-loser home (n=253, z=+0.21) / away (n=227, z=−0.14) | 480 | — | NO EVIDENCE |
| T7 margin compression, division games (`\|mov\| ~ \|elo_diff\| + is_div`, OLS) | 2,639 | −0.71 | NO EVIDENCE |

Raw (non-incremental) numbers for the record: division favorites went
0.620 actual vs 0.689 Elo-implied (n=960, z=−4.43) — but non-division
favorites show the same pattern (0.620 vs 0.669, n=1679, z=−4.13). The gap
is the global Elo overconfidence already addressed by the weekly
calibration refit (see IN_SEASON_RECALIBRATION.md §4), not a division
effect. Streak ≥3 teams: 0.572 vs 0.657 (n=711, z=−4.55) — again mostly
the same overconfidence once controlled.

## The two WEAK signals (tested-and-excluded)

Both point the *opposite* way from rivalry narratives — teams with recent
H2H dominance slightly **underperform** Elo, consistent with plain
mean-reversion, not with "owning" an opponent:

- **T3b**: streak ≥3 coefficient −0.182, z=−2.13; robust to quadratic
  miscalibration control (z=−2.14); halves −1.33 / −1.69 (stable sign).
- **T4**: last-5 H2H edge coefficient −0.296, z=−1.71; with quad control
  z=−1.78; halves −1.57 / −0.90 (stable sign). (An early run mixed the
  favorite frame into the home-frame regression and printed a spurious
  +4.06 — caught and corrected; the corrected value is reported here.)

Neither clears the pre-set INCLUDE bar (|z| ≥ 2.5). Per protocol — the same
standard that excluded the WEAK momentum factor in Phase 1 — they are
documented as tested-and-excluded, not wired. No post-hoc mechanism was
promoted to justify them.

## The rematch narrative is dead null

Same-season rematch (all 480 are division second-meetings): the first-game
loser won game 2 at 0.417 vs 0.415 Elo-implied (z=+0.06), halves −0.022 /
+0.025. Home/away splits are equally null. There is no measurable
"loser bounces back" effect.

## Not tested (stated, not hidden)

- Venue-specific effects: per-stadium samples are too thin to separate from
  team strength; no venue factor was attempted.
- Playoff rematches: n=122 postseason games cannot support the splits;
  covered qualitatively in PLAYOFF_SIM_REPORT.md (rematch: NO EVIDENCE).

## Production decision

**No rivalry adjustment wired.** The `"rivalry-adjustment"` tag is reserved
in the tagging convention (cf. `"situational-adjustment"`) for any future
factor that clears the bar; nothing currently carries it. If a future
re-measurement promotes a matchup factor, it must pass the same |z| ≥ 2.5 +
stable-halves gate and travel with the tag — never silent.
