"""
RSVP & Where-to-Play utility module.

Analyses current season RPH event data to classify Ontario Lorcana store events
as Regular or Occasional, persists state to Google Sheets, and determines which
events are expected on a given date.

Grouping key:
  Each unique (store_id, day_of_week, time, format) combination is tracked
  independently. A store running Standard on Saturdays at 6PM and Core
  Constructed on Wednesdays at 7PM gets two separate rows with separate streaks.

Classification rules (symmetric):
  Regular    — current consecutive streak >= RSVP_MIN_CONSECUTIVE_WEEKS
  Occasional — has some history but streak < RSVP_MIN_CONSECUTIVE_WEEKS,
               or missed RSVP_MISS_WEEKS_BEFORE_RELEGATE consecutive weeks

State persistence:
  Classifications are read from and written back to the
  STORE_CLASSIFICATIONS_SHEET_NAME tab in LEAGUE_SPREADSHEET_ID.
  This survives Fly.io restarts. The bootstrap script seeds this sheet
  using the last 2 weeks of RPH data.

Times:
  All event times are converted to America/Toronto (handles EST/EDT automatically).
"""

from collections import Counter, defaultdict
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

from util.rph_api_utils import RphApi
from util.google_sheets_api_utils import GoogleSheetsApi
from constants import (
    SEASON_START_DT,
    SEASON_END_DT,
    LEAGUE_SPREADSHEET_ID,
    STORE_CLASSIFICATIONS_RANGE_NAME,
    RSVP_MIN_CONSECUTIVE_WEEKS,
    RSVP_MISS_WEEKS_BEFORE_RELEGATE,
)

_TZ_TORONTO = ZoneInfo("America/Toronto")

# Singletons — reuse existing connections if already constructed in rph_util
_rph_api = RphApi()
_gs      = GoogleSheetsApi()

_DAY_NAMES = ['Monday', 'Tuesday', 'Wednesday', 'Thursday', 'Friday', 'Saturday', 'Sunday']


# ── Internal helpers ──────────────────────────────────────────────────────────

def _fetch_current_season_events() -> list:
    """Fetch all Ontario Lorcana events for the current season from RPH."""
    print(f"  → Fetching current season RPH events...")
    events = _rph_api.get_events(SEASON_START_DT, SEASON_END_DT)
    print(f"  ✓ {len(events)} events fetched")
    return events


def _get_week_start(d: date) -> date:
    """Return the Monday of the week containing date d."""
    return d - timedelta(days=d.weekday())


def _parse_event_time_toronto(start_datetime: str) -> str:
    """
    Parse an RPH start_datetime string and return the time in Toronto time.
    RPH datetimes are UTC ISO 8601, e.g. '2026-02-15T19:00:00Z'.
    Returns a human-readable string like '7:00 PM'.
    """
    try:
        dt_utc     = datetime.fromisoformat(start_datetime.replace('Z', '+00:00'))
        dt_toronto = dt_utc.astimezone(_TZ_TORONTO)
        return dt_toronto.strftime('%I:%M %p').lstrip('0')
    except Exception:
        return ''


def _build_event_type_map(events: list) -> dict:
    """
    Build a map keyed by (store_id, day_of_week, time, format) — one entry per
    unique event type. Each entry tracks the weeks it ran so streaks can be
    computed independently per event type.

    Returns:
        {
            (store_id, day_of_week_str, time_str, format_str): {
                'store_id':    str,
                'store_name':  str,
                'day':         str,   # e.g. 'Saturday'
                'time':        str,   # e.g. '7:00 PM'
                'format':      str,   # e.g. 'Standard'
                'week_starts': {date, ...},
            }
        }
    """
    event_map = defaultdict(lambda: {
        'store_id':    '',
        'store_name':  '',
        'day':         '',
        'time':        '',
        'format':      '',
        'week_starts': set(),
    })

    for event in events:
        store_id   = event['store']['id']
        store_name = event['store']['name']
        event_date = date.fromisoformat(event['start_datetime'][:10])
        day_str    = _DAY_NAMES[event_date.weekday()]
        time_str   = _parse_event_time_toronto(event['start_datetime'])
        format_str = event['gameplay_format']['name']
        week_start = _get_week_start(event_date)

        key = (store_id, day_str, time_str, format_str)
        event_map[key]['store_id']   = store_id
        event_map[key]['store_name'] = store_name
        event_map[key]['day']        = day_str
        event_map[key]['time']       = time_str
        event_map[key]['format']     = format_str
        event_map[key]['week_starts'].add(week_start)

    return event_map


def _compute_streaks(week_starts: set, reference_date: date) -> tuple[int, int]:
    """
    Compute streak metrics for an event type given its week-start dates.

    Args:
        week_starts:    Set of Monday dates on which this event type ran.
        reference_date: Evaluate streaks as of this date (typically today).

    Returns:
        (current_streak, current_miss_streak) where:
          current_streak      — consecutive weeks WITH events ending at reference_date
          current_miss_streak — consecutive weeks WITHOUT events ending at reference_date
    """
    if not week_starts:
        return 0, 0

    ref_week = _get_week_start(reference_date)
    min_week = min(week_starts)

    current_streak = 0
    check = ref_week
    while check in week_starts:
        current_streak += 1
        check -= timedelta(weeks=1)

    current_miss_streak = 0
    check = ref_week
    while check not in week_starts and check >= min_week:
        current_miss_streak += 1
        check -= timedelta(weeks=1)

    return current_streak, current_miss_streak


