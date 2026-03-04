import os
from datetime import datetime, timezone

from util.google_sheets_api_utils import GoogleSheetsApi
from util.rph_api_utils import RphApi
from constants import (
    LEAGUE_SPREADSHEET_ID,
    EVENTS_RANGE_NAME,
    STANDINGS_RANGE_NAME,
    EVENTS_TIMESTAMP_RANGE_NAME,
    RESULTS_REPORTING_CHANNEL_URL,
    EVENTS_SHEET_NAME,
)

# ── Singletons ────────────────────────────────────────────────────────────────
#
# GoogleSheetsApi and RphApi are constructed once at module load time and
# reused for every call. This is critical for memory:
#
# googleapiclient.discovery.build() — called inside GoogleSheetsApi.__init__ —
# downloads and parses Google's full API discovery document, allocating ~160 MB
# (93 MB in discovery.py + 67 MB in schema.py per tracemalloc). Python's memory
# allocator does not release RSS back to the OS after the object is freed, so
# constructing a new instance on every process_event() call caused RSS to grow
# permanently with each invocation, eventually triggering OOM on Fly.io.
#
# By constructing once here, the discovery document is fetched exactly once per
# process lifetime regardless of how many times process_event() is called.

_gs = GoogleSheetsApi()
_rph_api = RphApi()


def _fetch_event_rows_and_standings(input_rows):
    """
    Fetch RPH data for every URL in input_rows.
    Returns (event_rows, standing_rows).
    Raises RuntimeError if any individual RPH API call fails all retries.
    """
    event_rows    = []
    standing_rows = []

    for row in input_rows:
        rph_url   = row[0]
        thread_id = row[1] if len(row) > 1 else None
        note      = row[2] if len(row) > 2 else None

        # 40 is the length of "https://tcg.ravensburgerplay.com/events/"
        event_id = rph_url[40:]

        print(f"    → Fetching RPH event {event_id}...")
        events = _rph_api.get_event_by_id(event_id)

        if not events:
            print(f"    ⚠ No matching event returned for {event_id} (filtered out or not found)")

        for event in events:
            gameplay_format_name = event['gameplay_format']['name']
            if note == "Format: Core Constructed":
                gameplay_format_name = "Core Constructed"

            event_rows.append([
                rph_url,
                thread_id,
                note,
                event['start_datetime'][:10],
                event['store']['name'],
                gameplay_format_name,
                event['starting_player_count'],
            ])

            if not event['tournament_phases']:
                continue

            last_phase = event['tournament_phases'][-1]
            if note == "No Single Elimination Phase":
                last_phase = event['tournament_phases'][-2]

            if not last_phase['rounds']:
                continue

            last_round_id = last_phase['rounds'][-1]['id']
            if note == "Remove Last Round":
                last_round_id = last_phase['rounds'][-2]['id']

            print(f"    → Fetching standings for round {last_round_id}...")
            standings = _rph_api.get_standings_from_tournament_round_id(str(last_round_id))
            print(f"    ✓ {len(standings)} standings retrieved for {event['store']['name']} {event['start_datetime'][:10]}")

            for standing in standings:
                standing_rows.append([
                    event['start_datetime'][:10],
                    event['store']['name'],
                    standing['rank'],
                    standing['user_event_status']['best_identifier'],
                    standing['record'],
                    standing['match_points'],
                ])

    if os.getenv("DEBUG") and os.getenv("FORCE_COUNT_MISMATCH"):
        event_rows.pop()

    return event_rows, standing_rows


