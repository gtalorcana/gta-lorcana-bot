"""
Season state — loaded from Bot State on startup, falls back to constants.py defaults.

Call season.init(bot_state) early in on_ready before any tasks run.

All consumers must access values via:
    import season
    season.CURRENT_SEASON          # correct — module attribute lookup at call time

NOT via:
    from season import CURRENT_SEASON   # wrong — captures value at import time
"""

from datetime import date, datetime, time, timedelta
from urllib.parse import quote
from zoneinfo import ZoneInfo

import constants as _c

_TZ_ET = ZoneInfo(_c.TIMEZONE_ET)


def _start_of_day_utc(iso_date: str) -> str:
    """Return URL-encoded UTC timestamp for midnight ET on the given date."""
    dt = datetime.combine(date.fromisoformat(iso_date), time.min, tzinfo=_TZ_ET)
    return quote(dt.astimezone(ZoneInfo("UTC")).strftime("%Y-%m-%dT%H:%M:%S.000Z"))


def _end_of_day_utc(iso_date: str) -> str:
    """Return URL-encoded UTC timestamp for 11:59:59.999 PM ET on the given date."""
    dt = datetime.combine(date.fromisoformat(iso_date) + timedelta(days=1), time.min, tzinfo=_TZ_ET)
    dt -= timedelta(milliseconds=1)
    return quote(dt.astimezone(ZoneInfo("UTC")).strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3] + "Z")

# ── Raw season values ──────────────────────────────────────────────────────────

CURRENT_SEASON:        str = None
SEASON_START_DATE:     str = None
SEASON_END_DATE:       str = None
SET_CHAMPS_START_DATE: str = None
SET_CHAMPS_END_DATE:   str = None
# Prereleases belong to the incoming set, not the season, so they are set on their
# own with /prerelease-dates rather than by /season-rollover.
PRERELEASE_START_DATE: str = None
PRERELEASE_END_DATE:   str = None
PRERELEASE_SET_NAME:   str = None

# ── Derived datetime strings (RPH API format) ──────────────────────────────────

SEASON_START_DT:     str = None
SEASON_END_DT:       str = None
SET_CHAMPS_START_DT: str = None
SET_CHAMPS_END_DT:   str = None
PRERELEASE_START_DT: str = None
PRERELEASE_END_DT:   str = None

# ── Derived sheet names ────────────────────────────────────────────────────────

STANDINGS_SHEET_NAME:         str = None
EVENTS_SHEET_NAME:            str = None
LEADERBOARD_SHEET_NAME:       str = None
RESULTS_SHEET_NAME:           str = None
SET_CHAMPS_EVENTS_SHEET_NAME: str = None

# ── Derived range names ────────────────────────────────────────────────────────

STANDINGS_RANGE_NAME:         str = None
EVENTS_RANGE_NAME:            str = None
EVENTS_TIMESTAMP_RANGE_NAME:  str = None
LEADERBOARD_RANGE_NAME:       str = None
RESULTS_RANGE_NAME:           str = None
SET_CHAMPS_EVENTS_RANGE_NAME: str = None

# ── Season calendar ────────────────────────────────────────────────────────────
#
# Every row of the Seasons tab, oldest first, so the bot can hold more than one
# season at a time. It has to: a set's prerelease is scheduled by stores weeks
# before that season starts, and the previous season's Set Champs can still be
# running when it does (S13 Set Champs Sep 4-27 2026, S14 prerelease Oct 16-22).
#
# League scoring — results eligibility, standings, leaderboard, roles, every sheet
# name — stays pinned to CURRENT_SEASON. Only the digests resolve their own window.

SEASONS: list[dict] = []        # all calendar rows
CURRENT: dict | None = None     # the row for CURRENT_SEASON, if there is one
CALENDAR_SOURCE: str = 'none'   # 'sheet' | 'bot_state' | 'none' — reported by /seasons

_KEEP = object()  # init(calendar=_KEEP): leave the loaded calendar as it is


def get_season(season_id: str) -> dict | None:
    """The calendar row for a season id, or None."""
    return next((r for r in SEASONS if r['season'] == season_id), None)


def rph_day_start(iso_date: str) -> str:
    """RPH-format UTC timestamp for the start of the given ET day."""
    return _start_of_day_utc(iso_date)


