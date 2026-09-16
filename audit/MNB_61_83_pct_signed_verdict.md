# SIGNED FORENSIC VERDICT — the "61.83%" accuracy claim

**MONEY BY NUMBERS · Forensic audit capstone**
**Date:** 2026-09-13
**Auditor:** Rosie (Meta AI assistant)
**Commissioned:** 2026-09-09 by Richard — 55-section forensic reconstruction
(LOCATE → READ → TRACE → RECONSTRUCT → REPRODUCE → VERIFY → DOCUMENT)

---

## 1. Subject

The claim that the MONEY BY NUMBERS prediction algorithm achieved **61.83%
accuracy** on a 2016–2025 backtest.

## 2. Method

- Workspace-wide search for `61.83` / `0.6183` across all code, docs,
  artifacts, JSONs, and CSVs (vendored venv packages excluded).
- Independent re-run of the documented backtest command
  (`python3 -m ml.run_backtest`); byte-level comparison of the regenerated
  artifact against the secured original.
- Independent md5 verification of the secured original, performed 2026-09-13.
- Review of the eight forensic deliverables in
  `~/workspace/money-by-numbers/audit/` (7 of 8 present; see §6).
- Attempted retrieval of the prior "Big Pick Energy"-era implementation.

## 3. Evidence

- **Zero occurrences** of 61.83 or 0.6183 in any project file. The figure
  exists only as a conversational reference ("the earlier approximate
  benchmark").
- **No numerator, no denominator, no model identity, and no sample
  definition** are on record for the figure — anywhere.
- The prior-era GitHub repository is unreachable (404); no implementation
  from that era could be examined.
- Reproduction: backtest re-ran with exit 0; the regenerated
  `ml/artifacts/backtest_2016_2025.json` is **byte-identical** to the
  original (0 field mismatches across all 14 aggregate fields and all 10
  folds). md5 `454d3767c33b334fb87749ee5f90dc41` confirmed independently on
  the secured original at `~/workspace/mnb_audit/backtest_2016_2025_ORIGINAL.json`.
- Leakage harness: **PASS, zero violations**, on the real 2016–2025 data
  (2,639 games; structural + fold-cutoff + truncation-invariance layers).

## 4. Verified figures (the reproduced backtest, pooled)

| Model | Correct / Total | Pooled accuracy |
|---|---|---|
| Elo baseline `MNB-NFL-2026.01` (champion, production) | 1640 / 2639 | **62.14%** |
| Ensemble challenger `MNB-NFL-2026.02` | 1570 / 2639 | **59.49%** |
| ATS, lined games | 1304 / 2574 | **50.66%** — no edge vs the spread |

Research lineage only (not production): logistic Elo + factors 63.77%;
systematic tuning best 64.27% — **not promoted** (selection bias across 29
configs; lost to baseline in both 2024 and 2025).

## 5. The verdict

**The 61.83% figure is NOT VERIFIED.**

It has no traceable provenance: no numerator, no denominator, no model, no
sample, and no occurrence in any artifact. It cannot be verified, partially
or otherwise. It must not be used in any public claim, marketing copy,
affiliate material, or track-record display.

The backtest itself is **VERIFIED as reproducible** — byte-identical
regeneration with a clean leakage harness. Reproducibility of the artifact is
independent of the 61.83% claim and does not rescue it.

## 6. Limitations and open items

- `exact_value_audit`: **NOT RUN** — crashed on a NumPy 2.x read-only-array
  incompatibility in the audit code itself, not a leakage finding. No
  pass/fail can be claimed from it.
- Deliverable 4 of 8 (`MNB_2016_2025_game_predictions.csv`) was never
  finalized into the audit directory. A working per-game file with 2,639
  rows exists at `~/workspace/mnb_audit/pergame_predictions_working.csv`.
- Ties are encoded as home losses (~8–10 in sample): accuracy is
  understated by roughly 0.1–0.3pp.
- Two CLV definitions exist in code (`settle.py` vs `routers/odds.py`);
  which one the frontend displays is **NOT VERIFIED**.
- Moneyline ROI was not computed — no valid historical odds feed existed at
  audit time. Correctly absent, not zero-filled.
- Business/affiliate metrics: no production traffic; correctly absent.

## 7. Directives

1. Never publish, advertise, or imply the 61.83% figure.
2. The public track record correctly shows only verified backtest numbers
   under a "historical backtest — not live results" banner. Keep it that way.
3. Any future accuracy claim must cite numerator, denominator, model
   version, and sample — and must reproduce from a secured artifact.

## 8. Signature

**Rosie — 2026-09-13.**

Evidence on file: `~/workspace/money-by-numbers/audit/` (7 deliverables),
`~/workspace/mnb_audit/` (original artifact, md5
`454d3767c33b334fb87749ee5f90dc41`, rerun logs, research leakage log).
This verdict can be independently re-checked by re-running the commands in §2.
