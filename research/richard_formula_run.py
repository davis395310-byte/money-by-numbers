"""Richard's hand-specified MONEY BY NUMBERS team-rating formula — honest walk-forward backtest, 2016-2025.

Research-only. Does NOT touch champion MNB-NFL-2026.01, the model registry, locked picks, or ledgers.

Implements the formula VERBATIM from Richard's spec:
  OffPower = 1.10 * sum(w_i * z(off_metric_i))        (10 metrics; QBScore has no history -> neutral 0)
  DefPower = 1.10 * sum(w_i * z(def_metric_i))        (10 metrics; Front7Health has no history -> neutral 0)
  SituationalScore = 0.22*TO_z + 0.10*Pen_z + 0.16*HFA_z + 0.10*Rest_z - 50
      (Weather, MarketMovement, OffensiveLine, SkillPositionHealth have no
       2016-2025 historical feed -> their contributions are exactly 0, REPORTED as a limitation)
  Overall = 0.50*OffPower + 0.50*DefPower + 0.12*SituationalScore
  Pick: higher Overall wins. PowerDifference = Overall_home - Overall_away.

Normalization: z-score vs league, direction-corrected (higher PA/G, YA/G,
explosive-allowed, EPA-allowed, success-allowed, RZ-TD%-allowed, 3rd%-allowed,
net-penalties = worse -> z sign inverted so higher z is always better).

Walk-forward: predict season S using seasons < S only. 2014-2015 burn-in.
STRICT as-of: rolling metrics use only games with game_date < predicted game date.
League mean/std for z-scoring use per-team season aggregates from seasons < S only.

Variants:
  V-R : Richard's hand-set weights, p(home) = expit(b * PowerDiff), b fit per fold (single param MLE).
  V-F : same inputs as home-away diffs, weights fit by logistic regression per fold.

Margin (ATS grading): pred_margin = slope * linear_score, through-origin OLS per fold.
"""
import json
import math
import os
import sys
from datetime import datetime

import numpy as np
import pandas as pd
from scipy.optimize import minimize_scalar
from sklearn.linear_model import LogisticRegression

PBP_DIR = "/home/hatch/workspace/epa_pbp"
GAMES_CSV = os.path.join(PBP_DIR, "epa_predictions.csv")  # verified 2,639-game sample w/ scores+spreads
OUT_DIR = "/home/hatch/workspace/epa_pbp"
PRED_CSV = os.path.join(OUT_DIR, "richard_formula_predictions.csv")
SUMMARY_JSON = os.path.join(OUT_DIR, "richard_formula_summary.json")
RUNLOG = os.path.join(OUT_DIR, "richard_run.log")

SEASONS_ALL = list(range(2014, 2026))
EVAL_SEASONS = list(range(2016, 2026))

OFF_W = [("off_epa", 0.18), ("pass_epa", 0.12), ("rush_epa", 0.06), ("off_succ", 0.10),
         ("off_expl", 0.08), ("ppg", 0.12), ("ypg", 0.06), ("rz_td", 0.10),
         ("third", 0.08), ("top_pg", 0.04)]  # sums to 0.94; QBScore 0.06 -> neutral 0
DEF_W = [("def_epa", 0.18), ("def_pass_epa", 0.12), ("def_rush_epa", 0.06), ("def_succ", 0.10),
         ("def_expl", 0.08), ("papg", 0.12), ("yapg", 0.06), ("rz_def", 0.10),
         ("third_def", 0.08), ("sack_rate", 0.04)]  # sums to 0.94; Front7Health 0.06 -> neutral 0
# metrics where higher raw value is WORSE (z sign inverted so higher z = better)
BAD = {"def_epa", "def_pass_epa", "def_rush_epa", "def_succ", "def_expl",
       "papg", "yapg", "rz_def", "third_def", "net_pen_pg"}
VF_FEATURES = [m for m, _ in OFF_W] + [m for m, _ in DEF_W] + ["to_margin_pg", "net_pen_pg", "rest_days"]


def log(msg):
    line = f"[{datetime.now().isoformat(timespec='seconds')}] {msg}"
    print(line, flush=True)
    with open(RUNLOG, "a") as f:
        f.write(line + "\n")


def parse_top(s):
    if pd.isna(s):
        return np.nan
    try:
        m, sec = str(s).split(":")
        return int(m) * 60 + int(sec)
    except Exception:
        return np.nan


