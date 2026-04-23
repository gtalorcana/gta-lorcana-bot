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


def _fetch_single_event(rph_url, thread_id, note=None, validate_date=False):
    """
    Fetch RPH data for a single event URL.
    Returns (event_row, standing_rows, warnings).

    note:          optional value for event_row[2] (e.g. 'Format: Core Constructed').
                   Auto-corrections will overwrite this if they fire.
    validate_date: if True, raises ValueError when the event date is outside
                   the current season window.

    Raises RuntimeError if the API call fails all retries or returns no event.
    Raises ValueError  if validate_date=True and the event is out of season.
    """
    warnings      = []
    standing_rows = []

    # 40 is the length of "https://tcg.ravensburgerplay.com/events/"
    event_id = rph_url[40:]

    print(f"    → Fetching RPH event {event_id}...")
    event = _rph_api.get_event_by_id(event_id)

    if not event:
        raise RuntimeError(
            f"No matching event returned from RPH for {event_id} "
            f"(filtered out or not found)."
        )

    gameplay_format_name = event['gameplay_format']['name']
    if note == "Format: Core Constructed":
        gameplay_format_name = "Core Constructed"

    event_date = event['start_datetime'][:10]

    if validate_date:
        if season.SEASON_START_DATE and event_date < season.SEASON_START_DATE:
            raise ValueError(
                f"Event date {event_date} is before the current season start ({season.SEASON_START_DATE})."
            )
        if season.SEASON_END_DATE and event_date > season.SEASON_END_DATE:
            raise ValueError(
                f"Event date {event_date} is after the current season end ({season.SEASON_END_DATE})."
            )

    event_row = [
        rph_url,
        str(thread_id) if thread_id is not None else None,
        note,
        event_date,
        event['store']['name'],
        gameplay_format_name,
        event['starting_player_count'],
    ]

    if not event['tournament_phases']:
        return event_row, standing_rows, warnings

    last_phase = event['tournament_phases'][-1]
    if (last_phase['round_type'] == 'RANKED_SINGLE_ELIMINATION'
            and not last_phase['rounds']
            and len(event['tournament_phases']) >= 2):
        print(f"    ⚠ Last phase is unplayed SE — auto-using previous phase")
        last_phase = event['tournament_phases'][-2]
        event_row[2] = "Auto: unplayed SE phase skipped"
        warnings.append("⚠️ Unplayed single-elimination phase detected and skipped automatically.")

    if not last_phase['rounds']:
        return event_row, standing_rows, warnings

    last_round_id = last_phase['rounds'][-1]['id']

    print(f"    → Fetching standings for round {last_round_id}...")
    standings = _rph_api.get_standings_from_tournament_round_id(str(last_round_id))
    print(f"    ✓ {len(standings)} standings retrieved for {event['store']['name']} {event_date}")

    if len(last_phase['rounds']) >= 2:
        matches = _rph_api.get_matches_from_tournament_round_id(str(last_round_id))
        if _is_all_draw_round(matches):
            print(f"    ⚠ Last round detected as all-draw — auto-using second-to-last round")
            last_round_id = last_phase['rounds'][-2]['id']
            standings = _rph_api.get_standings_from_tournament_round_id(str(last_round_id))
            event_row[2] = "Auto: all-draw last round removed"
            warnings.append("⚠️ Last round was all-draws — standings taken from the previous round automatically.")

    for standing in standings:
        standing_rows.append([
            event_date,
            event['store']['name'],
            standing['rank'],
            standing['user_event_status']['best_identifier'],
            standing['record'],
            standing['match_points'],
            str(standing['player']['id']),  # playhub_id — col G
        ])

    return event_row, standing_rows, warnings


def _fetch_event_rows_and_standings(input_rows):
    """
    Fetch RPH data for every URL in input_rows.
    Returns (event_rows, standing_rows, warnings).
    Used by the standalone __main__ refresh path only.
    Raises RuntimeError if any individual RPH API call fails all retries.
    """
    event_rows    = []
    standing_rows = []
    warnings      = []

    for row in input_rows:
        rph_url   = row[0]
        thread_id = row[1] if len(row) > 1 else None
        note      = row[2] if len(row) > 2 else None

        e_row, s_rows, w = _fetch_single_event(rph_url, thread_id, note=note)
        event_rows.append(e_row)
        standing_rows.extend(s_rows)
        warnings.extend(w)

    return event_rows, standing_rows, warnings


