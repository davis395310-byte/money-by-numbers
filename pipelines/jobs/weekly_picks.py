"""Weekly picks lock job — generates and publishes the public weekly board.

Runs Tuesdays in season (after Monday's postgame settlement and Tuesday's
odds pull):

- Determines the target week: the earliest regular-season week whose games
  ALL kick off in the future. Partial weeks are skipped outright — a week
  with any completed game is never locked (no backfilling, ever).
- Replays the registered champion's Elo through all finals via
  ``ml.features.build_feature_frame``. The kickoff rule is structural:
  state updates apply only after every game with an equal-or-earlier
  kickoff, so future games never leak into the ratings.
- Predicted margin uses the backtest's own Elo-baseline method
  (``ml/backtest.py``): least-squares slope of home margin on
  ``elo_diff + HFA`` over the 2016-2025 finals, applied without the
  intercept. Predicted total is the league-mean total over the same
  window — a documented constant, computed at runtime, never invented
  per game.
- Locks each pick through ``backend/app/picks/ledger.py::lock_pick``.
  The ledger itself refuses post-kickoff writes and duplicates, so
  backfilling is structurally impossible even if this job is misused.
- Generates tipping-point explainers into ``pick_explainers`` (the
  immutable ledger is never touched).
- Market lines attach only from a real odds snapshot (Tuesday's
  ``odds_pull``); when none exists the pick locks without market data
  and the explainer skips the market comparison.

The champion version is read from the ``models`` registry
(``champion: true``); the ``MNB-NFL-2026.01`` fallback is recorded as
such in the run detail, never silently substituted.

Dry-run mode (``context["dry_run"] = True``) computes the full board and
returns it in the record detail without writing anything.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any, Dict, List, Optional

from pipelines.jobs._common import ensure_repo_root, require_db, utcnow
from pipelines.schedule import current_season
from pipelines.scheduler import Job, JobRecord, JobStatus, SkipJob

CHAMPION_FALLBACK = "MNB-NFL-2026.01"
TRAIN_START = 2016
TRAIN_END = 2025
HISTORY_START = 1999  # Elo warm-up, same as the audited backtest


def _champion_version(db: Any) -> Dict[str, Any]:
    """Registered champion version, with an explicit fallback record."""
    try:
        doc = db["models"].find_one({"champion": True}, {"_id": 0})
    except Exception:
        doc = None
    if isinstance(doc, dict) and doc.get("version"):
        return {"model_version": str(doc["version"]), "source": "models_registry"}
    return {
        "model_version": CHAMPION_FALLBACK,
        "source": "fallback_constant",
        "note": "no document with champion:true in the models collection",
    }


def _target_week(games: Any, season: int, now: datetime) -> int:
    """Earliest REG week of ``season`` whose games all kick off after ``now``.

    Raises SkipJob when no such week exists (season not started in a
    usable way, or season complete).
    """
    weeks: Dict[int, List[Any]] = {}
    for row in games.itertuples(index=False):
        if row.season != season:
            continue
        weeks.setdefault(int(row.week), []).append(row.kickoff)
    for week in sorted(weeks):
        kickoffs = weeks[week]
        if kickoffs and all(k is not None and k > now for k in kickoffs):
            return week
    raise SkipJob(
        f"no lockable week for season {season}: every scheduled week "
        "has at least one game at or past kickoff"
    )


def _latest_market_line(db: Any, game_id: str) -> Optional[Dict[str, Any]]:
    """Newest odds snapshot for a game, or None when no snapshot exists."""
    try:
        doc = db["odds"].find_one(
            {"game_id": game_id},
            {
                "_id": 0,
                "moneyline_home": 1,
                "moneyline_away": 1,
                "timestamp": 1,
                "sportsbook": 1,
            },
            sort=[("timestamp", -1)],
        )
    except Exception:
        return None
    if not doc:
        return None
    return doc


class WeeklyPicksJob(Job):
    """Lock the upcoming week's champion picks before kickoff."""

    name = "weekly_picks"

    def run(self, context: Optional[Dict[str, Any]] = None) -> JobRecord:
        record = JobRecord(job_id=f"{self.name}-{id(self)}")
        context = context or {}
        dry_run = bool(context.get("dry_run"))
        try:
            db = require_db(context)
        except Exception as exc:  # noqa: BLE001 - honest failure path
            record.finish(JobStatus.FAILED, error=str(exc)[:500])
            return record

        now = context.get("now") or utcnow()
        season = current_season(now)

        try:
            ensure_repo_root()
            from ml.data_loader import load_games
            from ml.features import (
                ELO_HFA,
                build_feature_frame,
                elo_win_probability,
            )

            from pipelines.jobs._common import ensure_backend

            ensure_backend()
            from app.picks.ledger import PickLedgerError, lock_pick
            from app.picks.explainers import build_explainer
        except Exception as exc:  # noqa: BLE001
            record.finish(JobStatus.FAILED, error=f"imports failed: {exc}"[:500])
            return record

        try:
            games = load_games(
                range(HISTORY_START, season + 1), game_types=("REG",)
            )
        except Exception as exc:  # noqa: BLE001
            record.finish(JobStatus.FAILED, error=f"schedule load failed: {exc}"[:500])
            return record

        try:
            week = _target_week(games, season, now)
        except SkipJob as exc:
            record.finish(JobStatus.SKIPPED, error=str(exc)[:500])
            return record

        frame = build_feature_frame(games)

        # Margin slope + mean total over the audited training window.
        # (The feature frame carries no scores, so join them from the
        # loaded games.)
        import numpy as np

        scores = games[["game_id", "home_score", "away_score"]].dropna(
            subset=["home_score", "away_score"]
        )
        scored = frame.merge(scores, on="game_id", how="inner")
        train = scored[
            (scored["season"] >= TRAIN_START) & (scored["season"] <= TRAIN_END)
        ]
        x = (train["elo_diff"] + ELO_HFA).to_numpy(dtype=float)
        y_margin = (
            train["home_score"] - train["away_score"]
        ).to_numpy(dtype=float)
        y_total = (train["home_score"] + train["away_score"]).to_numpy(dtype=float)
        slope = float(np.polyfit(x, y_margin, 1)[0])  # intercept dropped, per backtest
        mean_total = float(np.mean(y_total))

        champion = _champion_version(db)
        model_version = champion["model_version"]

        target = frame[
            (frame["season"] == season) & (frame["week"] == week)
        ].sort_values("kickoff")

        locked: List[Dict[str, Any]] = []
        skipped: List[Dict[str, str]] = []
        for row in target.itertuples(index=False):
            kickoff = row.kickoff
            if kickoff is None or kickoff <= now:
                skipped.append(
                    {"game_id": row.game_id, "reason": "kickoff not in the future"}
                )
                continue
            p_home = float(elo_win_probability(row.elo_diff))
            if p_home >= 0.5:
                winner, prob = row.home_team, p_home
            else:
                winner, prob = row.away_team, 1.0 - p_home
            margin = slope * (float(row.elo_diff) + ELO_HFA)
            pred_home = int(round((mean_total + margin) / 2))
            pred_away = int(round((mean_total - margin) / 2))
            prediction_id = (
                f"{season}-w{week:02d}-{row.away_team}-{row.home_team}"
            )
            market = None if dry_run else _latest_market_line(db, row.game_id)
            market_kwargs: Dict[str, Any] = {}
            if market and market.get("moneyline_home") is not None:
                market_kwargs["market_price_at_pick"] = (
                    int(market["moneyline_home"])
                    if winner == row.home_team
                    else int(market["moneyline_away"])
                )
                market_kwargs["sportsbook"] = str(market.get("sportsbook") or "odds_snapshot")
            pick_doc: Dict[str, Any] = {
                "prediction_id": prediction_id,
                "game_id": row.game_id,
                "model_version": model_version,
                "training_cutoff": f"{TRAIN_END} season",
                "model_probability": round(prob, 4),
                "predicted_winner": winner,
                "kickoff": kickoff,
                "season": season,
                "week": week,
                "home_team": row.home_team,
                "away_team": row.away_team,
                "predicted_home_score": pred_home,
                "predicted_away_score": pred_away,
            }
            if dry_run:
                locked.append({**pick_doc, "kickoff": kickoff.isoformat()})
                continue
            try:
                stored = lock_pick(db, **pick_doc, **market_kwargs)
            except PickLedgerError as exc:
                skipped.append({"game_id": row.game_id, "reason": str(exc)[:200]})
                continue
            explainer_in = {
                "prediction_id": prediction_id,
                "predicted_winner": winner,
                "home_team": row.home_team,
                "away_team": row.away_team,
                "model_probability": round(prob, 4),
                "confidence": stored.get("confidence"),
                "model_version": model_version,
            }
            market_in = None
            if market and market.get("moneyline_home") is not None:
                market_in = {
                    "moneyline_home": market["moneyline_home"],
                    "moneyline_away": market["moneyline_away"],
                    "snapshot_at": str(market.get("timestamp")),
                }
            try:
                rest = row.rest_diff
                rest_int = int(rest) if rest is not None else 0
            except Exception:
                rest_int = 0
            explainer_doc = build_explainer(
                explainer_in, rest_diff=rest_int, market=market_in
            )
            try:
                if (
                    db["pick_explainers"].find_one(
                        {"prediction_id": prediction_id}, {"_id": 1}
                    )
                    is None
                ):
                    db["pick_explainers"].insert_one(explainer_doc)
            except Exception as exc:  # noqa: BLE001 - explainer failure never blocks the lock
                skipped.append(
                    {
                        "game_id": row.game_id,
                        "reason": f"pick locked, explainer failed: {exc}"[:200],
                    }
                )
                continue
            locked.append(
                {
                    "prediction_id": prediction_id,
                    "game_id": row.game_id,
                    "predicted_winner": winner,
                    "model_probability": round(prob, 4),
                    "kickoff": kickoff.isoformat(),
                }
            )

        record.detail = {
            "season": season,
            "week": week,
            "dry_run": dry_run,
            "model_version": model_version,
            "champion_source": champion.get("source"),
            "margin_slope": round(slope, 5),
            "mean_total": round(mean_total, 2),
            "training_window": f"{TRAIN_START}-{TRAIN_END}",
            "locked": len(locked),
            "skipped": skipped,
            "board": locked,
        }
        if not locked and not dry_run:
            record.finish(
                JobStatus.FAILED,
                error=f"week {week}: no picks locked ({len(skipped)} skipped)",
            )
        else:
            record.finish(JobStatus.SUCCEEDED)
        return record