def build_team_games():
    """One row per team per REG game with raw sum-stat columns."""
    frames = []
    for season in SEASONS_ALL:
        path = os.path.join(PBP_DIR, f"play_by_play_{season}.parquet")
        cols = ["game_id", "game_date", "season", "week", "season_type", "play_type",
                "posteam", "defteam", "home_team", "away_team", "epa", "success",
                "yards_gained", "down", "yardline_100", "penalty", "penalty_team",
                "penalty_yards", "interception", "fumble_lost", "sack", "qb_dropback",
                "first_down", "touchdown", "fixed_drive", "drive_time_of_possession",
                "total_home_score", "total_away_score"]
        df = pd.read_parquet(path, columns=cols)
        df = df[df["season_type"] == "REG"].copy()
        df["game_date"] = pd.to_datetime(df["game_date"]).dt.date
        frames.append(df)
        log(f"loaded {season}: {len(df)} REG plays")
    pbp = pd.concat(frames, ignore_index=True)

    off = pbp[(pbp["play_type"].isin(["pass", "run"])) & pbp["posteam"].notna() & pbp["epa"].notna()].copy()
    off["is_pass"] = (off["play_type"] == "pass").astype(int)
    off["is_run"] = (off["play_type"] == "run").astype(int)
    off["epa_pass"] = off["epa"] * off["is_pass"]
    off["epa_run"] = off["epa"] * off["is_run"]
    off["expl"] = (((off["is_pass"] == 1) & (off["yards_gained"] >= 20)) |
                   ((off["is_run"] == 1) & (off["yards_gained"] >= 10))).astype(int)
    off["give"] = off["interception"].fillna(0).astype(int) + off["fumble_lost"].fillna(0).astype(int)
    off["conv3"] = (((off["down"] == 3)) & ((off["first_down"] == 1) | (off["touchdown"] == 1))).astype(int)
    off["att3"] = (off["down"] == 3).astype(int)

    g = off.groupby(["game_id", "posteam"], observed=True)
    O = g.agg(o_n=("epa", "size"), o_epa=("epa", "sum"),
              o_pass_n=("is_pass", "sum"), o_pass_epa=("epa_pass", "sum"),
              o_run_n=("is_run", "sum"), o_run_epa=("epa_run", "sum"),
              o_succ=("success", "sum"), o_expl=("expl", "sum"), o_yards=("yards_gained", "sum"),
              give=("give", "sum"),
              third_att=("att3", "sum"), third_conv=("conv3", "sum")).reset_index().rename(columns={"posteam": "team"})
    # red zone trips
    rz = off.groupby(["game_id", "fixed_drive", "posteam"], observed=True).agg(
        min_yl=("yardline_100", "min"), td=("touchdown", "max")).reset_index()
    rz = rz[rz["min_yl"] <= 20].rename(columns={"posteam": "team"})
    r = rz.groupby(["game_id", "team"], observed=True).agg(rz_trips=("td", "size"), rz_tds=("td", "sum")).reset_index()
    O = O.merge(r, on=["game_id", "team"], how="left")
    # sacks allowed not needed on offense; defensive sacks below
    D = off.groupby(["game_id", "defteam"], observed=True).agg(
        d_n=("epa", "size"), d_epa=("epa", "sum"),
        d_pass_n=("is_pass", "sum"), d_pass_epa=("epa_pass", "sum"),
        d_run_n=("is_run", "sum"), d_run_epa=("epa_run", "sum"),
        d_succ=("success", "sum"), d_expl=("expl", "sum"), d_yards=("yards_gained", "sum"),
        take=("give", "sum"),
        d_third_att=("att3", "sum"), d_third_conv=("conv3", "sum"),
        sacks=("sack", "sum"), dropbacks=("qb_dropback", "sum")).reset_index().rename(columns={"defteam": "team"})
    # defensive red zone: opponent's trips when this team is defteam
    # defensive red zone: drives where the opponent reached the red zone vs this defense
    drv = off.groupby(["game_id", "fixed_drive", "posteam", "defteam"], observed=True).agg(
        min_yl=("yardline_100", "min"), td=("touchdown", "max")).reset_index()
    drv = drv[drv["min_yl"] <= 20]
    dr = drv.groupby(["game_id", "defteam"], observed=True).agg(
        d_rz_trips=("td", "size"), d_rz_tds=("td", "sum")).reset_index().rename(columns={"defteam": "team"})
    D = D.merge(dr, on=["game_id", "team"], how="left")

    # scores
    sc = pbp.groupby("game_id", observed=True).agg(
        home_team=("home_team", "first"), away_team=("away_team", "first"),
        game_date=("game_date", "first"), season=("season", "first"), week=("week", "first"),
        home_score=("total_home_score", "max"), away_score=("total_away_score", "max")).reset_index()
    # TOP per drive
    top = pbp[["game_id", "fixed_drive", "posteam", "drive_time_of_possession"]].dropna().drop_duplicates(
        ["game_id", "fixed_drive"]).copy()
    top["top_sec"] = top["drive_time_of_possession"].apply(parse_top)
    top = top.groupby(["game_id", "posteam"], observed=True).agg(top_sec=("top_sec", "sum")).reset_index(
        ).rename(columns={"posteam": "team"})
    # penalties committed
    pen = pbp[(pbp["penalty"] == 1) & pbp["penalty_team"].notna()].copy()
    pen["py"] = pen["penalty_yards"].fillna(0)
    pg = pen.groupby(["game_id", "penalty_team"], observed=True).agg(pen_committed=("py", "sum")).reset_index(
        ).rename(columns={"penalty_team": "team"})

    rows = []
    for _, gm in sc.iterrows():
        for team, is_home in ((gm["home_team"], 1), (gm["away_team"], 0)):
            rows.append({"game_id": gm["game_id"], "team": team, "is_home": is_home,
                         "game_date": gm["game_date"], "season": gm["season"], "week": gm["week"],
                         "points_for": gm["home_score"] if is_home else gm["away_score"],
                         "points_against": gm["away_score"] if is_home else gm["home_score"]})
    tg = pd.DataFrame(rows)
    tg = tg.merge(O, on=["game_id", "team"], how="left")
    tg = tg.merge(D, on=["game_id", "team"], how="left")
    tg = tg.merge(top, on=["game_id", "team"], how="left")
    tg = tg.merge(pg, on=["game_id", "team"], how="left")
    # opponent penalties committed (for net)
    pg2 = pg.rename(columns={"team": "opp", "pen_committed": "pen_opp_committed"})
    tg["_opp"] = tg.apply(lambda r: r["team"], axis=1)  # placeholder
    # map opponent via game
    opp_map = {}
    for _, gm in sc.iterrows():
        opp_map[(gm["game_id"], gm["home_team"])] = gm["away_team"]
        opp_map[(gm["game_id"], gm["away_team"])] = gm["home_team"]
    tg["opp"] = [opp_map[(gid, t)] for gid, t in zip(tg["game_id"], tg["team"])]
    pg2b = pg.rename(columns={"team": "opp", "pen_committed": "pen_opp"})
    tg = tg.merge(pg2b[["game_id", "opp", "pen_opp"]], on=["game_id", "opp"], how="left")
    numcols = tg.select_dtypes(include=[np.number]).columns
    tg[numcols] = tg[numcols].fillna(0)
    # rest days (capped at 14), strictly from previous game date
    tg = tg.sort_values(["team", "game_date", "game_id"]).reset_index(drop=True)
    tg["prev_date"] = tg.groupby("team")["game_date"].shift(1)
    tg["rest_days"] = tg.apply(
        lambda r: min((r["game_date"] - r["prev_date"]).days, 14) if pd.notna(r["prev_date"]) else 7, axis=1)
    tg["n_prior_check"] = tg.groupby(["team", "season"]).cumcount()
    log(f"team-game rows: {len(tg)}")
    return tg, sc[["game_id", "season", "week", "game_date", "home_team", "away_team",
                  "home_score", "away_score"]]

