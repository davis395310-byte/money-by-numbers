"""Point-in-time feature engineering for MONEY BY NUMBERS NFL models.

THE KICKOFF RULE (product spec section 26, hard requirement):
    Every feature value for a game may only use information available
    strictly before that game's kickoff. No exceptions.

Implementation: a single chronological pass over games sorted by kickoff.
Per-team state (Elo, recent scores, streaks, head-to-head) is updated only
*after* a game is final, and games sharing an identical kickoff timestamp
are computed from a snapshot taken before any of them is applied. The
frame also carries ``_data_through`` — the latest kickoff timestamp of any
game that contributed to the row's features — so the leakage harness
(``ml.leakage``) can audit the invariant ``_data_through < kickoff``.

Feature groups implemented (spec section 25):
    - team_strength : margin-aware Elo (538-style), home/away components.
    - recent_form   : last-5 scoring differentials (offense/defense/net).
    - matchup       : head-to-head margin over the last 3 meetings.
    - situational   : rest differential, divisional game, dome, neutral site,
                      week number, win streaks.

NOT yet wired into training (Phase 5 hooks — data now available):
    - injuries      : ``injury_context_features()`` documents the hook.
                      Official weekly injury reports land in the
                      ``injuries`` collection (nflverse release, 2009+).
                      NOT used in training until a full walk-forward
                      backtest (spec 72) validates them — the stub below
                      returns neutral defaults and must never be fed to a
                      model as if it were real signal.
    - weather       : ``weather_context_features()`` documents the hook.
                      Kickoff-hour venue conditions land in the ``weather``
                      collection (Open-Meteo). Same rule: neutral defaults
                      until validated by walk-forward.
    - market        : odds features deliberately EXCLUDED from the feature
                      set. Closing lines are used only as the ATS benchmark
                      (see ml.backtest), never as inputs.

No fabricated values: when a team has no prior games, differential
features are 0.0 (neutral prior) — never invented statistics.
"""

from __future__ import annotations

import math
from typing import Any

import pandas as pd

# ---------------------------------------------------------------------------
# Elo engine (margin-aware, 538-style). Same parameterization as the
# validated v1 baseline: K=20, scale=400, HFA fit at 35 Elo points.
# ---------------------------------------------------------------------------

ELO_START = 1500.0
ELO_K = 20.0
ELO_SCALE = 400.0
ELO_HFA = 35.0  # fit by log-loss grid search on 2016+ pre-game diffs (v1)


def elo_expected(elo_diff: float) -> float:
    """Expected score for a team with ``elo_diff`` Elo advantage."""
    return 1.0 / (1.0 + 10.0 ** (-elo_diff / ELO_SCALE))


def elo_mov_multiplier(margin: float, elo_diff: float) -> float:
    """Margin-of-victory multiplier (538 formula)."""
    return (abs(margin) + 3.0) ** 0.8 / (7.5 + 0.006 * abs(elo_diff))


def elo_update(
    home_elo: float, away_elo: float, margin: float, hfa: float = ELO_HFA
) -> tuple[float, float]:
    """Update Elo ratings after a game.

    ``margin`` is the home-team point margin (positive = home won).
    ``hfa`` shifts only the *expectation* (home teams are expected to do
    better at home); the stored ratings stay home-neutral, so no HFA
    leaks into the ratings themselves. Zero-sum: rating points gained by
    one team are lost by the other. Ties change nothing.
    """
    diff = (home_elo - away_elo) + hfa
    expected_home = elo_expected(diff)
    k = ELO_K * elo_mov_multiplier(margin, diff)
    if margin > 0:
        return home_elo + k * (1.0 - expected_home), away_elo - k * (1.0 - expected_home)
    if margin < 0:
        return home_elo - k * expected_home, away_elo + k * expected_home
    return home_elo, away_elo


def elo_win_probability(elo_diff: float, home: bool = True) -> float:
    """Win probability from an Elo diff; adds HFA for the home team."""
    return elo_expected(elo_diff + (ELO_HFA if home else 0.0))


# ---------------------------------------------------------------------------
# Feature schema
# ---------------------------------------------------------------------------

