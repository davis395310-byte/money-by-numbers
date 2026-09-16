# MONEY BY NUMBERS — 2016–2025 BACKTEST REPORT

Human-readable companion to `MNB_2016_2025_backtest_report.json`.
Model under audit: `MNB-NFL-2026.02` (stacked ensemble, challenger).
Champion/production model: `MNB-NFL-2026.01` (Elo-only).

---

## Protocol

Walk-forward, one fold per season 2016–2025: train on all final regular-season
games with season < Y (features warmed from 1999), predict season Y. n = 2,639
games. Leakage harness (`ml/leakage.py`) gates every fold; per-fold
max-train-kickoff < min-test-kickoff asserted. Calibration: isotonic regression
on the last 20% (chronological) of each training fold. Target: home_win ∈ {1,0}
(ties encoded 0); winner predicted home iff calibrated P ≥ 0.5. Closing lines
(nflverse, PFR source) used ONLY for ATS/O-U grading — never model inputs.

## Headline results

| Metric | Ensemble (2026.02) | Elo baseline (2026.01) |
|---|---|---|
| Winner accuracy (macro, as reported) | **59.47%** | **62.16%** |
| Winner accuracy (pooled, corrected) | **59.49%** (1570/2639) | **62.14%** (1640/2639) |
| Brier (macro) | 0.2371 | 0.2344 |
| Log loss (macro) | 0.7062 | — |
| Margin MAE (macro) | 10.57 | — |
| Total MAE (macro) | 11.39 | — |
| ATS accuracy (pooled) | 50.66% (1304/2574) | — |
| O/U accuracy (pooled) | 49.39% (1293/2618) | — |

**The ensemble did NOT beat the Elo baseline** (−2.69pp macro). No improvement
claim is made. ATS 50.66% is indistinguishable from a coin flip net of vig —
no market edge demonstrated.

**Aggregation note (audit finding):** the code comment says "pooled" but
accuracy/Brier/log-loss/MAE are macro-averages (mean of seasonal values); ATS
and O/U are genuinely pooled. Both figures are reported above; the deltas are
≤0.02pp and change nothing.

## Season-by-season

| Season | Games | Ens correct | Ens acc | Elo acc | ATS acc (n) |
|---|---|---|---|---|---|
| 2016 | 256 | 155 | 60.55% | 60.16% | 51.00% (251) |
| 2017 | 256 | 146 | 57.03% | 65.23% | 48.79% (248) |
| 2018 | 256 | 157 | 61.33% | 63.28% | 53.85% (247) |
| 2019 | 256 | 148 | 57.81% | 62.89% | 45.53% (246) |
| 2020 | 256 | 146 | 57.03% | 62.50% | 49.22% (256) |
| 2021 | 272 | 157 | 57.72% | 63.24% | 47.76% (268) |
| 2022 | 271 | 168 | 61.99% | 59.04% | 50.96% (261) |
| 2023 | 272 | 149 | 54.78% | 57.72% | 55.43% (258) |
| 2024 | 272 | 175 | 64.34% | 66.18% | 50.75% (268) |
| 2025 | 272 | 169 | 62.13% | 61.40% | 53.14% (271) |
| TOTAL | 2639 | 1570 | 59.49% pooled | 62.14% pooled | 50.66% (2574) |

Worst season: 2023 (54.78%). Best season: 2024 (64.34%). The ensemble beat Elo
in only 2 of 10 seasons (2022, 2025).

## Calibration

Pooled 10-bin reliability (predicted vs observed home-win rate):

| Bin | n | Avg predicted | Observed |
|---|---|---|---|
| 0.05 | 12 | 0.008 | 0.333 |
| 0.15 | 38 | 0.159 | 0.421 |
| 0.25 | 59 | 0.268 | 0.288 |
| 0.35 | 382 | 0.359 | 0.380 |
| 0.45 | 598 | 0.470 | 0.495 |
| 0.55 | 851 | 0.557 | 0.571 |
| 0.65 | 408 | 0.630 | 0.618 |
| 0.75 | 258 | 0.736 | 0.752 |
| 0.85 | 22 | 0.851 | 0.864 |
| 0.95 | 11 | 0.959 | 0.727 |

Calibration is good in the populated middle bins; the sparse tails (n ≤ 38)
show noise, as expected. Brier 0.2371 vs Elo 0.2344 — the ensemble is slightly
worse-calibrated than the baseline too.

## The 61.83% claim — verdict: NOT VERIFIED

A workspace-wide grep (all project code, docs, artifacts, JSONs, CSVs, logs;
vendored venv packages excluded) found **zero occurrences** of 61.83 or 0.6183.
The old Bpenergy GitHub repository is unreachable (HTTP 404). The figure exists
only as a conversational reference ("the earlier approximate benchmark"). No
numerator, denominator, model, sample, or computation is on record. It cannot be
reproduced from any available source and must not be cited as a measured result.

For reference, the verified numbers on record: ensemble 59.47% (macro) /
59.49% (pooled); Elo champion 62.16% (macro) / 62.14% (pooled); research
logistic candidate 63.77% (macro, separate lineage, not production).

## Reproduction note

**Reproduction: VERIFIED (byte-identical).** On 2026-09-09 the documented command
`python3 -m ml.run_backtest` was re-run end-to-end (EXIT=0; run log at
`~/workspace/mnb_audit/backtest_rerun2.log`). The regenerated
`ml/artifacts/backtest_2016_2025.json` is byte-identical to the original
artifact (md5 `454d3767c33b334fb87749ee5f90dc41`; 0 field mismatches across all
14 aggregate fields and all 10 folds; original secured at
`~/workspace/mnb_audit/backtest_2016_2025_ORIGINAL.json`).

The artifact stores fold aggregates, not per-game predictions.
`MNB_2016_2025_game_predictions.csv` was produced by re-running the identical
walk-forward pipeline (`python3 -m ml.run_backtest` code path) with per-game
capture; fold-level metrics from the re-run were compared against the artifact
before the CSV was accepted. Columns present are only those the pipeline
actually emits — no manufactured fields.

## Caveats recorded during inventory

- Champion status is **code-declared, not DB-verified**: `register_elo_champion.py`
  sets `champion:true` on 2026.01 / `false` on 2026.02, but no live MongoDB was
  inspected. Registry state: NOT VERIFIED.
- 2026.01 has no artifacts directory in `ml/artifacts/`; its registration exists
  in the registration script and `~/workspace/nfl-week1-backtest/model_meta.json`.
- The research logistic candidate (63.77%) is **not wired into production `ml/`**;
  promotion requires an explicit manual registry step that no code performs.
- Threshold convention differs: research uses `p > 0.5`, production `>= 0.5`.
- Research ablation (5-fold, logistic): removing EPA actually *raised* accuracy to
  64.23% (vs 63.77% full); every other group's removal cost ≤0.5pp — direction
  noted, not a production claim.
- Research vs production Elo differ by 0.02pp (62.18% vs 62.16%) because the
  research Elo pass includes POST games in ratings; production uses REG only.

## What the backtest proves — and does not prove

- PROVEN: the walk-forward protocol is leakage-clean; the ensemble's measured
  winner accuracy is 59.49% pooled over 2,639 games; it underperforms the
  in-harness Elo baseline; ATS performance is ~coin-flip.
- NOT PROVEN: any betting profitability (no ROI computed — no valid historical
  odds feed was available at audit time; moneyline ROI fields are absent, not zero).
- NOT PROVEN: the 61.83% figure (see above).
- Winner accuracy, ATS accuracy, ROI, and affiliate business metrics are
  distinct and are reported separately throughout this audit. They are never
  combined.