SUM_COLS = ["o_n", "o_epa", "o_pass_n", "o_pass_epa", "o_run_n", "o_run_epa", "o_succ",
            "o_expl", "o_yards", "points_for", "rz_trips", "rz_tds", "third_att",
            "third_conv", "top_sec", "give", "d_n", "d_epa", "d_pass_n", "d_pass_epa",
            "d_run_n", "d_run_epa", "d_succ", "d_expl", "d_yards", "points_against",
            "d_rz_trips", "d_rz_tds", "d_third_att", "d_third_conv", "sacks",
            "dropbacks", "take", "pen_committed", "pen_opp"]


def derive_metrics(s):
    """s: DataFrame/Series with SUM_COLS + n_games. Returns metric DataFrame."""
    d = {}
    n = s["n_games"].replace(0, np.nan)
    d["off_epa"] = s["o_epa"] / s["o_n"].replace(0, np.nan)
    d["pass_epa"] = s["o_pass_epa"] / s["o_pass_n"].replace(0, np.nan)
    d["rush_epa"] = s["o_run_epa"] / s["o_run_n"].replace(0, np.nan)
    d["off_succ"] = s["o_succ"] / s["o_n"].replace(0, np.nan)
    d["off_expl"] = s["o_expl"] / s["o_n"].replace(0, np.nan)
    d["ppg"] = s["points_for"] / n
    d["ypg"] = s["o_yards"] / n
    d["rz_td"] = s["rz_tds"] / s["rz_trips"].replace(0, np.nan)
    d["third"] = s["third_conv"] / s["third_att"].replace(0, np.nan)
    d["top_pg"] = s["top_sec"] / n
    d["def_epa"] = s["d_epa"] / s["d_n"].replace(0, np.nan)
    d["def_pass_epa"] = s["d_pass_epa"] / s["d_pass_n"].replace(0, np.nan)
    d["def_rush_epa"] = s["d_run_epa"] / s["d_run_n"].replace(0, np.nan)
    d["def_succ"] = s["d_succ"] / s["d_n"].replace(0, np.nan)
    d["def_expl"] = s["d_expl"] / s["d_n"].replace(0, np.nan)
    d["papg"] = s["points_against"] / n
    d["yapg"] = s["d_yards"] / n
    d["rz_def"] = s["d_rz_tds"] / s["d_rz_trips"].replace(0, np.nan)
    d["third_def"] = s["d_third_conv"] / s["d_third_att"].replace(0, np.nan)
    d["sack_rate"] = s["sacks"] / s["dropbacks"].replace(0, np.nan)
    d["to_margin_pg"] = (s["take"] - s["give"]) / n
    d["net_pen_pg"] = (s["pen_committed"] - s["pen_opp"]) / n
    return pd.DataFrame(d, index=s.index)


