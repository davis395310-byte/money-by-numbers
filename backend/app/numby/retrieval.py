"""Grounding layer for Numby (Phase 7).

``gather_context`` turns a user message into a structured bundle of facts
drawn ONLY from the platform's real data stores:

- ``predictions`` / ``models`` — stored model predictions + champion version
- ``odds`` — latest stored odds snapshots (per game)
- ``games`` / ``injuries`` / ``weather`` — game context
- ``pick_results`` — the immutable track-record ledger
- on-disk ``ml/artifacts`` — the measured walk-forward methodology numbers

Every fact in the bundle carries a ``source`` string naming the collection
(or artifact file) it came from. The responder may only state facts that
appear in the bundle; anything else is answered with an honest
"I don't have that data."

Team detection: 32 canonical abbreviations plus common full names, so
"chiefs vs broncos" and "KC vs DEN" resolve to the same teams.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

_ARTIFACTS_DIR = Path(__file__).resolve().parents[3] / "ml" / "artifacts"

TEAMS: Dict[str, List[str]] = {
    "ARI": ["cardinals", "arizona cardinals"],
    "ATL": ["falcons", "atlanta falcons"],
    "BAL": ["ravens", "baltimore ravens"],
    "BUF": ["bills", "buffalo bills"],
    "CAR": ["panthers", "carolina panthers"],
    "CHI": ["bears", "chicago bears"],
    "CIN": ["bengals", "cincinnati bengals"],
    "CLE": ["browns", "cleveland browns"],
    "DAL": ["cowboys", "dallas cowboys"],
    "DEN": ["broncos", "denver broncos"],
    "DET": ["lions", "detroit lions"],
    "GB": ["packers", "green bay packers", "green bay"],
    "HOU": ["texans", "houston texans"],
    "IND": ["colts", "indianapolis colts"],
    "JAX": ["jaguars", "jacksonville jaguars", "jags"],
    "KC": ["chiefs", "kansas city chiefs", "kansas city"],
    "LAC": ["chargers", "los angeles chargers", "la chargers"],
    "LAR": ["rams", "los angeles rams", "la rams"],
    "LV": ["raiders", "las vegas raiders", "vegas raiders"],
    "MIA": ["dolphins", "miami dolphins"],
    "MIN": ["vikings", "minnesota vikings"],
    "NE": ["patriots", "new england patriots", "new england"],
    "NO": ["saints", "new orleans saints", "new orleans"],
    "NYG": ["giants", "new york giants", "ny giants"],
    "NYJ": ["jets", "new york jets", "ny jets"],
    "PHI": ["eagles", "philadelphia eagles", "philadelphia"],
    "PIT": ["steelers", "pittsburgh steelers", "pittsburgh"],
    "SEA": ["seahawks", "seattle seahawks", "seattle"],
    "SF": ["49ers", "niners", "san francisco 49ers", "san francisco"],
    "TB": ["buccaneers", "bucs", "tampa bay buccaneers", "tampa bay", "tampa"],
    "TEN": ["titans", "tennessee titans"],
    "WAS": ["commanders", "washington commanders", "washington"],
}


def detect_teams(message: str) -> List[str]:
    """Return canonical team abbreviations mentioned in the message, in order
    of first appearance."""
    lowered = f" {message.lower()} "
    hits: List[Tuple[int, str]] = []
    for abbr, names in TEAMS.items():
        candidates = [abbr.lower()] + names
        for name in candidates:
            # Word-boundary match so "NE" doesn't hit "money", "KC" not "hockey", etc.
            match = re.search(r"\b" + re.escape(name) + r"\b", lowered)
            if match:
                hits.append((match.start(), abbr))
                break
    hits.sort(key=lambda h: h[0])
    return [abbr for _, abbr in hits]


@dataclass
class Fact:
    kind: str  # prediction | odds | injuries | weather | track_record | methodology | game
    text: str
    source: str
    data: Dict[str, Any] = field(default_factory=dict)


@dataclass
class ContextBundle:
    teams: List[str]
    intents: List[str]
    facts: List[Fact] = field(default_factory=list)
    notes: List[str] = field(default_factory=list)  # honest availability notes


_INTENT_KEYWORDS: List[Tuple[str, List[str]]] = [
    ("prediction", ["predict", "prediction", "who wins", "who will win", "pick", "score prediction", "forecast"]),
    ("edges", ["edge", "edges", "best bet", "best bets", "value", "bet on", "should i bet"]),
    ("odds", ["odds", "line", "spread", "moneyline", "total", "over/under", "over under", "price"]),
    ("injuries", ["injur", "hurt", "out", "questionable", "doubtful", "injury report"]),
    ("weather", ["weather", "wind", "rain", "snow", "temperature", "dome", "forecast"]),
    ("track_record", ["record", "track record", "how are you doing", "accuracy so far", "results", "win rate", "roi"]),
    ("methodology", ["how does", "methodology", "model work", "how accurate", "backtest", "walk-forward", "walk forward", "elo"]),
    ("help", ["help", "what can you", "hello", "hi", "hey"]),
]


def detect_intents(message: str) -> List[str]:
    lowered = f" {message.lower()} "
    intents = [
        name
        for name, kws in _INTENT_KEYWORDS
        # Word-boundary on the left (so "out" doesn't match "about"), stem
        # matching on the right (so "injur" matches "injuries").
        if any(re.search(r"\b" + re.escape(k) + r"\w*\b", lowered) for k in kws)
    ]
    return intents or ["general"]


def _read_json(path: Path) -> Optional[Dict[str, Any]]:
    try:
        return json.loads(path.read_text())
    except Exception:
        return None


def _champion_model_version(db: Any) -> Optional[str]:
    try:
        from ..edges.engine import resolve_champion_model

        champ = resolve_champion_model(db)
        return champ.get("model_version")
    except Exception:
        return None


def gather_context(db: Any, message: str) -> ContextBundle:
    """Build the grounded fact bundle for a user message.

    ``db`` may be None (no database configured) — the bundle then carries
    honest availability notes and zero facts.
    """
    teams = detect_teams(message)
    intents = detect_intents(message)
    bundle = ContextBundle(teams=teams, intents=intents)

    if db is None:
        bundle.notes.append(
            "The database is not configured, so I can't look up predictions, "
            "odds, or the track record right now."
        )
        # Methodology numbers live on disk, not in the DB — still available.
        _add_methodology_facts(bundle)
        return bundle

    if "prediction" in intents or "edges" in intents or "odds" in intents:
        _add_prediction_facts(db, bundle, teams)
    if "edges" in intents:
        _add_edge_facts(db, bundle, teams)
    if "injuries" in intents or "weather" in intents:
        _add_game_context_facts(db, bundle, teams)
    if "track_record" in intents:
        _add_track_record_facts(db, bundle)
    if "methodology" in intents:
        _add_methodology_facts(bundle)
    if "general" in intents and not teams:
        _add_methodology_facts(bundle, brief=True)
    return bundle


def _latest_game_for_teams(db: Any, teams: List[str]) -> Optional[Dict[str, Any]]:
    """Most recent game matching the mentioned teams.

    Two teams -> their head-to-head meeting. One team -> that team's most
    recent game. Anything else -> None.
    """
    if not teams:
        return None
    try:
        if len(teams) >= 2:
            query: Dict[str, Any] = {
                "$or": [
                    {"home_team": teams[0], "away_team": teams[1]},
                    {"home_team": teams[1], "away_team": teams[0]},
                ]
            }
        else:
            query = {
                "$or": [{"home_team": teams[0]}, {"away_team": teams[0]}]
            }
        docs = list(
            db["games"]
            .find(query, {"_id": 0})
            .sort("game_date", -1)
            .limit(1)
        )
    except Exception:
        return None
    return docs[0] if docs else None


def _add_prediction_facts(db: Any, bundle: ContextBundle, teams: List[str]) -> None:
    version = _champion_model_version(db)
    game = _latest_game_for_teams(db, teams)
    if game is None:
        if teams:
            bundle.notes.append(
                "I couldn't find a game matching those teams in the database."
            )
        else:
            bundle.notes.append(
                "Tell me which teams (e.g. 'Chiefs vs Broncos') and I'll look up the model's prediction."
            )
        return
    query: Dict[str, Any] = {"game_id": game.get("game_id")}
    if version:
        query["model_version"] = version
    try:
        pred = db["predictions"].find_one(query, {"_id": 0}, sort=[("created_at", -1)])
    except Exception:
        pred = None
    game_label = f"{game.get('away_team')} at {game.get('home_team')}"
    if pred is None:
        bundle.notes.append(
            f"I don't have a stored model prediction for {game_label} "
            f"(week {game.get('week')}, {game.get('season')})."
        )
        return
    prob = pred.get("model_probability")
    prob_txt = f"{prob:.1%}" if isinstance(prob, (int, float)) else "unavailable"
    bundle.facts.append(
        Fact(
            kind="prediction",
            text=(
                f"Model {pred.get('model_version')} picks "
                f"{pred.get('predicted_winner')} to beat "
                f"{game.get('away_team') if pred.get('predicted_winner') == game.get('home_team') else game.get('home_team')} "
                f"in {game_label} (week {game.get('week')}, {game.get('season')}), "
                f"win probability {prob_txt}, confidence {pred.get('confidence', 'unknown')}."
            ),
            source="predictions collection",
            data={
                "game_id": game.get("game_id"),
                "model_version": pred.get("model_version"),
                "predicted_winner": pred.get("predicted_winner"),
                "model_probability": prob,
                "confidence": pred.get("confidence"),
                "predicted_home_score": pred.get("predicted_home_score"),
                "predicted_away_score": pred.get("predicted_away_score"),
            },
        )
    )
    hs, aws = pred.get("predicted_home_score"), pred.get("predicted_away_score")
    if isinstance(hs, (int, float)) and isinstance(aws, (int, float)):
        bundle.facts.append(
            Fact(
                kind="prediction",
                text=(
                    f"Predicted score for {game_label}: "
                    f"{game.get('home_team')} {hs:g}, {game.get('away_team')} {aws:g}."
                ),
                source="predictions collection",
                data={"predicted_home_score": hs, "predicted_away_score": aws},
            )
        )


def _add_edge_facts(db: Any, bundle: ContextBundle, teams: List[str]) -> None:
    try:
        from ..edges.engine import compute_game_edges, resolve_champion_model
    except Exception:
        bundle.notes.append("The edge engine isn't available right now.")
        return
    champ = resolve_champion_model(db)
    version = champ.get("model_version")
    try:
        preds = {
            p["game_id"]: p
            for p in db["predictions"].find({"model_version": version}, {"_id": 0})
            if p.get("game_id")
        }
    except Exception:
        preds = {}
    if not preds:
        bundle.notes.append("I can't compute edges without stored model predictions.")
        return
    # Latest odds snapshot per game.
    try:
        snaps = list(db["odds"].find({}, {"_id": 0}).sort("timestamp", -1).limit(500))
    except Exception:
        snaps = []
    latest: Dict[str, Dict[str, Any]] = {}
    for s in snaps:
        gid = s.get("game_id")
        if gid and gid not in latest:
            latest[gid] = s
    if not latest:
        bundle.notes.append(
            "No odds snapshots are stored, so I can't compare the model against market lines."
        )
        return
    rows: List[Dict[str, Any]] = []
    for gid, pred in preds.items():
        snap = latest.get(gid)
        if not snap:
            continue
        if teams and not (pred.get("home_team") in teams or pred.get("away_team") in teams):
            continue
        try:
            for row in compute_game_edges(gid, snap, pred, threshold=0.03):
                row["game_id"] = gid
                rows.append(row)
        except Exception:
            continue
    rows.sort(key=lambda r: r.get("edge", 0), reverse=True)
    if not rows:
        bundle.notes.append(
            "No sides currently clear the 3% edge threshold against stored market lines."
        )
        return
    for row in rows[:5]:
        bundle.facts.append(
            Fact(
                kind="odds",
                text=(
                    f"Edge: {row.get('side')} {row.get('market')} — model "
                    f"{row.get('model_probability', 0):.1%} vs no-vig market "
                    f"{row.get('novig_implied', 0):.1%}, edge "
                    f"{row.get('edge', 0):.1%}."
                ),
                source="odds snapshots + predictions (edge engine)",
                data={
                    "side": row.get("side"),
                    "market": row.get("market"),
                    "edge": row.get("edge"),
                    "model_probability": row.get("model_probability"),
                    "market_probability": row.get("novig_implied"),
                    "model_version": version,
                },
            )
        )


def _add_game_context_facts(db: Any, bundle: ContextBundle, teams: List[str]) -> None:
    game = _latest_game_for_teams(db, teams)
    if game is None:
        bundle.notes.append(
            "I couldn't find a game matching those teams to pull injuries/weather for."
        )
        return
    label = f"{game.get('away_team')} at {game.get('home_team')}"
    if "injuries" in bundle.intents:
        try:
            docs = list(
                db["injuries"].find(
                    {
                        "season": game.get("season"),
                        "week": game.get("week"),
                        "team": {"$in": [game.get("home_team"), game.get("away_team")]},
                    },
                    {"_id": 0},
                )
            )
        except Exception:
            docs = None
        if docs is None:
            bundle.notes.append("Couldn't read the injury collection right now.")
        elif not docs:
            bundle.notes.append(
                f"No injury report rows are stored for {label} "
                f"(week {game.get('week')}) — I won't guess at who's hurt."
            )
        else:
            notable = [
                d for d in docs if d.get("report_status") in ("Out", "Doubtful", "Questionable")
            ]
            names = ", ".join(
                f"{d.get('full_name')} ({d.get('team')}, {d.get('report_status')})"
                for d in notable[:8]
            )
            bundle.facts.append(
                Fact(
                    kind="injuries",
                    text=(
                        f"Injury report for {label} (week {game.get('week')}): "
                        f"{len(notable)} players listed as Out/Doubtful/Questionable"
                        + (f" — {names}" if names else "")
                        + ". Official weekly NFL injury report, not a live news wire."
                    ),
                    source="injuries collection",
                    data={"n_notable": len(notable)},
                )
            )
    if "weather" in bundle.intents:
        try:
            w = db["weather"].find_one({"game_id": game.get("game_id")}, {"_id": 0})
        except Exception:
            w = None
        if not w:
            bundle.notes.append(
                f"No weather record is stored for {label} — I won't invent a forecast."
            )
        elif w.get("dome"):
            bundle.facts.append(
                Fact(
                    kind="weather",
                    text=f"{label} is played in a dome; outdoor weather doesn't apply.",
                    source="weather collection",
                    data={"dome": True},
                )
            )
        else:
            bundle.facts.append(
                Fact(
                    kind="weather",
                    text=(
                        f"Kickoff weather for {label}: {w.get('temp_f')}°F, "
                        f"wind {w.get('wind_mph')} mph, precipitation chance "
                        f"{w.get('precip_prob')}."
                    ),
                    source="weather collection",
                    data={
                        "temp_f": w.get("temp_f"),
                        "wind_mph": w.get("wind_mph"),
                        "precip_prob": w.get("precip_prob"),
                    },
                )
            )


def _add_track_record_facts(db: Any, bundle: ContextBundle) -> None:
    try:
        docs = list(db["pick_results"].find({}, {"_id": 0}))
    except Exception:
        bundle.notes.append("Couldn't read the track-record ledger right now.")
        return
    decided = [d for d in docs if d.get("result") in ("win", "loss")]
    if not decided:
        bundle.notes.append(
            "No picks have been settled yet, so there's no track record to report."
        )
        return
    wins = sum(1 for d in decided if d.get("result") == "win")
    acc = wins / len(decided)
    bundle.facts.append(
        Fact(
            kind="track_record",
            text=(
                f"Settled track record: {wins}-{len(decided) - wins} "
                f"({acc:.1%} accuracy over {len(decided)} graded picks). "
                f"Losing picks are never removed from the record."
            ),
            source="pick_results ledger",
            data={"wins": wins, "losses": len(decided) - wins, "accuracy": acc},
        )
    )


def _read_methodology() -> Optional[Dict[str, Any]]:
    meta = _read_json(_ARTIFACTS_DIR / "MNB-NFL-2026.02" / "metadata.json")
    if not meta:
        return None
    wf = meta.get("walk_forward") or {}
    return {
        "version": meta.get("version"),
        "n_games": wf.get("n_games"),
        "seasons": wf.get("seasons"),
        "ensemble_accuracy": wf.get("accuracy"),
        "elo_accuracy": wf.get("elo_accuracy"),
        "ats_accuracy": wf.get("ats_accuracy"),
        "ats_n": wf.get("ats_n"),
        "note": wf.get("note"),
    }


def _add_methodology_facts(bundle: ContextBundle, brief: bool = False) -> None:
    m = _read_methodology()
    if not m:
        bundle.notes.append(
            "The methodology writeup isn't available on this server right now."
        )
        return
    ens = m["ensemble_accuracy"]
    elo = m["elo_accuracy"]
    text = (
        f"2016–2025 walk-forward ({m['n_games']} games): the ensemble scored "
        f"{ens:.2%} winner accuracy vs {elo:.2%} for the simple Elo baseline — "
        f"the Elo model won, so the platform uses it. "
        f"Against closing lines: {m['ats_accuracy']:.1%} ATS (n={m['ats_n']}), "
        f"no demonstrated market edge."
    )
    if brief:
        text = (
            f"Quick background: the model was walk-forward tested on "
            f"{m['n_games']} games from 2016–2025. Ask me 'how does the model work' for details."
        )
    bundle.facts.append(
        Fact(
            kind="methodology",
            text=text,
            source="ml/artifacts walk-forward results",
            data=m,
        )
    )
