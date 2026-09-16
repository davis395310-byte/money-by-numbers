# Playoff Simulator — Build & Research Report

**MONEY BY NUMBERS · NFL Intelligence. Powered by Numbers.**
**Date:** 2026-09-10 · **Phase:** 1 (playoff simulator + playoff-factor research)

---

## 1. What was built

A real, pure-function Monte Carlo NFL playoff simulator (no stubs, no `random.random()` placeholders):

| File | Purpose |
|---|---|
| `backend/app/playoffs/bracket.py` | Playoff formats (12-team 2016–2019; 14-team 2020+), Wild Card / Divisional (re-seeded) / Championship / neutral-site Super Bowl construction, NFL tiebreak pipeline |
| `backend/app/playoffs/engine.py` | Monte Carlo engine: `simulate_playoff_bracket` (explicit seeds) and `simulate_remaining_season` (explicit remaining games + standings) |
| `backend/app/routers/playoffs.py` | Explicit-input API: `GET /api/playoffs/formats`, `POST /api/playoffs/simulate-bracket`, `GET /api/playoffs/retrospective` |
| `tests/test_playoffs.py` | 46 tests |

**Independent inspection verdict (2026-09-10):** code read in full. Test results reproduced: **46/46 playoff tests pass; full suite 421 passed, 1 skipped.** The engine is honest: missing ratings raise errors (never invented), unresolved tiebreaks are reported not silently decided, simulation cap 50,000, no write routes. The retrospective endpoint returns honest DATA UNAVAILABLE until the artifact exists — the artifact now exists (see §4).

**Fixed 2026-09-10:** `bracket.apply_tiebreakers` now awards wild-card
spots one at a time, re-applying the procedure to the remaining clubs with
the winner's division advanced to its next club — the official multi-club
semantics. The 2020 AFC pattern is pinned by a regression test
(`test_multi_spot_wild_card_tie_reapplies_to_remaining_clubs`): BAL #5,
CLE #6, IND #7. The engine's remaining-season seeding path delegates to
the same function, so it is fixed as well. Retrospective unaffected (it
uses verified actual seeds, never the tiebreaker).

**Configurable adjustments** default to pure Elo: `hfa_elo=35`; `bye_rest_elo`, `qb_playoff_exp_elo_per_start`, `momentum_elo` all `0.0` — placeholders pending the research below, which found **no factor earning inclusion**.

---

## 2. Playoff-factor research — verdicts

Dataset: **122 actual postseason games, 2016–2025** (11/year 2016–2019, 13/year 2020–2025), all with final scores, QB names, rest data; 87/122 with weather. End-of-regular-season Elo frozen before playoffs (spec: R0=1500, K=20, scale=400, HFA=35, margin-of-victory multiplier; playoff results never applied). Elo-only playoff winner accuracy: **60.7%**; non-neutral home win rate **65.2%** (112 games).

Method per factor: single-factor logistic regression controlling for Elo-implied logit. Evidence bar set **before** reading results: **INCLUDE** requires |z| ≥ 2.5 **and** same sign in 2016–2020 and 2021–2025; **WEAK** requires |z| ≥ 1.5; otherwise **NO EVIDENCE**. Small-sample honesty: n=122 is thin; confidence limits apply to every line below.

| Factor | n | z | Split-half sign | Verdict |
|---|---|---|---|---|
| Rest differential / bye effect | 122 | +1.30 | stable + | **NO EVIDENCE** (div-round bye teams: 28–12 vs 26.1 Elo-implied) |
| QB playoff-start experience differential | 122 | +0.34 | unstable | **NO EVIDENCE** |
| Regular-season rematch | 122 | +1.34 | unstable | **NO EVIDENCE** (68 rematches; home won 47 vs 42.5 Elo-implied) |
| "Revenge" (reg-season loser hosts playoff game) | 68 | −0.84 | — | **NO EVIDENCE** |
| Late-season momentum (last-4 win% differential) | 122 | **+2.17** | stable + | **WEAK** — below the INCLUDE bar; stays excluded |
| Cold game (≤38°F) | 122 | +1.03 | stable + | **NO EVIDENCE** (48 cold games; home won 34 vs 31.4 Elo-implied) |
| Playoff home-field uplift (MLE ≈60 vs 35) | 122 | LR 0.49 (< 3.84) | — | **NO EVIDENCE** for changing HFA |
| **Turnover margin** (reg-season takeaways−giveaways/game, from nflverse play-by-play 2016–2025, 320 team-seasons) | 122 | −0.25 | unstable (+0.21 / −0.46) | **NO EVIDENCE** |

**Bottom line: no playoff-specific factor has earned inclusion.** Momentum is the only WEAK signal and remains unwired pending stronger validation. The simulator runs pure Elo (+ standard 35 HFA) by default, and that is an evidence-based choice, not a placeholder.

