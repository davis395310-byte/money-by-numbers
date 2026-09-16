"""Odds and edge endpoints (Phase 4, spec sections 26-29).

- ``GET /api/odds/status`` — provider status: whether an API key is
  configured, remaining credits (from the ``odds_credit_usage`` collection),
  and snapshot inventory. Without a key it returns an honest
  ``"odds_unavailable"`` status on HTTP 200 — never fabricated lines.
- ``GET /api/odds/games`` — latest odds snapshot per game (query: season,
  week), with honest empty states.
- ``GET /api/edges`` — model-vs-market edges: latest odds snapshots joined
  with champion-model predictions. Computed only when BOTH inputs exist;
  otherwise an honest ``"edges_unavailable"`` naming the missing input.
- ``GET /api/edges/clv`` — closing-line value on graded predictions:
  pick-time line vs closing line aggregates.

The odds provider module is imported lazily (``try/except ImportError``
inside functions) so these endpoints work even if the provider/ingestion
half of Phase 4 is not finished yet.
"""

from datetime import datetime
from typing import Any, Dict, List, Optional, Tuple

from fastapi import APIRouter, Query

from ..config import get_settings
from ..database import get_database_name, get_mongo_client
from ..edges import compute_game_edges, resolve_champion_model

router = APIRouter(tags=["odds", "edges"])


# Freshness policy for the local stored-edges fallback (see
# docs/best-bets-stored-edges-policy.md). Single place of definition: the
# /api/edges response carries ``snapshot_as_of`` (newest odds snapshot used)
# and ``snapshot_stale``; the frontend renders from those flags and holds no
# threshold of its own.
STORED_EDGES_STALE_AFTER_DAYS = 7


def _snapshot_staleness(as_of_iso: Optional[str]) -> Tuple[Optional[str], bool]:
    """Return (as_of_iso, stale) for a snapshot timestamp.

    A snapshot is stale when it is older than one weekly refresh cycle.
    ``None`` as-of can never happen for a real snapshot set, but is treated
    as stale rather than trusted.
    """
    if not as_of_iso:
        return None, True
    try:
        as_of = datetime.fromisoformat(as_of_iso)
    except ValueError:
        return as_of_iso, True
    age_days = (datetime.now(as_of.tzinfo) - as_of).total_seconds() / 86400
    return as_of_iso, age_days > STORED_EDGES_STALE_AFTER_DAYS


def _db() -> Any | None:
    if not get_settings().MONGODB_URI:
        return None
    try:
        client = get_mongo_client()
    except Exception:
        return None
    if client is None:
        return None
    return client[get_database_name()]


def _serialize(doc: Dict[str, Any]) -> Dict[str, Any]:
    out: Dict[str, Any] = {}
    for key, value in doc.items():
        if key == "_id":
            continue
        out[key] = value.isoformat() if hasattr(value, "isoformat") else value
    return out


def _provider_credits_remaining() -> Optional[int]:
    """Ask the (lazily imported) odds provider for remaining API credits.

    Returns ``None`` when the provider module is not finished yet, has no
    such function, or the call fails — the router then falls back to the
    ``odds_credit_usage`` collection / ``null``.
    """
    try:
        from ..providers import odds_provider  # Phase 4 part 1; may not exist yet
    except ImportError:
        return None
    get_credits = getattr(odds_provider, "get_credits_remaining", None)
    if not callable(get_credits):
        return None
    try:
        value = get_credits(get_settings().ODDS_API_KEY)
    except Exception:
        return None
    return value if isinstance(value, int) else None


def _opt_int(value: Any) -> Optional[int]:
    """Coerce a query param to int-or-None.

    FastAPI resolves ``Query(default=None)`` to ``None`` at runtime, but
    direct calls (tests, scripts) may pass the ``Query`` default object
    itself; treat anything non-numeric as missing rather than crashing.
    """
    return value if isinstance(value, int) and not isinstance(value, bool) else None


def _latest_snapshots(
    db: Any, season: Optional[int] = None, week: Optional[int] = None
) -> Dict[str, Dict[str, Any]]:
    """Latest odds snapshot per game_id (optionally filtered by season/week).

    Odds snapshots do not carry season/week fields, so the filter uses the
    ``game_id`` convention ``"{season}_{week:02d}_..."``. Returns a mapping
    game_id -> snapshot doc.
    """
    season, week = _opt_int(season), _opt_int(week)
    match: Dict[str, Any] = {}
    if season is not None and week is not None:
        match["game_id"] = {"$regex": f"^{season}_{week:02d}_"}
    elif season is not None:
        match["game_id"] = {"$regex": f"^{season}_"}
    pipeline = [
        {"$match": match},
        {"$sort": {"timestamp": -1}},
        {"$group": {"_id": "$game_id", "doc": {"$first": "$$ROOT"}}},
        {"$replaceRoot": {"newRoot": "$doc"}},
    ]
    try:
        docs = list(db["odds"].aggregate(pipeline))
    except Exception:
        return {}
    return {d["game_id"]: _serialize(d) for d in docs if d.get("game_id")}


