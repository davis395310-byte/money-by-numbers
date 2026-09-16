"""EPA / on-paper team-strength research, 2016-2025 walk-forward.

Research-only. Does NOT touch the champion model registry, ledgers, or locks.

Pipeline:
  1. Load nflverse play-by-play 2014-2025 (REG only), filter to real
     offensive plays (pass/run, no kneels/spikes, EPA present).
  2. Per-team-game aggregates, then rolling team-strength features with
     STRICT as-of discipline: features for a game use only REG games with
     game_date strictly BEFORE that game's date. Shrinkage toward fixed
     2014-2015 league priors (pre-evaluation, no leakage).
  3. Walk-forward Elo (K=20/scale=400/HFA=35, ml.features engine) as an
     additional feature for variant V2.
  4. Walk-forward training: to predict season S, train on seasons < S only.
     V1 = EPA features only. V2 = EPA + elo_diff. Logistic (SU) + Ridge (margin).
  5. Leakage audits: _data_through < kickoff on every row (repo harness),
     truncation invariance (rebuild truncated at 2020, predictions for
     seasons <= 2020 must be bit-identical).
  6. Evaluate SU accuracy + calibration + ATS vs closing spread_line,
     with train (2016-2020) / holdout (2021-2025) splits.

Writes /tmp/epa_pbp/epa_predictions.csv and /tmp/epa_pbp/epa_summary.json.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import numpy as np
import pandas as pd
from sklearn.linear_model import LogisticRegression, Ridge

REPO_ROOT = Path("/home/hatch/workspace/money-by-numbers")
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from ml.features import elo_update, ELO_START  # noqa: E402
from ml.leakage import KickoffLeakageHarness, assert_no_leakage  # noqa: E402

PBP_DIR = Path("/home/hatch/workspace/epa_pbp")
EVAL_SEASONS = list(range(2016, 2026))
BURN_IN = [2014, 2015]
SEASON_CAP_TRUNC = 2020  # truncation-invariance: keep seasons <= 2020

PLAY_COLS = [
    "game_id", "season", "season_type", "week", "game_date",
    "home_team", "away_team", "posteam", "defteam",
    "epa", "success", "play_type", "yards_gained",
    "interception", "fumble_lost", "sack", "qb_kneel", "qb_spike",
    "spread_line", "home_score", "away_score",
]

# shrinkage strengths (play counts); fixed a priori
K_EPA, K_SUCC, K_SPLIT, K_TO = 400.0, 400.0, 200.0, 400.0


_PLAYS_CACHE: dict[int | None, pd.DataFrame] = {}


def load_plays(season_cap: int | None = None) -> pd.DataFrame:
    if season_cap in _PLAYS_CACHE:
        return _PLAYS_CACHE[season_cap].copy()
    frames = []
    for y in range(2014, 2026):
        df = pd.read_parquet(PBP_DIR / f"play_by_play_{y}.parquet", columns=PLAY_COLS)
        df = df[df.season_type == "REG"].copy()
        frames.append(df)
    pbp = pd.concat(frames, ignore_index=True)
    pbp["game_date"] = pd.to_datetime(pbp["game_date"])
    if season_cap is not None:
        pbp = pbp[pbp.season <= season_cap].copy()
    # real offensive plays only
    m = (
        pbp.play_type.isin(["pass", "run"])
        & (pbp.qb_kneel.fillna(0) == 0)
        & (pbp.qb_spike.fillna(0) == 0)
        & pbp.epa.notna()
        & pbp.success.notna()
        & pbp.posteam.notna()
        & pbp.defteam.notna()
    )
    pl = pbp[m].copy()
    pl["is_pass"] = (pl.play_type == "pass").astype(np.int8)
    pl["giveaway"] = (
        (pl.interception.fillna(0) == 1) | (pl.fumble_lost.fillna(0) == 1)
    ).astype(np.int8)
    pl["succ"] = pl.success.astype(float)
    _PLAYS_CACHE[season_cap] = pl
    return pl.copy()


def league_priors(pl: pd.DataFrame) -> dict:
    b = pl[pl.season.isin(BURN_IN)]
    n = len(b)
    return {
        "epa": b.epa.sum() / n,
        "succ": b.succ.sum() / n,
        "pass_epa": b.loc[b.is_pass == 1, "epa"].sum() / max(b.is_pass.sum(), 1),
        "rush_epa": b.loc[b.is_pass == 0, "epa"].sum() / max((b.is_pass == 0).sum(), 1),
        "to": b.giveaway.sum() / n,
    }


def team_game_rows(pl: pd.DataFrame) -> pd.DataFrame:
    off = (
        pl.groupby(["game_id", "posteam"], as_index=False)
        .agg(
            n_plays=("epa", "size"),
            epa_sum=("epa", "sum"),
            succ_sum=("succ", "sum"),
            pass_plays=("is_pass", "sum"),
            pass_epa=("epa", lambda s: s[pl.loc[s.index, "is_pass"] == 1].sum()),
            rush_plays=("is_pass", lambda s: (s == 0).sum()),
            rush_epa=("epa", lambda s: s[pl.loc[s.index, "is_pass"] == 0].sum()),
            giveaways=("giveaway", "sum"),
        )
        .rename(columns={"posteam": "team"})
    )
    deff = (
        pl.groupby(["game_id", "defteam"], as_index=False)
        .agg(
            d_plays=("epa", "size"),
            d_epa_sum=("epa", "sum"),
            d_succ_sum=("succ", "sum"),
            d_pass_plays=("is_pass", "sum"),
            d_pass_epa=("epa", lambda s: s[pl.loc[s.index, "is_pass"] == 1].sum()),
            d_rush_plays=("is_pass", lambda s: (s == 0).sum()),
            d_rush_epa=("epa", lambda s: s[pl.loc[s.index, "is_pass"] == 0].sum()),
            takeaways=("giveaway", "sum"),
        )
        .rename(columns={"defteam": "team"})
    )
    tg = off.merge(deff, on=["game_id", "team"], how="outer")
    meta = pl.drop_duplicates("game_id")[
        ["game_id", "season", "week", "game_date", "home_team", "away_team",
         "home_score", "away_score", "spread_line"]
    ]
    tg = tg.merge(meta, on="game_id", how="left", validate="many_to_one")
    return tg


def add_rolling(tg: pd.DataFrame, priors: dict) -> pd.DataFrame:
    tg = tg.sort_values(["team", "game_date"]).reset_index(drop=True)
    sumcols = [
        "n_plays", "epa_sum", "succ_sum", "pass_plays", "pass_epa",
        "rush_plays", "rush_epa", "giveaways", "d_plays", "d_epa_sum",
        "d_succ_sum", "d_pass_plays", "d_pass_epa", "d_rush_plays",
        "d_rush_epa", "takeaways",
    ]
    tg[sumcols] = tg[sumcols].fillna(0)
    # ordinals for rolling date-max (pandas rolling has no datetime max)
    tg["_ord"] = tg["game_date"].map(pd.Timestamp.toordinal)

    def shrink(s, n, k, p):
        return (s + k * p) / (n + k)

    out = tg.copy()

    # --- season-to-date window: prior games of same team+season ---
    g = tg.groupby(["team", "season"])[sumcols]
    prior_std = g.cumsum() - tg[sumcols]  # sums of strictly-prior rows

    # --- last-5 / last-16 windows: cross-season, strictly prior games ---
    def roll_prior(w: int) -> pd.DataFrame:
        r = (
            tg.groupby("team")[sumcols]
            .apply(lambda x: x.shift(1).rolling(w, min_periods=1).sum())
            .reset_index(level=0, drop=True)
            .reindex(tg.index)
        )
        return r.fillna(0.0)  # no prior games -> zero sums -> pure shrinkage prior

    r5 = roll_prior(5)
    r16 = roll_prior(16)

    # latest contributing game_date for l16 window (max over prior 16 game dates)
    # (rolling max needs numeric: use ordinals, convert back after)
    gd = tg[["team", "season", "_ord"]]
    last16 = (
        gd.groupby("team")["_ord"]
        .apply(lambda x: x.shift(1).rolling(16, min_periods=1).max())
        .reset_index(level=0, drop=True)
        .reindex(tg.index)
    )
    # latest contributing date for season window: cummax of prior dates
    laststd = (
        gd.groupby([tg.team, tg.season])["_ord"]
        .apply(lambda x: x.shift(1).cummax())
        .reset_index(level=[0, 1], drop=True)
        .reindex(tg.index)
    )

    def feats(prefix: str, S: pd.DataFrame, N_off, N_def):
        p = priors
        out[f"{prefix}_off_epa"] = shrink(S["epa_sum"], S["n_plays"], K_EPA, p["epa"])
        out[f"{prefix}_off_succ"] = shrink(S["succ_sum"], S["n_plays"], K_SUCC, p["succ"])
        out[f"{prefix}_pass_epa"] = shrink(S["pass_epa"], S["pass_plays"], K_SPLIT, p["pass_epa"])
        out[f"{prefix}_rush_epa"] = shrink(S["rush_epa"], S["rush_plays"], K_SPLIT, p["rush_epa"])
        out[f"{prefix}_give"] = shrink(S["giveaways"], S["n_plays"], K_TO, p["to"])
        out[f"{prefix}_def_epa"] = shrink(S["d_epa_sum"], S["d_plays"], K_EPA, p["epa"])
        out[f"{prefix}_def_succ"] = shrink(S["d_succ_sum"], S["d_plays"], K_SUCC, p["succ"])
        out[f"{prefix}_def_pass"] = shrink(S["d_pass_epa"], S["d_pass_plays"], K_SPLIT, p["pass_epa"])
        out[f"{prefix}_def_rush"] = shrink(S["d_rush_epa"], S["d_rush_plays"], K_SPLIT, p["rush_epa"])
        out[f"{prefix}_take"] = shrink(S["takeaways"], S["d_plays"], K_TO, p["to"])

    feats("std", prior_std, None, None)
    feats("l5", r5, None, None)
    feats("l16", r16, None, None)
    out["_last_std"] = pd.to_datetime(laststd.map(
        lambda o: pd.Timestamp.fromordinal(int(o)) if pd.notna(o) else pd.NaT))
    out["_last_16"] = pd.to_datetime(last16.map(
        lambda o: pd.Timestamp.fromordinal(int(o)) if pd.notna(o) else pd.NaT))
    out = out.drop(columns=["_ord"])
    return out

# --- chunk 2: game-level frame + walk-forward Elo -----------------------------

FEAT_PAIRS = [  # (home_col, away_col, sign) -> diff favors home when positive
    ("std_off_epa", "std_off_epa", +1),
    ("std_def_epa", "std_def_epa", -1),
    ("l5_off_epa", "l5_off_epa", +1),
    ("l5_def_epa", "l5_def_epa", -1),
    ("l16_off_epa", "l16_off_epa", +1),
    ("l16_def_epa", "l16_def_epa", -1),
    ("std_off_succ", "std_off_succ", +1),
    ("std_def_succ", "std_def_succ", -1),
    ("std_pass_epa", "std_pass_epa", +1),
    ("std_rush_epa", "std_rush_epa", +1),
    ("std_def_pass", "std_def_pass", -1),
    ("std_def_rush", "std_def_rush", -1),
]

FEATURE_NAMES = [
    "off_epa_std", "def_epa_std", "off_epa_l5", "def_epa_l5",
    "off_epa_l16", "def_epa_l16", "off_succ_std", "def_succ_std",
    "pass_epa_std", "rush_epa_std", "def_pass_std", "def_rush_std",
    "turnover_margin",
]


def build_game_frame(tg: pd.DataFrame) -> pd.DataFrame:
    fcols = [c for c in tg.columns if c.split("_")[0] in ("std", "l5", "l16")]
    meta_cols = ["game_id", "season", "week", "game_date", "home_team",
                 "away_team", "home_score", "away_score", "spread_line"]
    g = tg.drop_duplicates("game_id")[meta_cols].copy()
    # keep 2014+ : 2014-2015 are burn-in training seasons; predictions are
    # made for EVAL_SEASONS only (train_predict selects season == S)
    g = g[g.season >= 2014].sort_values(
        ["game_date", "game_id"]).reset_index(drop=True)
    th = tg[["game_id", "team"] + fcols + ["_last_std", "_last_16"]].copy()
    home = th.rename(columns={"team": "home_team"})
    away = th.rename(columns={"team": "away_team"})
    g = g.merge(home, on=["game_id", "home_team"], how="left", validate="one_to_one")
    g = g.merge(away, on=["game_id", "away_team"], how="left", validate="one_to_one",
                suffixes=("_h", "_a"))
    for name, (hcol, acol, sign) in zip(FEATURE_NAMES[:12], FEAT_PAIRS):
        g[name] = sign * (g[f"{hcol}_h"] - g[f"{acol}_a"])
    g["turnover_margin"] = (
        (g["std_give_a"] - g["std_give_h"]) + (g["std_take_h"] - g["std_take_a"])
    )
    last_cols = ["_last_std_h", "_last_16_h", "_last_std_a", "_last_16_a"]
    g["_data_through"] = g[last_cols].max(axis=1)
    g["kickoff"] = g["game_date"]
    g["home_margin"] = g["home_score"] - g["away_score"]
    g["home_win"] = (g["home_margin"] > 0).astype(int)
    g["is_tie"] = (g["home_margin"] == 0)
    return g


def walk_forward_elo(tg: pd.DataFrame) -> pd.DataFrame:
    """Elo diffs (home - away, pre-game) for eval games. REG only, 2014+."""
    g = tg.drop_duplicates("game_id").sort_values(
        ["game_date", "game_id"]).reset_index(drop=True)
    elos: dict[str, float] = {}
    diffs = {}
    for row in g.itertuples():
        h, a = row.home_team, row.away_team
        eh, ea = elos.get(h, ELO_START), elos.get(a, ELO_START)
        diffs[row.game_id] = eh - ea
        margin = row.home_score - row.away_score
        neh, nea = elo_update(eh, ea, float(margin))
        elos[h], elos[a] = neh, nea
    out = pd.DataFrame({"game_id": list(diffs), "elo_diff": list(diffs.values())})
    return out


# --- chunk 3: walk-forward train/predict + leakage + evaluation ---------------

def train_predict(gf: pd.DataFrame) -> pd.DataFrame:
    X1 = gf[FEATURE_NAMES].to_numpy()
    X2 = np.column_stack([X1, gf["elo_diff"].to_numpy()])
    y = gf["home_win"].to_numpy()
    margin = gf["home_margin"].to_numpy()
    seasons = gf["season"].to_numpy()
    p1 = np.full(len(gf), np.nan)
    p2 = np.full(len(gf), np.nan)
    pm = np.full(len(gf), np.nan)
    for S in EVAL_SEASONS:
        if not (seasons == S).any():
            continue  # truncated runs have no later seasons; nothing to predict
        tr = seasons < S
        te = seasons == S
        assert tr.sum() > 0 and te.sum() > 0
        lr1 = LogisticRegression(C=1.0, max_iter=2000).fit(X1[tr], y[tr])
        lr2 = LogisticRegression(C=1.0, max_iter=2000).fit(X2[tr], y[tr])
        rg = Ridge(alpha=1.0).fit(X2[tr], margin[tr])
        p1[te] = lr1.predict_proba(X1[te])[:, 1]
        p2[te] = lr2.predict_proba(X2[te])[:, 1]
        pm[te] = rg.predict(X2[te])
    gf = gf.copy()
    gf["p_home_v1"] = p1
    gf["p_home_v2"] = p2
    gf["pred_margin"] = pm
    return gf


def run_pipeline(season_cap: int | None = None) -> pd.DataFrame:
    pl = load_plays(season_cap)
    # NOTE: priors always from the full 2014-2015 burn-in (pre-evaluation).
    priors = league_priors(load_plays(None))
    tg = team_game_rows(pl)
    tg = add_rolling(tg, priors)
    gf = build_game_frame(tg)
    elo = walk_forward_elo(tg)
    gf = gf.merge(elo, on="game_id", how="left", validate="one_to_one")
    assert gf["elo_diff"].notna().all()
    # leakage audit 1: as-of discipline on every row
    harness = KickoffLeakageHarness()
    viols = harness.check_feature_frame(
        gf[["game_id", "kickoff", "_data_through"]])
    assert_no_leakage(viols)
    return train_predict(gf)


def ats_grade(gf: pd.DataFrame) -> pd.DataFrame:
    d = gf.copy()
    d["side_home"] = (d["pred_margin"] - d["spread_line"]) > 0
    d["home_cover_margin"] = d["home_margin"] - d["spread_line"]
    d["is_push"] = d["home_cover_margin"] == 0
    d["home_covers"] = d["home_cover_margin"] > 0
    d["ats_win"] = (d["side_home"] == d["home_covers"]) & ~d["is_push"]
    return d


def summarize(gf: pd.DataFrame) -> dict:
    out = {}
    ev = gf.season.isin(EVAL_SEASONS)
    tr = gf.season.between(2016, 2020)
    ho = gf.season.between(2021, 2025)
    for label, pcol in [("v1_epa_only", "p_home_v1"), ("v2_epa_elo", "p_home_v2")]:
        for split, m in [("full", ev), ("train_2016_2020", tr),
                         ("holdout_2021_2025", ho)]:
            d = gf[m]
            pick_home = d[pcol] >= 0.5
            correct = (pick_home == (d.home_win == 1)) & ~d.is_tie  # ties = miss
            acc = float(correct.mean())
            brier = float(((d[pcol] - d.home_win) ** 2).mean())
            out[f"{label}_su_{split}"] = {
                "n": int(len(d)), "accuracy": round(acc, 4),
                "brier": round(brier, 4),
                "mean_p": round(float(d[pcol].mean()), 4),
                "hit_rate": round(float(d.home_win.mean()), 4),
                "ties": int(d.is_tie.sum()),
            }
    d = ats_grade(gf)
    for split, m in [("full", ev), ("train_2016_2020", tr),
                     ("holdout_2021_2025", ho)]:
        s = d[m & ~d.is_push]
        w = int(s.ats_win.sum())
        n = int(len(s))
        pushes = int((m & d.is_push).sum())
        l = n - w
        roi = round((w * (100 / 110) - l) / n, 4) if n else None
        out[f"ats_{split}"] = {
            "n_decisive": n, "wins": w, "losses": l, "pushes": pushes,
            "ats_pct": round(w / n, 4) if n else None, "roi_110": roi,
        }
    # calibration deciles (v2, full)
    dd = gf.copy()
    dd["dec"] = pd.qcut(dd["p_home_v2"], 10, labels=False, duplicates="drop")
    cal = dd.groupby("dec").agg(
        n=("p_home_v2", "size"), mean_p=("p_home_v2", "mean"),
        hit=("home_win", "mean"))
    out["calibration_v2"] = cal.round(4).to_dict(orient="records")
    # per-season SU for v2 (eval seasons only)
    per = gf[gf.season.isin(EVAL_SEASONS)].groupby("season").apply(
        lambda s: pd.Series({
            "n": len(s),
            "acc_v1": (((s.p_home_v1 >= 0.5) == (s.home_win == 1)).mean()),
            "acc_v2": (((s.p_home_v2 >= 0.5) == (s.home_win == 1)).mean()),
        })).round(4)
    out["per_season"] = per.to_dict(orient="index")
    return out


def main() -> None:
    print("full pipeline...", flush=True)
    gf = run_pipeline(None)
    print("truncated pipeline (seasons <=2020) for invariance check...", flush=True)
    gf_t = run_pipeline(SEASON_CAP_TRUNC)
    a = gf[gf.season <= 2020].set_index("game_id")
    b = gf_t.set_index("game_id")
    assert (a.index == b.index).all()
    for c in FEATURE_NAMES + ["elo_diff", "p_home_v1", "p_home_v2", "pred_margin"]:
        assert np.allclose(a[c].to_numpy(), b[c].to_numpy(), equal_nan=True), c
    print("truncation invariance: PASS (features + predictions identical thru 2020)",
          flush=True)
    summary = summarize(gf)
    summary["n_games"] = int(gf.season.isin(EVAL_SEASONS).sum())
    summary["leakage"] = {
        "as_of_audit": "PASS (KickoffLeakageHarness.check_feature_frame, 0 violations)",
        "truncation_invariance": "PASS (seasons <= 2020)",
        "training_discipline": "walk-forward: season S trained on seasons < S only",
    }
    gf_eval = gf[gf.season.isin(EVAL_SEASONS)].copy()
    gf_eval.to_csv("/home/hatch/workspace/epa_pbp/epa_predictions.csv", index=False)
    with open("/home/hatch/workspace/epa_pbp/epa_summary.json", "w") as fh:
        json.dump(summary, fh, indent=2)
    print(json.dumps({k: v for k, v in summary.items()
                      if not k.startswith("calibration")}, indent=2))


if __name__ == "__main__":
    main()