def process_event_data(rph_url, thread_id):
    """
    Main entry point called by bot.py when a new results thread is submitted.

    Flow:
      1. Read existing event data from the sheet (for duplicate check only —
         existing events are never re-fetched from RPH)
      2. Duplicate check — same URL from a different thread raises ValueError.
         Same URL from the same thread is a retry after a partial failure and
         is allowed (is_retry=True).
      3. Fetch RPH data for the new event only
      4. Write event row first, then append standings.
         On retry: overwrite the event row in place and clear any standings
         already written for this event before re-appending, so there are no
         duplicates regardless of where a previous attempt crashed.
    """
    print(f"  → process_event_data: reading existing events from sheet...")
    existing_data = _gs.get_values(LEAGUE_SPREADSHEET_ID, season.EVENTS_RANGE_NAME)
    existing_rows = existing_data.get('values', [])

    # ── Step 1: Duplicate check ───────────────────────────────
    # Same URL + different thread = true duplicate, reject.
    # Same URL + same thread = retry after a failure, allow it to overwrite.
    is_retry       = False
    retry_sheet_row = None  # 1-based sheet row number of the existing event row

    for idx, row in enumerate(existing_rows):
        if row[0] == rph_url:
            existing_thread_id = row[1] if len(row) > 1 else None
            if str(existing_thread_id) == str(thread_id):
                print(f"  ↩ Same thread retry detected for {rph_url} — will overwrite in place")
                is_retry        = True
                retry_sheet_row = idx + 2  # sheet rows start at A2
                break
            thread_url = RESULTS_REPORTING_CHANNEL_URL + str(existing_thread_id) if existing_thread_id else "unknown"
            raise ValueError(
                f"Play Hub link is already reported.\n"
                f"URL: {rph_url}\n"
                f"Previously submitted in: {thread_url}"
            )

    # ── Step 2: Fetch new event from RPH ─────────────────────
    # Only the submitted event is fetched — historical events are not re-fetched
    # because playhub_id (not display name) is stored, so player identity is stable.
    print(f"  → Fetching RPH data for new event...")
    event_row, standing_rows, warnings = _fetch_single_event(
        rph_url, thread_id, validate_date=True
    )
    event_date = event_row[3]
    store_name = event_row[4]
    print(f"  ✓ RPH data fetched: {store_name} {event_date}, {len(standing_rows)} standings rows")

    # ── Step 3: Write event row ───────────────────────────────
    # Event row is written before standings so that any crash between the two
    # leaves the event row present, letting the next retry detect same-thread
    # and re-append standings cleanly.
    if is_retry:
        retry_range = season.EVENTS_SHEET_NAME + f"!A{retry_sheet_row}:G{retry_sheet_row}"
        print(f"  → Overwriting event row at sheet row {retry_sheet_row}...")
        _gs.update_values(LEAGUE_SPREADSHEET_ID, retry_range, "USER_ENTERED", [event_row])

        # Clear any standings already written for this event (from a previous
        # attempt that crashed after the standings write) before re-appending.
        standings_data     = _gs.get_values(LEAGUE_SPREADSHEET_ID, season.STANDINGS_RANGE_NAME)
        existing_standings = standings_data.get('values', [])
        to_clear = [
            {"range": season.STANDINGS_SHEET_NAME + f"!A{idx + 3}:G{idx + 3}", "values": [[""] * 7]}
            for idx, row in enumerate(existing_standings)
            if len(row) >= 2 and row[0] == event_date and row[1] == store_name
        ]
        if to_clear:
            print(f"  ↩ Clearing {len(to_clear)} existing standings row(s) for {store_name} {event_date}...")
            _gs.batch_update_values(LEAGUE_SPREADSHEET_ID, to_clear)
    else:
        print(f"  → Appending event row...")
        _gs.append_values(LEAGUE_SPREADSHEET_ID, season.EVENTS_RANGE_NAME, "USER_ENTERED", [event_row])

    # ── Step 4: Append standings ──────────────────────────────
    if standing_rows:
        print(f"  → Appending {len(standing_rows)} standings rows...")
        _gs.append_values(LEAGUE_SPREADSHEET_ID, season.STANDINGS_RANGE_NAME, "USER_ENTERED", standing_rows)

    utc_dt   = datetime.now(timezone.utc)
    local_dt = utc_dt.astimezone().isoformat()
    _gs.update_values(LEAGUE_SPREADSHEET_ID, season.EVENTS_TIMESTAMP_RANGE_NAME, "USER_ENTERED", [['Last updated', local_dt]])

    print(f"  ✓ All sheets updated successfully")
    return standing_rows, warnings


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
    existing_rows = [row for row in existing_data.get('values', []) if row and row[0]]

    event_rows, standing_rows, _ = _fetch_event_rows_and_standings(existing_rows)

    _gs.update_values(LEAGUE_SPREADSHEET_ID, season.EVENTS_RANGE_NAME, "USER_ENTERED", event_rows)
    _gs.update_values(LEAGUE_SPREADSHEET_ID, season.STANDINGS_RANGE_NAME, "USER_ENTERED", standing_rows)

    utc_dt   = datetime.now(timezone.utc)
    local_dt = utc_dt.astimezone().isoformat()
    _gs.update_values(LEAGUE_SPREADSHEET_ID, season.EVENTS_TIMESTAMP_RANGE_NAME, "USER_ENTERED", [['Last updated', local_dt]])

    print(f"Done — {len(event_rows)} events, {len(standing_rows)} standings rows written.")
