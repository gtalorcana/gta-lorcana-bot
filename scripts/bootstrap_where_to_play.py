"""
Bootstrap Where-to-Play store classifications for a new season.

Run this ONCE at the start of each season. It pulls the last 4 weeks of RPH
data up to today — 4 weeks of history ensures the reference date is current
so streak calculations are accurate, while 2 consecutive weeks is enough to
qualify as Regular.

Once bootstrapped, rsvp_util.analyse_stores() takes over each Sunday,
re-running against current season data and updating the sheet automatically.

Re-running this script mid-season will overwrite current season data — only
run it at season start.

Usage:
    python scripts/bootstrap_where_to_play.py

Output:
    - Google Sheet: STORE_SPREADSHEET_ID / STORE_CLASSIFICATIONS_SHEET_NAME
    - Google Sheet: STORE_SPREADSHEET_ID / Bootstrap Raw Data  (if WRITE_RAW_DATA = True)
"""

import os
import sys
from datetime import date, timedelta

# Allow running from project root or scripts/
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from util.rph_api_utils import RphApi
from util.google_sheets_api_utils import GoogleSheetsApi
from constants import (
    STORE_SPREADSHEET_ID,
    STORE_CLASSIFICATIONS_RANGE_NAME,
)
from rsvp_util import (
    _build_event_type_map,
    _classify_event_types,
    _store_analysis_to_rows,
)

# ── Bootstrap config ──────────────────────────────────────────────────────────
# Always uses the 4 weeks up to today — no manual date updates needed.
START_OF_DAY = "T05%3A00%3A00.000Z"
END_OF_DAY   = "T04%3A59%3A59.999Z"

BOOTSTRAP_END   = date.today()
BOOTSTRAP_START = BOOTSTRAP_END - timedelta(weeks=4)

BOOTSTRAP_START_DT = BOOTSTRAP_START.isoformat() + START_OF_DAY
BOOTSTRAP_END_DT   = BOOTSTRAP_END.isoformat() + END_OF_DAY

# Set to True to write raw event_map data to a "Bootstrap Raw Data" tab for debugging.
WRITE_RAW_DATA = True

RAW_DATA_SHEET  = "Bootstrap Raw Data"
RAW_DATA_RANGE  = RAW_DATA_SHEET + "!A1:G"
RAW_DATA_HEADER = ['store_id', 'store_name', 'day', 'floored_time', 'format', 'week_starts', 'raw_times']


def main():
    rph_api = RphApi()
    gs      = GoogleSheetsApi()

    print(f"\nBootstrapping Where-to-Play...")
    print(f"  Date range: {BOOTSTRAP_START} → {BOOTSTRAP_END}")

    events = rph_api.get_events(BOOTSTRAP_START_DT, BOOTSTRAP_END_DT)
    print(f"  ✓ {len(events)} events fetched")

    reference_date = BOOTSTRAP_END
    event_map      = _build_event_type_map(events)
    analysis       = _classify_event_types(event_map, reference_date)

    print(f"\nResults:")
    print(f"  ✅ Regular event types ({len(analysis['regular'])}):")
    for s in analysis['regular']:
        print(f"     {s['store_name']} — {s['day']} @ {s['time']} · {s['format']} ({s['streak']} weeks)")

    print(f"\n  🔄 Semi-Regular event types ({len(analysis['semi_regular'])}):")
    for s in analysis['semi_regular']:
        print(f"     {s['store_name']} — {s['day']} · {s['format']} ({s['event_count']} event(s))")

    # ── Write classifications to Google Sheet ─────────────────
    rows = _store_analysis_to_rows(analysis)
    print(f"\n  → Writing {len(rows) - 1} event types to Google Sheet...")
    gs.update_values(
        STORE_SPREADSHEET_ID,
        STORE_CLASSIFICATIONS_RANGE_NAME,
        "USER_ENTERED",
        rows,
    )
    print(f"  ✓ Store Classifications sheet updated")

    # ── Optionally write raw event_map for debugging ──────────
    if WRITE_RAW_DATA:
        print(f"\n  → Writing raw event data to '{RAW_DATA_SHEET}' tab...")
        raw_rows = [RAW_DATA_HEADER]
        for (store_id, day, floored_time, fmt), info in sorted(event_map.items(), key=lambda x: x[1]['store_name']):
            raw_rows.append([
                store_id,
                info['store_name'],
                day,
                floored_time,
                fmt,
                ", ".join(sorted(str(w) for w in info['week_starts'])),
                ", ".join(info['raw_times']),
            ])
        gs.update_values(
            STORE_SPREADSHEET_ID,
            RAW_DATA_RANGE,
            "USER_ENTERED",
            raw_rows,
        )
        print(f"  ✓ Bootstrap Raw Data sheet updated ({len(raw_rows) - 1} rows)")

    print(f"\nDone. The bot will now use the Google Sheet for ongoing classifications.")
    print(f"Do not re-run this script mid-season — it will overwrite current season data.")


if __name__ == "__main__":
    main()