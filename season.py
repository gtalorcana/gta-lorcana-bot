"""
Season state — loaded from Bot State on startup, falls back to constants.py defaults.

Call season.init(bot_state) early in on_ready before any tasks run.

All consumers must access values via:
    import season
    season.CURRENT_SEASON          # correct — module attribute lookup at call time

NOT via:
    from season import CURRENT_SEASON   # wrong — captures value at import time
"""

import constants as _c

# ── Raw season values ──────────────────────────────────────────────────────────

CURRENT_SEASON:        str = None
SEASON_START_DATE:     str = None
SEASON_END_DATE:       str = None
SET_CHAMPS_START_DATE: str = None
SET_CHAMPS_END_DATE:   str = None

# ── Derived datetime strings (RPH API format) ──────────────────────────────────

SEASON_START_DT:     str = None
SEASON_END_DT:       str = None
SET_CHAMPS_START_DT: str = None
SET_CHAMPS_END_DT:   str = None

# ── Derived sheet names ────────────────────────────────────────────────────────

STANDINGS_SHEET_NAME:         str = None
EVENTS_SHEET_NAME:            str = None
LEADERBOARD_SHEET_NAME:       str = None
SET_CHAMPS_EVENTS_SHEET_NAME: str = None

# ── Derived range names ────────────────────────────────────────────────────────

STANDINGS_RANGE_NAME:         str = None
EVENTS_RANGE_NAME:            str = None
EVENTS_TIMESTAMP_RANGE_NAME:  str = None
LEADERBOARD_RANGE_NAME:       str = None
SET_CHAMPS_EVENTS_RANGE_NAME: str = None


def init(bot_state: dict) -> None:
    """
    Load season config from bot_state and rebuild all derived values.
    Falls back to constants.py defaults if keys are absent from bot_state.
    """
    global CURRENT_SEASON, SEASON_START_DATE, SEASON_END_DATE
    global SET_CHAMPS_START_DATE, SET_CHAMPS_END_DATE
    global SEASON_START_DT, SEASON_END_DT, SET_CHAMPS_START_DT, SET_CHAMPS_END_DT
    global STANDINGS_SHEET_NAME, EVENTS_SHEET_NAME, LEADERBOARD_SHEET_NAME
    global SET_CHAMPS_EVENTS_SHEET_NAME
    global STANDINGS_RANGE_NAME, EVENTS_RANGE_NAME, EVENTS_TIMESTAMP_RANGE_NAME
    global LEADERBOARD_RANGE_NAME, SET_CHAMPS_EVENTS_RANGE_NAME

    CURRENT_SEASON        = bot_state.get('season',                _c.CURRENT_SEASON)
    SEASON_START_DATE     = bot_state.get('season_start_date',     _c.SEASON_START_DATE)
    SEASON_END_DATE       = bot_state.get('season_end_date',       _c.SEASON_END_DATE)
    SET_CHAMPS_START_DATE = bot_state.get('set_champs_start_date', _c.SET_CHAMPS_START_DATE)
    SET_CHAMPS_END_DATE   = bot_state.get('set_champs_end_date',   _c.SET_CHAMPS_END_DATE)

    SEASON_START_DT     = SEASON_START_DATE     + _c.START_OF_DAY
    SEASON_END_DT       = SEASON_END_DATE       + _c.END_OF_DAY
    SET_CHAMPS_START_DT = SET_CHAMPS_START_DATE + _c.START_OF_DAY
    SET_CHAMPS_END_DT   = SET_CHAMPS_END_DATE   + _c.END_OF_DAY

    STANDINGS_SHEET_NAME         = CURRENT_SEASON + " Standings - User Reported"
    EVENTS_SHEET_NAME            = CURRENT_SEASON + " Events - User Reported"
    LEADERBOARD_SHEET_NAME       = CURRENT_SEASON + " Leaderboard"
    SET_CHAMPS_EVENTS_SHEET_NAME = CURRENT_SEASON + " Set Champs"

    STANDINGS_RANGE_NAME         = STANDINGS_SHEET_NAME         + "!A3:G"
    EVENTS_RANGE_NAME            = EVENTS_SHEET_NAME            + "!A2:G"
    EVENTS_TIMESTAMP_RANGE_NAME  = EVENTS_SHEET_NAME            + "!J1:K1"
    LEADERBOARD_RANGE_NAME       = LEADERBOARD_SHEET_NAME       + "!A2:D"
    SET_CHAMPS_EVENTS_RANGE_NAME = SET_CHAMPS_EVENTS_SHEET_NAME + "!A2:H"

    print(f"  ♻ Season: {CURRENT_SEASON}  ({SEASON_START_DATE} → {SEASON_END_DATE})")


# Initialise from constants defaults immediately so the module is usable
# before on_ready fires (e.g. in tests or standalone scripts).
init({})
