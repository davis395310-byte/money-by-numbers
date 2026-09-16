"""NFL data providers."""

from .base import DiskCache, NFLDataProvider, ProviderResult, RateLimiter, retry_with_backoff
from .injuries import NflverseInjuryProvider
from .nflverse import NflverseProvider, normalize_team_abbr
from .odds import OddsProvider, TheOddsApiProvider, check_budget
from .stadiums import STADIUM_COORDS, lookup_stadium
from .teams_data import TEAMS
from .weather import OpenMeteoWeatherProvider, classify_roof, describe_weathercode

__all__ = [
    "DiskCache",
    "NFLDataProvider",
    "NflverseProvider",
    "NflverseInjuryProvider",
    "OddsProvider",
    "OpenMeteoWeatherProvider",
    "ProviderResult",
    "RateLimiter",
    "STADIUM_COORDS",
    "TEAMS",
    "TheOddsApiProvider",
    "check_budget",
    "classify_roof",
    "describe_weathercode",
    "lookup_stadium",
    "normalize_team_abbr",
    "retry_with_backoff",
]
