"""NFL playoff bracket construction + tiebreakers (pure functions).

Format rules (verified):
- 2016-2019: 12 teams (6 per conference); seeds 1-2 get Wild Card byes;
  Wild Card round is 3v6 and 4v5 per conference.
- 2020-present: 14 teams (7 per conference); ONLY the #1 seed gets a bye;
  Wild Card round is 2v7, 3v6, 4v5 per conference.
- Seeds 1-4 are division winners ordered by record; seeds 5+ are wild
  cards by record. Division winners always host Wild Card games.
- After the Wild Card round the bracket is RE-SEEDED: the lowest remaining
  seed visits #1 (2020+; in the 12-team format #1 hosts the lowest
  remaining seed and #2 hosts the other), the other survivors pair off,
  higher seed hosting. The conference championship is hosted by the
  higher seed; the Super Bowl is at a neutral site.

Tiebreak order (verified against the official NFL tiebreaking
procedures): for two clubs in the same division — head-to-head, division
record, common games, conference record, strength of victory, strength of
schedule, then points rankings / net points / net touchdowns and finally
a coin toss. Wild-card ties first apply the division tiebreak inside each
division, then (for 3+ clubs) the head-to-head sweep, conference record,
common games (minimum 4), strength of victory, strength of schedule,
then points rankings / net points / touchdowns and coin toss.

Implemented here: every step through strength of schedule, driven ONLY
by explicit evidence fields on the input rows. Anything beyond strength
of schedule is NOT VERIFIED and is reported honestly as
``"unresolved"`` / ``DATA UNAVAILABLE`` — never decided silently.
"""

from __future__ import annotations

import random
from typing import Any, Dict, List, Mapping, Optional, Sequence, Tuple

#: Seasons using the 12-team format (top-2 seeds get byes).
TWELVE_TEAM_SEASONS = range(2016, 2020)

#: First season of the 14-team format (only the #1 seed gets a bye).
FOURTEEN_TEAM_FIRST_SEASON = 2020

#: Tiebreak steps we implement, in official order, for same-division ties.
_DIVISION_TIEBREAK_STEPS = (
    "head_to_head",
    "division_record",
    "common_games",
    "conference_record",
    "strength_of_victory",
    "strength_of_schedule",
)

#: Tiebreak steps we implement, in official order, for cross-division
#: (division-winner seeding and wild-card) ties.
_CROSS_DIVISION_TIEBREAK_STEPS = (
    "head_to_head",
    "conference_record",
    "common_games",
    "strength_of_victory",
    "strength_of_schedule",
)


def playoff_format(season: int) -> Dict[str, Any]:
    """Return the playoff format for a season.

    ``{"season", "n_teams", "seeds_per_conference", "byes",
    "wild_card_pairs"}`` where ``byes`` are the seed numbers that skip the
    Wild Card round and ``wild_card_pairs`` are ``(higher_seed,
    lower_seed)`` tuples. Raises :class:`ValueError` for seasons before
    2016 (earlier formats are out of scope) or non-integer input.
    """
    if isinstance(season, bool) or not isinstance(season, int):
        raise ValueError(f"season must be an integer, got {season!r}")
    if season < 2016:
        raise ValueError(
            f"playoff formats before 2016 are not supported (got {season})"
        )
    if season in TWELVE_TEAM_SEASONS:
        return {
            "season": season,
            "n_teams": 12,
            "seeds_per_conference": 6,
            "byes": [1, 2],
            "wild_card_pairs": [(3, 6), (4, 5)],
            "era": "2016-2019",
        }
    return {
        "season": season,
        "n_teams": 14,
        "seeds_per_conference": 7,
        "byes": [1],
        "wild_card_pairs": [(2, 7), (3, 6), (4, 5)],
        "era": "2020-present",
    }