---

## 3. Verified historical seeds (2016–2025)

The naive standings-plus-tiebreak reconstruction produced 4 known errors (2020 AFC, 2025 NFC). Seeds were instead derived from **actual Wild Card pairings as ground truth**: WC hosts are division winners, bye teams = Divisional participants minus WC winners, and each WC away team's seed is forced by its host's seed (2v7, 3v6, 4v5). Thirteen record-ties in division-winner ordering were resolved with real tiebreak steps computed from game data (head-to-head, conference record, common games — e.g., 2017 NFC: PHI over MIN on common games 5-0 vs 4-1; 2020 AFC: BAL 5 / CLE 6 / IND 7 via the per-spot division set-aside + head-to-head).

**Validation: 0 mismatches across all 52 Wild Card games**; Divisional = byes + WC winners; Conference Championships = Divisional winners; Super Bowl winners reconcile. File: `nfl-model-research/playoff_research/seeds_verified.json` (supersedes the earlier `seeds_2016_2025.json`, which is retained but marked unreliable).

---

## 4. Retrospective artifact

**`ml/artifacts/playoff_retrospective_2016_2025.json`** — 10 seasons, each with format, verified AFC/NFC seeds, end-of-season Elo, per-team `p_win_wc` (`null` for bye teams), `p_win_div`, `p_win_conf`, `p_win_sb`, `n_sims` (50,000), model version (`MNB-SIM-1.0/elo-K20-HFA35-MOV-frozen-eoy`), actual WC/DIV/CON/SB results, rng seeds, and method notes. Leakage-safe: no playoff outcome was used as input; ratings frozen at end of regular season.

### Sanity metrics (n=10 — stated beside every champion-level claim)

| Season | Champion | Model p_win_sb | Rank in field | Favorite won? |
|---|---|---|---|---|
| 2016 | NE | 0.510 | 1/12 | yes (NE) |
| 2017 | PHI | 0.105 | 3/12 | no (NE) |
| 2018 | NE | 0.373 | 1/12 | yes (NE) |
| 2019 | KC | 0.164 | 3/12 | no (BAL) |
| 2020 | TB | 0.020 | 9/14 | no (KC) |
| 2021 | LAR | 0.068 | 6/14 | no (GB) |
| 2022 | KC | 0.373 | 1/14 | yes (KC) |
| 2023 | KC | 0.144 | 4/14 | no (BAL) |
| 2024 | PHI | 0.091 | 5/14 | no (KC) |
| 2025 | SEA | 0.183 | 2/14 | no (BUF) |

- **Champion's simulated probability:** mean 0.203, median 0.154 (n=10) vs base rate ≈ 0.071 — the model rates actual champions ~2–3× base rate on average, but single-season probabilities are descriptive, not calibration-validated at n=10.
- **Champion rank:** mean 3.5, median 3.0 (n=10) — the eventual champion was typically a top-3–4 team by model probability. The 2020 Buccaneers (rank 9, p=0.020) are the genuine long-shot exception.
- **Favorite won the Super Bowl:** 3/10 (n=10).
- **Top-4 hit rate:** 22/40 = 55% of actual conference-championship participants were in the model's top-4 by Super Bowl probability.

Honest reading: the simulator's probabilities are sane and roughly ordered, but n=10 champions cannot validate calibration, and favorites lose most years — as they do in reality.

---

## 5. Production & integrity notes

- **Nothing touched:** production champion flag (`MNB-NFL-2026.01`), locked Week 1 picks, live ledger, and all historical results are unmodified. The retrospective used a separate research Elo spec, clearly labeled.
- **No fabrication:** every number above traces to `playoff_games_2016_2025.csv` (122 real games), `games.parquet`, nflverse play-by-play, or the Monte Carlo runs. Unresolved items are labeled, not guessed.
- **No leakage:** H2H windows, Elo ratings, turnover margins, and seeds use only data available at prediction time (end of regular season).

## 6. Limitations & next steps

1. ~~Fix `apply_tiebreakers` multi-spot wild-card semantics (Cleveland-2020 case) before live use.~~ **DONE 2026-09-10** — one-at-a-time spot allocation with re-application; regression test pins BAL #5 / CLE #6 / IND #7.
2. Momentum (z=2.17, WEAK) is the only candidate worth re-testing with more seasons or a sharper definition; everything else tested-and-excluded.
3. n=10 seasons is the fundamental limit on champion-level validation — the retrospective should be re-run as new postseasons complete.
4. The retrospective Elo spec (K=20) differs from the tuned research config (K=32, 8-season window, 64.27%); if the factor model is ever promoted, the retrospective should be re-run under it for an apples-to-apples read.
