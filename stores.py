"""
Whos-Going & Where-to-Play utility module.

Analyses current season RPH event data to classify Ontario Lorcana store events
as Regular or Semi-Regular, persists state to Google Sheets, and determines which
events are expected on a given date.

Grouping key:
  Each unique (store_id, day_of_week, floored_hour, format) combination is
  tracked independently. The floored hour groups events that start at slightly
  different times due to organizer edits (e.g. 7:00 PM and 7:30 PM both key
  as 7:00 PM). The displayed time is the most common raw time in the group.
  A ~ prefix is added when raw times vary within a group.

Classification rules (symmetric):
  Regular    — current consecutive streak >= WHOS_GOING_MIN_CONSECUTIVE_WEEKS
  Semi-Regular — has some history but streak < WHOS_GOING_MIN_CONSECUTIVE_WEEKS

State persistence:
  Classifications are read from and written back to the
  STORE_CLASSIFICATIONS_SHEET_NAME tab in STORE_SPREADSHEET_ID.
  This survives Fly.io restarts. The bootstrap script seeds this sheet
  using the last 2 weeks of RPH data.

Times:
  All event times are converted to America/Toronto (handles EST/EDT automatically).
"""

from collections import Counter, defaultdict
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo
import re

from clients import gs as _gs, rph_api as _rph_api
from constants import (
    SEASON_START_DT,
    SEASON_END_DT,
    STORE_SPREADSHEET_ID,
    STORE_CLASSIFICATIONS_RANGE_NAME,
    STORE_OVERRIDES_RANGE_NAME,
    BOT_STATE_RANGE_NAME,
    WHERE_TO_PLAY_MIN_CONSECUTIVE_WEEKS,
    SET_CHAMPS_START_DT,
    SET_CHAMPS_END_DT,
    SET_CHAMPS_SPREADSHEET_ID,
    SET_CHAMPS_EVENTS_RANGE_NAME,
    STORE_DEBUG_SHEET_NAME,
    STORE_DEBUG_RANGE_NAME,
)

_TZ_TORONTO = ZoneInfo("America/Toronto")

# ── Singletons ────────────────────────────────────────────────────────────────
#
# Imported from clients.py — see that module for the full explanation of why
# these are shared rather than constructed per-module.

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


def _parse_event_time_toronto(start_datetime: str) -> tuple[str, str, datetime | None]:
    """
    Parse an RPH start_datetime string and return two time representations
    in Toronto time (handles EST/EDT via zoneinfo):

      raw_time    — exact time string, e.g. '7:30 PM'
      floored_time — hour-floored time string, e.g. '7:00 PM'
      dt_toronto  — the full timezone-aware datetime in Toronto time

    The floored_time is used as the grouping key to merge events whose
    organizer adjusted the start time slightly week to week. The raw_time
    is collected across events so the most common value can be displayed.

    Returns ('', '', None) on parse failure.
    """
    try:
        dt_utc     = datetime.fromisoformat(start_datetime.replace('Z', '+00:00'))
        dt_toronto = dt_utc.astimezone(_TZ_TORONTO)
        raw_time     = dt_toronto.strftime('%I:%M %p').lstrip('0')
        floored      = dt_toronto.replace(minute=0, second=0, microsecond=0)
        floored_time = floored.strftime('%I:%M %p').lstrip('0')
        return raw_time, floored_time, dt_toronto
    except Exception:
        return '', '', None


