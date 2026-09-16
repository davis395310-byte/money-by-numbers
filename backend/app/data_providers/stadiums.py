"""NFL stadium coordinates.

Reference data: latitude/longitude for the 30 current NFL venues plus the
international venues the league uses, compiled from public stadium location
facts (street addresses are well-documented public information). Keyed by
lowercase venue name as it appears in the nflverse schedules ``stadium``
column, with common aliases (sponsor renames happen often).

Provenance and verification, 2026-09-09:
- Every coordinate was cross-checked against city-level geocoding: each
  venue is within 60 km of its metro center. Weather is a metro-scale
  phenomenon — a few kilometres of error do not change kickoff conditions.
- The curated map is consulted FIRST, specifically because city-name
  geocoding can be ambiguous (an early draft geocoded "Santa Clara" to
  Santa Clara, Cuba instead of Santa Clara, California). Ambiguous
  geocoding results are never accepted silently: the weather provider's
  geocoding fallback only applies to venues missing from this map, and
  unresolved venues are skipped with a recorded error.

Source of truth for venue *names*: the nflverse schedules release. If a
venue is missing here, the weather provider falls back to the Open-Meteo
geocoding API, then skips honestly.
"""

from typing import Dict, Optional, Tuple

# (latitude, longitude)
STADIUM_COORDS: Dict[str, Tuple[float, float]] = {
    # AFC East
    "highmark stadium": (42.7738, -78.7868),          # Buffalo (Orchard Park)
    "hard rock stadium": (25.9580, -80.2389),         # Miami (Miami Gardens)
    "gillette stadium": (42.0909, -71.2643),          # New England (Foxborough)
    "metlife stadium": (40.8135, -74.0745),           # NY Jets/Giants (E. Rutherford)
    # AFC North
    "m&t bank stadium": (39.2780, -76.6227),          # Baltimore
    "paycor stadium": (39.0954, -84.5160),            # Cincinnati
    "huntington bank field": (41.5061, -81.6995),     # Cleveland
    "cleveland browns stadium": (41.5061, -81.6995),  # alias
    "acrisure stadium": (40.4468, -80.0158),          # Pittsburgh
    "heinz field": (40.4468, -80.0158),               # alias
    # AFC South
    "nrg stadium": (29.6847, -95.4107),               # Houston
    "lucas oil stadium": (39.7601, -86.1639),         # Indianapolis
    "everbank stadium": (30.3239, -81.6373),          # Jacksonville
    "tiaa bank field": (30.3239, -81.6373),           # alias
    "nissan stadium": (36.1665, -86.7713),            # Tennessee (Nashville)
    # AFC West
    "empower field at mile high": (39.7439, -105.0201),  # Denver
    "sports authority field": (39.7439, -105.0201),      # alias
    "arrowhead stadium": (39.0489, -94.4839),            # Kansas City
    "geha field at arrowhead stadium": (39.0489, -94.4839),  # alias
    "allegiant stadium": (36.0908, -115.1839),           # Las Vegas
    "sofi stadium": (33.9535, -118.3392),               # LA Chargers/Rams (Inglewood)
    # NFC East
    "at&t stadium": (32.7473, -97.0945),              # Dallas (Arlington)
    "lincoln financial field": (39.9008, -75.1675),   # Philadelphia
    "northwest stadium": (38.9076, -76.8645),         # Washington (Landover)
    "fedexfield": (38.9076, -76.8645),                # alias
    # NFC North
    "soldier field": (41.8623, -87.6167),             # Chicago
    "ford field": (42.3400, -83.0456),                # Detroit
    "lambeau field": (44.5013, -88.0622),             # Green Bay
    "u.s. bank stadium": (44.9738, -93.2581),         # Minnesota (Minneapolis)
    "us bank stadium": (44.9738, -93.2581),           # alias (punctuation variant)
    # NFC South
    "mercedes-benz stadium": (33.7554, -84.4008),     # Atlanta
    "bank of america stadium": (35.2258, -80.8528),   # Carolina (Charlotte)
    "caesars superdome": (29.9508, -90.0811),         # New Orleans
    "mercedes-benz superdome": (29.9508, -90.0811),   # alias
    "raymond james stadium": (27.9759, -82.5033),     # Tampa Bay
    # NFC West
    "state farm stadium": (33.5277, -112.2626),       # Arizona (Glendale)
    "levis stadium": (37.4030, -121.9700),            # San Francisco (Santa Clara)
    "levi's stadium": (37.4030, -121.9700),           # alias (punctuation variant)
    "lumen field": (47.5952, -122.3316),              # Seattle
    # International series venues
    "wembley stadium": (51.5560, -0.2795),            # London
    "tottenham hotspur stadium": (51.6043, -0.0664),  # London
    "allianz arena": (48.2188, 11.6247),              # Munich
    "deutsche bank park": (50.0686, 8.6455),          # Frankfurt
    "estadio azteca": (19.3029, -99.1505),            # Mexico City
    "arena corinthians": (-23.5452, -46.4743),        # Sao Paulo
    "melbourne cricket ground": (-37.8199, 144.9834),  # Melbourne
    "croke park": (53.3608, -6.2511),                 # Dublin
    "santiago bernabeu stadium": (40.4531, -3.6883),   # Madrid
    # Historical venues (relocations/renames; for archive backfill)
    "georgia dome": (33.7554, -84.4008),              # Atlanta (pre-2017)
    "rca dome": (39.7601, -86.1639),                  # Indianapolis (pre-2008)
    "alltel stadium": (30.3239, -81.6373),            # Jacksonville (pre-2010)
    "hubert h. humphrey metrodome": (44.9738, -93.2581),  # Minnesota (pre-2014)
    "metrodome": (44.9738, -93.2581),                 # alias
    "edward jones dome": (38.6328, -90.1888),         # St. Louis Rams (pre-2016)
    "qualcomm stadium": (32.7831, -117.1196),         # San Diego (pre-2017)
    "sdccu stadium": (32.7831, -117.1196),            # alias
    "oakland coliseum": (37.7516, -122.2005),         # Oakland Raiders (pre-2020)
    "o.co coliseum": (37.7516, -122.2005),            # alias
    "candlestick park": (37.7136, -122.3861),         # San Francisco (pre-2014)
    "texas stadium": (32.8372, -96.9136),             # Dallas (pre-2009)
}


def lookup_stadium(venue: Optional[str]) -> Optional[Tuple[float, float]]:
    """Return (lat, lon) for a venue name, or None when unknown."""
    if not venue:
        return None
    key = str(venue).strip().lower()
    if key in STADIUM_COORDS:
        return STADIUM_COORDS[key]
    # Try without punctuation (handles "U.S. Bank" vs "US Bank" etc.).
    stripped = "".join(ch for ch in key if ch.isalnum() or ch.isspace()).strip()
    for name, coords in STADIUM_COORDS.items():
        flat = "".join(ch for ch in name if ch.isalnum() or ch.isspace()).strip()
        if flat == stripped:
            return coords
    return None
