from datetime import datetime, timezone

from util.google_sheets_api_utils import GoogleSheetsApi
from util.rph_api_utils import RphApi
from var.constants import SAMPLE_SPREADSHEET_ID, HALF_AUTO_EVENTS_RANGE_NAME, HALF_AUTO_STANDINGS_RANGE_NAME, \
    HALF_AUTO_EVENTS_TIMESTAMP_RANGE_NAME, HALF_AUTO_EVENTS_URLS_RANGE_NAME


def get_standings():
    rph_api = RphApi()
    gs = GoogleSheetsApi()
    event_rows = []
    standing_rows = []

    # Write to Standings and Events page
    user_input_data = gs.get_values(
        SAMPLE_SPREADSHEET_ID,
        HALF_AUTO_EVENTS_RANGE_NAME
    )

    for row in user_input_data['values']:
        # 40 is the length of "https://tcg.ravensburgerplay.com/events/"
        event_id = row[4][40:]
        note = row[5] if 5 < len(row) else None

        for event in rph_api.get_event_by_id(event_id):

            gameplay_format_name = event['gameplay_format']['name']

            if note == "Format: Core Constructed":
                gameplay_format_name = "Core Constructed"

            # Event should be added no matter what.  To not remove the event_id from the original spreadsheet
            event_rows.append([
                event['start_datetime'][:10],
                event['store']['name'],
                gameplay_format_name,
                event['starting_player_count'],
                "https://tcg.ravensburgerplay.com/events/" + str(event['id']),
            ])

            if len(event['tournament_phases']) > 0:
                last_tournament_phase = event['tournament_phases'][-1]

                if note == "No Top Cut":
                    last_tournament_phase = event['tournament_phases'][-2]

                if len(last_tournament_phase['rounds']) > 0:
                    last_round_id = last_tournament_phase['rounds'][-1]['id']

                    if note == "Remove Last Round":
                        last_round_id = last_tournament_phase['rounds'][-2]['id']

                    standings = rph_api.get_standings_from_tournament_round_id(str(last_round_id))

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
    gs.update_values(
        SAMPLE_SPREADSHEET_ID,
        HALF_AUTO_EVENTS_RANGE_NAME,
        "USER_ENTERED",
        event_rows
    )

    # Update Standings Data
    gs.update_values(
        SAMPLE_SPREADSHEET_ID,
        HALF_AUTO_STANDINGS_RANGE_NAME,
        "USER_ENTERED",
        standing_rows
    )

    # Timestamp
    utc_dt = datetime.now(timezone.utc)

    local_dt = utc_dt.astimezone().isoformat()

    gs.update_values(
        SAMPLE_SPREADSHEET_ID,
        HALF_AUTO_EVENTS_TIMESTAMP_RANGE_NAME,
        "USER_ENTERED",
        [['Last updated', local_dt]]
    )


def append_play_hub_url(url):
    gs = GoogleSheetsApi()

    previous_playhub_urls = gs.get_values(
        SAMPLE_SPREADSHEET_ID,
        HALF_AUTO_EVENTS_URLS_RANGE_NAME
    )

    for idx, row in enumerate(previous_playhub_urls['values']):
        if row[0] == url:
            raise ValueError(f"Play Hub link is already recorded.\nURL: {url}\nCell: E{idx + 2}")

    gs.append_values(
        SAMPLE_SPREADSHEET_ID,
        HALF_AUTO_EVENTS_URLS_RANGE_NAME,
        "USER_ENTERED",
        [[None, None, None, None, url]]
    )