def _build_event_type_map(events: list) -> dict:
    """
    Build a map keyed by (store_id, day_of_week, floored_hour, format).

    Events within the same store/day/format that start within the same clock
    hour are merged into one entry. This handles the common case where an
    organizer creates a new event with a slightly adjusted start time.

    Each entry accumulates:
      - week_starts:     the Monday dates on which this event type ran (for streaks)
      - raw_times:       all exact start times seen (to derive displayed time + variance)
      - event_ids:       RPH event IDs for all events in this group (for debug URLs)
      - week_raw_times:  {week_start_date: raw_time} — the start time for each specific week

    Returns:
        {
            (store_id, day_str, floored_time_str, format_str): {
                'store_id':      str,
                'store_name':    str,
                'day':           str,        # e.g. 'Saturday'
                'floored_time':  str,        # e.g. '7:00 PM' (key only)
                'raw_times':     [str, ...], # e.g. ['7:00 PM', '7:30 PM']
                'format':        str,        # e.g. 'Core Constructed'
                'week_starts':   {date, ...},
                'event_ids':     [int, ...],
                'week_raw_times': {date: str},
            }
        }
    """
    event_map = defaultdict(lambda: {
        'store_id':       '',
        'store_name':     '',
        'full_address':   '',
        'day':            '',
        'floored_time':   '',
        'raw_times':      [],
        'format':         '',
        'week_starts':    set(),
        'event_ids':      [],
        'week_raw_times': {},
    })

    for event in events:
        store_id     = event['store']['id']
        store_name   = event['store']['name']
        full_address = event['store'].get('full_address', '')
        format_str   = event['gameplay_format']['name']

        raw_time, floored_time, dt_toronto = _parse_event_time_toronto(event['start_datetime'])
        if not dt_toronto:
            continue
        event_date = dt_toronto.date()
        day_str    = _DAY_NAMES[event_date.weekday()]
        week_start = _get_week_start(event_date)

        key = (store_id, day_str, floored_time, format_str)
        event_map[key]['store_id']     = store_id
        event_map[key]['store_name']   = store_name
        event_map[key]['full_address'] = full_address
        event_map[key]['day']          = day_str
        event_map[key]['floored_time'] = floored_time
        event_map[key]['format']       = format_str
        event_map[key]['week_starts'].add(week_start)
        event_map[key]['event_ids'].append(event['id'])
        if raw_time:
            event_map[key]['raw_times'].append(raw_time)
            event_map[key]['week_raw_times'][week_start] = raw_time

    return event_map


def _display_time(raw_times: list) -> str:
    """
    Derive a display time from a list of raw event times.

    Returns the most common raw time, prefixed with '~' if there is variance
    (i.e. not all times in the group are identical). On a tie, picks the
    earliest time — better to show up early than late.

    Examples:
      ['7:00 PM', '7:00 PM']             -> '7:00 PM'
      ['7:00 PM', '7:30 PM']             -> '~7:00 PM'
      ['6:30 PM', '6:30 PM', '6:15 PM']  -> '~6:30 PM'
    """
    if not raw_times:
        return ''

    has_variance = len(set(raw_times)) > 1

    # Parse times for comparison — convert to 24h minutes-since-midnight
    def _to_minutes(t: str) -> int:
        try:
            dt = datetime.strptime(t.lstrip('~'), '%I:%M %p')
            return dt.hour * 60 + dt.minute
        except Exception:
            return 9999

    counts = Counter(raw_times)
    max_count = max(counts.values())
    # Among times with the highest count, pick the earliest
    candidates = [t for t, c in counts.items() if c == max_count]
    most_common = min(candidates, key=_to_minutes)

    return f"~{most_common}" if has_variance else most_common


