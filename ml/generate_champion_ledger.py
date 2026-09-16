"""Regenerate the champion (MNB-NFL-2026.01) per-game ledger for 2016-2025.

Replays the EXACT verified champion pipeline:
  - ml.data_loader.load_games(range(1999, 2026), game_types=("REG",))
  - ml.features.build_feature_frame  (point-in-time Elo walk-forward)
  - p_home = elo_win_probability(elo_diff); pick home iff p_home >= 0.5
  - grading identical to the audited harness: correct = (pick_home == home_win),
    where home_win = (home_score > away_score) so ties count as home losses.

The output CSV is REFUSED unless every per-season correct count matches the
audited backtest (~/workspace/mnb_audit/backtest_2016_2025_ORIGINAL.json)
exactly. Aggregates are the ground truth; the ledger must reproduce them.

Usage:
    cd ~/workspace/money-by-numbers && python3 -m ml.generate_champion_ledger
"""

from __future__ import annotations

import csv
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from ml.data_loader import load_games  # noqa: E402
from ml.features import build_feature_frame, elo_win_probability  # noqa: E402

# Verified per-season correct counts, champion .01 (Elo), from the audited
# backtest folds. The ledger must reproduce these EXACTLY or it is not written.
VERIFIED_SEASON_CORRECT = {
    2016: 154, 2017: 167, 2018: 162, 2019: 161, 2020: 160,
    2021: 172, 2022: 160, 2023: 157, 2024: 180, 2025: 167,
}
VERIFIED_TOTAL = 1640
VERIFIED_N = 2639

OUT = REPO_ROOT / "frontend" / "public" / "track-record" / "MNB_2016_2025_game_ledger.csv"


def main() -> None:
    print("loading games...", flush=True)
    games = load_games(range(1999, 2026), game_types=("REG",))
    print(f"games: {len(games)}", flush=True)

    print("building feature frame (Elo walk-forward)...", flush=True)
    frame = build_feature_frame(games)

    g = games.set_index("game_id")
    rows = []
    season_correct: dict[int, int] = {}
    season_n: dict[int, int] = {}
    ties = 0
    for r in frame.itertuples(index=False):
        if not (2016 <= r.season <= 2025):
            continue
        gm = g.loc[r.game_id]
        hs, aws = gm["home_score"], gm["away_score"]
        if hs is None or aws is None or __import__("pandas").isna(hs):
            continue  # mirrors harness dropna(y_win)
        hs, aws = int(hs), int(aws)
        p_home = elo_win_probability(float(r.elo_diff))
        pick_home = p_home >= 0.5
        home_win = hs > aws
        if hs == aws:
            ties += 1
        correct = int(pick_home == home_win)
        season_correct[r.season] = season_correct.get(r.season, 0) + correct
        season_n[r.season] = season_n.get(r.season, 0) + 1
        predicted = r.home_team if pick_home else r.away_team
        actual = r.home_team if home_win else (r.away_team if aws > hs else "TIE")
        rows.append({
            "season": r.season,
            "week": int(r.week),
            "date": str(r.kickoff)[:10],
            "away_team": r.away_team,
            "home_team": r.home_team,
            "elo_diff_pre_game": round(float(r.elo_diff), 2),
            "p_home": round(p_home, 4),
            "predicted_winner": predicted,
            "model_probability": round(p_home if pick_home else 1.0 - p_home, 4),
            "actual_home_score": hs,
            "actual_away_score": aws,
            "actual_winner": actual,
            "correct": correct,
        })

    # ---- verification gate ----
    total_n = sum(season_n.values())
    total_c = sum(season_correct.values())
    print(f"reconstructed: {total_c}/{total_n} = {100*total_c/total_n:.4f}%", flush=True)
    print(f"ties in sample: {ties}", flush=True)
    ok = True
    for season, want in VERIFIED_SEASON_CORRECT.items():
        got = season_correct.get(season, -1)
        mark = "OK" if got == want else "MISMATCH"
        if got != want:
            ok = False
        print(f"  {season}: {got} (verified {want}) {mark}", flush=True)
    if total_c != VERIFIED_TOTAL or total_n != VERIFIED_N:
        print(f"TOTAL MISMATCH: got {total_c}/{total_n}, want {VERIFIED_TOTAL}/{VERIFIED_N}", flush=True)
        ok = False
    if not ok:
        print("REFUSED: ledger does not reproduce the audited aggregates. Not written.", flush=True)
        sys.exit(1)

    OUT.parent.mkdir(parents=True, exist_ok=True)
    with open(OUT, "w", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=list(rows[0].keys()))
        w.writeheader()
        w.writerows(rows)
    print(f"wrote {len(rows)} rows -> {OUT}", flush=True)


if __name__ == "__main__":
    main()
