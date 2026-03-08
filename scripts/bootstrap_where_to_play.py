"""
Bootstrap Where-to-Play store classifications for a new season.

Run this ONCE at the start of each season. It pulls the final 2 weeks of RPH
data up to today — enough to seed any store that was running consistently —
classifies them as Regular or Up & Coming, and writes the result to the
Google Sheet (STORE_CLASSIFICATIONS_SHEET_NAME).

Once bootstrapped, rsvp_util.analyse_stores() takes over each Sunday,
re-running against current season data and updating the sheet automatically.

Re-running this script mid-season will overwrite current season data — only
run it at season start.

Usage:
    python scripts/bootstrap_where_to_play.py

Output:
    - Google Sheet: LEAGUE_SPREADSHEET_ID / STORE_CLASSIFICATIONS_SHEET_NAME
"""

import os
import sys
from datetime import date, timedelta

# Allow running from project root or scripts/
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from util.rph_api_utils import RphApi
from util.google_sheets_api_utils import GoogleSheetsApi
from constants import (
    LEAGUE_SPREADSHEET_ID,
    STORE_CLASSIFICATIONS_RANGE_NAME,
)
from rsvp_util import (
    _build_event_type_map,
    _classify_event_types,
    _store_analysis_to_rows,
)

# ── Bootstrap config ──────────────────────────────────────────────────────────
# Always uses the 2 weeks up to today — no manual date updates needed.
START_OF_DAY = "T05%3A00%3A00.000Z"
END_OF_DAY   = "T04%3A59%3A59.999Z"

BOOTSTRAP_END   = date.today()
BOOTSTRAP_START = BOOTSTRAP_END - timedelta(weeks=2)

BOOTSTRAP_START_DT = BOOTSTRAP_START.isoformat() + START_OF_DAY
BOOTSTRAP_END_DT   = BOOTSTRAP_END.isoformat() + END_OF_DAY


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

    print(f"\n  🌱 Occasional event types ({len(analysis['occasional'])}):")
    for s in analysis['occasional']:
        print(f"     {s['store_name']} — {s['day']} · {s['format']} ({s['event_count']} event(s))")

    rows = _store_analysis_to_rows(analysis)
    print(f"\n  → Writing {len(rows) - 1} stores to Google Sheet...")
    gs.update_values(
        LEAGUE_SPREADSHEET_ID,
        STORE_CLASSIFICATIONS_RANGE_NAME,
        "USER_ENTERED",
        rows,
    )
    print(f"  ✓ Google Sheet updated")
    print(f"\nDone. The bot will now use the Google Sheet for ongoing classifications.")
    print(f"Do not re-run this script mid-season — it will overwrite current season data.")


if __name__ == "__main__":
    main()