def _parse_city(full_address: str) -> str:
    """
    Extract city from an RPH full_address string by anchoring on the
    2-letter province code (ON, QC, etc.) and taking the token before it.

    Handles edge cases seen in real RPH data:
      - "Canada" vs "CA" as country
      - No comma before postal code (e.g. "ON L6H 4L3")
      - Lowercase city names (e.g. "ottawa") — normalised with .title()
      - Extra unit/suite tokens before the city
      - Unusual street prefixes

    Examples:
      "55 Saint Clair Street, Chatham, ON, N7L 3H8, CA"           → "Chatham"
      "1700 Dundas Street, Unit 7, London, ON, N5W 3C9, CA"       → "London"
      "607 Somerset St W, Ottawa, ON K1R 6C6, Canada"             → "Ottawa"
      "1615 Orléans Boulevard, 108, ottawa, ON, K1C7E2, CA"       → "Ottawa"
      "1398 Danforth Avenue, Old Toronto, ON, M4J 1M9, CA"        → "Old Toronto"
    """
    if not full_address:
        return ''
    # Normalise "Canada" → "CA" so the split is consistent
    normalised = full_address.replace(', Canada', ', CA').replace(',Canada', ', CA')
    parts = [p.strip() for p in normalised.split(',')]
    for i, part in enumerate(parts):
        if re.match(r'^[A-Z]{2}(\s|$)', part) and i > 0:
            # Province found — city is the token immediately before it
            return parts[i - 1].title()
    return ''
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
    Classify each (store, day, floored_hour, format) event type into:
      Regular      — ran both of the last 2 weeks (streak >= 2)
      Semi-Regular — ran at least once in the last 2 weeks AND at least
                     twice total in the window (active but inconsistent)

    Returns:
        {
            'regular':      [ {store_id, store_name, status, streak,
                               event_count, day, time, format}, ... ],
            'semi_regular': [ ... same shape ... ],
        }
    """
    regular      = []
    semi_regular = []

    ref_week      = _get_week_start(reference_date)
    prev_week     = ref_week - timedelta(weeks=1)

    for key, info in event_map.items():
        current_streak, miss_streak = _compute_streaks(info['week_starts'], reference_date)
        event_count  = len(info['week_starts'])
        display_time = _display_time(info['raw_times'])

        ran_recently = (ref_week in info['week_starts'] or prev_week in info['week_starts'])

        entry = {
            'store_id':     info['store_id'],
            'store_name':   info['store_name'],
            'city':         _parse_city(info['full_address']),
            'full_address': info['full_address'],
            'streak':       current_streak,
            'event_count':  event_count,
            'day':          info['day'],
            'time':         display_time,
            'format':       info['format'],
        }

        if current_streak >= WHERE_TO_PLAY_MIN_CONSECUTIVE_WEEKS:
            entry['status'] = 'Regular'
            regular.append(entry)
        elif ran_recently and event_count >= 2:
            entry['status'] = 'Semi-Regular'
            semi_regular.append(entry)

    def _sort_key(s):
        day_order = {d: i for i, d in enumerate(_DAY_NAMES)}
        try:
            dt = datetime.strptime(s['time'].lstrip('~'), '%I:%M %p')
            minutes = dt.hour * 60 + dt.minute
        except Exception:
            minutes = 9999
        return (day_order.get(s['day'], 99), minutes, s['store_name'])

    regular.sort(key=_sort_key)
    semi_regular.sort(key=_sort_key)

    return {'regular': regular, 'semi_regular': semi_regular}


# ── Sheet persistence ─────────────────────────────────────────────────────────

_SHEET_HEADER = ['store_id', 'store_name', 'city', 'status', 'day', 'time', 'format', 'override']


def _store_analysis_to_rows(store_analysis: dict) -> list:
    """Convert a store analysis dict to sheet rows (header + data)."""
    rows = [_SHEET_HEADER]
    for entry in store_analysis['regular'] + store_analysis['semi_regular']:
        rows.append([
            entry['store_id'],
            entry['store_name'],
            entry.get('city', ''),
            entry['status'],
            entry['day'],
            entry['time'],
            entry['format'],
            entry.get('override', ''),
        ])
    return rows


def _rows_to_store_analysis(rows: list) -> dict:
    """
    Convert sheet rows back into a store analysis dict.
    Expects a header row first; skips malformed rows.
    """
    regular      = []
    semi_regular = []

    for row in rows[1:]:  # skip header
        if len(row) < 7:  # override column is optional
            continue
        entry = {
            'store_id':   row[0],
            'store_name': row[1],
            'city':       row[2],
            'status':     row[3],
            'day':        row[4],
            'time':       row[5],
            'format':     row[6],
        }
        if row[3] == 'Regular':
            regular.append(entry)
        else:
            semi_regular.append(entry)

    return {'regular': regular, 'semi_regular': semi_regular}


def save_store_analysis(store_analysis: dict) -> None:
    """Write store classifications to the Google Sheet."""
    rows = _store_analysis_to_rows(store_analysis)
    _gs.update_values(
        STORE_SPREADSHEET_ID,
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
    result = _gs.get_values(STORE_SPREADSHEET_ID, STORE_CLASSIFICATIONS_RANGE_NAME)
    rows   = result.get('values', [])
    if len(rows) <= 1:
        print(f"  ⚠ Store classifications sheet is empty — run bootstrap script first")
        return None
    analysis = _rows_to_store_analysis(rows)
    print(f"  ✓ Loaded {len(analysis['regular'])} regular, {len(analysis['semi_regular'])} semi-regular from sheet")
    return analysis


# ── Overrides ─────────────────────────────────────────────────────────────────

def _load_overrides() -> list:
    """
    Read manual overrides from the Overrides tab in STORE_SPREADSHEET_ID.

    Each row matches a classified entry by (store_id, day, time, format) and
    can force a new status, day, and/or time.

    Sheet columns:
      store_id | store_name | day | time | format | override_status | override_day | override_time | reason

    override_status: 'Regular', 'Semi-Regular', or 'Exclude'
    override_day:    replacement day (e.g. 'Tuesday') — leave blank to keep original
    override_time:   replacement time (e.g. '6:30 PM') — leave blank to keep original

    Returns a list of override dicts, or [] if the sheet is empty or missing.
    """
    try:
        result = _gs.get_values(STORE_SPREADSHEET_ID, STORE_OVERRIDES_RANGE_NAME)
        rows   = result.get('values', [])
        if len(rows) <= 1:
            return []
        overrides = []
        for row in rows[1:]:  # skip header
            if len(row) < 6:
                continue
            overrides.append({
                'store_id':        str(row[0]).strip(),
                'store_name':      row[1],
                'day':             row[2],
                'time':            row[3],
                'format':          row[4],
                'override_status': row[5],
                'override_day':    row[6].strip() if len(row) > 6 else '',
                'override_time':   row[7].strip() if len(row) > 7 else '',
                'reason':          row[8] if len(row) > 8 else '',
            })
        print(f"  ✓ Loaded {len(overrides)} override(s)")
        return overrides
    except Exception as e:
        print(f"  ⚠ Could not load overrides: {e}")
        return []


def _apply_overrides(analysis: dict, overrides: list, event_map: dict = None) -> dict:
    """
    Apply manual overrides to a store analysis dict.

    Matches on (store_id, day, time, format). For each match:
      - override_status 'Regular' / 'Semi-Regular': forces status, optionally replaces day/time
      - override_status 'Exclude': removes the entry entirely
      - override_status 'Add': injects a brand new entry using override_day/override_time
        (no match key needed — store_id, store_name, format, override_day, override_time, reason required)

    event_map is optional — if provided, city is looked up from RPH data for Add overrides
    whose store appears in the season data (e.g. stores with bad RPH event data).

    Returns a new analysis dict with overrides applied.
    """
    if not overrides:
        return analysis

    # Build store_id → city lookup from event_map for Add override city population
    store_city: dict[str, str] = {}
    if event_map:
        for (store_id, *_), info in event_map.items():
            city = _parse_city(info.get('full_address', ''))
            if city:
                store_city[str(store_id)] = city

    # Separate Add overrides from match-based overrides
    add_overrides   = [o for o in overrides if o['override_status'] == 'Add']
    match_overrides = {
        (o['store_id'], o['day'], o['time'], o['format']): o
        for o in overrides if o['override_status'] != 'Add'
    }

    def _key(entry):
        return (str(entry['store_id']), entry['day'], entry['time'], entry['format'])

    regular      = []
    semi_regular = []

    for entry in analysis['regular'] + analysis['semi_regular']:
        k = _key(entry)
        if k in match_overrides:
            ov = match_overrides[k]
            status = ov['override_status']
            print(f"  ↪ Override: {entry['store_name']} {entry['day']} {entry['time']} → {status}"
                  f"{' ' + ov['override_day'] if ov['override_day'] else ''}"
                  f"{' ' + ov['override_time'] if ov['override_time'] else ''}"
                  f" ({ov['reason']})")
            if status == 'Exclude':
                continue
            entry = {
                **entry,
                'status':   status,
                'day':      ov['override_day']  or entry['day'],
                'time':     ov['override_time'] or entry['time'],
                'override': ov['reason'] or 'overridden',
            }
            if status == 'Regular':
                regular.append(entry)
            else:
                semi_regular.append(entry)
        elif entry['status'] == 'Regular':
            regular.append(entry)
        else:
            semi_regular.append(entry)

    # Inject Add overrides as new entries
    for ov in add_overrides:
        if not ov['override_day'] or not ov['override_time']:
            print(f"  ⚠ Add override for {ov['store_name']} missing override_day or override_time — skipping")
            continue
        entry = {
            'store_id':   ov['store_id'],
            'store_name': ov['store_name'],
            'city':       store_city.get(str(ov['store_id']), ''),
            'status':     'Regular',
            'day':        ov['override_day'],
            'time':       ov['override_time'],
            'format':     ov['format'],
            'override':   ov['reason'] or 'manually added',
        }
        print(f"  ↪ Add override: {entry['store_name']} {entry['day']} {entry['time']} · {entry['format']} ({ov['reason']})")
        regular.append(entry)

    def _sort_key(s):
        day_order = {d: i for i, d in enumerate(_DAY_NAMES)}
        try:
            dt = datetime.strptime(s['time'].lstrip('~'), '%I:%M %p')
            minutes = dt.hour * 60 + dt.minute
        except Exception:
            minutes = 9999
        return (day_order.get(s['day'], 99), minutes, s['store_name'])

    regular.sort(key=_sort_key)
    semi_regular.sort(key=_sort_key)

    return {'regular': regular, 'semi_regular': semi_regular}


# ── Raw event map persistence ─────────────────────────────────────────────────


def save_debug_sheet(event_map: dict, analysis: dict, reference_date: date) -> None:
    """
    Write a human-readable debug sheet showing raw RPH classification and
    a calendar of the last 4 weeks. No overrides applied — this is pure
    algorithm output so you can see exactly why a store was classified the
    way it was before any manual intervention.

    Store Classifications is the post-override version of this data.

    Columns:
      store_name | day | floored_time | format | status | streak |
      <week_monday> | <week_monday> | <week_monday> | <week_monday> |
      event_ids

    Each week column shows the raw start time if the store ran that week,
    blank if they didn't. Gaps in streaks are immediately visible.
    Event IDs are rightmost for reference without cluttering the calendar.
    """
    try:
        # Build the 4 week column headers (oldest → newest)
        ref_week  = _get_week_start(reference_date)
        week_cols = [ref_week - timedelta(weeks=i) for i in range(3, -1, -1)]  # oldest first
        week_hdrs = [str(w) for w in week_cols]

        # Build status lookup from pre-override analysis
        status_lookup: dict[tuple, str] = {}
        for entry in analysis['regular']:
            k = (str(entry['store_id']), entry['day'], entry['time'], entry['format'])
            status_lookup[k] = 'Regular'
        for entry in analysis['semi_regular']:
            k = (str(entry['store_id']), entry['day'], entry['time'], entry['format'])
            status_lookup[k] = 'Semi-Regular'

        fixed_headers = ['store_id', 'store_name', 'city', 'full_address', 'day', 'floored_time', 'format', 'status', 'streak']
        header_row    = fixed_headers + week_hdrs + ['event_ids']

        rows = [header_row]

        for (store_id, day, floored_time, fmt), info in sorted(
            event_map.items(), key=lambda x: (x[1]['store_name'], x[0][1])
        ):
            display_time = _display_time(info['raw_times'])
            streak, _    = _compute_streaks(info['week_starts'], reference_date)
            city         = _parse_city(info['full_address'])

            status_key = (str(store_id), day, display_time, fmt)
            status     = status_lookup.get(status_key, '')

            # Week columns — raw start time if ran that week, blank if not
            week_times: list[str] = []
            for w in week_cols:
                if w in info['week_starts']:
                    week_raw = info.get('week_raw_times', {}).get(w)
                    week_times.append(week_raw if week_raw else display_time)
                else:
                    week_times.append('')

            # Event IDs rightmost — comma-separated for all events in this group
            event_ids = ', '.join(str(eid) for eid in sorted(info.get('event_ids', [])))

            rows.append([
                info['store_id'],
                info['store_name'],
                city,
                info['full_address'],
                day,
                floored_time,
                fmt,
                status,
                streak,
            ] + week_times + [event_ids])

        _gs.update_values(STORE_SPREADSHEET_ID, STORE_DEBUG_RANGE_NAME, "USER_ENTERED", rows)
        print(f"  ✓ Store Debug sheet updated ({len(rows) - 1} rows, weeks: {week_hdrs})")
    except Exception as e:
        print(f"  ⚠ Could not save debug sheet: {e}")


# ── Bot state persistence ─────────────────────────────────────────────────────


# ── Bot state persistence ─────────────────────────────────────────────────────

def load_bot_state() -> dict:
    """
    Read persistent bot state from the Bot State tab in STORE_SPREADSHEET_ID.
    Returns a dict of key -> value strings, or {} if the sheet is empty.

    # TODO: Replace Google Sheets bot state with a proper database (Postgres/SQLite)
    # when white-labelling. Sheets is fine for a single-server bot but won't scale
    # to concurrent multi-server writes.
    """
    try:
        result = _gs.get_values(STORE_SPREADSHEET_ID, BOT_STATE_RANGE_NAME)
        rows   = result.get('values', [])
        return {row[0]: row[1] for row in rows if len(row) >= 2}
    except Exception as e:
        print(f"  ⚠ Could not load bot state: {e}")
        return {}


def save_bot_state(state: dict) -> None:
    """
    Write persistent bot state to the Bot State tab in STORE_SPREADSHEET_ID.
    Clears the range first so deleted keys don't linger as stale rows.
    """
    try:
        _gs.clear_values(STORE_SPREADSHEET_ID, BOT_STATE_RANGE_NAME)
        if state:
            rows = [[k, v] for k, v in state.items()]
            _gs.update_values(STORE_SPREADSHEET_ID, BOT_STATE_RANGE_NAME, "USER_ENTERED", rows)
    except Exception as e:
        print(f"  ⚠ Could not save bot state: {e}")


def set_bot_state_key(key: str, value: str) -> None:
    """Add or update a single key in bot state without overwriting other keys."""
    state = load_bot_state()
    state[key] = value
    save_bot_state(state)


def delete_bot_state_key(key: str) -> None:
    """Remove a single key from bot state, silently ignoring if it doesn't exist."""
    state = load_bot_state()
    if key in state:
        del state[key]
        save_bot_state(state)