def _validate_seeds(seeds_by_conf: Mapping[str, Sequence[str]], season: int) -> Dict[str, List[str]]:
    """Normalize and validate ``{"AFC": [...], "NFC": [...]}`` seed lists.

    Each conference list must be in seed order (index 0 = #1 seed) and
    cover exactly the format's seeds. Raises :class:`ValueError` on any
    mismatch — seeds are never padded or invented.
    """
    fmt = playoff_format(season)
    n = fmt["seeds_per_conference"]
    if set(seeds_by_conf.keys()) != {"AFC", "NFC"}:
        raise ValueError(
            "seeds_by_conf must have exactly the conferences AFC and NFC, "
            f"got {sorted(seeds_by_conf.keys())}"
        )
    out: Dict[str, List[str]] = {}
    for conf in ("AFC", "NFC"):
        seeds = list(seeds_by_conf[conf])
        if len(seeds) != n:
            raise ValueError(
                f"{conf}: season {season} needs {n} seeds, got {len(seeds)}"
            )
        if len(set(seeds)) != len(seeds):
            raise ValueError(f"{conf}: duplicate team in seeds: {seeds}")
        out[conf] = seeds
    return out


def _seed_of(team: str, seeds: Sequence[str]) -> int:
    return list(seeds).index(team) + 1


def wild_card_matchups(
    seeds_by_conf: Mapping[str, Sequence[str]], season: int
) -> List[Dict[str, Any]]:
    """Concrete Wild Card round matchups.

    Higher seed hosts (division winners always host). Each matchup is
    ``{"conference", "round": "wild_card", "home_team", "away_team",
    "home_seed", "away_seed", "neutral": False}``.
    """
    seeds = _validate_seeds(seeds_by_conf, season)
    fmt = playoff_format(season)
    matchups: List[Dict[str, Any]] = []
    for conf in ("AFC", "NFC"):
        for hi, lo in fmt["wild_card_pairs"]:
            matchups.append(
                {
                    "conference": conf,
                    "round": "wild_card",
                    "home_team": seeds[conf][hi - 1],
                    "away_team": seeds[conf][lo - 1],
                    "home_seed": hi,
                    "away_seed": lo,
                    "neutral": False,
                }
            )
    return matchups


def divisional_matchups(
    wc_winners_by_conf: Mapping[str, Sequence[str]],
    seeds_by_conf: Mapping[str, Sequence[str]],
    season: int,
) -> List[Dict[str, Any]]:
    """Divisional round matchups after re-seeding.

    ``wc_winners_by_conf`` maps each conference to its Wild Card winners.
    The bye seeds re-enter and the survivors are re-seeded by original
    seed number: the lowest remaining seed visits the #1 seed (14-team
    format); in the 12-team format #1 hosts the lowest remaining seed
    and #2 hosts the other survivor. Higher seed hosts every game.
    """
    seeds = _validate_seeds(seeds_by_conf, season)
    fmt = playoff_format(season)
    byes = fmt["byes"]
    n_wc_games = len(fmt["wild_card_pairs"])
    matchups: List[Dict[str, Any]] = []
    for conf in ("AFC", "NFC"):
        winners = list(wc_winners_by_conf.get(conf, []))
        if len(winners) != n_wc_games:
            raise ValueError(
                f"{conf}: expected {n_wc_games} Wild Card winners, "
                f"got {len(winners)}"
            )
        remaining = [( _seed_of(t, seeds[conf]), t) for t in winners]
        for b in byes:
            remaining.append((b, seeds[conf][b - 1]))
        remaining.sort(key=lambda x: x[0])
        # Pair highest remaining seed vs lowest remaining seed, then the
        # middle two (higher seed hosts each game).
        ordered = [t for _, t in remaining]
        pairs = [(ordered[0], ordered[-1])]
        middle = ordered[1:-1]
        if middle:
            pairs.append((middle[0], middle[-1]))
        for hi_team, lo_team in pairs:
            hi_seed, lo_seed = _seed_of(hi_team, seeds[conf]), _seed_of(lo_team, seeds[conf])
            if hi_seed > lo_seed:
                hi_team, lo_team, hi_seed, lo_seed = lo_team, hi_team, lo_seed, hi_seed
            matchups.append(
                {
                    "conference": conf,
                    "round": "divisional",
                    "home_team": hi_team,
                    "away_team": lo_team,
                    "home_seed": hi_seed,
                    "away_seed": lo_seed,
                    "neutral": False,
                }
            )
    return matchups