def rolling_with_fallback(tg):
    """Season-to-date rolling sums over STRICTLY prior games; fallback = prior full season."""
    tg = tg.sort_values(["team", "season", "game_date", "game_id"]).reset_index(drop=True)
    grp = tg.groupby(["team", "season"], observed=True)
    prior = grp[SUM_COLS].cumsum() - tg[SUM_COLS]  # strictly prior games this season
    prior_n = grp.cumcount().rename("n_prior")
    # prior full-season totals for fallback
    seas = tg.groupby(["team", "season"], observed=True)[SUM_COLS].sum()
    seas_n = tg.groupby(["team", "season"], observed=True).size().rename("n_games")
    seas = seas.join(seas_n)
    fb = tg[["team", "season"]].copy()
    fb["fb_season"] = fb["season"] - 1
    fb = fb.merge(seas.reset_index(), left_on=["team", "fb_season"],
                  right_on=["team", "season"], how="left", suffixes=("", "_fb"))
    use_fb = (prior_n == 0).to_numpy()
    sums = prior.copy()
    n_games = prior_n.copy().astype(float)
    fb_cols = [c for c in SUM_COLS]
    for c in fb_cols:
        sums.loc[use_fb, c] = fb.loc[use_fb, c].to_numpy()
    n_games.loc[use_fb] = fb.loc[use_fb, "n_games"].to_numpy()
    sums["n_games"] = n_games
    # data-through date: latest contributing game date (< this game's date by construction)
    prev_dates = tg.groupby(["team", "season"], observed=True)["game_date"].transform(
        lambda s: s.shift(1))
    seas_max = tg.groupby(["team", "season"], observed=True)["game_date"].max().reset_index()
    seas_max["season_plus1"] = seas_max["season"] + 1
    fb_dates = tg[["team", "season"]].merge(
        seas_max[["team", "season_plus1", "game_date"]].rename(columns={"game_date": "fb_max"}),
        left_on=["team", "season"], right_on=["team", "season_plus1"], how="left")["fb_max"]
    data_through = prev_dates.where(prior_n > 0, fb_dates)
    out = tg[["game_id", "team", "is_home", "game_date", "season", "week", "rest_days"]].copy()
    out = pd.concat([out, derive_metrics(sums)], axis=1)
    out["data_through"] = pd.to_datetime(data_through).dt.date
    out["n_src_games"] = n_games
    return out