# ── Public API ────────────────────────────────────────────────────────────────

def analyse_stores(reference_date: date = None) -> dict:
    """
    Run a fresh classification against current season RPH data,
    save the result to Google Sheets, and return it.

    Called every Sunday by the where_to_play_weekly task in bot.py.

    Args:
        reference_date: Evaluate streaks as of this date. Defaults to today.

    Returns:
        {'regular': [...], 'semi_regular': [...]}
    """
    if reference_date is None:
        reference_date = date.today()

    # Always evaluate streaks as of the last completed week so that
    # the current in-progress week is never counted against a streak.
    # Floor to the Monday of the current week; if today IS that Monday,
    # step back one more week since the current week hasn't completed.
    ref_week = _get_week_start(reference_date)
    if reference_date == ref_week:
        # Today is Monday — current week just started, use previous week
        reference_date = ref_week - timedelta(weeks=1)
    else:
        # Mid-week or Sunday — current week is in progress, use its Monday as ref
        reference_date = ref_week

    events         = _fetch_current_season_events()
    event_map      = _build_event_type_map(events)
    raw_analysis   = _classify_event_types(event_map, reference_date)

    save_debug_sheet(event_map, raw_analysis, reference_date)  # raw — before overrides

    overrides = _load_overrides()
    analysis  = _apply_overrides(raw_analysis, overrides, event_map)

    save_store_analysis(analysis)  # final — after overrides
    print(f"  ✓ {len(analysis['regular'])} regular, {len(analysis['semi_regular'])} semi-regular event type(s)")
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


