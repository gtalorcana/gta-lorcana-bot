from datetime import datetime, timezone
from sys import exception

from util.google_sheets_api_utils import GoogleSheetsApi
from util.rph_api_utils import RphApi
from constants import LEAGUE_SPREADSHEET_ID, EVENTS_RANGE_NAME, STANDINGS_RANGE_NAME, \
    EVENTS_TIMESTAMP_RANGE_NAME, EVENTS_INPUT_RANGE_NAME, RESULTS_REPORTING_CHANNEL_URL, EVENTS_SHEET_NAME

# ── Singletons ────────────────────────────────────────────────────────────────
#
# GoogleSheetsApi and RphApi are constructed once at module load time and
# reused for every call. This is critical for memory:
#
# googleapiclient.discovery.build() — called inside GoogleSheetsApi.__init__ —
# downloads and parses Google's full API discovery document, allocating ~160 MB
# (93 MB in discovery.py + 67 MB in schema.py per tracemalloc). Python's memory
# allocator does not release RSS back to the OS after the object is freed, so
# constructing a new GoogleSheetsApi() on every get_standings() call causes RSS
# to grow permanently with each invocation, eventually triggering OOM.
#
# By constructing once here, the discovery document is fetched exactly once per
# process lifetime regardless of how many times get_standings() is called.

_gs = GoogleSheetsApi()
_rph_api = RphApi()


def get_standings():
    event_rows = []
    standing_rows = []

    # Write to Standings and Events page
    user_input_data = _gs.get_values(
        LEAGUE_SPREADSHEET_ID,
        EVENTS_RANGE_NAME
    )

    for row in user_input_data['values']:
        # 40 is the length of "https://tcg.ravensburgerplay.com/events/"
        event_id = row[0][40:]
        thread_id= row[1] if 1 < len(row) else None
        note = row[2] if 2 < len(row) else None

        for event in _rph_api.get_event_by_id(event_id):

            gameplay_format_name = event['gameplay_format']['name']

            if note == "Format: Core Constructed":
                gameplay_format_name = "Core Constructed"

            # Event should be added no matter what. To not remove the event_id from the original spreadsheet
            event_rows.append([
                row[0], # RPH Url
                thread_id, # Discord Thread ID
                note,
                event['start_datetime'][:10],
                event['store']['name'],
                gameplay_format_name,
                event['starting_player_count'],
            ])

            if len(event['tournament_phases']) > 0:
                last_tournament_phase = event['tournament_phases'][-1]

                if note == "No Single Elimination Phase":
                    last_tournament_phase = event['tournament_phases'][-2]

                if len(last_tournament_phase['rounds']) > 0:
                    last_round_id = last_tournament_phase['rounds'][-1]['id']

                    if note == "Remove Last Round":
                        last_round_id = last_tournament_phase['rounds'][-2]['id']
                    standings = _rph_api.get_standings_from_tournament_round_id(str(last_round_id))

                    for standing in standings:
                        standing_rows.append([
                            event['start_datetime'][:10],
                            event['store']['name'],
                            standing['rank'],
                            standing['user_event_status']['best_identifier'],
                            standing['record'],
                            standing['match_points'],
                        ])

    # Update Events Data
    _gs.update_values(
        LEAGUE_SPREADSHEET_ID,
        EVENTS_RANGE_NAME,
        "USER_ENTERED",
        event_rows
    )

    # Update Standings Data
    _gs.update_values(
        LEAGUE_SPREADSHEET_ID,
        STANDINGS_RANGE_NAME,
        "USER_ENTERED",
        standing_rows
    )

    # Timestamp
    utc_dt = datetime.now(timezone.utc)
    local_dt = utc_dt.astimezone().isoformat()

    _gs.update_values(
        LEAGUE_SPREADSHEET_ID,
        EVENTS_TIMESTAMP_RANGE_NAME,
        "USER_ENTERED",
        [['Last updated', local_dt]]
    )


def append_event_data(rph_url, thread_id):
    previous_play_hub_urls = _gs.get_values(
        LEAGUE_SPREADSHEET_ID,
        EVENTS_INPUT_RANGE_NAME
    )

    for row in previous_play_hub_urls['values']:
        if row[0] == rph_url:
            thread_url = RESULTS_REPORTING_CHANNEL_URL + str(thread_id)
            raise ValueError(
                f"Play Hub link is already reported.\n"
                f"URL: {rph_url}\n"
                f"Thread: {thread_url}"
            )

    _gs.append_values(
        LEAGUE_SPREADSHEET_ID,
        EVENTS_INPUT_RANGE_NAME,
        "USER_ENTERED",
        [[rph_url, str(thread_id)]]
    )

def remove_event_data(thread_id):
    """Clear the row matching thread_id from the events input sheet."""
    recorded = _gs.get_values(
        LEAGUE_SPREADSHEET_ID,
        EVENTS_INPUT_RANGE_NAME
    )

    for idx, row in enumerate(recorded['values']):
        if len(row) > 1 and row[1] == str(thread_id):
            print("found match!")
            # Rows in the sheet start at row 2 (A2), so sheet row = idx + 2
            sheet_row = idx + 2
            clear_range = EVENTS_SHEET_NAME + f"!A{sheet_row}:G{sheet_row}"
            _gs.update_values(
                LEAGUE_SPREADSHEET_ID,
                clear_range,
                "USER_ENTERED",
                [[""] * 7]
            )
            return

    raise ValueError(f"No event data found for thread ID: {thread_id}")