def rph_day_end(iso_date: str) -> str:
    """RPH-format UTC timestamp for the end of the given ET day."""
    return _end_of_day_utc(iso_date)


def set_champs_window() -> tuple[str, str] | None:
    """
    (start, end) RPH timestamps for the CURRENT season's Set Champs, or None.

    Pinned to CURRENT_SEASON rather than "whichever Set Champs window is open",
    because the sheet these events are written to is SET_CHAMPS_EVENTS_RANGE_NAME,
    which is derived from CURRENT_SEASON. Rolling over mid-Set-Champs therefore
    freezes the outgoing season's tab — /season-rollover warns when it would.
    """
    if not (SET_CHAMPS_START_DATE and SET_CHAMPS_END_DATE):
        return None
    return rph_day_start(SET_CHAMPS_START_DATE), rph_day_end(SET_CHAMPS_END_DATE)


def active_prerelease(today: date | None = None) -> dict | None:
    """
    The calendar row whose prerelease has not finished — earliest prerelease_end
    on or after today.

    This is the *incoming* season for most of the year: while S13 was current, the
    open prerelease window was S14's. It stays S14's after rollover until Oct 22,
    then moves to S15 as soon as S15's row has dates. That is exactly why the
    prerelease digest cannot hang off CURRENT_SEASON.
    """
    today = today or datetime.now(_TZ_ET).date()
    candidates = [r for r in SEASONS
                  if r.get('prerelease_start') and r.get('prerelease_end')
                  and date.fromisoformat(r['prerelease_end']) >= today]
    return min(candidates, key=lambda r: r['prerelease_end']) if candidates else None


def prerelease_window(today: date | None = None) -> tuple[str, str, str, str] | None:
    """(start, end, set_name, season_id) for active_prerelease(), or None."""
    row = active_prerelease(today)
    if not row:
        return None
    return (rph_day_start(row['prerelease_start']), rph_day_end(row['prerelease_end']),
            row.get('set_name') or '', row['season'])