def championship_matchup(
    div_winners_by_conf: Mapping[str, Sequence[str]],
    seeds_by_conf: Mapping[str, Sequence[str]],
    season: int,
) -> List[Dict[str, Any]]:
    """Conference championship games: the two divisional winners per
    conference, higher seed hosting."""
    seeds = _validate_seeds(seeds_by_conf, season)
    matchups: List[Dict[str, Any]] = []
    for conf in ("AFC", "NFC"):
        winners = list(div_winners_by_conf.get(conf, []))
        if len(winners) != 2:
            raise ValueError(
                f"{conf}: expected 2 divisional winners, got {len(winners)}"
            )
        a, b = winners
        if _seed_of(a, seeds[conf]) > _seed_of(b, seeds[conf]):
            a, b = b, a
        matchups.append(
            {
                "conference": conf,
                "round": "championship",
                "home_team": a,
                "away_team": b,
                "home_seed": _seed_of(a, seeds[conf]),
                "away_seed": _seed_of(b, seeds[conf]),
                "neutral": False,
            }
        )
    return matchups


def build_bracket_rounds(
    seeds_by_conf: Mapping[str, Sequence[str]], season: int
) -> Dict[str, Any]:
    """Describe the playoff bracket for explicit seeds.

    Returns the format, the concrete Wild Card matchups, the bye teams,
    and the reseeding procedure for the later rounds (which cannot be
    made concrete until winners are known — the Monte Carlo engine
    applies :func:`divisional_matchups` / :func:`championship_matchup`
    per simulated realization instead of guessing).
    """
    seeds = _validate_seeds(seeds_by_conf, season)
    fmt = playoff_format(season)
    return {
        "season": season,
        "format": fmt,
        "wild_card": wild_card_matchups(seeds, season),
        "byes": {
            conf: [seeds[conf][b - 1] for b in fmt["byes"]] for conf in ("AFC", "NFC")
        },
        "reseeding": (
            "After the Wild Card round the bracket is re-seeded within each "
            "conference by original seed number: the lowest remaining seed "
            f"visits the #{fmt['byes'][0]} seed and the other survivors pair "
            "off, higher seed hosting. Conference championships are hosted "
            "by the higher seed; the Super Bowl is at a neutral site."
        ),
        "rounds": ["wild_card", "divisional", "championship", "super_bowl"],
    }


# ---------------------------------------------------------------------------
# Tiebreakers
# ---------------------------------------------------------------------------


def _win_pct(row: Mapping[str, Any]) -> float:
    w = float(row.get("wins", 0) or 0)
    l = float(row.get("losses", 0) or 0)
    t = float(row.get("ties", 0) or 0)
    games = w + l + t
    if games <= 0:
        return 0.0
    return (w + 0.5 * t) / games


def _rec_pct(rec: Optional[Mapping[str, Any]]) -> Optional[float]:
    """Win pct from an explicit {"w","l","t"} record, or None when the
    evidence was not supplied."""
    if not isinstance(rec, Mapping):
        return None
    try:
        w = float(rec.get("w", 0) or 0)
        l = float(rec.get("l", 0) or 0)
        t = float(rec.get("t", 0) or 0)
    except (TypeError, ValueError):
        return None
    games = w + l + t
    if games <= 0:
        return None
    return (w + 0.5 * t) / games


def _step_score(
    step: str, team: str, clubs: Sequence[str], rows: Mapping[str, Mapping[str, Any]]
) -> Optional[float]:
    """Score for one club at one tiebreak step, or None when the required
    explicit evidence is missing (DATA UNAVAILABLE — never invented)."""
    row = rows[team]
    ev = row.get("evidence") or {}
    if step == "head_to_head":
        h2h = ev.get("h2h")
        if not isinstance(h2h, Mapping):
            return None
        # Evidence: {"OPP": "W"/"L"} results vs each tied opponent.
        vals = []
        for opp in clubs:
            if opp == team:
                continue
            r = h2h.get(opp)
            if r == "W":
                vals.append(1.0)
            elif r == "L":
                vals.append(0.0)
            elif r == "T":
                vals.append(0.5)
            else:
                return None
        if not vals:
            return None
        return sum(vals) / len(vals)
    if step == "division_record":
        return _rec_pct(ev.get("div"))
    if step == "common_games":
        return _rec_pct(ev.get("common"))
    if step == "conference_record":
        return _rec_pct(ev.get("conf"))
    if step == "strength_of_victory":
        v = ev.get("sov")
        return float(v) if isinstance(v, (int, float)) else None
    if step == "strength_of_schedule":
        v = ev.get("sos")
        return float(v) if isinstance(v, (int, float)) else None
    return None


