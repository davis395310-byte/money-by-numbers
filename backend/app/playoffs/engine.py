"""Monte Carlo NFL playoff + remaining-season simulator (pure functions).

No function here opens a database connection or invents data: seeds,
ratings, records, and remaining games are explicit caller inputs, and
missing ratings raise a clear error.

Adjustment parameters (``adjustments``) are applied as Elo-point shifts
to the rating differential. Every adjustment EXCEPT ``hfa_elo`` defaults
to 0.0 — i.e. pure Elo — and each is documented as a PLACEHOLDER pending
the independent research verdict on evidence-based values. The parent
agent will supply those values; until then the engine runs Elo-only by
default and says so.
"""

from __future__ import annotations

import math
import random
from typing import Any, Dict, List, Mapping, Optional, Sequence, Tuple

from .bracket import (
    apply_tiebreakers,
    championship_matchup,
    divisional_matchups,
    playoff_format,
    wild_card_matchups,
)

#: Elo scale for the standard logistic win-probability curve.
ELO_SCALE = 400.0

#: Default adjustment set. ``hfa_elo`` is the long-standard home-field
#: value; every other adjustment is 0.0 (PLACEHOLDER pending the research
#: verdict) so the engine is pure-Elo unless the caller opts in.
DEFAULT_ADJUSTMENTS: Dict[str, float] = {
    # Home-field advantage in Elo points, applied to the home team in
    # non-neutral games. 35 Elo ≈ 55% for evenly matched teams.
    "hfa_elo": 35.0,
    # Extra Elo for a team coming off a first-round playoff bye.
    # PLACEHOLDER (0.0 = no effect) pending the research verdict on
    # evidence-based values.
    "bye_rest_elo": 0.0,
    # Elo points per career playoff start by the team's starting QB.
    # PLACEHOLDER (0.0 = no effect) pending the research verdict.
    "qb_playoff_exp_elo_per_start": 0.0,
    # Elo points per unit of the team's late-season momentum factor.
    # PLACEHOLDER (0.0 = no effect) pending the research verdict.
    "momentum_elo": 0.0,
}

#: Upper bound for Monte Carlo iterations on any public entry point.
MAX_SIMS = 50000


class PlayoffSimError(ValueError):
    """Raised when simulator inputs are missing or inconsistent."""


def _num(value: Any, name: str) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        raise PlayoffSimError(f"{name} must be numeric, got {value!r}")


def resolve_adjustments(adjustments: Optional[Mapping[str, Any]]) -> Dict[str, float]:
    """Merge caller adjustments over :data:`DEFAULT_ADJUSTMENTS`.

    Unknown keys raise :class:`PlayoffSimError` (typos must not silently
    become no-ops); values must be numeric. Returns a plain dict so the
    caller can echo exactly what was used.
    """
    resolved = dict(DEFAULT_ADJUSTMENTS)
    if adjustments is None:
        return resolved
    for key, value in adjustments.items():
        if key not in DEFAULT_ADJUSTMENTS:
            raise PlayoffSimError(
                f"unknown adjustment {key!r}; known keys: {sorted(DEFAULT_ADJUSTMENTS)}"
            )
        resolved[key] = _num(value, f"adjustment {key!r}")
    return resolved


def win_probability(
    elo_home: Any,
    elo_away: Any,
    neutral: bool = False,
    hfa_elo: float = 35.0,
) -> float:
    """Home-team win probability from the standard Elo logistic.

    ``p = 1 / (1 + 10^(-(diff + hfa) / 400))`` where ``diff = elo_home -
    elo_away`` and ``hfa`` is ``hfa_elo`` for non-neutral games, 0.0 for
    neutral-site games. Non-numeric ratings raise
    :class:`PlayoffSimError`.
    """
    eh = _num(elo_home, "elo_home")
    ea = _num(elo_away, "elo_away")
    hfa = 0.0 if neutral else _num(hfa_elo, "hfa_elo")
    return 1.0 / (1.0 + 10.0 ** (-(eh - ea + hfa) / ELO_SCALE))


