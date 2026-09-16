"""Weekly Upset Special — 2016-2025 walk-forward backtest (RESEARCH ONLY).

Rule (exact): for each regular-season week, among games where the champion
model (MNB-NFL-2026.01, pure Elo family) picks the underdog by the CLOSING
LINE, the week's Upset Special is the ONE such pick with the highest model
win probability for the picked team. Weeks with no underdog pick -> no special.

Does NOT touch the champion registry, locked 2026 picks, or any ledger.

Model pipeline replicates the audited harness exactly:
  games = load_games(range(1999, 2026), game_types=("REG",))
  frame = build_feature_frame(games)          # point-in-time Elo walk-forward
  p_home = elo_win_probability(elo_diff)       # Elo diff + 35 HFA, scale 400
  pick home iff p_home >= 0.5                 # audited harness grading rule
  correct iff (pick_home == home_win), home_win = home_score > away_score
  (ties -> home_win False, graded like the audited harness)

Spread convention (verified empirically against known games):
  spread_line > 0 -> HOME team favored (giving points)
  spread_line < 0 -> HOME team underdog (receiving points)
  spread_line == 0 -> pick'em (no underdog)
Underdog pick = model pick is the team receiving points.
"""

from __future__ import annotations

import math
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from ml.data_loader import load_games  # noqa: E402
from ml.features import build_feature_frame, elo_win_probability  # noqa: E402

EXPECTED_PER_SEASON = {2016: 154, 2017: 167, 2018: 162, 2019: 161, 2020: 160,
                       2021: 172, 2022: 160, 2023: 157, 2024: 180, 2025: 167}


def wilson_ci(k: int, n: int, z: float = 1.96) -> tuple[float, float]:
    """95% Wilson score interval for a binomial proportion."""
    if n == 0:
        return (float("nan"), float("nan"))
    p = k / n
    denom = 1 + z * z / n
    center = (p + z * z / (2 * n)) / denom
    half = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / denom
    return (max(0.0, center - half), min(1.0, center + half))