def init(bot_state: dict, calendar=_KEEP) -> None:
    """
    Load season config from bot_state plus the Seasons calendar and rebuild all
    derived values.

    calendar: rows from stores.load_season_calendar(), or _KEEP (the default) to
        leave the calendar already in memory untouched. The sentinel is _KEEP rather
        than None so that None stays available to mean "there is genuinely no
        calendar", and so a caller reloading only Bot State can never blank it.

    The calendar is passed in, not read here: this module imports nothing but
    constants, while the Sheets client lives in stores.py.
    """
    global CURRENT_SEASON, SEASON_START_DATE, SEASON_END_DATE
    global SET_CHAMPS_START_DATE, SET_CHAMPS_END_DATE
    global SEASON_START_DT, SEASON_END_DT, SET_CHAMPS_START_DT, SET_CHAMPS_END_DT
    global PRERELEASE_START_DATE, PRERELEASE_END_DATE, PRERELEASE_SET_NAME
    global PRERELEASE_START_DT, PRERELEASE_END_DT
    global STANDINGS_SHEET_NAME, EVENTS_SHEET_NAME, LEADERBOARD_SHEET_NAME
    global RESULTS_SHEET_NAME, SET_CHAMPS_EVENTS_SHEET_NAME
    global STANDINGS_RANGE_NAME, EVENTS_RANGE_NAME, EVENTS_TIMESTAMP_RANGE_NAME
    global LEADERBOARD_RANGE_NAME, RESULTS_RANGE_NAME, SET_CHAMPS_EVENTS_RANGE_NAME
    global SEASONS, CURRENT, CALENDAR_SOURCE

    if calendar is not _KEEP:
        SEASONS = list(calendar or [])

    CURRENT_SEASON = bot_state.get('season', _c.CURRENT_SEASON)
    CURRENT        = get_season(CURRENT_SEASON)

    if CURRENT:
        CALENDAR_SOURCE = 'sheet'
    else:
        # Legacy fallback: synthesize the current season from the flat Bot State keys
        # the Seasons tab replaces, so this ships before the tab exists and can be
        # rolled back to. Once a season has passed on the tab, delete this branch
        # and the keys with it.
        # TODO (after S14 ends): drop the flat-key fallback.
        legacy = {
            'season':           CURRENT_SEASON,
            'set_name':         '',
            'sheet_row':        None,
            'source':           'bot_state',
            'prerelease_start': bot_state.get('prerelease_start_date') or None,
            'prerelease_end':   bot_state.get('prerelease_end_date')   or None,
            'season_start':     bot_state.get('season_start_date')     or None,
            'season_end':       bot_state.get('season_end_date')       or None,
            'set_champs_start': bot_state.get('set_champs_start_date') or None,
            'set_champs_end':   bot_state.get('set_champs_end_date')   or None,
        }
        if legacy['season_start'] or legacy['set_champs_start']:
            CURRENT         = legacy
            CALENDAR_SOURCE = 'bot_state'
            print(f"  ⚠ {CURRENT_SEASON} has no row in the Seasons tab — using the legacy "
                  f"Bot State date keys. Add the row to the tab.")
        else:
            CALENDAR_SOURCE = 'none'

    _c_row                = CURRENT or {}
    SEASON_START_DATE     = _c_row.get('season_start')     or None
    SEASON_END_DATE       = _c_row.get('season_end')       or None
    SET_CHAMPS_START_DATE = _c_row.get('set_champs_start') or None
    SET_CHAMPS_END_DATE   = _c_row.get('set_champs_end')   or None
    PRERELEASE_START_DATE = bot_state.get('prerelease_start_date') or None
    PRERELEASE_END_DATE   = bot_state.get('prerelease_end_date')   or None
    PRERELEASE_SET_NAME   = bot_state.get('prerelease_set_name')   or None

    # Derived datetime strings (DST-aware) — None if dates not configured
    SEASON_START_DT     = _start_of_day_utc(SEASON_START_DATE)     if SEASON_START_DATE     else None
    SEASON_END_DT       = _end_of_day_utc(SEASON_END_DATE)         if SEASON_END_DATE       else None
    SET_CHAMPS_START_DT = _start_of_day_utc(SET_CHAMPS_START_DATE) if SET_CHAMPS_START_DATE else None
    SET_CHAMPS_END_DT   = _end_of_day_utc(SET_CHAMPS_END_DATE)    if SET_CHAMPS_END_DATE   else None
    PRERELEASE_START_DT = _start_of_day_utc(PRERELEASE_START_DATE) if PRERELEASE_START_DATE else None
    PRERELEASE_END_DT   = _end_of_day_utc(PRERELEASE_END_DATE)     if PRERELEASE_END_DATE   else None

    STANDINGS_SHEET_NAME         = CURRENT_SEASON + " Standings"
    EVENTS_SHEET_NAME            = CURRENT_SEASON + " Events"
    LEADERBOARD_SHEET_NAME       = CURRENT_SEASON + " Leaderboard"
    RESULTS_SHEET_NAME           = CURRENT_SEASON + " Results"
    SET_CHAMPS_EVENTS_SHEET_NAME = CURRENT_SEASON + " Set Champs"

    STANDINGS_RANGE_NAME         = STANDINGS_SHEET_NAME         + "!A3:I"
    EVENTS_RANGE_NAME            = EVENTS_SHEET_NAME            + "!A2:G"
    EVENTS_TIMESTAMP_RANGE_NAME  = EVENTS_SHEET_NAME            + "!J1:K1"
    LEADERBOARD_RANGE_NAME       = LEADERBOARD_SHEET_NAME       + "!A2:E"
    RESULTS_RANGE_NAME           = RESULTS_SHEET_NAME           + "!A2:P"
    SET_CHAMPS_EVENTS_RANGE_NAME = SET_CHAMPS_EVENTS_SHEET_NAME + "!A2:I"

    print(f"  ♻ Season: {CURRENT_SEASON}  ({SEASON_START_DATE} → {SEASON_END_DATE})")


def is_past_reporting_cutoff(when: datetime) -> bool:
    """
    True if `when` (a timezone-aware datetime, e.g. a thread's created_at) falls
    after the season-end date, evaluated in ET. Results must be reported by the
    end of the season's final day — there is no reporting buffer, so the cutoff
    is simply the season end. Always False when the season end is not configured.
    """
    if not SEASON_END_DATE:
        return False
    when_et_date = when.astimezone(_TZ_ET).date()
    return when_et_date > date.fromisoformat(SEASON_END_DATE)


# Initialise from constants defaults immediately so the module is usable
# before on_ready fires (e.g. in tests or standalone scripts).
init({})
