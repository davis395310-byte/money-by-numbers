"""Post-game pick settlement (Phase 6, spec section 38).

``run_settlement`` grades every locked pick whose game is final and writes
one record per pick into the ``pick_results`` collection. It is idempotent:
re-running settles the same picks to the same values and never
double-counts (unique index on ``prediction_id``; upsert by that key).

The ``predictions`` ledger is NEVER modified here — settlement results
live only in ``pick_results``.

Grading rules (all from actual final scores, never invented):

- **moneyline**: ``predicted_winner == actual_winner`` -> win; a tied game
  -> push; otherwise loss.
- **spread**: ``market_line_at_pick`` is quoted from the HOME perspective
  (e.g. -3.5 means the home team is favored by 3.5). Cover margin for the
  picked side: ``home: (home_score - away_score) + line``,
  ``away: (away_score - home_score) - line``. ``> 0`` -> win, ``== 0`` ->
  push, ``< 0`` -> loss. Missing line -> ``"ungraded"`` (never guessed).
- **total**: over/under vs ``market_line_at_pick`` on total points scored.
  ``>`` wins for over (``<`` for under), equality -> push, missing line ->
  ``"ungraded"``.

CLV (closing-line value) compares the pick-time line/price with the
closing line/price — the latest odds snapshot at or before kickoff:

- spread/total: ``clv_points = closing_line - line_at_pick``; ``clv_favorable``
  is True when the move helped the picked side:
  - spread home: closing_line < line_at_pick (got points)
  - spread away: closing_line > line_at_pick
  - total over: closing_line < line_at_pick (got a lower total)
  - total under: closing_line > line_at_pick
- moneyline: prices -> raw implied probabilities;
  ``clv_prob_points = implied_close - implied_pick`` for the picked side,
  favorable when positive. ``None`` whenever either price is missing.

Brier contribution is recorded for moneyline picks only:
``(p - 1)^2`` on a win, ``(p - 0)^2`` on a loss, ``None`` on a push.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from ..data_providers.base import utcnow

RESULT_WIN = "win"
RESULT_LOSS = "loss"
RESULT_PUSH = "push"
RESULT_UNGRADED = "ungraded"


class SettlementError(Exception):
    """Raised when settlement cannot run (e.g. no database)."""


def _implied(price: Any) -> Optional[float]:
    """Raw implied probability from an American-odds price; None if missing."""
    if price is None:
        return None
    try:
        a = float(price)
    except (TypeError, ValueError):
        return None
    if a == 0:
        return None
    if a < 0:
        return -a / (-a + 100.0)
    return 100.0 / (a + 100.0)


def grade_pick(pick: Dict[str, Any], game: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    """Grade one locked pick against a final game.

    Returns ``None`` when the game is not final or scores are missing —
    the pick stays pending. Otherwise returns a result dict (without DB
    fields) with ``result`` in {win, loss, push, ungraded}.
    """
    if game.get("status") != "final":
        return None
    home_score = game.get("home_score")
    away_score = game.get("away_score")
    if home_score is None or away_score is None:
        return None

    market = (pick.get("market") or "moneyline").lower()
    side = (pick.get("side") or "").lower()
    predicted_winner = pick.get("predicted_winner")
    home_team = pick.get("home_team")
    away_team = pick.get("away_team")
    model_p = pick.get("model_probability")
    line = pick.get("market_line_at_pick")

    if home_score > away_score:
        actual_winner = home_team
    elif away_score > home_score:
        actual_winner = away_team
    else:
        actual_winner = None  # tie -> push on moneyline

    result: str = RESULT_UNGRADED
    brier: Optional[float] = None

    if market == "moneyline":
        if actual_winner is None:
            result = RESULT_PUSH
        elif predicted_winner == actual_winner:
            result = RESULT_WIN
        else:
            result = RESULT_LOSS
        if model_p is not None and result in (RESULT_WIN, RESULT_LOSS):
            try:
                p = float(model_p)
                outcome = 1.0 if result == RESULT_WIN else 0.0
                brier = (p - outcome) ** 2
            except (TypeError, ValueError):
                brier = None
    elif market == "spread":
        if line is None:
            result = RESULT_UNGRADED
        else:
            home_margin = home_score - away_score
            if side == "home":
                cover = home_margin + float(line)
            elif side == "away":
                cover = -home_margin - float(line)
            else:
                return {"result": RESULT_UNGRADED, "brier_contribution": None,
                        "actual_home_score": home_score,
                        "actual_away_score": away_score,
                        "actual_winner": actual_winner}
            if cover > 0:
                result = RESULT_WIN
            elif cover == 0:
                result = RESULT_PUSH
            else:
                result = RESULT_LOSS
    elif market == "total":
        if line is None:
            result = RESULT_UNGRADED
        else:
            total_points = home_score + away_score
            line_f = float(line)
            if side == "over":
                result = (RESULT_WIN if total_points > line_f
                          else RESULT_PUSH if total_points == line_f
                          else RESULT_LOSS)
            elif side == "under":
                result = (RESULT_WIN if total_points < line_f
                          else RESULT_PUSH if total_points == line_f
                          else RESULT_LOSS)
            else:
                result = RESULT_UNGRADED
    else:
        result = RESULT_UNGRADED

    return {
        "result": result,
        "brier_contribution": brier,
        "actual_home_score": home_score,
        "actual_away_score": away_score,
        "actual_winner": actual_winner,
    }


def _closing_snapshot(
    db: Any, game_id: str, kickoff: Any
) -> Optional[Dict[str, Any]]:
    """Latest odds snapshot for the game at or before kickoff.

    Returns ``None`` when no snapshots exist — CLV is then honestly
    unavailable, never estimated.
    """
    query: Dict[str, Any] = {"game_id": game_id}
    if kickoff is not None:
        query["timestamp"] = {"$lte": kickoff}
    docs = list(
        db["odds"].find(query).sort("timestamp", -1).limit(1)
    )
    return docs[0] if docs else None


def _closing_line_for_pick(
    snapshot: Optional[Dict[str, Any]], pick: Dict[str, Any]
) -> tuple[Optional[float], Optional[int]]:
    """(closing_line, closing_price) relevant to the pick's market/side."""
    if snapshot is None:
        return None, None
    market = (pick.get("market") or "moneyline").lower()
    side = (pick.get("side") or "").lower()
    if market == "spread":
        return snapshot.get("spread"), (
            snapshot.get("spread_home_price") if side == "home"
            else snapshot.get("spread_away_price")
        )
    if market == "total":
        return snapshot.get("total"), (
            snapshot.get("total_over_price") if side == "over"
            else snapshot.get("total_under_price")
        )
    # moneyline: line is meaningless; price depends on the picked side.
    home_team = pick.get("home_team")
    if pick.get("predicted_winner") == home_team:
        return None, snapshot.get("moneyline_home")
    return None, snapshot.get("moneyline_away")