def _team_factor(team: str, team_factors: Optional[Mapping[str, Mapping[str, Any]]]) -> Mapping[str, Any]:
    if not team_factors:
        return {}
    f = team_factors.get(team)
    return f if isinstance(f, Mapping) else {}


def _effective_elo(
    team: str,
    ratings: Mapping[str, float],
    *,
    is_home: bool,
    neutral: bool,
    adjustments: Mapping[str, float],
    team_factors: Optional[Mapping[str, Mapping[str, Any]]],
    had_bye: bool,
) -> float:
    """Rating after Elo-point adjustments.

    ``team_factors[team]`` may carry ``"qb_playoff_starts"`` (int) and
    ``"momentum"`` (float); ``had_bye`` marks teams coming off a
    first-round bye. With default adjustments every shift is 0.0 and this
    reduces to the raw rating (+ home-field when applicable).
    """
    elo = ratings[team]
    adj = adjustments
    if is_home and not neutral:
        elo += adj["hfa_elo"]
    if had_bye:
        elo += adj["bye_rest_elo"]
    factors = _team_factor(team, team_factors)
    try:
        starts = float(factors.get("qb_playoff_starts", 0) or 0)
    except (TypeError, ValueError):
        raise PlayoffSimError(
            f"team_factors[{team!r}]['qb_playoff_starts'] must be numeric"
        )
    try:
        momentum = float(factors.get("momentum", 0.0) or 0.0)
    except (TypeError, ValueError):
        raise PlayoffSimError(
            f"team_factors[{team!r}]['momentum'] must be numeric"
        )
    elo += adj["qb_playoff_exp_elo_per_start"] * starts
    elo += adj["momentum_elo"] * momentum
    return elo


def _validate_bracket_inputs(
    seeds_by_conf: Mapping[str, Sequence[str]],
    ratings: Mapping[str, Any],
    season: int,
) -> Tuple[Dict[str, List[str]], Dict[str, float]]:
    fmt = playoff_format(season)  # validates season
    n = fmt["seeds_per_conference"]
    if set(seeds_by_conf.keys()) != {"AFC", "NFC"}:
        raise PlayoffSimError(
            "seeds_by_conf must have exactly AFC and NFC, "
            f"got {sorted(seeds_by_conf.keys())}"
        )
    seeds: Dict[str, List[str]] = {}
    missing: List[str] = []
    for conf in ("AFC", "NFC"):
        lst = list(seeds_by_conf[conf])
        if len(lst) != n:
            raise PlayoffSimError(
                f"{conf}: season {season} needs {n} seeds, got {len(lst)}"
            )
        if len(set(lst)) != len(lst):
            raise PlayoffSimError(f"{conf}: duplicate team in seeds")
        seeds[conf] = lst
        for t in lst:
            if t not in ratings:
                missing.append(t)
    if missing:
        raise PlayoffSimError(
            "ratings missing for seeded teams (not invented): "
            + ", ".join(sorted(set(missing)))
        )
    clean = {t: _num(ratings[t], f"ratings[{t!r}]") for conf in ("AFC", "NFC") for t in seeds[conf]}
    return seeds, clean


def _validate_n_sims(n_sims: Any) -> int:
    try:
        n = int(n_sims)
    except (TypeError, ValueError):
        raise PlayoffSimError(f"n_sims must be an integer, got {n_sims!r}")
    if n < 1:
        raise PlayoffSimError(f"n_sims must be >= 1, got {n}")
    if n > MAX_SIMS:
        raise PlayoffSimError(f"n_sims must be <= {MAX_SIMS}, got {n}")
    return n


