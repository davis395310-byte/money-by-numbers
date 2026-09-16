"""Playoff simulation endpoints (tag: playoffs).

- ``GET /api/playoffs/formats`` — static format + tiebreak documentation.
  No database involved.
- ``POST /api/playoffs/simulate-bracket`` — Monte Carlo over caller-supplied
  seeds and ratings. Compute-only: nothing is written anywhere.
- ``GET /api/playoffs/retrospective`` — the on-disk playoff retrospective
  artifact (``ml/artifacts/playoff_retrospective_2016_2025.json``), or an
  honest DATA UNAVAILABLE state when it has not been generated yet.

There are intentionally no PUT/PATCH/DELETE routes here and no database
writes of any kind: simulations are stateless computations.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel, Field

from ..playoffs import (
    MAX_SIMS,
    DEFAULT_ADJUSTMENTS,
    PlayoffSimError,
    playoff_format,
    simulate_playoff_bracket,
)

router = APIRouter(tags=["playoffs"])

# ml/artifacts/ lives at the repo root, three levels above this file
# (routers/ -> app/ -> backend/ -> repo root).
_RETRO_PATH = (
    Path(__file__).resolve().parents[3]
    / "ml"
    / "artifacts"
    / "playoff_retrospective_2016_2025.json"
)

_DISCLAIMER = (
    "Model estimates from a Monte Carlo simulation over Elo ratings, "
    "not guarantees of outcomes. Prediction accuracy, betting "
    "performance, and business metrics are separate; nothing here "
    "implies or guarantees profit."
)


@router.get("/api/playoffs/formats")
def playoff_formats() -> Dict[str, Any]:
    """Static documentation of the playoff formats and tiebreak order."""
    return {
        "status": "ok",
        "formats": {
            "2016-2019": {
                "n_teams": 12,
                "seeds_per_conference": 6,
                "byes": [1, 2],
                "wild_card_pairs": [[3, 6], [4, 5]],
                "note": "Top-2 seeds per conference get Wild Card byes.",
            },
            "2020-present": {
                "n_teams": 14,
                "seeds_per_conference": 7,
                "byes": [1],
                "wild_card_pairs": [[2, 7], [3, 6], [4, 5]],
                "note": "Only the #1 seed per conference gets a bye.",
            },
        },
        "seeding": (
            "Seeds 1-4 are the division winners ordered by record; seeds "
            "5+ are wild cards by record. Division winners always host "
            "Wild Card games. After the Wild Card round the bracket is "
            "re-seeded: the lowest remaining seed visits the #1 seed "
            "(#1 hosts lowest / #2 hosts the other survivor in the 12-team "
            "format). Conference championships are hosted by the higher "
            "seed; the Super Bowl is at a neutral site."
        ),
        "tiebreak_order": {
            "implemented": [
                "head_to_head",
                "division_record (same-division ties)",
                "common_games",
                "conference_record",
                "strength_of_victory",
                "strength_of_schedule",
            ],
            "not_implemented": (
                "NOT VERIFIED: steps beyond strength of schedule (points "
                "rankings, net points, net touchdowns, coin toss) are not "
                "implemented. Ties surviving the implemented steps are "
                "reported as unresolved / DATA UNAVAILABLE, never decided "
                "silently. The remaining-season simulator applies an "
                "explicit RNG coin toss for such ties inside each "
                "simulated season."
            ),
        },
        "adjustment_defaults": {
            key: {
                "default": value,
                "status": (
                    "standard value"
                    if key == "hfa_elo"
                    else "PLACEHOLDER pending the independent research verdict "
                    "(0.0 = no effect; engine is Elo-only until "
                    "evidence-based values are supplied)"
                ),
            }
            for key, value in DEFAULT_ADJUSTMENTS.items()
        },
    }


class SimulateBracketBody(BaseModel):
    season: int = Field(description="Season the bracket belongs to (format rules)")
    seeds: Dict[str, Dict[str, str]] = Field(
        description='{"AFC": {"1": "KC", ...}, "NFC": {"1": "PHI", ...}} in seed order'
    )
    ratings: Dict[str, float] = Field(description="Elo rating per team abbreviation")
    n_sims: int = Field(default=10000, ge=1, le=MAX_SIMS)
    adjustments: Optional[Dict[str, float]] = Field(
        default=None, description="Elo-point adjustments; unknown keys are rejected"
    )
    team_factors: Optional[Dict[str, Dict[str, float]]] = Field(
        default=None,
        description='Per-team factors, e.g. {"KC": {"qb_playoff_starts": 12, "momentum": 0.5}}',
    )
    rng_seed: Optional[int] = Field(
        default=None, description="Seed for reproducibility; omit for non-deterministic runs"
    )


def _seeds_to_lists(seeds: Dict[str, Dict[str, str]], season: int) -> Dict[str, List[str]]:
    fmt = playoff_format(season)
    n = fmt["seeds_per_conference"]
    out: Dict[str, List[str]] = {}
    for conf in ("AFC", "NFC"):
        conf_seeds = seeds.get(conf)
        if not isinstance(conf_seeds, dict):
            raise PlayoffSimError(f"seeds must include conference {conf!r}")
        try:
            ordered = [conf_seeds[str(i)] for i in range(1, n + 1)]
        except KeyError as exc:
            raise PlayoffSimError(
                f"seeds[{conf}] must map seed numbers '1'..'{n}' to teams; missing {exc}"
            )
        out[conf] = ordered
    return out


@router.post("/api/playoffs/simulate-bracket")
def simulate_bracket(body: SimulateBracketBody) -> Dict[str, Any]:
    """Run a Monte Carlo playoff simulation over explicit seeds + ratings.

    All inputs are caller-supplied; nothing is read from a database and
    nothing is written. Invalid inputs return 422 with a clear reason.
    """
    try:
        seeds = _seeds_to_lists(body.seeds, body.season)
        result = simulate_playoff_bracket(
            seeds_by_conf=seeds,
            ratings=body.ratings,
            season=body.season,
            n_sims=body.n_sims,
            adjustments=body.adjustments,
            rng_seed=body.rng_seed,
            team_factors=body.team_factors,
        )
    except ValueError as exc:  # PlayoffSimError included: bad input -> 422
        raise HTTPException(status_code=422, detail=str(exc))
    result["status"] = "ok"
    result["disclaimer"] = _DISCLAIMER
    return result


def _read_retrospective() -> Optional[Dict[str, Any]]:
    try:
        return json.loads(_RETRO_PATH.read_text())
    except FileNotFoundError:
        return None
    except Exception:
        return {"_corrupt": True}


@router.get("/api/playoffs/retrospective")
def playoff_retrospective(
    season: Optional[int] = Query(default=None, ge=2016, le=2100),
) -> Dict[str, Any]:
    """Playoff retrospective artifact (2016-2025), read from disk.

    Expected file: ``ml/artifacts/playoff_retrospective_2016_2025.json``
    (schema documented in this module's docstring and the build report).
    Honest empty state while the artifact has not been generated yet.
    """
    doc = _read_retrospective()
    if doc is None:
        return {
            "status": "unavailable",
            "reason": (
                "DATA UNAVAILABLE: the playoff retrospective artifact has "
                "not been generated yet (expected at "
                "ml/artifacts/playoff_retrospective_2016_2025.json)."
            ),
            "seasons": None,
        }
    if doc.get("_corrupt"):
        return {
            "status": "error",
            "reason": "playoff retrospective artifact exists but is not valid JSON",
            "seasons": None,
        }
    seasons = doc.get("seasons") or []
    if season is not None:
        seasons = [s for s in seasons if s.get("season") == season]
        if not seasons:
            return {
                "status": "no_such_season",
                "reason": f"season {season} not present in the retrospective artifact",
                "seasons": [],
            }
    return {
        "status": "ok",
        "artifact": doc.get("artifact"),
        "generated_at": doc.get("generated_at"),
        "generated_by": doc.get("generated_by"),
        "method": doc.get("method"),
        "seasons": seasons,
    }


#: Exact JSON schema of ``ml/artifacts/playoff_retrospective_2016_2025.json``.
#: The parent agent generates this file next; the endpoint above only
#: reads it.
RETROSPECTIVE_ARTIFACT_SCHEMA: Dict[str, Any] = {
    "$schema": "http://json-schema.org/draft-07/schema#",
    "title": "playoff_retrospective_2016_2025",
    "type": "object",
    "required": ["artifact", "generated_at", "generated_by", "method", "seasons"],
    "properties": {
        "artifact": {"const": "playoff_retrospective_2016_2025"},
        "generated_at": {"type": "string", "format": "date-time"},
        "generated_by": {"type": "string"},
        "method": {"type": "string"},
        "seasons": {
            "type": "array",
            "items": {
                "type": "object",
                "required": ["season", "format", "seeds", "actual", "model"],
                "properties": {
                    "season": {"type": "integer"},
                    "format": {
                        "type": "object",
                        "required": ["n_teams", "byes"],
                        "properties": {
                            "n_teams": {"enum": [12, 14]},
                            "byes": {"type": "array", "items": {"type": "integer"}},
                        },
                    },
                    "seeds": {
                        "type": "object",
                        "required": ["AFC", "NFC"],
                        "properties": {
                            "AFC": {"type": "array", "items": {"type": "string"}},
                            "NFC": {"type": "array", "items": {"type": "string"}},
                        },
                    },
                    "actual": {
                        "type": "object",
                        "description": "Verified historical results; every game listed once.",
                        "required": [
                            "wild_card",
                            "divisional",
                            "conference_championship",
                            "super_bowl",
                        ],
                        "properties": {
                            "wild_card": {
                                "type": "array",
                                "items": {
                                    "type": "object",
                                    "required": [
                                        "conference", "winner", "winner_seed",
                                        "loser", "loser_seed",
                                    ],
                                },
                            },
                            "divisional": {
                                "type": "array",
                                "items": {
                                    "type": "object",
                                    "required": [
                                        "conference", "winner", "winner_seed",
                                        "loser", "loser_seed",
                                    ],
                                },
                            },
                            "conference_championship": {
                                "type": "array",
                                "items": {
                                    "type": "object",
                                    "required": [
                                        "conference", "winner", "winner_seed",
                                        "loser", "loser_seed",
                                    ],
                                },
                            },
                            "super_bowl": {
                                "type": "object",
                                "required": [
                                    "champion", "champion_seed",
                                    "champion_conference", "runner_up",
                                ],
                            },
                        },
                    },
                    "model": {
                        "type": "object",
                        "description": (
                            "Per-team simulated probabilities for that season's "
                            "bracket (from simulate_playoff_bracket over the "
                            "actual seeds and the model's ratings at the time). "
                            "Null when not computed — never fabricated."
                        ),
                        "additionalProperties": {
                            "type": ["object", "null"],
                            "properties": {
                                "p_win_wc": {"type": ["number", "null"]},
                                "p_win_div": {"type": "number"},
                                "p_win_conf": {"type": "number"},
                                "p_win_sb": {"type": "number"},
                                "n_sims": {"type": "integer"},
                                "model_version": {"type": "string"},
                            },
                        },
                    },
                    "notes": {"type": "string"},
                },
            },
        },
    },
}