def compute_clv(
    pick: Dict[str, Any], snapshot: Optional[Dict[str, Any]]
) -> Dict[str, Any]:
    """Closing-line value for one pick.

    Returns ``closing_line``, ``closing_price``, ``clv_points`` /
    ``clv_prob_points`` and ``clv_favorable`` — all ``None``/null when the
    comparison cannot be computed honestly.
    """
    closing_line, closing_price = _closing_line_for_pick(snapshot, pick)
    market = (pick.get("market") or "moneyline").lower()
    side = (pick.get("side") or "").lower()
    line_at_pick = pick.get("market_line_at_pick")
    price_at_pick = pick.get("market_price_at_pick")

    clv_points: Optional[float] = None
    clv_prob_points: Optional[float] = None
    favorable: Optional[bool] = None

    if market in ("spread", "total"):
        if closing_line is not None and line_at_pick is not None:
            clv_points = float(closing_line) - float(line_at_pick)
            if market == "spread":
                favorable = (clv_points < 0) if side == "home" else (clv_points > 0)
            else:  # total
                favorable = (clv_points < 0) if side == "over" else (clv_points > 0)
    else:  # moneyline: compare implied probabilities of the picked side
        imp_pick = _implied(price_at_pick)
        imp_close = _implied(closing_price)
        if imp_pick is not None and imp_close is not None:
            clv_prob_points = imp_close - imp_pick
            favorable = clv_prob_points > 0

    return {
        "closing_line": closing_line,
        "closing_price": closing_price,
        "clv_points": clv_points,
        "clv_prob_points": clv_prob_points,
        "clv_favorable": favorable,
    }