def league_norm_params(feat, S):
    """Mean/std per metric over per-team full-season aggregates of seasons < S.
    For S in (2014, 2015) (training-only seasons) use 2014-2015 combined."""
    if S <= 2015:
        past = feat[feat["season"].isin([2014, 2015])]
    else:
        past = feat[feat["season"] < S]
    agg = past.groupby(["team", "season"], observed=True)[[m for m, _ in OFF_W] + [m for m, _ in DEF_W]].mean()
    mu = agg.mean()
    sd = agg.std().replace(0, np.nan).fillna(1.0)
    # rest: over team-game rest values in prior seasons
    rmu = past["rest_days"].mean()
    rsd = past["rest_days"].std()
    # hfa: binary team-game values in prior seasons (exactly mean .5 / std .5)
    h = past["is_home"].astype(float)
    hmu, hsd = h.mean(), h.std()
    # turnover / penalties: over per-team season aggregates too
    tmu = past.groupby(["team", "season"], observed=True)["to_margin_pg"].mean().mean()
    tsd = past.groupby(["team", "season"], observed=True)["to_margin_pg"].mean().std()
    pmu = past.groupby(["team", "season"], observed=True)["net_pen_pg"].mean().mean()
    psd = past.groupby(["team", "season"], observed=True)["net_pen_pg"].mean().std()
    return {"mu": mu, "sd": sd, "rest": (rmu, rsd if rsd else 1.0),
            "hfa": (hmu, hsd if hsd else 0.5),
            "to": (tmu, tsd if tsd else 1.0), "pen": (pmu, psd if psd else 1.0)}


def zscore(val, mu, sd, invert=False):
    z = (val - mu) / sd
    return -z if invert else z


def overall_for(team_feat, P):
    """team_feat: Series with z-scored metrics (columns like 'off_epa_z'). Returns Overall (verbatim formula)."""
    off = sum(w * team_feat[m + "_z"] for m, w in OFF_W)
    dfn = sum(w * team_feat[m + "_z"] for m, w in DEF_W)
    off_p = 1.10 * off
    def_p = 1.10 * dfn
    sit = (0.22 * team_feat["to_z"] + 0.10 * team_feat["pen_z"] + 0.16 * team_feat["hfa_z"]
           + 0.10 * team_feat["rest_z"] + 0.08 * 0.0 + 0.10 * 0.0 + 0.14 * 0.0 + 0.10 * 0.0 - 50)
    return 0.50 * off_p + 0.50 * def_p + 0.12 * sit


def build_game_features(feat):
    """Per-game PowerDiff (+ component z's) for every game 2014-2025, strictly as-of.
    2014-2015 rows are training-only (never evaluated)."""
    games = []
    for S in [2014, 2015] + EVAL_SEASONS:
        P = league_norm_params(feat, S)
        fS = feat[feat["season"] == S].copy()
        # z-score every metric (NaN -> 0 = league average, after scoring)
        for m, _ in OFF_W + DEF_W:
            fS[m + "_z"] = zscore(fS[m], P["mu"][m], P["sd"][m], invert=(m in BAD)).fillna(0)
        fS["to_z"] = zscore(fS["to_margin_pg"], *P["to"]).fillna(0)
        fS["pen_z"] = zscore(fS["net_pen_pg"], *P["pen"], invert=True).fillna(0)
        fS["hfa_z"] = zscore(fS["is_home"].astype(float), *P["hfa"])
        fS["rest_z"] = zscore(fS["rest_days"], *P["rest"])
        fS["Overall"] = fS.apply(lambda r: overall_for(r, P), axis=1)
        num_vals = ["Overall"] + [m + "_z" for m, _ in OFF_W] + [m + "_z" for m, _ in DEF_W] + ["to_z", "pen_z", "rest_z"]
        piv = fS.pivot_table(index=["game_id", "game_date", "week"], columns="is_home",
                             values=num_vals, observed=True)
        piv.columns = [f"{a}_{'h' if b == 1 else 'a'}" for a, b in piv.columns]
        piv = piv.reset_index()
        piv["season"] = S
        piv["PowerDiff"] = piv["Overall_h"] - piv["Overall_a"]
        dt_max = fS.groupby("game_id", observed=True)["data_through"].max()
        piv["data_through"] = piv["game_id"].map(dt_max)
        _zname = {m: f"{m}_z" for m, _ in OFF_W + DEF_W}
        _zname.update({"to_margin_pg": "to_z", "net_pen_pg": "pen_z", "rest_days": "rest_z"})
        for m in VF_FEATURES:
            zc = _zname[m]
            piv[f"d_{m}"] = piv[f"{zc}_h"] - piv[f"{zc}_a"]
        games.append(piv)
        log(f"season {S}: {len(piv)} games, |PowerDiff| mean={piv['PowerDiff'].abs().mean():.3f}")
    G = pd.concat(games, ignore_index=True)
    return G