@router.get("/api/odds/status")
def odds_status() -> Dict[str, Any]:
    """Odds provider status and snapshot inventory (honest, never fabricated)."""
    settings = get_settings()
    key_configured = bool(settings.ODDS_API_KEY)
    if not key_configured:
        return {"status": "odds_unavailable", "reason": "no API key configured"}

    credits_remaining: Optional[int] = _provider_credits_remaining()
    last_snapshot_at: Optional[str] = None
    snapshot_count = 0
    db = _db()
    if db is not None:
        if credits_remaining is None:
            try:
                usage = db["odds_credit_usage"].find_one(sort=[("recorded_at", -1)])
                if usage and isinstance(usage.get("credits_remaining"), int):
                    credits_remaining = usage["credits_remaining"]
            except Exception:
                pass
        try:
            snapshot_count = db["odds"].count_documents({})
            latest = db["odds"].find_one(
                sort=[("timestamp", -1)], projection={"timestamp": 1}
            )
            if latest and isinstance(latest.get("timestamp"), datetime):
                last_snapshot_at = latest["timestamp"].isoformat()
        except Exception:
            pass
    return {
        "status": "odds_available",
        "key_configured": True,
        "credits_remaining": credits_remaining,
        "last_snapshot_at": last_snapshot_at,
        "snapshot_count": snapshot_count,
    }


@router.get("/api/odds/games")
def odds_games(
    season: Optional[int] = Query(default=None, ge=1920),
    week: Optional[int] = Query(default=None, ge=1, le=22),
) -> Dict[str, Any]:
    """Latest odds snapshot per game (newest timestamp wins per game_id)."""
    db = _db()
    if db is None:
        return {"status": "no_odds", "reason": "database not configured", "games": []}
    try:
        snapshots = _latest_snapshots(db, season=season, week=week)
    except Exception as exc:  # noqa: BLE001 - honest failure, not a crash
        return {"status": "error", "reason": str(exc)[:200], "games": []}
    if not snapshots:
        return {"status": "no_odds", "reason": "no odds snapshots stored yet", "games": []}
    games = sorted(snapshots.values(), key=lambda d: d.get("game_id", ""))
    return {"status": "ok", "games": games}


@router.get("/api/edges")
def list_edges(
    season: Optional[int] = Query(default=None, ge=1920),
    week: Optional[int] = Query(default=None, ge=1, le=22),
    min_edge: float = Query(default=0.03, ge=0.0, le=1.0),
) -> Dict[str, Any]:
    """Model-vs-market edges for the champion model.

    Joins the latest odds snapshot per game with predictions from the
    champion model version. A side is flagged only when
    ``edge = model_probability - no_vig_implied >= min_edge``. Every row
    labels the ``model_version`` used.
    """
    db = _db()
    if db is None:
        return {"status": "edges_unavailable", "reason": "database not configured", "edges": []}

    champion = resolve_champion_model(db)
    model_version = champion["model_version"]
    season, week = _opt_int(season), _opt_int(week)
    min_edge = float(min_edge) if isinstance(min_edge, (int, float)) else 0.03

    try:
        snapshots = _latest_snapshots(db, season=season, week=week)
    except Exception as exc:  # noqa: BLE001
        return {"status": "error", "reason": str(exc)[:200], "edges": []}

    pred_query: Dict[str, Any] = {"model_version": model_version}
    if season is not None:
        pred_query["season"] = season
    if week is not None:
        pred_query["week"] = week
    try:
        preds = {
            p["game_id"]: p
            for p in db["predictions"].find(pred_query, {"_id": 0})
            if p.get("game_id")
        }
    except Exception as exc:  # noqa: BLE001
        return {"status": "error", "reason": str(exc)[:200], "edges": []}

    missing: List[str] = []
    if not snapshots:
        missing.append("odds snapshots")
    if not preds:
        missing.append(f"predictions from champion model {model_version}")
    if missing:
        return {
            "status": "edges_unavailable",
            "reason": "missing: " + ", ".join(missing),
            "model_version": model_version,
            "edges": [],
        }

    rows: List[Dict[str, Any]] = []
    for game_id, snapshot in snapshots.items():
        prediction = preds.get(game_id)
        if prediction is None:
            continue
        rows.extend(
            compute_game_edges(
                game_id, snapshot, prediction, threshold=min_edge
            )
        )
    rows.sort(key=lambda r: r["edge"], reverse=True)
    # Newest odds snapshot backing these edges (ISO strings sort
    # chronologically). Reported so clients can show an honest as-of label
    # and apply the stored-edges freshness policy.
    as_of_candidates = [
        s.get("timestamp") for s in snapshots.values() if s.get("timestamp")
    ]
    snapshot_as_of, snapshot_stale = _snapshot_staleness(
        max(as_of_candidates) if as_of_candidates else None
    )
    if not rows:
        return {
            "status": "no_edges",
            "reason": f"no sides with edge >= {min_edge}",
            "model_version": model_version,
            "threshold": min_edge,
            "snapshot_as_of": snapshot_as_of,
            "snapshot_stale": snapshot_stale,
            "edges": [],
        }
    return {
        "status": "ok",
        "model_version": model_version,
        "threshold": min_edge,
        "snapshot_as_of": snapshot_as_of,
        "snapshot_stale": snapshot_stale,
        "edges": [_serialize(r) for r in rows],
    }


