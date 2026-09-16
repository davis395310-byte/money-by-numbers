"""Document models for Money by Numbers."""

from .business import (
    AffiliateCampaign,
    AffiliateClick,
    AnalyticsEvent,
    SchedulerJob,
    Session,
    Subscription,
    User,
    build_click_record,
)
from .core import NFL_TEAMS, Game, GameStats, Injury, OddsSnapshot, Player, Team, Weather
from .predictions import Backtest, ModelVersion, Prediction

__all__ = [
    "NFL_TEAMS",
    "Team",
    "Player",
    "Game",
    "GameStats",
    "Injury",
    "Weather",
    "OddsSnapshot",
    "Prediction",
    "ModelVersion",
    "Backtest",
    "User",
    "Session",
    "AffiliateClick",
    "AffiliateCampaign",
    "Subscription",
    "AnalyticsEvent",
    "SchedulerJob",
    "build_click_record",
]