FEATURE_NAMES: list[str] = [
    "elo_diff",        # home_elo - away_elo (no HFA baked in; model learns it)
    "off_form_diff",   # avg pts scored last-5: home - away
    "def_form_diff",   # avg pts allowed last-5: away - home (positive = home D better)
    "net_form_diff",   # avg margin last-5: home - away
    "streak_diff",     # signed win streak: home - away
    "rest_diff",       # days rest: home - away
    "div_game",        # divisional matchup 0/1
    "dome",            # roof in (dome, closed) 0/1
    "neutral",         # neutral-site game 0/1
    "h2h_margin_avg",  # avg home-perspective margin, last 3 meetings
    "week",            # season week number
]

#: Columns every built feature table carries (schema contract).
REQUIRED_JOIN_KEYS: tuple[str, ...] = ("game_id", "season", "week", "home_team", "away_team")


def list_feature_names() -> list[str]:
    """Return the concrete feature column names for the current model."""
    return list(FEATURE_NAMES)


class _TeamState:
    """Pre-kickoff state for one team. Updated only after finals."""

    __slots__ = ("elo", "margins", "points_for", "points_against", "streak")

    def __init__(self) -> None:
        self.elo = ELO_START
        self.margins: list[float] = []        # team-perspective margins, chronological
        self.points_for: list[float] = []
        self.points_against: list[float] = []
        self.streak = 0                        # signed: +wins / -losses


def _avg_last(values: list[float], n: int) -> float:
    if not values:
        return 0.0
    tail = values[-n:]
    return sum(tail) / len(tail)


def build_feature_frame(games: pd.DataFrame) -> pd.DataFrame:
    """Build point-in-time features for every game in ``games``.

    Args:
        games: Normalized games frame from ``ml.data_loader.load_games``
            (must contain ``kickoff``, ``home_team``, ``away_team``,
            ``home_score``, ``away_score``, ``status``).

    Returns:
        DataFrame with ``REQUIRED_JOIN_KEYS`` + ``FEATURE_NAMES`` +
        ``_data_through`` (latest contributing kickoff, for the leakage
        audit) + ``kickoff``. One row per input game, in input order.
        Elo and rolling features are computed over ALL games in the frame
        (including postseason if present); callers filter first.

    The kickoff rule is structural: state updates from a game are applied
    only after every game with an equal-or-earlier kickoff has had its
    features computed.
    """
    games = games.sort_values("kickoff", kind="mergesort").reset_index(drop=True)

    teams: dict[str, _TeamState] = {}
    h2h: dict[frozenset, list[tuple[Any, float]]] = {}  # pair -> [(kickoff, home_margin)]

    def state(abbr: str) -> _TeamState:
        if abbr not in teams:
            teams[abbr] = _TeamState()
        return teams[abbr]

    rows: list[dict[str, Any]] = []
    # Latest kickoff strictly before the current batch: upper bound on
    # contributing data (nothing newer could have been seen).
    prev_kickoff: Any = None

    kickoffs = games["kickoff"].tolist()
    i = 0
    n = len(games)
    while i < n:
        j = i
        # Batch games sharing an identical kickoff timestamp.
        while j < n and kickoffs[j] == kickoffs[i]:
            j += 1
        batch = games.iloc[i:j]

        for row in batch.itertuples(index=False):
            home, away = row.home_team, row.away_team
            hs, aws = state(home), state(away)

            pair = frozenset((home, away))
            past = h2h.get(pair, [])
            h2h_vals = []
            for _ko, past_home_margin, past_home in past[-3:]:
                # Convert to current-home perspective.
                h2h_vals.append(past_home_margin if past_home == home else -past_home_margin)

            rows.append(
                {
                    "game_id": row.game_id,
                    "season": row.season,
                    "week": row.week,
                    "home_team": home,
                    "away_team": away,
                    "kickoff": row.kickoff,
                    "_data_through": prev_kickoff,
                    "elo_diff": hs.elo - aws.elo,
                    "off_form_diff": _avg_last(hs.points_for, 5) - _avg_last(aws.points_for, 5),
                    "def_form_diff": _avg_last(aws.points_against, 5) - _avg_last(hs.points_against, 5),
                    "net_form_diff": _avg_last(hs.margins, 5) - _avg_last(aws.margins, 5),
                    "streak_diff": hs.streak - aws.streak,
                    "rest_diff": (row.home_rest if row.home_rest is not None else 7)
                    - (row.away_rest if row.away_rest is not None else 7),
                    "div_game": 1 if row.div_game == 1 else 0,
                    "dome": 1 if str(row.roof or "") in ("dome", "closed") else 0,
                    "neutral": 1 if str(row.location or "") == "Neutral" else 0,
                    "h2h_margin_avg": sum(h2h_vals) / len(h2h_vals) if h2h_vals else 0.0,
                    "week": int(row.week),
                }
            )

        # Now apply the batch's results to the state (finals only).
        for row in batch.itertuples(index=False):
            if row.status != "final" or row.home_score is None or row.away_score is None:
                continue
            home, away = row.home_team, row.away_team
            hs, aws = state(home), state(away)
            margin = float(row.home_score - row.away_score)
            # HFA shifts the expectation only; stored ratings stay
            # home-neutral (see elo_update).
            hs.elo, aws.elo = elo_update(hs.elo, aws.elo, margin)
            hs.margins.append(margin)
            aws.margins.append(-margin)
            hs.points_for.append(float(row.home_score))
            hs.points_against.append(float(row.away_score))
            aws.points_for.append(float(row.away_score))
            aws.points_against.append(float(row.home_score))
            hs.streak = hs.streak + 1 if margin > 0 else (-1 if margin < 0 else 0)
            aws.streak = aws.streak + 1 if margin < 0 else (-1 if margin > 0 else 0)
            h2h.setdefault(frozenset((home, away)), []).append((row.kickoff, margin, home))

        prev_kickoff = kickoffs[i]
        i = j

    frame = pd.DataFrame.from_records(rows)
    # Restore the caller's row order (by game_id).
    order = {gid: k for k, gid in enumerate(games["game_id"].tolist())}
    frame["_order"] = frame["game_id"].map(order)
    frame = frame.sort_values("_order").drop(columns=["_order"]).reset_index(drop=True)
    return frame