def main() -> None:
    import numpy as np
    import pandas as pd

    print("loading games...", flush=True)
    games = load_games(range(1999, 2026), game_types=("REG",))
    print("building feature frame...", flush=True)
    frame = build_feature_frame(games)

    keep = ["game_id", "home_score", "away_score", "spread_line",
            "status", "kickoff"]
    df = frame.merge(games[keep], on="game_id", suffixes=("", "_g"))
    df = df[(df["season"] >= 2016) & (df["season"] <= 2025)].copy()
    # Same sample as the audited harness: finals with scores only.
    df = df[(df["status"] == "final") & df["home_score"].notna()
            & df["away_score"].notna()].copy()
    df = df.reset_index(drop=True)

    df["p_home"] = elo_win_probability(df["elo_diff"].to_numpy(dtype=float))
    df["pick_home"] = df["p_home"] >= 0.5
    df["home_win"] = df["home_score"] > df["away_score"]
    df["correct"] = df["pick_home"] == df["home_win"]
    df["model_p"] = np.where(df["pick_home"], df["p_home"], 1.0 - df["p_home"])
    df["predicted_winner"] = np.where(df["pick_home"], df["home_team"],
                                      df["away_team"])
    df["is_tie"] = df["home_score"] == df["away_score"]

    # ---- Champion-pipeline sanity gate (must match verified record) ----
    per_season = df.groupby("season")["correct"].agg(["sum", "count"])
    ok = True
    for season, row in per_season.iterrows():
        exp = EXPECTED_PER_SEASON[int(season)]
        if int(row["sum"]) != exp:
            ok = False
            print(f"MISMATCH season {season}: got {int(row['sum'])}, "
                  f"expected {exp}")
    total_correct = int(df["correct"].sum())
    print(f"sanity: total {total_correct}/{len(df)} "
          f"(expected 1640/2639) -> {'PASS' if ok and total_correct == 1640 else 'FAIL'}")
    if not (ok and total_correct == 1640):
        raise SystemExit("champion pipeline mismatch — aborting")

    n_null_spread = int(df["spread_line"].isna().sum())
    print(f"games with null spread_line: {n_null_spread}")
    df["pickem"] = df["spread_line"] == 0
    # Underdog pick: model picked the team RECEIVING points.
    df["dog_pick"] = ((df["pick_home"] & (df["spread_line"] < 0))
                      | (~df["pick_home"] & (df["spread_line"] > 0)))

    # ---- Weekly Upset Special ----
    specials = []
    weeks = sorted(df[["season", "week"]].drop_duplicates()
                   .itertuples(index=False), key=lambda r: (r.season, r.week))
    weeks_no_dog = 0
    for season, week in weeks:
        wk = df[(df["season"] == season) & (df["week"] == week)]
        cands = wk[wk["dog_pick"]].copy()
        if cands.empty:
            weeks_no_dog += 1
            continue
        # Highest model win prob for the picked team; deterministic tie-break:
        # earliest kickoff, then game_id.
        cands = cands.sort_values(["model_p", "kickoff", "game_id"],
                                 ascending=[False, True, True])
        best = cands.iloc[0]
        specials.append({
            "season": int(season), "week": int(week),
            "game_id": best["game_id"],
            "date": str(best["kickoff"].date()) if pd.notna(best["kickoff"]) else "",
            "away_team": best["away_team"], "home_team": best["home_team"],
            "predicted_winner": best["predicted_winner"],
            "model_p": float(best["model_p"]),
            "spread_line": float(best["spread_line"]),
            "home_score": int(best["home_score"]),
            "away_score": int(best["away_score"]),
            "correct": bool(best["correct"]),
            "is_tie": bool(best["is_tie"]),
            "n_dog_picks_that_week": int(len(cands)),
        })

    sp = pd.DataFrame(specials)
    n_sp = len(sp)
    k_sp = int(sp["correct"].sum())
    lo, hi = wilson_ci(k_sp, n_sp)

    print("\n===== WEEKLY UPSET SPECIAL =====")
    print(f"distinct regular-season weeks in sample: {len(weeks)}")
    print(f"weeks with NO underdog pick: {weeks_no_dog}")
    print(f"Upset Specials selected: {n_sp}")
    print(f"SU record: {k_sp}-{n_sp - k_sp}  hit rate {100*k_sp/n_sp:.2f}%")
    print(f"95% Wilson CI: [{100*lo:.2f}%, {100*hi:.2f}%]")
    print(f"ties among specials: {int(sp['is_tie'].sum())}")
    mp = sp["model_p"]
    print(f"model_p: mean {mp.mean():.4f}  min {mp.min():.4f}  max {mp.max():.4f}")
    print(f"observed hit rate vs avg model prob: "
          f"{100*k_sp/n_sp:.2f}% vs {100*mp.mean():.2f}% "
          f"(gap {100*(k_sp/n_sp - mp.mean()):+.2f}pp)")

    print("\nPer-season Upset Specials:")
    for season, grp in sp.groupby("season"):
        kk = int(grp["correct"].sum())
        nn = len(grp)
        slo, shi = wilson_ci(kk, nn)
        print(f"  {int(season)}: {nn} specials, {kk}-{nn-kk} "
              f"({100*kk/nn:.1f}%), avg model_p {grp['model_p'].mean():.3f}")

    # Calibration buckets on the specials
    print("\nCalibration buckets (specials):")
    bins = [(0.50, 0.55), (0.55, 0.60), (0.60, 0.65), (0.65, 1.01)]
    for b0, b1 in bins:
        b = sp[(sp["model_p"] >= b0) & (sp["model_p"] < b1)]
        if len(b):
            print(f"  [{b0:.2f},{b1:.2f}): n={len(b)} "
                  f"hit {100*b['correct'].mean():.1f}% "
                  f"avg_p {100*b['model_p'].mean():.1f}%")

    # ---- Context: ALL model underdog picks ----
    dogs = df[df["dog_pick"]]
    kd = int(dogs["correct"].sum())
    nd = len(dogs)
    dlo, dhi = wilson_ci(kd, nd)
    print("\n===== CONTEXT =====")
    print(f"ALL model underdog picks: {kd}/{nd} "
          f"({100*kd/nd:.2f}%), 95% CI [{100*dlo:.2f}%, {100*dhi:.2f}%]")
    print(f"ties among all dog picks: {int(dogs['is_tie'].sum())}")
    favs = df[~df["dog_pick"] & ~df["pickem"] & df["spread_line"].notna()]
    kf = int(favs["correct"].sum())
    print(f"ALL model favorite picks: {kf}/{len(favs)} ({100*kf/len(favs):.2f}%)")
    print(f"pick'em model picks (excluded from dog/fav): "
          f"{int(df['pickem'].sum())}")

    # Base rate: underdog side graded by the same harness rule.
    lined = df[df["spread_line"].notna() & (df["spread_line"] != 0)].copy()
    lined["dog_is_home"] = lined["spread_line"] < 0
    lined["dog_correct"] = lined["dog_is_home"] == lined["home_win"]
    kb = int(lined["dog_correct"].sum())
    nb = len(lined)
    blo, bhi = wilson_ci(kb, nb)
    print(f"BASE RATE (underdog wins outright, same grading): {kb}/{nb} "
          f"({100*kb/nb:.2f}%), 95% CI [{100*blo:.2f}%, {100*bhi:.2f}%]")
    strict = ((lined["dog_is_home"] & (lined["home_score"] > lined["away_score"]))
              | (~lined["dog_is_home"] & (lined["away_score"] > lined["home_score"])))
    print(f"BASE RATE strict outright (ties not wins): {int(strict.sum())}/{nb} "
          f"({100*strict.mean():.2f}%)")
    print(f"ties in lined games: {int(lined['is_tie'].sum())}")

    # Dog picks per week distribution
    dpw = df[df["dog_pick"]].groupby(["season", "week"]).size()
    print(f"\ndog picks/week: mean {dpw.mean():.2f}, max {dpw.max()}, "
          f"weeks with 0: {(dpw == 0).sum()} (of evaluated), "
          f"weeks in sample: {len(weeks)}")

    # Save specials list for the report appendix
    out = REPO_ROOT / "research" / "upset_special_picks.csv"
    sp.to_csv(out, index=False)
    print(f"\nspecials list -> {out}")


if __name__ == "__main__":
    main()