def _classify_event_types(event_map: dict, reference_date: date) -> dict:
    """
    Classify each (store, day, time, format) event type into Regular or Occasional.

    Returns:
        {
            'regular': [
                {
                    'store_id':    str,
                    'store_name':  str,
                        'status':      'Regular',
                    'streak':      int,
                    'event_count': int,
                    'day':         str,   # e.g. 'Saturday'
                    'time':        str,   # e.g. '7:00 PM'
                    'format':      str,   # e.g. 'Standard'
                },
                ...
            ],
            'occasional': [ ... same shape ... ],
        }
    """
    regular    = []
    occasional = []

    for key, info in event_map.items():
        current_streak, miss_streak = _compute_streaks(info['week_starts'], reference_date)
        event_count = len(info['week_starts'])

        entry = {
            'store_id':    info['store_id'],
            'store_name':  info['store_name'],
            'streak':      current_streak,
            'event_count': event_count,
            'day':         info['day'],
            'time':        info['time'],
            'format':      info['format'],
        }

        if current_streak >= RSVP_MIN_CONSECUTIVE_WEEKS:
            entry['status'] = 'Regular'
            regular.append(entry)
        elif event_count > 0:
            entry['status'] = 'Occasional'
            occasional.append(entry)

    regular.sort(key=lambda s: (-s['streak'], s['store_name'], s['day'], s['time']))
    occasional.sort(key=lambda s: (-s['event_count'], s['store_name']))

    return {'regular': regular, 'occasional': occasional}


# ── Sheet persistence ─────────────────────────────────────────────────────────

_SHEET_HEADER = ['store_id', 'store_name', 'status', 'streak', 'event_count', 'day', 'time', 'format']


def _store_analysis_to_rows(store_analysis: dict) -> list:
    """Convert a store analysis dict to sheet rows (header + data)."""
    rows = [_SHEET_HEADER]
    for entry in store_analysis['regular'] + store_analysis['occasional']:
        rows.append([
            entry['store_id'],
            entry['store_name'],
            entry['status'],
            entry['streak'],
            entry['event_count'],
            entry['day'],
            entry['time'],
            entry['format'],
        ])
    return rows


def _rows_to_store_analysis(rows: list) -> dict:
    """
    Convert sheet rows back into a store analysis dict.
    Expects a header row first; skips malformed rows.
    """
    regular    = []
    occasional = []

    for row in rows[1:]:  # skip header
        if len(row) < 8:
            continue
        entry = {
            'store_id':    row[0],
            'store_name':  row[1],
            'status':      row[2],
            'streak':      int(row[3]),
            'event_count': int(row[4]),
            'day':         row[5],
            'time':        row[6],
            'format':      row[7],
        }
        if row[2] == 'Regular':
            regular.append(entry)
        else:
            occasional.append(entry)

    return {'regular': regular, 'occasional': occasional}


def save_store_analysis(store_analysis: dict) -> None:
    """Write store classifications to the Google Sheet."""
    rows = _store_analysis_to_rows(store_analysis)
    _gs.update_values(
        LEAGUE_SPREADSHEET_ID,
        STORE_CLASSIFICATIONS_RANGE_NAME,
        "USER_ENTERED",
        rows,
    )
    print(f"  ✓ Store classifications saved ({len(rows) - 1} event type(s))")


def load_store_analysis() -> dict | None:
    """
    Read store classifications from the Google Sheet.
    Returns None if the sheet is empty (not yet bootstrapped).
    """
    result = _gs.get_values(LEAGUE_SPREADSHEET_ID, STORE_CLASSIFICATIONS_RANGE_NAME)
    rows   = result.get('values', [])
    if len(rows) <= 1:
        print(f"  ⚠ Store classifications sheet is empty — run bootstrap script first")
        return None
    analysis = _rows_to_store_analysis(rows)
    print(f"  ✓ Loaded {len(analysis['regular'])} regular, {len(analysis['occasional'])} occasional from sheet")
    return analysis


# ── Public API ────────────────────────────────────────────────────────────────

def analyse_stores(reference_date: date = None) -> dict:
    """
    Run a fresh classification against current season RPH data,
    save the result to Google Sheets, and return it.

    Called every Sunday by the where_to_play_weekly task in bot.py.

    Args:
        reference_date: Evaluate streaks as of this date. Defaults to today.

    Returns:
        {'regular': [...], 'occasional': [...]}
    """
    if reference_date is None:
        reference_date = date.today()

    events    = _fetch_current_season_events()
    event_map = _build_event_type_map(events)
    analysis  = _classify_event_types(event_map, reference_date)

    save_store_analysis(analysis)
    print(f"  ✓ {len(analysis['regular'])} regular, {len(analysis['occasional'])} occasional event type(s)")
    return analysis


def get_expected_stores_for_date(target_date: date, store_analysis: dict = None) -> list:
    """
    Return Regular event types expected to run on target_date based on their day.

    Loads from the Google Sheet if store_analysis is not provided.
    Falls back to a fresh RPH analysis if the sheet is empty.

    Args:
        target_date:    The date to check (typically today).
        store_analysis: Pre-loaded result of analyse_stores() or load_store_analysis().

    Returns:
        List of Regular event type dicts whose day matches target_date's weekday name.
    """
    if store_analysis is None:
        store_analysis = load_store_analysis()
    if store_analysis is None:
        print(f"  ⚠ No store classifications found — running fresh analysis")
        store_analysis = analyse_stores(reference_date=target_date)

    target_day_name = _DAY_NAMES[target_date.weekday()]
    expected = [
        e for e in store_analysis['regular']
        if e['day'] == target_day_name
    ]

    print(f"  ✓ {len(expected)} event type(s) expected on {target_day_name} ({target_date})")
    return expected


# ── Formatting helpers ────────────────────────────────────────────────────────

def format_store_days(days_of_week: list) -> str:
    """e.g. [5, 6] -> 'Saturday, Sunday'"""
    return ', '.join(_DAY_NAMES[d] for d in sorted(days_of_week))