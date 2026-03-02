from datetime import datetime, timezone

from util.google_sheets_api_utils import GoogleSheetsApi
from util.rph_api_utils import RphApi
from constants import SAMPLE_SPREADSHEET_ID, HALF_AUTO_EVENTS_RANGE_NAME, HALF_AUTO_STANDINGS_RANGE_NAME, \
    HALF_AUTO_EVENTS_TIMESTAMP_RANGE_NAME, HALF_AUTO_EVENTS_URLS_RANGE_NAME
# ── 1. Add to the top of rph_util.py ───────────────────────────────────────────────

import tracemalloc

# ── 2. Replace get_standings() in rph_util.py with this instrumented version ──
#    Remove all the log_memory() calls once the culprit is identified.

def log_memory(label: str):
    """Log RSS and tracemalloc at any point during execution."""
    try:
        with open("/proc/self/status") as f:
            for line in f:
                if line.startswith("VmRSS"):
                    rss_kb = int(line.split()[1])
                    print(f"  🧠 [{label}] RSS: {rss_kb / 1024:.1f} MB")
                    break
    except Exception:
        pass
    if tracemalloc.is_tracing():
        current, peak = tracemalloc.get_traced_memory()
        print(f"  🧠 [{label}] current: {current / 1024**2:.1f} MB  peak: {peak / 1024**2:.1f} MB")

def get_standings():
    log_memory("get_standings: start")

    rph_api = RphApi()
    log_memory("after RphApi()")

    gs = GoogleSheetsApi()
    log_memory("after GoogleSheetsApi()")       # likely spike — builds the discovery doc

    event_rows = []
    standing_rows = []

    user_input_data = gs.get_values(
        SAMPLE_SPREADSHEET_ID,
        HALF_AUTO_EVENTS_RANGE_NAME
    )
    log_memory("after get_values()")

    for i, row in enumerate(user_input_data['values']):
        event_id = row[4][40:]
        note = row[5] if 5 < len(row) else None

        for event in rph_api.get_event_by_id(event_id):
            log_memory(f"event {i} ({event_id}): after get_event_by_id")

            gameplay_format_name = event['gameplay_format']['name']
            if note == "Format: Core Constructed":
                gameplay_format_name = "Core Constructed"

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
                    log_memory(f"event {i} ({event_id}): after get_standings_from_tournament_round_id — {len(standings)} entries")

                    for standing in standings:
                        standing_rows.append([
                            event['start_datetime'][:10],
                            event['store']['name'],
                            standing['rank'],
                            standing['user_event_status']['best_identifier'],
                            standing['record'],
                            standing['match_points'],
                        ])

        log_memory(f"event {i} ({event_id}): after full processing — {len(standing_rows)} standing rows so far")

    log_memory("after all events — before update_values")

    gs.update_values(SAMPLE_SPREADSHEET_ID, HALF_AUTO_EVENTS_RANGE_NAME, "USER_ENTERED", event_rows)
    log_memory("after update events")

    gs.update_values(SAMPLE_SPREADSHEET_ID, HALF_AUTO_STANDINGS_RANGE_NAME, "USER_ENTERED", standing_rows)
    log_memory("after update standings")

    utc_dt = datetime.now(timezone.utc)
    local_dt = utc_dt.astimezone().isoformat()
    gs.update_values(
        SAMPLE_SPREADSHEET_ID,
        HALF_AUTO_EVENTS_TIMESTAMP_RANGE_NAME,
        "USER_ENTERED",
        [['Last updated', local_dt]]
    )
    log_memory("get_standings: done")

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
