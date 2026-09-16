"""Playoff-stakes classifier (pure functions).

For each team in each game, compute playoff stakes from the standings as
they stand *before* the game plus remaining-schedule max/min math. No
future data is used: only games already played feed the records, and the
"hypothetical" win/lose checks only perturb the current game.

Clinch/elimination math source: conservative sufficient conditions on
points (``pts = wins + 0.5*ties``), in the spirit of magic/elimination
numbers:

- ``max_pts(T) = pts(T) + remaining(T)`` (win out),
- ``min_pts(T) = pts(T)`` (lose out; further ties assumed not to occur).

Categories
- ``ELIMINATED``: at least 7 conference mates already have ``pts`` strictly
  above ``max_pts(T)`` — seven teams are uncatchable, so T cannot reach the
  7-team field. (Sufficient; truly dead.)
- ``CLINCHED`` (internal): at most 6 conference mates have ``max_pts``
  strictly above ``pts(T)`` — at most six teams can finish ahead of T, so T
  is guaranteed a top-7 spot. Strict ``>`` means exact ties do not clinch
  (tiebreak risk) — conservative by design.
- ``DEAD_RUBBER``: T's seed is locked — no tied win% with anyone, every team
  ranked above has ``pts > max_pts(T)`` (cannot be caught) and every team
  ranked below has ``max_pts < pts(T)`` (cannot catch T). Berth-and-seed
  locked: the classic rest-starters risk. (Implies clinched.)
- ``MUST_WIN``: win-and-in (a win clinches a berth T has not yet clinched),
  lose-and-out (a loss eliminates T), or a week>=15 seeding battle (within
  1.0 pts of an adjacent seed while unlocked).
- ``NORMAL``: everything else.

Priority: ELIMINATED > DEAD_RUBBER > MUST_WIN > NORMAL.

Documented limitations
- NFL tiebreaks (head-to-head, conference record, etc.) are ignored; the
  strict inequalities keep every label conservative (fewer non-NORMAL
  labels, all of them truly earned). A team eliminated purely on tiebreak
  subtleties will read NORMAL — the measurement in
  ``docs/IN_SEASON_RECALIBRATION.md`` is unaffected (conservative labels
  only shrink the non-NORMAL samples).
- ``remaining`` assumes every team plays ``season_games`` (16 for seasons
  <= 2020, 17 after); the cancelled 2022 BUF-CIN game is a 1-game
  overcount for two teams in one week — negligible, noted here.
- Ranks for the seed-lock check order by win% with no tiebreak; any win%
  tie with another team vetoes DEAD_RUBBER.
"""

from __future__ import annotations

from typing import Any, Dict, List, Mapping, Optional, Tuple

#: The four stakes categories, in priority order.
STAKES: Tuple[str, ...] = ("ELIMINATED", "DEAD_RUBBER", "MUST_WIN", "NORMAL")

#: Default playoff field size per conference (14-team era, 2020+).
DEFAULT_PLAYOFF_SLOTS = 7


def _snapshot(
    records: Mapping[str, Mapping[str, Any]], season_games: int
) -> Dict[str, Dict[str, Any]]:
    snap: Dict[str, Dict[str, Any]] = {}
    for team, rec in records.items():
        w = float(rec.get("wins", 0) or 0)
        l = float(rec.get("losses", 0) or 0)
        t = float(rec.get("ties", 0) or 0)
        played = w + l + t
        pts = w + 0.5 * t
        rem = season_games - played
        snap[team] = {
            "conference": rec.get("conference"),
            "pts": pts,
            "played": played,
            "rem": rem,
            "max_pts": pts + rem,
            "winpct": (pts / played) if played > 0 else None,
        }
    return snap


def _is_eliminated(team: str, snap: Mapping[str, Mapping[str, Any]], slots: int) -> bool:
    conf = snap[team]["conference"]
    foes = [u for u in snap if u != team and snap[u]["conference"] == conf]
    return sum(1 for u in foes if snap[u]["pts"] > snap[team]["max_pts"]) >= slots


def _is_clinched(team: str, snap: Mapping[str, Mapping[str, Any]], slots: int) -> bool:
    conf = snap[team]["conference"]
    foes = [u for u in snap if u != team and snap[u]["conference"] == conf]
    return sum(1 for u in foes if snap[u]["max_pts"] > snap[team]["pts"]) <= (slots - 1)