def _play_game(
    home: str,
    away: str,
    ratings: Mapping[str, float],
    rng: random.Random,
    *,
    neutral: bool,
    adjustments: Mapping[str, float],
    team_factors: Optional[Mapping[str, Mapping[str, Any]]],
    home_had_bye: bool = False,
    away_had_bye: bool = False,
) -> str:
    """Simulate one game; return the winner."""
    eh = _effective_elo(
        home, ratings, is_home=True, neutral=neutral,
        adjustments=adjustments, team_factors=team_factors,
        had_bye=home_had_bye,
    )
    ea = _effective_elo(
        away, ratings, is_home=False, neutral=neutral,
        adjustments=adjustments, team_factors=team_factors,
        had_bye=away_had_bye,
    )
    # Adjustments are already folded into the effective ratings, so the
    # logistic itself runs with no extra home-field term.
    p_home = 1.0 / (1.0 + 10.0 ** (-(eh - ea) / ELO_SCALE))
    return home if rng.random() < p_home else away


def _simulate_bracket_once(
    seeds: Dict[str, List[str]],
    ratings: Mapping[str, float],
    season: int,
    rng: random.Random,
    adjustments: Mapping[str, float],
    team_factors: Optional[Mapping[str, Mapping[str, Any]]],
) -> Dict[str, Any]:
    """One full bracket realization: per-round winners and game counts."""
    fmt = playoff_format(season)
    byes = fmt["byes"]
    rounds: Dict[str, List[str]] = {"wild_card": [], "divisional": [], "championship": [], "super_bowl": []}
    played_wc = {t: False for conf in ("AFC", "NFC") for t in seeds[conf]}

    wc_winners: Dict[str, List[str]] = {"AFC": [], "NFC": []}
    for m in wild_card_matchups(seeds, season):
        winner = _play_game(
            m["home_team"], m["away_team"], ratings, rng, neutral=False,
            adjustments=adjustments, team_factors=team_factors,
        )
        wc_winners[m["conference"]].append(winner)
        rounds["wild_card"].append(winner)
        played_wc[m["home_team"]] = True
        played_wc[m["away_team"]] = True

    div_winners: Dict[str, List[str]] = {"AFC": [], "NFC": []}
    for m in divisional_matchups(wc_winners, seeds, season):
        winner = _play_game(
            m["home_team"], m["away_team"], ratings, rng, neutral=False,
            adjustments=adjustments, team_factors=team_factors,
            home_had_bye=m["home_seed"] in byes,
            away_had_bye=m["away_seed"] in byes,
        )
        div_winners[m["conference"]].append(winner)
        rounds["divisional"].append(winner)

    conf_champs: Dict[str, str] = {}
    for m in championship_matchup(div_winners, seeds, season):
        winner = _play_game(
            m["home_team"], m["away_team"], ratings, rng, neutral=False,
            adjustments=adjustments, team_factors=team_factors,
        )
        conf_champs[m["conference"]] = winner
        rounds["championship"].append(winner)

    sb_winner = _play_game(
        conf_champs["AFC"], conf_champs["NFC"], ratings, rng, neutral=True,
        adjustments=adjustments, team_factors=team_factors,
    )
    rounds["super_bowl"].append(sb_winner)
    return {"rounds": rounds, "played_wc": played_wc}