def fetch_event_status(event_id: int) -> dict | None:
    """
    Fetch live status for a single RPH event by ID.
    Returns a dict with availability info, or None on failure.

    Does NOT filter by country/player_count — works for future events too.

    Returns:
        {
            'event_id':    int,
            'name':        str,
            'registered':  int,
            'capacity':    int,
            'available':   bool,   # registered < capacity AND queue_status == ACCEPTING_SIGNUPS
            'queue_status': str,
            'start_date':  str,    # e.g. "2026-03-29"
            'url':         str,
        }
    """
    try:
        pages = list(_rph_api.fetch_event_by_id(event_id))
        results = [e for page in pages for e in page]
        if not results:
            return None
        event = results[0]
        registered = event.get('registered_user_count', 0)
        capacity   = event.get('capacity', 0)
        queue      = event.get('queue_status', '')
        start_raw  = event.get('start_datetime', '')
        start_date = start_raw[:10] if start_raw else ''
        available  = (capacity == 0 or registered < capacity) and queue == 'ACCEPTING_SIGNUPS'
        return {
            'event_id':    event_id,
            'name':        event.get('name', f'Event {event_id}'),
            'registered':  registered,
            'capacity':    capacity,
            'available':   available,
            'queue_status': queue,
            'start_date':  start_date,
            'url':         f"https://tcg.ravensburgerplay.com/events/{event_id}",
        }
    except Exception as e:
        print(f"  ⚠ fetch_event_status({event_id}): {e}")
        return None
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



