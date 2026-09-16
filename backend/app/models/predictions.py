"""Prediction and model document models."""

from datetime import datetime
from typing import Any, Dict, Literal, Optional

from pydantic import BaseModel, Field, field_validator

from .core import _validate_team_abbr


class Prediction(BaseModel):
    """One locked model pick — an IMMUTABLE ledger record (Phase 6).

    Written exactly once via ``backend/app/picks/ledger.py::lock_pick``
    BEFORE kickoff. After insertion nothing may alter or delete the row:
    there are no update/delete endpoints for the ``predictions`` collection
    anywhere in the API, and the ledger writer refuses duplicate writes.
    Settlement results are recorded in the separate ``pick_results``
    collection — the prediction row itself is never touched after kickoff.
    """

    prediction_id: str
    game_id: str
    created_at: datetime
    model_version: str
    training_cutoff: str
    season: Optional[int] = Field(default=None, ge=1920)
    week: Optional[int] = Field(default=None, ge=1, le=22)
    home_team: Optional[str] = None
    away_team: Optional[str] = None
    # Scheduled kickoff (UTC). lock_pick() refuses to write when it is
    # missing or already past — a pick cannot exist after kickoff.
    kickoff: Optional[datetime] = None
    model_probability: float = Field(ge=0, le=1)
    predicted_winner: str
    predicted_home_score: Optional[int] = Field(default=None, ge=0)
    predicted_away_score: Optional[int] = Field(default=None, ge=0)
    # Which market this published pick targets. A model may publish one
    # pick per (game, model_version, market).
    market: Optional[Literal["moneyline", "spread", "total"]] = None
    # Side of the market: "home"/"away" for moneyline+spread,
    # "over"/"under" for totals.
    side: Optional[Literal["home", "away", "over", "under"]] = None
    # Market line at pick time (spread from the home perspective, or the
    # total). None for moneyline or when no line was available.
    market_line_at_pick: Optional[float] = None
    # American-odds price for the picked side at pick time, if quoted.
    market_price_at_pick: Optional[int] = None
    # Sportsbook the pick-time line/price was quoted by, if any.
    sportsbook: Optional[str] = None
    market_probability: Optional[float] = Field(default=None, ge=0, le=1)
    edge: Optional[float] = None
    confidence: Literal["low", "medium", "high"]
    # Result fields: nullable until the game resolves. Losing picks are
    # never deleted — they are graded in place.
    actual_winner: Optional[str] = None
    actual_home_score: Optional[int] = Field(default=None, ge=0)
    actual_away_score: Optional[int] = Field(default=None, ge=0)
    correct: Optional[bool] = None
    # Situational adjustments applied at prediction time (Phase 2). Any
    # non-empty ``adjustments`` entry (e.g. "situational-adjustment") means
    # the model_probability was moved by an explicit, documented shift —
    # it is never silent. ``adjustment_detail`` carries the audit trail
    # (team, stakes, Elo shift, basis). Both are None when no adjustment
    # applied.
    adjustments: Optional[list[str]] = None
    adjustment_detail: Optional[Dict[str, Any]] = None

    @field_validator("model_version")
    @classmethod
    def model_version_must_be_registered_format(cls, v: str) -> str:
        import re

        if not re.match(r"^MNB-NFL-\d{4}\.\d{2}$", v):
            raise ValueError(f"model_version must look like MNB-NFL-2026.02, got {v!r}")
        return v

    @field_validator("home_team", "away_team")
    @classmethod
    def teams_must_be_valid(cls, v: Optional[str]) -> Optional[str]:
        if v is None:
            return v
        return _validate_team_abbr(v)

    @field_validator("predicted_winner")
    @classmethod
    def predicted_winner_must_be_valid(cls, v: str) -> str:
        return _validate_team_abbr(v)

    @field_validator("actual_winner")
    @classmethod
    def actual_winner_must_be_valid(cls, v: Optional[str]) -> Optional[str]:
        if v is None:
            return v
        return _validate_team_abbr(v)


class ModelVersion(BaseModel):
    version: str
    created_at: datetime
    notes: Optional[str] = None
    metrics: Optional[Dict[str, Any]] = None


class Backtest(BaseModel):
    backtest_id: str
    model_version: str
    season_start: int = Field(ge=1920)
    season_end: int = Field(ge=1920)
    created_at: datetime
    metrics: Dict[str, Any] = Field(default_factory=dict)