def _clv_records(db: Any) -> List[Dict[str, Any]]:
    """Per-game CLV records for graded predictions.

    Pick-time line: earliest snapshot at/after the prediction's ``created_at``
    for the predicted winner's moneyline. Closing line: latest snapshot at or
    before kickoff (``games.game_date``). ``clv_cents = pick_price - close_price``
    (positive means the pick beat the close) for both favorites and underdogs.
    Games where any piece is missing are skipped, never guessed.
    """
    records: List[Dict[str, Any]] = []
    try:
        graded = list(
            db["predictions"].find(
                {"correct": {"$ne": None}}, {"_id": 0}
            )
        )
    except Exception:
        return records
    for pred in graded:
        game_id = pred.get("game_id")
        created_at = pred.get("created_at")
        predicted_winner = pred.get("predicted_winner")
        home_team = pred.get("home_team")
        if not game_id or not isinstance(created_at, datetime) or not predicted_winner:
            continue
        side = (
            "home"
            if predicted_winner == home_team
            else "away"
            if predicted_winner == pred.get("away_team")
            else None
        )
        if side is None:
            continue
        price_field = f"moneyline_{side}"
        try:
            game = db["games"].find_one({"game_id": game_id}, {"game_date": 1})
            snaps = list(
                db["odds"]
                .find({"game_id": game_id}, {"timestamp": 1, price_field: 1})
                .sort("timestamp", 1)
            )
        except Exception:
            continue
        if not game or not isinstance(game.get("game_date"), datetime) or not snaps:
            continue
        kickoff = game["game_date"]
        pick_snaps = [s for s in snaps if s.get("timestamp") >= created_at]
        close_snaps = [s for s in snaps if s.get("timestamp") <= kickoff]
        if not pick_snaps or not close_snaps:
            continue
        pick_price = pick_snaps[0].get(price_field)
        close_price = close_snaps[-1].get(price_field)
        if not isinstance(pick_price, (int, float)) or not isinstance(
            close_price, (int, float)
        ):
            continue
        clv_cents = pick_price - close_price
        records.append(
            {
                "game_id": game_id,
                "side": side,
                "pick_price": pick_price,
                "close_price": close_price,
                "clv_cents": clv_cents,
                "beat_close": clv_cents > 0,
            }
        )
    return records


@router.get("/api/edges/clv")
def closing_line_value() -> Dict[str, Any]:
    """Closing-line value aggregates over graded predictions.

    Returns ``{"status": "data_unavailable"}`` when no closing lines have
    been recorded yet — CLV is never fabricated.
    """
    db = _db()
    if db is None:
        return {
            "status": "data_unavailable",
            "reason": "database not configured",
        }
    try:
        records = _clv_records(db)
    except Exception as exc:  # noqa: BLE001
        return {"status": "error", "reason": str(exc)[:200]}
    if not records:
        return {
            "status": "data_unavailable",
            "reason": "no closing lines recorded yet",
        }
    n = len(records)
    avg_clv_cents = sum(r["clv_cents"] for r in records) / n
    beat_close_rate = sum(1 for r in records if r["beat_close"]) / n
    return {
        "status": "ok",
        "n": n,
        "avg_clv_cents": avg_clv_cents,
        "beat_close_rate": beat_close_rate,
    }