# ---------------------------------------------------------------------------
# Phase 5 context-feature hooks (NOT wired into training)
# ---------------------------------------------------------------------------
# These functions document the shape that injury/weather context features
# WILL take once validated. They return neutral defaults and MUST NOT be
# used as model inputs: spec section 72 requires a full 2016-2025
# walk-forward backtest before any new feature enters the champion model.
# Feeding these stubs to a model would silently inject zeros — that is why
# they are separate from FEATURE_NAMES and build_feature_frame.

#: Documented future feature names (not in FEATURE_NAMES).
CONTEXT_FEATURE_NAMES: list[str] = [
    "n_out",           # players listed Out across both teams (injury report)
    "n_doubtful",      # players listed Doubtful
    "n_questionable",  # players listed Questionable
    "temp_f",          # kickoff temperature (F) at venue, None if dome/unknown
    "wind_mph",        # kickoff wind (mph), None if dome/unknown
    "precip_prob",     # precipitation probability 0-1, None if dome/unknown
    "is_dome",         # 1.0 dome/closed, 0.0 outdoor, None unknown
]


def injury_context_features(game_id: str, injuries_collection: object = None) -> dict:
    """Documented hook: injury-report context for one game.

    DO NOT USE IN TRAINING. Returns neutral defaults; the real
    implementation (counting Out/Doubtful/Questionable from the
    ``injuries`` collection for the game's season/week/teams) is pending
    walk-forward validation per spec 72.
    """
    _ = (game_id, injuries_collection)  # hook signature only
    return {"n_out": 0, "n_doubtful": 0, "n_questionable": 0}


def weather_context_features(game_id: str, weather_collection: object = None) -> dict:
    """Documented hook: kickoff weather context for one game.

    DO NOT USE IN TRAINING. Returns neutral defaults; the real
    implementation (reading the ``weather`` collection row for the game)
    is pending walk-forward validation per spec 72. ``None`` means
    unknown/dome — never invent a temperature.
    """
    _ = (game_id, weather_collection)  # hook signature only
    return {"temp_f": None, "wind_mph": None, "precip_prob": None, "is_dome": None}
