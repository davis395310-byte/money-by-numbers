"""Static NFL team reference data.

The 32 franchises with canonical abbreviations matching
``app.models.core.VALID_TEAM_ABBRS``. Team identity data (name, city,
conference, division) is stable reference information, not scraped data,
and lives here so providers and ingestion share one source of truth.
"""

TEAMS = [
    # AFC East
    {"abbr": "BUF", "name": "Bills", "city": "Buffalo", "conference": "AFC", "division": "East"},
    {"abbr": "MIA", "name": "Dolphins", "city": "Miami", "conference": "AFC", "division": "East"},
    {"abbr": "NE", "name": "Patriots", "city": "New England", "conference": "AFC", "division": "East"},
    {"abbr": "NYJ", "name": "Jets", "city": "New York", "conference": "AFC", "division": "East"},
    # AFC North
    {"abbr": "BAL", "name": "Ravens", "city": "Baltimore", "conference": "AFC", "division": "North"},
    {"abbr": "CIN", "name": "Bengals", "city": "Cincinnati", "conference": "AFC", "division": "North"},
    {"abbr": "CLE", "name": "Browns", "city": "Cleveland", "conference": "AFC", "division": "North"},
    {"abbr": "PIT", "name": "Steelers", "city": "Pittsburgh", "conference": "AFC", "division": "North"},
    # AFC South
    {"abbr": "HOU", "name": "Texans", "city": "Houston", "conference": "AFC", "division": "South"},
    {"abbr": "IND", "name": "Colts", "city": "Indianapolis", "conference": "AFC", "division": "South"},
    {"abbr": "JAX", "name": "Jaguars", "city": "Jacksonville", "conference": "AFC", "division": "South"},
    {"abbr": "TEN", "name": "Titans", "city": "Tennessee", "conference": "AFC", "division": "South"},
    # AFC West
    {"abbr": "DEN", "name": "Broncos", "city": "Denver", "conference": "AFC", "division": "West"},
    {"abbr": "KC", "name": "Chiefs", "city": "Kansas City", "conference": "AFC", "division": "West"},
    {"abbr": "LV", "name": "Raiders", "city": "Las Vegas", "conference": "AFC", "division": "West"},
    {"abbr": "LAC", "name": "Chargers", "city": "Los Angeles", "conference": "AFC", "division": "West"},
    # NFC East
    {"abbr": "DAL", "name": "Cowboys", "city": "Dallas", "conference": "NFC", "division": "East"},
    {"abbr": "NYG", "name": "Giants", "city": "New York", "conference": "NFC", "division": "East"},
    {"abbr": "PHI", "name": "Eagles", "city": "Philadelphia", "conference": "NFC", "division": "East"},
    {"abbr": "WAS", "name": "Commanders", "city": "Washington", "conference": "NFC", "division": "East"},
    # NFC North
    {"abbr": "CHI", "name": "Bears", "city": "Chicago", "conference": "NFC", "division": "North"},
    {"abbr": "DET", "name": "Lions", "city": "Detroit", "conference": "NFC", "division": "North"},
    {"abbr": "GB", "name": "Packers", "city": "Green Bay", "conference": "NFC", "division": "North"},
    {"abbr": "MIN", "name": "Vikings", "city": "Minnesota", "conference": "NFC", "division": "North"},
    # NFC South
    {"abbr": "ATL", "name": "Falcons", "city": "Atlanta", "conference": "NFC", "division": "South"},
    {"abbr": "CAR", "name": "Panthers", "city": "Carolina", "conference": "NFC", "division": "South"},
    {"abbr": "NO", "name": "Saints", "city": "New Orleans", "conference": "NFC", "division": "South"},
    {"abbr": "TB", "name": "Buccaneers", "city": "Tampa Bay", "conference": "NFC", "division": "South"},
    # NFC West
    {"abbr": "ARI", "name": "Cardinals", "city": "Arizona", "conference": "NFC", "division": "West"},
    {"abbr": "LAR", "name": "Rams", "city": "Los Angeles", "conference": "NFC", "division": "West"},
    {"abbr": "SF", "name": "49ers", "city": "San Francisco", "conference": "NFC", "division": "West"},
    {"abbr": "SEA", "name": "Seahawks", "city": "Seattle", "conference": "NFC", "division": "West"},
]