def fit_single_logistic(x, y):
    """MLE for p = expit(b*x), single parameter b."""
    x = np.asarray(x, dtype=float)
    y = np.asarray(y, dtype=float)

    def nll(b):
        p = 1.0 / (1.0 + np.exp(-b * x))
        eps = 1e-12
        p = np.clip(p, eps, 1 - eps)
        return -(y * np.log(p) + (1 - y) * np.log(1 - p)).sum()

    res = minimize_scalar(nll, bounds=(-2, 2), method="bounded",
                          options={"xatol": 1e-6})
    return float(res.x)


def fit_slope_origin(x, y):
    x = np.asarray(x, dtype=float)
    y = np.asarray(y, dtype=float)
    den = (x ** 2).sum()
    return float((x * y).sum() / den) if den > 0 else 0.0


def wilson(p, n, z=1.96):
    if n == 0:
        return (float("nan"), float("nan"))
    d = 1 + z * z / n
    c = p + z * z / (2 * n)
    m = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n))
    return ((c - m) / d, (c + m) / d)


def run_walk_forward(G, games_meta):
    G = G.merge(games_meta, on="game_id", how="left", suffixes=("", "_meta"))
    G = G[G["home_score"].notna()].copy()  # 2014-2015 training rows without meta stay only if matched; eval rows always matched
    G["actual_margin"] = G["home_score"] - G["away_score"]
    G["home_win"] = (G["actual_margin"] > 0).astype(int)  # ties -> 0 (miss), champion convention
    G = G.sort_values(["season", "game_date", "game_id"]).reset_index(drop=True)
    dcols = [f"d_{m}" for m in VF_FEATURES]
    rows = []
    for S in EVAL_SEASONS:
        tr = G[G["season"] < S]
        te = G[G["season"] == S].copy()
        b = fit_single_logistic(tr["PowerDiff"], tr["home_win"])
        slope_r = fit_slope_origin(tr["PowerDiff"], tr["actual_margin"])
        lr = LogisticRegression(C=1.0, max_iter=2000)
        lr.fit(tr[dcols].fillna(0), tr["home_win"])
        dec = lr.decision_function(te[dcols].fillna(0))
        slope_f = fit_slope_origin(lr.decision_function(tr[dcols].fillna(0)), tr["actual_margin"])
        te["p_vr"] = 1.0 / (1.0 + np.exp(-b * te["PowerDiff"]))
        te["pick_home_vr"] = (te["PowerDiff"] > 0).astype(int)
        te["margin_vr"] = slope_r * te["PowerDiff"]
        te["p_vf"] = lr.predict_proba(te[dcols].fillna(0))[:, 1]
        te["pick_home_vf"] = (te["p_vf"] > 0.5).astype(int)
        te["margin_vf"] = slope_f * dec
        te["b_fold"], te["slope_r_fold"], te["slope_f_fold"] = b, slope_r, slope_f
        rows.append(te)
        log(f"fold {S}: b={b:.4f} slope_r={slope_r:.3f} slope_f={slope_f:.3f} "
            f"VR_acc={(te['pick_home_vr'] == te['home_win']).mean():.4f} "
            f"VF_acc={(te['pick_home_vf'] == te['home_win']).mean():.4f}")
    return pd.concat(rows, ignore_index=True)


def ats_side(row, mcol):
    pm, sl, am = row[mcol], row["spread_line"], row["actual_margin"]
    if pd.isna(pm) or pd.isna(sl):
        return None
    pick_home = (pm - sl) > 0
    if (pm - sl) == 0 or (am - sl) == 0:
        return None  # push
    actual_home = (am - sl) > 0
    return pick_home == actual_home


