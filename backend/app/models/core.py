"""Core NFL data document models: teams, players, games, stats, injuries, weather, odds."""

from datetime import datetime
from typing import Literal, Optional

from pydantic import BaseModel, Field, field_validator, model_validator

# The 32 valid NFL team abbreviations.
NFL_TEAMS: tuple = (
    "ARI", "ATL", "BAL", "BUF", "CAR", "CHI", "CIN", "CLE",
    "DAL", "DEN", "DET", "GB", "HOU", "IND", "JAX", "KC",
    "LV", "LAC", "LAR", "MIA", "MIN", "NE", "NO", "NYG",
    "NYJ", "PHI", "PIT", "SF", "SEA", "TB", "TEN", "WAS",
)
VALID_TEAM_ABBRS = frozenset(NFL_TEAMS)


def _validate_team_abbr(value: str) -> str:
    if value not in VALID_TEAM_ABBRS:
        raise ValueError(
            f"Invalid NFL team abbreviation: {value!r}. "
            f"Must be one of the 32 valid abbreviations."
        )
    return value


class Team(BaseModel):
    abbr: str
    name: str
    city: str
    conference: Literal["AFC", "NFC"]
    division: str

    @field_validator("abbr")
    @classmethod
    def abbr_must_be_valid(cls, v: str) -> str:
        return _validate_team_abbr(v)


class Player(BaseModel):
    player_id: str
    name: str
    team: str
    position: str
    jersey_number: Optional[int] = Field(default=None, ge=0)

    @field_validator("team")
    @classmethod
    def team_must_be_valid(cls, v: str) -> str:
        return _validate_team_abbr(v)


class Game(BaseModel):
    """An NFL game. Duplicates are prevented by a UNIQUE compound index on
    (season, week, home_team, away_team) created in database.ensure_indexes."""

    game_id: str
    season: int = Field(ge=1920)
    week: int = Field(ge=1, le=22)
    home_team: str
    away_team: str
    game_date: datetime
    status: Literal["scheduled", "in_progress", "final"] = "scheduled"
    home_score: Optional[int] = Field(default=None, ge=0)
    away_score: Optional[int] = Field(default=None, ge=0)
    venue: Optional[str] = None
    roof: Optional[str] = None  # nflverse roof: dome/outdoors/closed/open (None when unknown)
    overtime: Optional[int] = Field(default=None, ge=0, le=1)

    @field_validator("home_team", "away_team")
    @classmethod
    def team_must_be_valid(cls, v: str) -> str:
        return _validate_team_abbr(v)

    @model_validator(mode="after")
    def teams_must_differ(self) -> "Game":
        if self.home_team == self.away_team:
            raise ValueError("home_team and away_team must be different teams")
        return self


class GameStats(BaseModel):
    game_id: str
    team: str
    points: int = Field(ge=0)
    yards_total: Optional[int] = Field(default=None, ge=0)
    turnovers: Optional[int] = Field(default=None, ge=0)

    @field_validator("team")
    @classmethod
    def team_must_be_valid(cls, v: str) -> str:
        return _validate_team_abbr(v)


class Injury(BaseModel):
    """One official NFL injury-report row (nflverse injuries release).

    The source's ``report_status`` is preserved VERBATIM (e.g. "Out",
    "Doubtful", "Questionable", "Note" — "Note" appears in the real 2024
    data). Unknown statuses are never rewritten; they are accepted with a
    warning by the ingestion validators. Practice-participation statuses
    are likewise kept as published.
    """

    season: int = Field(ge=1960)
    week: int = Field(ge=1, le=22)
    game_type: Optional[str] = None
    team: str
    gsis_id: Optional[str] = None
    position: Optional[str] = None
    full_name: str
    first_name: Optional[str] = None
    last_name: Optional[str] = None
    report_status: Optional[str] = None
    report_primary_injury: Optional[str] = None
    report_secondary_injury: Optional[str] = None
    practice_status: Optional[str] = None
    practice_primary_injury: Optional[str] = None
    practice_secondary_injury: Optional[str] = None
    date_modified: Optional[datetime] = None
    source: str = "nflverse-injuries"
    ingested_at: Optional[datetime] = None

    @field_validator("team")
    @classmethod
    def team_must_be_valid(cls, v: str) -> str:
        return _validate_team_abbr(v)


class Weather(BaseModel):
    """Kickoff-hour weather at the venue (Open-Meteo record).

    Null measurements mean unknown — never estimated. Dome games have
    ``dome=True`` and null outdoor measurements. ``kickoff`` is the UTC
    hour the values describe (nearest-hour match to scheduled kickoff).
    """

    game_id: str
    venue: Optional[str] = None
    latitude: Optional[float] = None
    longitude: Optional[float] = None
    kickoff: Optional[datetime] = None
    temp_f: Optional[float] = None
    wind_mph: Optional[float] = Field(default=None, ge=0)
    wind_gust_mph: Optional[float] = Field(default=None, ge=0)
    precip_prob: Optional[float] = Field(default=None, ge=0, le=1)
    precip_in: Optional[float] = Field(default=None, ge=0)
    weathercode: Optional[int] = None
    conditions: Optional[str] = None
    dome: Optional[bool] = None
    source: str = "open-meteo"
    ingested_at: Optional[datetime] = None


class OddsSnapshot(BaseModel):
    game_id: str
    timestamp: datetime
    sportsbook: str
    spread: Optional[float] = None
    moneyline_home: Optional[int] = None
    moneyline_away: Optional[int] = None
    total: Optional[float] = Field(default=None, ge=0)
    # Per-side American-odds prices for spread/total markets. Optional so
    # older snapshots (moneyline-only) stay valid; the edge engine skips a
    # market when either side's price is missing rather than guessing.
    spread_home_price: Optional[int] = None
    spread_away_price: Optional[int] = None
    total_over_price: Optional[int] = None
    total_under_price: Optional[int] = None


class ChatMessage(BaseModel):
    role: Literal["user", "assistant"]
    content: str
    created_at: Optional[datetime] = None


class Conversation(BaseModel):
    """Numby chat session (Phase 7, spec 52 ``conversations`` collection)."""

    session_id: str
    user_id: Optional[str] = None
    messages: list[ChatMessage] = Field(default_factory=list)
    created_at: Optional[datetime] = None
    updated_at: Optional[datetime] = None