def settle_pick(
    db: Any, pick: Dict[str, Any], run_id: str
) -> Optional[Dict[str, Any]]:
    """Settle one locked pick. Returns the result doc, or ``None`` when the
    game is not final yet (pick stays pending). Upserts by ``prediction_id``
    so re-runs are idempotent."""
    game = db["games"].find_one({"game_id": pick.get("game_id")})
    if game is None:
        return None
    graded = grade_pick(pick, game)
    if graded is None:
        return None

    snapshot = _closing_snapshot(db, pick["game_id"], game.get("game_date"))
    clv = compute_clv(pick, snapshot)

    now = utcnow()
    doc: Dict[str, Any] = {
        "result_id": f"{pick['prediction_id']}:result",
        "prediction_id": pick["prediction_id"],
        "game_id": pick["game_id"],
        "season": pick.get("season"),
        "week": pick.get("week"),
        "home_team": pick.get("home_team"),
        "away_team": pick.get("away_team"),
        "model_version": pick.get("model_version"),
        "market": (pick.get("market") or "moneyline"),
        "side": pick.get("side"),
        "confidence": pick.get("confidence"),
        "model_probability": pick.get("model_probability"),
        "predicted_winner": pick.get("predicted_winner"),
        "market_line_at_pick": pick.get("market_line_at_pick"),
        "market_price_at_pick": pick.get("market_price_at_pick"),
        "sportsbook": pick.get("sportsbook"),
        "edge": pick.get("edge"),
        **graded,
        **clv,
        "settled_at": now,
        "settlement_run_id": run_id,
    }
    # Idempotent: one result per prediction, re-settled in place.
    db["pick_results"].update_one(
        {"prediction_id": pick["prediction_id"]},
        {"$set": doc},
        upsert=True,
    )
    return doc


def run_settlement(
    db: Any,
    season: Optional[int] = None,
    week: Optional[int] = None,
) -> Dict[str, Any]:
    """Settle all locked picks whose games are final.

    Returns a run summary ``{run_id, settled, pending, errors}`` and writes
    an audit doc to ``settlement_runs``. Safe to re-run: settled picks are
    re-graded to identical values; nothing is double-counted.
    """
    run_id = f"settle-{utcnow().strftime('%Y%m%dT%H%M%S%fZ')}"
    query: Dict[str, Any] = {}
    if season is not None:
        query["season"] = season
    if week is not None:
        query["week"] = week

    settled = 0
    pending = 0
    errors: List[str] = []

    for pick in db["predictions"].find(query):
        try:
            # Skip picks that already have a result — unless the game data
            # changed, re-grading would be identical; still re-run through
            # settle_pick for idempotency verification.
            doc = settle_pick(db, pick, run_id)
            if doc is None:
                pending += 1
            else:
                settled += 1
        except Exception as exc:  # noqa: BLE001 - one bad pick must not stop the run
            errors.append(f"{pick.get('prediction_id')}: {exc}")

    summary = {
        "run_id": run_id,
        "settled": settled,
        "pending": pending,
        "error_count": len(errors),
        "errors": errors[:20],
        "season": season,
        "week": week,
        "finished_at": utcnow(),
    }
    db["settlement_runs"].insert_one(dict(summary))
    return summary