def grade(P, label):
    n = len(P)
    out = {"label": label, "n": n}
    for tag, pk, mk, pc in [("vr", "pick_home_vr", "margin_vr", "p_vr"),
                            ("vf", "pick_home_vf", "margin_vf", "p_vf")]:
        acc = float(((P[pk] == P["home_win"])).mean())
        ci = wilson(acc, n)
        ats = P.apply(lambda r: ats_side(r, mk), axis=1)
        ats = ats[ats.notna()]
        aw = float(ats.mean()) if len(ats) else float("nan")
        w, l = int(ats.sum()), int((~ats.astype(bool)).sum())
        roi = (w - 1.1 * l) / (1.1 * (w + l)) if (w + l) else float("nan")
        brier = float((((P[pc] - P["home_win"]) ** 2).mean()))
        out[tag] = {"su_acc": acc, "su_ci": ci, "su_n": n,
                    "ats": aw, "ats_w": w, "ats_l": l, "ats_n": w + l, "roi": roi,
                    "brier": brier}
    return out


def calibration_deciles(P, pcol):
    P = P.copy()
    P["bin"] = pd.qcut(P[pcol], 10, labels=False, duplicates="drop")
    return [(float(g[pcol].mean()), float(g["home_win"].mean()), len(g))
            for _, g in P.groupby("bin", observed=True)]


def leakage_audit(P):
    """Every evaluated game's feature data-through must be strictly before kickoff."""
    ev = P[P["season"].isin(EVAL_SEASONS)].copy()
    ev["gd"] = pd.to_datetime(ev["game_date"]).dt.date
    bad = ev[~(ev["data_through"] < ev["gd"])]
    log(f"leakage audit: {len(ev)} games checked, {len(bad)} violations")
    # hand-check one game: rolling sums == sums over strictly-prior games
    return len(bad), len(ev)


def main():
    if os.path.exists(RUNLOG):
        os.remove(RUNLOG)
    log("== Richard formula backtest start ==")
    tg, sc = build_team_games()
    feat = rolling_with_fallback(tg)
    log(f"feature rows: {len(feat)}")
    G = build_game_features(feat)
    games_meta = pd.read_csv(GAMES_CSV, usecols=["game_id", "spread_line"])
    full_meta = sc.merge(games_meta, on="game_id", how="left")
    Geval = G[G["season"].isin(EVAL_SEASONS)].copy()
    assert len(Geval) == 2639, f"expected 2639 eval games, got {len(Geval)}"
    assert set(Geval["game_id"]) == set(pd.read_csv(GAMES_CSV, usecols=["game_id"])["game_id"]), "game list mismatch"
    P = run_walk_forward(G, full_meta)
    assert set(P["game_id"]) == set(games_meta["game_id"]), "prediction game list mismatch"
    viol, checked = leakage_audit(P)
    assert viol == 0, f"LEAKAGE: {viol} violations"

    P.to_csv(PRED_CSV, index=False)
    log(f"saved {PRED_CSV}")

    full = grade(P, "full 2016-2025")
    train = grade(P[P["season"] <= 2020], "train 2016-2020")
    hold = grade(P[P["season"] >= 2021], "holdout 2021-2025")
    by_season = {str(s): grade(P[P["season"] == s], f"season {s}") for s in EVAL_SEASONS}
    cal_vr = calibration_deciles(P, "p_vr")
    cal_vf = calibration_deciles(P, "p_vf")
    summary = {"full": full, "train": train, "holdout": hold, "by_season": by_season,
               "cal_vr": cal_vr, "cal_vf": cal_vf,
               "leakage": {"violations": viol, "checked": checked},
               "bars": {"champion_01_su": 0.6214, "epa_v1_su": 0.6321, "ats_breakeven": 0.5238},
               "neutral_inputs": ["QBScore", "Front7Health", "OffensiveLine",
                                  "SkillPositionHealth", "Weather", "MarketMovement"]}
    with open(SUMMARY_JSON, "w") as f:
        json.dump(summary, f, indent=2, default=str)
    log(f"saved {SUMMARY_JSON}")
    for g in (full, train, hold):
        for tag in ("vr", "vf"):
            d = g[tag]
            log(f"{g['label']} {tag.upper()}: SU={d['su_acc']:.4f} CI=[{d['su_ci'][0]:.4f},{d['su_ci'][1]:.4f}] "
                f"ATS={d['ats']:.4f} ({d['ats_w']}-{d['ats_l']}) ROI={d['roi']:.4f} Brier={d['brier']:.4f}")
    log("== done ==")


if __name__ == "__main__":
    main()
