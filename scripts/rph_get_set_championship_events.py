"""
Fetch Set Championship events from RPH and write them to the Set Champs sheet.

Pulls all Ontario Lorcana events in the SET_CHAMPS date window, including
upcoming and in-progress events (unlike the league results pipeline which only
fetches past events).

Sheet columns (A2:G):
  Date | Time (Toronto) | Store Name | Full Address | Player Cap | Format | RPH Link

Usage:
    python scripts/rph_get_set_championship_events.py

Set WRITE_TO_SHEET = True once you've verified the printed output looks correct.

Filtering:
    RPH has no server-side filter for "set championship" events specifically.
    Use NAME_FILTER to drop events whose name doesn't contain a keyword.
    Leave NAME_FILTER as None to see everything and figure out the right keyword first.
"""

import os
import sys
from zoneinfo import ZoneInfo

# Allow running from project root or scripts/
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from datetime import datetime
from util.rph_api_utils import RphApi
from util.google_sheets_api_utils import GoogleSheetsApi
import season
from constants import SET_CHAMPS_SPREADSHEET_ID
from stores import _parse_city

_TZ_TORONTO = ZoneInfo("America/Toronto")

# Set to True once you've verified the printed output looks correct
WRITE_TO_SHEET = False

# Only include events whose name contains this string (case-insensitive).
# Set to None to include all events and inspect names first.
NAME_FILTER = "Set Champ"

# RPH event page base URL — event ID is appended
RPH_EVENT_BASE_URL = "https://tcg.ravensburgerplay.com/events/"


def _format_event_row(event: dict) -> list:
    """
    Transform a raw RPH event object into a sheet row.

    Columns: Date | Time (Toronto) | Store Name | Full Address | Player Cap | Format | RPH Link
    """
    dt_utc     = datetime.fromisoformat(event['start_datetime'].replace('Z', '+00:00'))
    dt_toronto = dt_utc.astimezone(_TZ_TORONTO)

    date_str     = dt_toronto.strftime('%Y-%m-%d')
    time_str     = dt_toronto.strftime('%I:%M %p').lstrip('0')
    store_name   = event['store']['name']
    full_address = _parse_city(event['store'].get('full_address'))
    player_cap   = event.get('capacity', '')
    format_str   = event['gameplay_format']['name']
    rph_link     = RPH_EVENT_BASE_URL + str(event['id'])

    return [date_str, time_str, store_name, full_address, player_cap, format_str, rph_link]


if __name__ == '__main__':
    rph_api = RphApi()
    gs      = GoogleSheetsApi()

    override_params = {
        'display_status':   None,
        'display_statuses': ['past', 'inProgress', 'upcoming'],
    }

    print(f"\nFetching Set Championship events...")
    print(f"  Date range: {season.SET_CHAMPS_START_DT} → {season.SET_CHAMPS_END_DT}")
    print(f"  Name filter: {NAME_FILTER!r}\n")

    events = rph_api.get_events(
        start_date_after=season.SET_CHAMPS_START_DT,
        start_date_before=season.SET_CHAMPS_END_DT,
        extra_params=override_params,
    )

    if not events:
        print("  ⚠ No events found — check date range or RPH filters.")
        sys.exit(0)

    print(f"  ✓ {len(events)} event(s) fetched from RPH\n")

    # Print raw keys from the first event so field names can be verified
    print("  — Raw fields on first event (for verification) —")
    for k, v in events[0].items():
        print(f"    {k}: {v}")
    print()

    # Show all event names before filtering so you can pick the right NAME_FILTER keyword
    print("  — All event names —")
    for e in sorted(events, key=lambda e: e['start_datetime']):
        dt_utc     = datetime.fromisoformat(e['start_datetime'].replace('Z', '+00:00'))
        dt_toronto = dt_utc.astimezone(_TZ_TORONTO)
        print(f"    {dt_toronto.strftime('%Y-%m-%d')}  {e['store']['name']:<40}  {e.get('name', '(no name)')}")
    print()

    # Apply name filter
    if NAME_FILTER:
        filtered = [e for e in events if NAME_FILTER.lower() in (e.get('name') or '').lower()]
        print(f"  → {len(filtered)} of {len(events)} event(s) match name filter {NAME_FILTER!r}\n")
    else:
        filtered = events
        print(f"  ⚠ NAME_FILTER is None — all {len(events)} event(s) will be included.\n"
              f"    Set NAME_FILTER to a keyword (e.g. 'championship') to narrow results.\n")

    rows = [_format_event_row(e) for e in filtered]
    rows.sort(key=lambda r: (r[0], r[1]))  # sort by date then time

    print(f"  {'#':<4} {'Date':<12} {'Time':<10} {'Cap':<5} {'Format':<22} {'Store':<35} {'RPH Link':<50} City")
    print(f"  {'-'*4} {'-'*12} {'-'*10} {'-'*5} {'-'*22} {'-'*35} {'-'*50} {'-'*40}")
    for i, row in enumerate(rows, 1):
        print(f"  {i:<4} {row[0]:<12} {row[1]:<10} {str(row[4]):<5} {row[5]:<22} {row[2]:<35} {row[6]:<50} {row[3]}")

    print(f"\n  {len(rows)} row(s) ready to write.")

    if WRITE_TO_SHEET:
        gs.update_values(SET_CHAMPS_SPREADSHEET_ID, season.SET_CHAMPS_EVENTS_RANGE_NAME, "USER_ENTERED", rows)
        print(f"  ✓ Written to sheet.")
    else:
        print(f"\n  ⚠ WRITE_TO_SHEET = False — set to True to write to the sheet.")