def _break_tie(
    clubs: List[str],
    rows: Mapping[str, Mapping[str, Any]],
    steps: Sequence[str],
    scope: str,
    log: List[Dict[str, Any]],
    rng: Optional[random.Random] = None,
) -> Tuple[List[str], List[str]]:
    """Order tied clubs via the tiebreak pipeline.

    Returns ``(ordered, unresolved)``. ``ordered`` is best-first; clubs
    still tied after every implemented step land in ``unresolved`` with a
    NOT VERIFIED / DATA UNAVAILABLE reason in the log — unless ``rng``
    is given, in which case the official final step (coin toss) is
    applied explicitly with that RNG, logged as ``"coin_toss"``, and
    ``unresolved`` is empty. The default (``rng=None``) never decides a
    tie silently.
    """
    remaining = list(clubs)
    ordered: List[str] = []
    unresolved: List[str] = []
    while remaining:
        if len(remaining) == 1:
            ordered.append(remaining[0])
            break
        decided = False
        for step in steps:
            scores: Dict[str, float] = {}
            missing = [t for t in remaining if _step_score(step, t, remaining, rows) is None]
            if missing:
                log.append(
                    {
                        "scope": scope,
                        "clubs": list(remaining),
                        "step": step,
                        "resolution": "skipped_no_evidence",
                        "detail": (
                            "DATA UNAVAILABLE: tiebreak evidence for step "
                            f"'{step}' not supplied for {missing}; "
                            "step skipped, never invented."
                        ),
                    }
                )
                continue
            if step == "head_to_head" and len(remaining) > 2:
                # Multi-club head-to-head requires the sweep rule; only a
                # clean sweep (one club beat every other tied club) is
                # applied here.
                for t in remaining:
                    if all(_step_score(step, t, [t, o], rows) == 1.0 for o in remaining if o != t):
                        log.append(
                            {
                                "scope": scope,
                                "clubs": list(remaining),
                                "step": step,
                                "resolution": "ordered",
                                "detail": f"{t} swept the tied clubs head-to-head.",
                            }
                        )
                        ordered.append(t)
                        remaining = [c for c in remaining if c != t]
                        decided = True
                        break
                if decided:
                    break
                log.append(
                    {
                        "scope": scope,
                        "clubs": list(remaining),
                        "step": step,
                        "resolution": "skipped_no_evidence",
                        "detail": (
                            "NOT VERIFIED: multi-club head-to-head sweep "
                            "semantics beyond a clean sweep are not "
                            "implemented; step skipped."
                        ),
                    }
                )
                continue
            scores = {t: _step_score(step, t, remaining, rows) for t in remaining}  # type: ignore[misc]
            best = max(scores.values())
            leaders = [t for t in remaining if scores[t] == best]
            if len(leaders) < len(remaining):
                log.append(
                    {
                        "scope": scope,
                        "clubs": list(remaining),
                        "step": step,
                        "resolution": "ordered",
                        "detail": f"resolved at step '{step}': {leaders} ahead.",
                    }
                )
                ordered.extend(leaders)
                remaining = [t for t in remaining if t not in leaders]
                decided = True
                break
            # Step did not separate the clubs; keep them tied, try next.
        if not decided:
            if rng is not None:
                toss = list(remaining)
                rng.shuffle(toss)
                log.append(
                    {
                        "scope": scope,
                        "clubs": list(remaining),
                        "step": "coin_toss",
                        "resolution": "coin_toss",
                        "detail": (
                            "Tie survived through strength of schedule; "
                            "decided by an explicit RNG coin toss (the "
                            "official final tiebreak step)."
                        ),
                    }
                )
                ordered.extend(toss)
            else:
                log.append(
                    {
                        "scope": scope,
                        "clubs": list(remaining),
                        "step": "beyond_strength_of_schedule",
                        "resolution": "unresolved",
                        "detail": (
                            "NOT VERIFIED: tie survives through strength of "
                            "schedule; points rankings / net points / net "
                            "touchdowns / coin toss are not implemented. "
                            "Order among these clubs is UNRESOLVED."
                        ),
                    }
                )
                unresolved.extend(remaining)
            break
    return ordered, unresolved