def simulate_playoff_bracket(
    seeds_by_conf: Mapping[str, Sequence[str]],
    ratings: Mapping[str, Any],
    season: int,
    n_sims: int = 10000,
    adjustments: Optional[Mapping[str, Any]] = None,
    rng_seed: Optional[int] = None,
    team_factors: Optional[Mapping[str, Mapping[str, Any]]] = None,
) -> Dict[str, Any]:
    """Monte Carlo over an explicit seeded playoff bracket.

    Every game is simulated from the caller's ratings via
    :func:`win_probability`-style Elo logistics (adjustments folded in as
    Elo-point shifts). Per team: ``p_win_wc`` (None for bye teams — they
    play no Wild Card game), ``p_win_div``, ``p_win_conf``, ``p_win_sb``
    (probabilities of *winning a game in* / *winning* each round),
    ``seed``, ``conference``, ``n_sims``.

    Deterministic when ``rng_seed`` is given (one ``random.Random``
    stream). Raises :class:`PlayoffSimError` for missing ratings, wrong
    seed counts, or out-of-range ``n_sims``.
    """
    seeds, clean_ratings = _validate_bracket_inputs(seeds_by_conf, ratings, season)
    n = _validate_n_sims(n_sims)
    adj = resolve_adjustments(adjustments)
    rng = random.Random(rng_seed)
    fmt = playoff_format(season)

    wins = {t: {"wc": 0, "div": 0, "conf": 0, "sb": 0} for conf in ("AFC", "NFC") for t in seeds[conf]}
    played_wc_any = {t: False for conf in ("AFC", "NFC") for t in seeds[conf]}

    for _ in range(n):
        real = _simulate_bracket_once(seeds, clean_ratings, season, rng, adj, team_factors)
        for t in real["rounds"]["wild_card"]:
            wins[t]["wc"] += 1
        for t in real["rounds"]["divisional"]:
            wins[t]["div"] += 1
        for t in real["rounds"]["championship"]:
            wins[t]["conf"] += 1
        wins[real["rounds"]["super_bowl"][0]]["sb"] += 1
        for t, played in real["played_wc"].items():
            played_wc_any[t] = played_wc_any[t] or played

    teams: Dict[str, Dict[str, Any]] = {}
    for conf in ("AFC", "NFC"):
        for i, t in enumerate(seeds[conf]):
            w = wins[t]
            teams[t] = {
                "conference": conf,
                "seed": i + 1,
                "p_win_wc": (w["wc"] / n) if played_wc_any[t] else None,
                "p_win_div": w["div"] / n,
                "p_win_conf": w["conf"] / n,
                "p_win_sb": w["sb"] / n,
                "n_sims": n,
            }
    return {
        "season": season,
        "format": {"n_teams": fmt["n_teams"], "byes": fmt["byes"]},
        "n_sims": n,
        "rng_seed": rng_seed,
        "adjustments_used": adj,
        "teams": teams,
    }


def _validate_season_inputs(
    teams_state: Mapping[str, Mapping[str, Any]],
    remaining_games: Sequence[Sequence[Any]],
    ratings: Mapping[str, Any],
) -> Tuple[Dict[str, Dict[str, float]], List[Tuple[str, str, bool]]]:
    if not teams_state:
        raise PlayoffSimError("teams_state must not be empty")
    clean_ratings: Dict[str, float] = {}
    for team, st in teams_state.items():
        if team not in ratings:
            raise PlayoffSimError(
                f"ratings missing for team {team!r} (not invented)"
            )
        clean_ratings[team] = _num(ratings[team], f"ratings[{team!r}]")
        for key in ("wins", "losses", "ties", "conference", "division"):
            if key not in st:
                raise PlayoffSimError(
                    f"teams_state[{team!r}] missing required key {key!r}"
                )
        if st["conference"] not in ("AFC", "NFC"):
            raise PlayoffSimError(
                f"teams_state[{team!r}]['conference'] must be AFC/NFC"
            )
    games: List[Tuple[str, str, bool]] = []
    for i, g in enumerate(remaining_games or []):
        if len(g) not in (2, 3):
            raise PlayoffSimError(
                f"remaining_games[{i}]: expected (away, home[, neutral]), got {g!r}"
            )
        away, home = g[0], g[1]
        neutral = bool(g[2]) if len(g) == 3 else False
        for t in (away, home):
            if t not in teams_state:
                raise PlayoffSimError(
                    f"remaining_games[{i}]: team {t!r} not in teams_state"
                )
        games.append((away, home, neutral))
    return clean_ratings, games


