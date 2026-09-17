"""Replay the champion (MNB-NFL-2026.01) Elo walk-forward and recover the
per-game tipping-point inputs for a future week.

The champion is pure Elo: pick home iff
``elo_win_probability(elo_diff) >= 0.5`` where
``elo_diff = home_elo - away_elo`` (end-of-prior-season ratings) and the
+35 home-field adjustment applies to the *expectation only*.

This module exposes:

- ``final_elos(seasons)`` — team ratings after the last game of ``seasons``.
- ``tipping_points(season, week, train_through)`` — for every game in the
  (season, week) slate: elo_diff, home/away Elos, win probability under the
  champion formula, rest differential, and the market moneylines (if an
  odds snapshot exists in MongoDB).

All inputs are point-in-time: nothing from after the slate's kickoffs is
used. These are the exact inputs the locked picks were derived from, so
the explainer layer can describe them honestly.

Usage:
    cd ~/workspace/money-by-numbers && python3 -m ml.tipping_points --season 2026 --week 1
"""

from __future__ import annotations

import argparse
import json
import math
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from ml.data_loader import load_games  # noqa: E402
from ml.features import ELO_HFA, ELO_START, elo_update, elo_win_probability  # noqa: E402


def final_elos(train_through: int) -> dict[str, float]:
    """Team Elo ratings after the last regular-season game of ``train_through``."""
    games = load_games(range(1999, train_through + 1), game_types=("REG",))
    games = games.sort_values("kickoff")
    elo: dict[str, float] = {}

    def rating(team: str) -> float:
        return elo.get(team, ELO_START)

    for _, row in games.iterrows():
        hs, aws = row["home_score"], row["away_score"]
        if hs is None or aws is None or (isinstance(hs, float) and math.isnan(hs)):
            continue  # unplayed / missing score: ratings do not move
        home, away = row["home_team"], row["away_team"]
        he, ae = rating(home), rating(away)
        he2, ae2 = elo_update(he, ae, float(hs) - float(aws))
        elo[home], elo[away] = he2, ae2
    return elo


def _moneyline_to_prob(ml: float) -> float:
    if ml > 0:
        return 100.0 / (ml + 100.0)
    return -ml / (-ml + 100.0)


def tipping_points(
    season: int,
    week: int,
    train_through: int,
    odds: dict[str, dict] | None = None,
) -> list[dict]:
    """Per-game tipping-point inputs for the (season, week) slate."""
    elos = final_elos(train_through)
    slate = load_games(range(season, season + 1), game_types=("REG",))
    slate = slate[slate["week"] == week].sort_values("kickoff")

    out: list[dict] = []
    for _, row in slate.iterrows():
        home, away = row["home_team"], row["away_team"]
        home_elo = elos.get(home, ELO_START)
        away_elo = elos.get(away, ELO_START)
        elo_diff = home_elo - away_elo
        p_home = elo_win_probability(elo_diff)  # HFA baked into expectation
        rest_diff = (row["home_rest"] if row["home_rest"] is not None else 7) - (
            row["away_rest"] if row["away_rest"] is not None else 7
        )
        game = {
            "game_id": row["game_id"],
            "home_team": home,
            "away_team": away,
            "kickoff": str(row["kickoff"]),
            "home_elo": round(home_elo, 1),
            "away_elo": round(away_elo, 1),
            "elo_diff": round(elo_diff, 1),  # home minus away, pre-HFA
            "hfa": ELO_HFA,  # expectation-only adjustment
            "model_prob_home": round(p_home, 4),
            "model_pick": home if p_home >= 0.5 else away,
            "rest_diff": int(rest_diff),
        }
        if odds and row["game_id"] in odds:
            snap = odds[row["game_id"]]
            mh, ma = snap.get("moneyline_home"), snap.get("moneyline_away")
            if mh and ma:
                ph = _moneyline_to_prob(float(mh))
                pa = _moneyline_to_prob(float(ma))
                game["market"] = {
                    "moneyline_home": mh,
                    "moneyline_away": ma,
                    "implied_prob_home": round(ph, 4),
                    "implied_prob_away": round(pa, 4),
                    "bookmaker_count": snap.get("bookmaker_count"),
                    "snapshot_at": str(snap.get("timestamp")),
                }
        out.append(game)
    return out


def _load_odds_snapshot() -> dict[str, dict]:
    try:
        from pymongo import MongoClient

        c = MongoClient("mongodb://127.0.0.1:27017/", serverSelectionTimeoutMS=3000)
        return {o["game_id"]: o for o in c["moneybynumbers"].odds.find({})}
    except Exception:
        return {}


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--season", type=int, default=2026)
    ap.add_argument("--week", type=int, default=1)
    ap.add_argument("--train-through", type=int, default=2025)
    args = ap.parse_args()
    games = tipping_points(
        args.season, args.week, args.train_through, odds=_load_odds_snapshot()
    )
    print(json.dumps(games, indent=1))


if __name__ == "__main__":
    main()