def _validate_standings(
    standings_rows: Sequence[Mapping[str, Any]],
) -> Dict[str, Mapping[str, Any]]:
    rows: Dict[str, Mapping[str, Any]] = {}
    for i, row in enumerate(standings_rows):
        team = row.get("team")
        conf = row.get("conference")
        div = row.get("division")
        if not team or conf not in ("AFC", "NFC") or not div:
            raise ValueError(
                f"standings row {i}: 'team', 'conference' (AFC/NFC) and "
                f"'division' are required, got {dict(row)}"
            )
        if team in rows:
            raise ValueError(f"duplicate team in standings: {team}")
        rows[team] = row
    return rows


def apply_tiebreakers(
    standings_rows: Sequence[Mapping[str, Any]],
    season: Optional[int] = None,
    rng: Optional[random.Random] = None,
) -> Dict[str, Any]:
    """Order teams into playoff seeds from explicit standings rows.

    Each row needs ``team``, ``conference`` (``"AFC"``/``"NFC"``),
    ``division``, ``wins``, ``losses``, ``ties``. Optional ``evidence``
    mapping carries the tiebreak inputs (``h2h``, ``div``, ``common``,
    ``conf``, ``sov``, ``sos`` — see :func:`_step_score`); steps whose
    evidence is missing are skipped as DATA UNAVAILABLE, never guessed.

    Procedure per conference: order each division by record (same-division
    tiebreak pipeline), take the four division winners, seed them 1-4 by
    record (cross-division pipeline for ties), then seed the wild cards
    5-7 (5-6 in the 12-team format) by record with the wild-card
    pipeline. Returns ``{"status", "season", "seeds", "division_winners",
    "tiebreak_log", "unresolved"}``; ``status`` is ``"partial"`` when any
    tie could not be resolved with the implemented steps.

    When ``rng`` is given, ties surviving the implemented steps are
    decided by an explicit coin toss with that RNG (the official final
    step), logged as ``"coin_toss"``; otherwise they are reported
    honestly as unresolved.
    """
    if season is not None:
        fmt = playoff_format(season)  # validates the season
        n_seeds = fmt["seeds_per_conference"]
    else:
        n_seeds = 7
    rows = _validate_standings(standings_rows)
    log: List[Dict[str, Any]] = []
    unresolved_all: List[Dict[str, Any]] = []
    seeds: Dict[str, List[str]] = {}
    div_winners: Dict[str, List[str]] = {}

    for conf in ("AFC", "NFC"):
        conf_rows = {t: r for t, r in rows.items() if r.get("conference") == conf}
        divisions: Dict[str, List[str]] = {}
        for t, r in conf_rows.items():
            divisions.setdefault(str(r.get("division")), []).append(t)

        # 1. Order within each division.
        ordered_divs: Dict[str, List[str]] = {}
        for div, clubs in divisions.items():
            by_pct: Dict[float, List[str]] = {}
            for t in clubs:
                by_pct.setdefault(_win_pct(rows[t]), []).append(t)
            div_order: List[str] = []
            for pct in sorted(by_pct, reverse=True):
                group = by_pct[pct]
                if len(group) == 1:
                    div_order.extend(group)
                else:
                    o, u = _break_tie(
                        group, rows, _DIVISION_TIEBREAK_STEPS,
                        f"{conf} {div} division title", log, rng,
                    )
                    div_order.extend(o)
                    if u:
                        unresolved_all.append(
                            {"conference": conf, "scope": f"{div} division title", "clubs": u}
                        )
                        div_order.extend(u)  # keep input-adjacent order; flagged above
            ordered_divs[div] = div_order

        winners = [ordered_divs[d][0] for d in sorted(ordered_divs)]
        # 2. Seed division winners 1-4 by record (cross-division pipeline).
        by_pct = {}
        for t in winners:
            by_pct.setdefault(_win_pct(rows[t]), []).append(t)
        winner_order: List[str] = []
        for pct in sorted(by_pct, reverse=True):
            group = by_pct[pct]
            if len(group) == 1:
                winner_order.extend(group)
            else:
                o, u = _break_tie(
                    group, rows, _CROSS_DIVISION_TIEBREAK_STEPS,
                    f"{conf} division-winner seeding", log, rng,
                )
                winner_order.extend(o)
                if u:
                    unresolved_all.append(
                        {"conference": conf, "scope": "division-winner seeding", "clubs": u}
                    )
                    winner_order.extend(u)

        # 3. Wild cards: non-winners by record. Within each record group
        #    (best first), award spots ONE AT A TIME following the official
        #    multi-club procedure: order each division subgroup with the
        #    division pipeline (that order is kept for subsequent rounds),
        #    take each division's top remaining club as the candidates,
        #    order the candidates with the cross-division pipeline, award
        #    the next spot to the winner, then re-apply to the remaining
        #    clubs with the winner's division advanced to its next club.
        #    (2020 AFC: BAL beats CLE in the North subgroup for the #5
        #    candidates; BAL takes #5; CLE re-enters and beats IND for #6.
        #    The old code discarded CLE entirely — wrong.)
        rest = [t for t in conf_rows if t not in winners]
        by_pct = {}
        for t in rest:
            by_pct.setdefault(_win_pct(rows[t]), []).append(t)
        wc_order: List[str] = []
        wc_spots = n_seeds - 4
        for pct in sorted(by_pct, reverse=True):
            spots_left = wc_spots - len(wc_order)
            if spots_left <= 0:
                break
            group = by_pct[pct]
            subgroups: Dict[str, List[str]] = {}
            for t in group:
                subgroups.setdefault(str(rows[t].get("division")), []).append(t)
            sub_orders: Dict[str, List[str]] = {}
            for div, sub in subgroups.items():
                if len(sub) == 1:
                    sub_orders[div] = list(sub)
                else:
                    o, u = _break_tie(
                        sub, rows, _DIVISION_TIEBREAK_STEPS,
                        f"{conf} {div} wild-card ordering", log, rng,
                    )
                    # The division subgroup order stands for all subsequent
                    # rounds; unresolvable clubs stay adjacent, flagged.
                    sub_orders[div] = o + u
                    if u:
                        unresolved_all.append(
                            {"conference": conf,
                             "scope": f"{div} wild-card ordering",
                             "clubs": u}
                        )
            next_idx: Dict[str, int] = {div: 0 for div in sub_orders}
            take = min(len(group), spots_left)
            for _ in range(take):
                candidates = [
                    sub_orders[div][next_idx[div]]
                    for div in sub_orders
                    if next_idx[div] < len(sub_orders[div])
                ]
                if len(candidates) == 1:
                    spot_order = candidates
                    spot_unresolved: List[str] = []
                else:
                    spot_order, spot_unresolved = _break_tie(
                        candidates, rows, _CROSS_DIVISION_TIEBREAK_STEPS,
                        f"{conf} wild-card seeding", log, rng,
                    )
                if spot_unresolved:
                    # Cannot fairly award one spot: seat every candidate in
                    # tiebreak order, flagged honestly, and stop this group.
                    unresolved_all.append(
                        {"conference": conf, "scope": "wild-card seeding",
                         "clubs": spot_unresolved}
                    )
                    for t in spot_order + spot_unresolved:
                        if t not in wc_order:
                            wc_order.append(t)
                            d = str(rows[t].get("division"))
                            if d in next_idx:
                                next_idx[d] += 1
                    break
                spot_winner = spot_order[0]
                wc_order.append(spot_winner)
                next_idx[str(rows[spot_winner].get("division"))] += 1

        seeds[conf] = (winner_order + wc_order)[:n_seeds]
        div_winners[conf] = winner_order

    return {
        "status": "partial" if unresolved_all else "ok",
        "season": season,
        "seeds": seeds,
        "division_winners": div_winners,
        "tiebreak_log": log,
        "unresolved": unresolved_all,
    }