def simulate_remaining_season(
    teams_state: Mapping[str, Mapping[str, Any]],
    remaining_games: Sequence[Sequence[Any]],
    ratings: Mapping[str, Any],
    season: int,
    n_sims: int = 10000,
    adjustments: Optional[Mapping[str, Any]] = None,
    rng_seed: Optional[int] = None,
    team_factors: Optional[Mapping[str, Mapping[str, Any]]] = None,
) -> Dict[str, Any]:
    """Monte Carlo over the rest of a season from explicit state.

    ``teams_state`` maps each team to ``{"wins","losses","ties",
    "conference","division"}``; ``remaining_games`` is an explicit list of
    ``(away, home[, neutral])`` tuples — teams with no listed games play
    none (no schedule is ever fabricated). Each simulated season: play
    the remaining games, seed via :func:`apply_tiebreakers` (ties that
    survive the implemented steps are decided by the sim RNG — the
    official coin-toss step, applied explicitly and logged), then
    simulate one playoff bracket realization.

    Per team: ``p_make_playoffs``, ``p_win_division``,
    ``p_seed_1``..``p_seed_7``, ``p_win_sb``, ``n_sims``.
    """
    clean_ratings, games = _validate_season_inputs(teams_state, remaining_games, ratings)
    n = _validate_n_sims(n_sims)
    adj = resolve_adjustments(adjustments)
    rng = random.Random(rng_seed)
    fmt = playoff_format(season)
    n_seeds = fmt["seeds_per_conference"]

    counts = {
        t: {"playoffs": 0, "division": 0, "sb": 0, "seeds": {i: 0 for i in range(1, 8)}}
        for t in teams_state
    }

    for _ in range(n):
        rec = {
            t: [float(st["wins"]), float(st["losses"]), float(st["ties"])]
            for t, st in teams_state.items()
        }
        for away, home, neutral in games:
            winner = _play_game(
                home, away, clean_ratings, rng, neutral=neutral,
                adjustments=adj, team_factors=team_factors,
            )
            if winner == home:
                rec[home][0] += 1
                rec[away][1] += 1
            else:
                rec[away][0] += 1
                rec[home][1] += 1

        rows = [
            {
                "team": t,
                "conference": st["conference"],
                "division": st["division"],
                "wins": rec[t][0],
                "losses": rec[t][1],
                "ties": rec[t][2],
            }
            for t, st in teams_state.items()
        ]
        # The sim RNG doubles as the coin-toss RNG for ties the
        # implemented tiebreak steps cannot resolve (official final step,
        # applied explicitly inside apply_tiebreakers and logged).
        tb = apply_tiebreakers(rows, season=season, rng=rng)
        seeds = {conf: list(lst) for conf, lst in tb["seeds"].items()}

        for conf in ("AFC", "NFC"):
            for i, t in enumerate(seeds[conf]):
                counts[t]["playoffs"] += 1
                counts[t]["seeds"][i + 1] += 1
            for t in tb["division_winners"][conf]:
                counts[t]["division"] += 1

        seeds_for_bracket = {
            conf: seeds[conf] for conf in ("AFC", "NFC")
        }
        real = _simulate_bracket_once(seeds_for_bracket, clean_ratings, season, rng, adj, team_factors)
        counts[real["rounds"]["super_bowl"][0]]["sb"] += 1

    teams: Dict[str, Dict[str, Any]] = {}
    for t in teams_state:
        c = counts[t]
        row: Dict[str, Any] = {
            "p_make_playoffs": c["playoffs"] / n,
            "p_win_division": c["division"] / n,
            "p_win_sb": c["sb"] / n,
            "n_sims": n,
        }
        for i in range(1, 8):
            row[f"p_seed_{i}"] = c["seeds"][i] / n
        teams[t] = row
    return {
        "season": season,
        "format": {"n_teams": fmt["n_teams"], "byes": fmt["byes"]},
        "n_sims": n,
        "rng_seed": rng_seed,
        "adjustments_used": adj,
        "n_remaining_games": len(games),
        "teams": teams,
    }