def process_event_data(rph_url, thread_id):
    """
    Main entry point called by bot.py when a new results thread is submitted.

    Flow:
      1. Read existing event data from the sheet
      2. Check for duplicate URL — raise ValueError if already reported
      3. Build full input list = existing rows + new entry
      4. Fetch RPH data for all entries (with retries per call)
      5. Validate fetched event count matches expected input count
      6. Write all sheets atomically — nothing is written if any step above fails
    """
    print(f"  → process_event_data: reading existing events from sheet...")
    existing_data = _gs.get_values(LEAGUE_SPREADSHEET_ID, EVENTS_RANGE_NAME)
    existing_rows = existing_data.get('values', [])

    # ── Step 1: Duplicate check ───────────────────────────────
    for row in existing_rows:
        if row[0] == rph_url:
            existing_thread_id = row[1] if len(row) > 1 else None
            thread_url = RESULTS_REPORTING_CHANNEL_URL + str(existing_thread_id) if existing_thread_id else "unknown"
            raise ValueError(
                f"Play Hub link is already reported.\n"
                f"URL: {rph_url}\n"
                f"Previously submitted in: {thread_url}"
            )

    # ── Step 2: Build full input list (existing + new) ────────
    full_input_rows = existing_rows + [[rph_url, str(thread_id)]]
    expected_count  = len(full_input_rows)
    print(f"  → Fetching RPH data for {expected_count} event(s) ({len(existing_rows)} existing + 1 new)...")

    # ── Step 3: Fetch all RPH data ────────────────────────────
    # RuntimeError raised here if any API call fails all retries
    event_rows, standing_rows = _fetch_event_rows_and_standings(full_input_rows)

    # ── Step 4: Validate count ────────────────────────────────
    if len(event_rows) != expected_count:
        raise RuntimeError(
            f"RPH data incomplete — expected {expected_count} event(s) but only retrieved {len(event_rows)}. "
            f"Sheet was not updated to prevent data loss."
        )
    print(f"  ✓ RPH data validated: {len(event_rows)} event(s), {len(standing_rows)} standings rows")

    # ── Step 5: Write all sheets ──────────────────────────────
    print(f"  → Writing {len(event_rows)} event rows to sheet...")
    _gs.update_values(LEAGUE_SPREADSHEET_ID, EVENTS_RANGE_NAME, "USER_ENTERED", event_rows)

    print(f"  → Writing {len(standing_rows)} standings rows to sheet...")
    _gs.update_values(LEAGUE_SPREADSHEET_ID, STANDINGS_RANGE_NAME, "USER_ENTERED", standing_rows)

    utc_dt   = datetime.now(timezone.utc)
    local_dt = utc_dt.astimezone().isoformat()
    _gs.update_values(LEAGUE_SPREADSHEET_ID, EVENTS_TIMESTAMP_RANGE_NAME, "USER_ENTERED", [['Last updated', local_dt]])

    print(f"  ✓ All sheets updated successfully")


def remove_event_data(thread_id):
    """Clear the row matching thread_id from the events sheet."""
    print(f"  → remove_event_data: searching for thread {thread_id}...")
    recorded = _gs.get_values(LEAGUE_SPREADSHEET_ID, EVENTS_RANGE_NAME)

    for idx, row in enumerate(recorded.get('values', [])):
        if len(row) > 1 and row[1] == str(thread_id):
            sheet_row   = idx + 2  # sheet rows start at A2
            clear_range = EVENTS_SHEET_NAME + f"!A{sheet_row}:G{sheet_row}"
            _gs.update_values(LEAGUE_SPREADSHEET_ID, clear_range, "USER_ENTERED", [[""] * 7])
            print(f"  ✓ Cleared row {sheet_row} for thread {thread_id}")
            return

    raise ValueError(f"No event data found for thread ID: {thread_id}")


if __name__ == "__main__":
    # Standalone: re-fetch and rewrite all standings without adding a new event.
    # Reads whatever URLs are currently in the sheet and rewrites everything.
    print("Running standalone standings refresh...")
    existing_data = _gs.get_values(LEAGUE_SPREADSHEET_ID, EVENTS_RANGE_NAME)
    existing_rows = existing_data.get('values', [])

    event_rows, standing_rows = _fetch_event_rows_and_standings(existing_rows)

    _gs.update_values(LEAGUE_SPREADSHEET_ID, EVENTS_RANGE_NAME, "USER_ENTERED", event_rows)
    _gs.update_values(LEAGUE_SPREADSHEET_ID, STANDINGS_RANGE_NAME, "USER_ENTERED", standing_rows)

    utc_dt   = datetime.now(timezone.utc)
    local_dt = utc_dt.astimezone().isoformat()
    _gs.update_values(LEAGUE_SPREADSHEET_ID, EVENTS_TIMESTAMP_RANGE_NAME, "USER_ENTERED", [['Last updated', local_dt]])

    print(f"Done — {len(event_rows)} events, {len(standing_rows)} standings rows written.")