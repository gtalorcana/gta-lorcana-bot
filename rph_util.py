from datetime import datetime, timezone

from util.google_sheets_api_utils import GoogleSheetsApi
from util.rph_api_utils import RphApi
from constants import LEAGUE_SPREADSHEET_ID, EVENTS_RANGE_NAME, STANDINGS_RANGE_NAME, \
    EVENTS_TIMESTAMP_RANGE_NAME, EVENTS_URLS_RANGE_NAME

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
        note = row[1] if 1 < len(row) else None

        for event in _rph_api.get_event_by_id(event_id):

            gameplay_format_name = event['gameplay_format']['name']

            if note == "Format: Core Constructed":
                gameplay_format_name = "Core Constructed"

            # Event should be added no matter what. To not remove the event_id from the original spreadsheet
            event_rows.append([
                row[0],
                row[1],
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


def append_play_hub_url(url):
    previous_playhub_urls = _gs.get_values(
        LEAGUE_SPREADSHEET_ID,
        EVENTS_URLS_RANGE_NAME
    )

    for idx, row in enumerate(previous_playhub_urls['values']):
        if row[0] == url:
            raise ValueError(f"Play Hub link is already recorded.\nURL: {url}\nCell: E{idx + 2}")

    _gs.append_values(
        LEAGUE_SPREADSHEET_ID,
        EVENTS_URLS_RANGE_NAME,
        "USER_ENTERED",
        [[url]]
    )