def _seed_locked(team: str, snap: Mapping[str, Mapping[str, Any]]) -> bool:
    s = snap[team]
    if s["winpct"] is None:
        return False
    conf = s["conference"]
    mates = [u for u in snap if u != team and snap[u]["conference"] == conf
             and snap[u]["winpct"] is not None]
    if any(abs(snap[u]["winpct"] - s["winpct"]) < 1e-9 for u in mates):
        return False  # tied win% with someone: seed cannot be locked
    above = [u for u in mates if snap[u]["winpct"] > s["winpct"]]
    below = [u for u in mates if snap[u]["winpct"] < s["winpct"]]
    if any(not (snap[u]["pts"] > s["max_pts"]) for u in above):
        return False
    if any(not (snap[u]["max_pts"] < s["pts"]) for u in below):
        return False
    return True


def _with_result(
    records: Mapping[str, Mapping[str, Any]],
    winner: str,
    loser: str,
) -> Dict[str, Dict[str, Any]]:
    """Records with one hypothetical game applied (winner beats loser)."""
    out: Dict[str, Dict[str, Any]] = {}
    for team, rec in records.items():
        rec = dict(rec)
        if team == winner:
            rec["wins"] = float(rec.get("wins", 0) or 0) + 1
        elif team == loser:
            rec["losses"] = float(rec.get("losses", 0) or 0) + 1
        out[team] = rec
    return out


def classify_stakes(
    team: str,
    opponent: str,
    records: Mapping[str, Mapping[str, Any]],
    season_games: int,
    week: int,
    playoff_slots: int = DEFAULT_PLAYOFF_SLOTS,
) -> Dict[str, Any]:
    """Classify ``team``'s stakes for its game vs ``opponent``.

    ``records`` maps every team to ``{"wins","losses","ties","conference"}``
    as they stand *before* the game. ``season_games`` is 16 (seasons <= 2020)
    or 17. ``playoff_slots`` is 7 (2020+) or 6 (2016-2019). Returns
    ``{"team","stakes","reason","detail"}`` where ``detail`` carries the
    clinch/elimination math inputs for auditability.
    """
    if team not in records or opponent not in records:
        raise ValueError(f"records missing team {team!r} or opponent {opponent!r}")
    if season_games <= 0:
        raise ValueError(f"season_games must be positive, got {season_games!r}")
    if playoff_slots not in (6, 7):
        raise ValueError(f"playoff_slots must be 6 or 7, got {playoff_slots!r}")
    snap = _snapshot(records, season_games)
    conf = snap[team]["conference"]

    eliminated = _is_eliminated(team, snap, playoff_slots)
    clinched = _is_clinched(team, snap, playoff_slots)
    locked = _seed_locked(team, snap)

    def result(stakes: str, reason: str) -> Dict[str, Any]:
        return {
            "team": team,
            "opponent": opponent,
            "stakes": stakes,
            "reason": reason,
            "detail": {
                "week": week,
                "conference": conf,
                "pts": snap[team]["pts"],
                "max_pts": snap[team]["max_pts"],
                "played": snap[team]["played"],
                "eliminated": eliminated,
                "clinched": clinched,
                "seed_locked": locked,
            },
        }

    if eliminated:
        return result("ELIMINATED", "7+ conference teams already above points ceiling")
    if locked:
        return result("DEAD_RUBBER", "seed locked by max/min points bounds (berth and seed fixed)")

    # Hypotheticals for win-and-in / lose-and-out (only the current game perturbed).
    win_snap = _snapshot(_with_result(records, team, opponent), season_games)
    lose_snap = _snapshot(_with_result(records, opponent, team), season_games)
    win_and_in = _is_clinched(team, win_snap, playoff_slots) and not clinched
    lose_and_out = _is_eliminated(team, lose_snap, playoff_slots) and not eliminated

    battle = False
    if week >= 15 and snap[team]["winpct"] is not None:
        mates = sorted(
            (u for u in snap if snap[u]["conference"] == conf and snap[u]["winpct"] is not None),
            key=lambda u: snap[u]["winpct"],
            reverse=True,
        )
        i = mates.index(team)
        adj = []
        if i > 0:
            adj.append(mates[i - 1])
        if i < len(mates) - 1:
            adj.append(mates[i + 1])
        battle = any(abs(snap[u]["pts"] - snap[team]["pts"]) <= 1.0 for u in adj)

    if win_and_in:
        return result("MUST_WIN", "win-and-in: a win clinches a playoff berth")
    if lose_and_out:
        return result("MUST_WIN", "lose-and-out: a loss eliminates the team")
    if battle:
        return result("MUST_WIN", "seeding battle: week>=15, within 1.0 pts of adjacent seed, unlocked")
    return result("NORMAL", "playoff fate not directly decided by this game")