# ── Set Championships ─────────────────────────────────────────────────────────

_RPH_EVENT_BASE_URL     = "https://tcg.ravensburgerplay.com/events/"
_SET_CHAMPS_NAME_FILTER = "Set Champ"


def refresh_set_champs() -> int:
    """
    Fetch Set Championship events from RPH and write them to the Set Champs sheet.

    Pulls all events in the SET_CHAMPS date window (including upcoming and
    in-progress), filters by name containing 'Set Champ', and overwrites
    the sheet with the latest data.

    Called daily by the set_champs_daily task in bot.py during the window
    defined by SET_CHAMPS_START_DATE and SET_CHAMPS_END_DATE.

    Returns the number of rows written.
    """
    override_params = {
        'display_status':   None,
        'display_statuses': ['past', 'inProgress', 'upcoming'],
    }

    print(f"  → Fetching Set Championship events ({SET_CHAMPS_START_DT} → {SET_CHAMPS_END_DT})...")
    events = _rph_api.get_events(
        start_date_after=SET_CHAMPS_START_DT,
        start_date_before=SET_CHAMPS_END_DT,
        extra_params=override_params,
    )

    filtered = [
        e for e in events
        if _SET_CHAMPS_NAME_FILTER.lower() in (e.get('name') or '').lower()
    ]
    print(f"  ✓ {len(filtered)} set champs event(s) found (of {len(events)} total in window)")

    rows = []
    for e in filtered:
        dt_utc     = datetime.fromisoformat(e['start_datetime'].replace('Z', '+00:00'))
        dt_toronto = dt_utc.astimezone(_TZ_TORONTO)
        rows.append([
            dt_toronto.strftime('%Y-%m-%d'),
            dt_toronto.strftime('%I:%M %p').lstrip('0'),
            e['store']['name'],
            _parse_city(['store'].get('full_address', '')),
            e.get('capacity', ''),
            e['gameplay_format']['name'],
            e.get('name', ''),
            _RPH_EVENT_BASE_URL + str(e['id']),
        ])

    rows.sort(key=lambda r: (r[0], r[1]))  # sort by date then time

    _gs.update_values(SET_CHAMPS_SPREADSHEET_ID, SET_CHAMPS_EVENTS_RANGE_NAME, "USER_ENTERED", rows)
    print(f"  ✓ Set Champs sheet updated ({len(rows)} row(s))")
    return len(rows)