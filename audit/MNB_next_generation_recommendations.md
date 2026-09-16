# MONEY BY NUMBERS — NEXT-GENERATION MODEL RECOMMENDATIONS

2026-09-09. This document is strictly separated from the audit findings.

---

## PART A — THE ORIGINAL MODEL (what was actually built and measured)

- `MNB-NFL-2026.02`: 11-feature stacked ensemble (LightGBM + HGB, logistic
  meta-learner, isotonic calibration). Walk-forward 2016–2025: **59.49% pooled**
  winner accuracy (1570/2639), Brier 0.2371, ATS 50.66% (1304/2574).
- `MNB-NFL-2026.01` (champion): Elo-only, **62.14% pooled** (1640/2639).
- The ensemble did not beat the baseline. ATS shows no market edge.
- Historical results in the audit documents are FINAL and must not be altered
  to make any proposed model look better.

## PART B — PROPOSED NEXT-GENERATION DIRECTIONS (not built, not measured)

Recommendations only. Each would require its own full walk-forward validation
with the leakage harness before any production claim.

1. **Promote the research logistic candidate through the registry.** The
   independent research (`~/workspace/nfl-model-research/`) measured a
   calibrated logistic regression on Elo + research factors (EPA, success rate,
   yards/play, sacks, explosives, 3rd-down, red zone, regressed turnovers, QB
   adjustment, rest, travel, dome, division, week) at 63.77% macro / Brier
   0.2229 vs Elo 62.16% / 0.2344, leakage audit passed. It is the only candidate
   that beat the champion. Promotion must go through the formal registry
   (never silent), with the Week 1 locked picks remaining untouched.
2. **Fix tie encoding.** Encode ties as 0.5 (or exclude them) rather than 0;
   re-run walk-forward and report both figures.
3. **Report pooled metrics as primary.** Change the aggregation to pooled
   correct/total (keeping macro as a diagnostic) and correct the "pooled"
   comment in `ml/backtest.py`.
4. **Add offseason Elo mean-reversion** (e.g. 1/3 toward 1505, per 538) and
   measure the delta in walk-forward.
5. **Validate the Phase 5 hooks.** Injury counts and kickoff weather exist as
   data providers with documented neutral-default hooks; per spec §72 they need
   a full walk-forward backtest before entering the model.
6. **Resolve the dual CLV definitions** (`settle.py` vs `routers/odds.py`) into
   one documented standard and verify what the frontend displays.
7. **Keep the model market-free.** The research showed closing-spread-only
   reaches 66.5% — the market is the benchmark to beat, not an input to absorb.
   Any market-informed variant must stay a separately labeled candidate.
8. **Calibration monitoring in production:** track the reliability curve on
   settled picks (the track-record pipeline already records Brier) and alert
   on drift; scheduled retraining already registers challengers only.
9. **Do not chase complexity.** Two separate tests (2026.02 ensemble, research
   LightGBM at 61.92%) showed gradient boosting over 11–19 features does not
   beat a well-specified logistic/Elo model. Better inputs beat bigger models.

## PART C — WHAT NOT TO DO

- Do not reintroduce the 61.83% figure anywhere unless a numerator,
  denominator, model, and sample are produced from a real computation.
- Do not claim XGBoost/CatBoost were used historically; the artifact proves
  LightGBM + HGB.
- Do not mix winner accuracy, ATS accuracy, ROI, and affiliate revenue —
  they remain separate metrics with separate evidence bars.
