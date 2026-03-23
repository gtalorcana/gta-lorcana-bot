from datetime import datetime, timezone

import season
from clients import gs as _gs, rph_api as _rph_api
from constants import (
    LEAGUE_SPREADSHEET_ID,
    RESULTS_REPORTING_CHANNEL_URL,
)

# ── Singletons ────────────────────────────────────────────────────────────────
#
# Imported from clients.py — see that module for the full explanation of why
# these are shared rather than constructed per-module.


def _is_all_draw_round(matches: list) -> bool:
    """
    Returns True if every completed non-bye match in the round was a draw
    (intentional or unintentional). Uses the /matches endpoint directly.
    """
    completed = [
        m for m in matches
        if m.get('status') == 'COMPLETE' and not m.get('match_is_bye')
    ]
    if not completed:
        return False
    return all(
        m.get('match_is_intentional_draw') or m.get('match_is_unintentional_draw')
        for m in completed
    )


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

            event_row = [
                rph_url,
                thread_id,
                note,
                event['start_datetime'][:10],
                event['store']['name'],
                gameplay_format_name,
                event['starting_player_count'],
            ]

            if not event['tournament_phases']:
                event_rows.append(event_row)
                continue

            last_phase = event['tournament_phases'][-1]
            if note == "No Single Elimination Phase":
                last_phase = event['tournament_phases'][-2]
            elif (last_phase['round_type'] == 'RANKED_SINGLE_ELIMINATION'
                  and not last_phase['rounds']
                  and len(event['tournament_phases']) >= 2):
                print(f"    ⚠ Last phase is unplayed SE — auto-using previous phase")
                last_phase = event['tournament_phases'][-2]
                event_row[2] = "Auto: unplayed SE phase skipped"

            if not last_phase['rounds']:
                event_rows.append(event_row)
                continue

            last_round_id = last_phase['rounds'][-1]['id']
            if note == "Remove Last Round":
                last_round_id = last_phase['rounds'][-2]['id']

            print(f"    → Fetching standings for round {last_round_id}...")
            standings = _rph_api.get_standings_from_tournament_round_id(str(last_round_id))
            print(f"    ✓ {len(standings)} standings retrieved for {event['store']['name']} {event['start_datetime'][:10]}")

            # Auto-detect all-draw last round (e.g. everyone IDs except byes).
            # Skip manual-override case — note already handled it above.
            if note != "Remove Last Round" and len(last_phase['rounds']) >= 2:
                matches = _rph_api.get_matches_from_tournament_round_id(str(last_round_id))
                if _is_all_draw_round(matches):
                    print(f"    ⚠ Last round detected as all-draw — auto-using second-to-last round")
                    last_round_id = last_phase['rounds'][-2]['id']
                    standings = _rph_api.get_standings_from_tournament_round_id(str(last_round_id))
                    event_row[2] = "Auto: all-draw last round removed"

            event_rows.append(event_row)

            for standing in standings:
                standing_rows.append([
                    event['start_datetime'][:10],
                    event['store']['name'],
                    standing['rank'],
                    standing['user_event_status']['best_identifier'],
                    standing['record'],
                    standing['match_points'],
                    str(standing['player']['id']),  # playhub_id — col G
                ])

    return event_rows, standing_rows


def process_event_data(rph_url, thread_id):
    """
    Main entry point called by bot.py when a new results thread is submitted.

    Flow:
      1. Read existing event data from the sheet
      2. Duplicate check — same URL from a different thread raises ValueError.
         Same URL from the same thread is a retry after a partial failure and is allowed.
      3. Build full input list = existing rows + new entry
      4. Fetch RPH data for all entries (with retries per call)
      5. Validate fetched event count matches expected input count
      6. Write standings first, then events — if a crash occurs between the two,
         the event row won't exist so /recheck can safely retry the thread.
    """
    print(f"  → process_event_data: reading existing events from sheet...")
    existing_data = _gs.get_values(LEAGUE_SPREADSHEET_ID, season.EVENTS_RANGE_NAME)
    existing_rows = existing_data.get('values', [])

    # ── Step 1: Duplicate check ───────────────────────────────
    # Same URL + different thread = true duplicate, reject.
    # Same URL + same thread = retry after a failure, allow it to overwrite.
    for row in existing_rows:
        if row[0] == rph_url:
            existing_thread_id = row[1] if len(row) > 1 else None
            if str(existing_thread_id) == str(thread_id):
                print(f"  ↩ Same thread retry detected for {rph_url} — overwriting previous partial write")
                break
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
    # Standings are written first intentionally — if an OOM or crash occurs
    # mid-write, the event row won't exist yet so the duplicate check won't
    # trigger and the thread can be safely retried via /recheck.
    print(f"  → Writing {len(standing_rows)} standings rows to sheet...")
    _gs.update_values(LEAGUE_SPREADSHEET_ID, season.STANDINGS_RANGE_NAME, "USER_ENTERED", standing_rows)

    print(f"  → Writing {len(event_rows)} event rows to sheet...")
    _gs.update_values(LEAGUE_SPREADSHEET_ID, season.EVENTS_RANGE_NAME, "USER_ENTERED", event_rows)

    utc_dt   = datetime.now(timezone.utc)
    local_dt = utc_dt.astimezone().isoformat()
    _gs.update_values(LEAGUE_SPREADSHEET_ID, season.EVENTS_TIMESTAMP_RANGE_NAME, "USER_ENTERED", [['Last updated', local_dt]])

    print(f"  ✓ All sheets updated successfully")
    return standing_rows


def remove_event_data(thread_id):
    """Clear the row matching thread_id from the events sheet."""
    print(f"  → remove_event_data: searching for thread {thread_id}...")
    recorded = _gs.get_values(LEAGUE_SPREADSHEET_ID, season.EVENTS_RANGE_NAME)

    for idx, row in enumerate(recorded.get('values', [])):
        if len(row) > 1 and row[1] == str(thread_id):
            sheet_row   = idx + 2  # sheet rows start at A2
            clear_range = season.EVENTS_SHEET_NAME + f"!A{sheet_row}:G{sheet_row}"
            _gs.update_values(LEAGUE_SPREADSHEET_ID, clear_range, "USER_ENTERED", [[""] * 7])
            print(f"  ✓ Cleared row {sheet_row} for thread {thread_id}")
            return

    raise ValueError(f"No event data found for thread ID: {thread_id}")


if __name__ == "__main__":
    # Standalone: re-fetch and rewrite all standings without adding a new event.
    # Reads whatever URLs are currently in the sheet and rewrites everything.
    print("Running standalone standings refresh...")
    existing_data = _gs.get_values(LEAGUE_SPREADSHEET_ID, season.EVENTS_RANGE_NAME)
    existing_rows = existing_data.get('values', [])

    event_rows, standing_rows = _fetch_event_rows_and_standings(existing_rows)

    _gs.update_values(LEAGUE_SPREADSHEET_ID, season.EVENTS_RANGE_NAME, "USER_ENTERED", event_rows)
    _gs.update_values(LEAGUE_SPREADSHEET_ID, season.STANDINGS_RANGE_NAME, "USER_ENTERED", standing_rows)

    utc_dt   = datetime.now(timezone.utc)
    local_dt = utc_dt.astimezone().isoformat()
    _gs.update_values(LEAGUE_SPREADSHEET_ID, season.EVENTS_TIMESTAMP_RANGE_NAME, "USER_ENTERED", [['Last updated', local_dt]])

    print(f"Done — {len(event_rows)} events, {len(standing_rows)} standings rows written.")