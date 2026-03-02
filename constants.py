SEASON_START_DATE = "2026-02-13T05%3A00%3A00.000Z"
SEASON_END_DATE = "2026-04-24T04%3A59%3A59.999Z"

LEAGUE_SPREADSHEET_ID = "1dSv5lzVwhot1DR0e2FghyS7R1Vm1ODZtH3d8e3C55Zo"

# Google Sheets range names
STANDINGS_RANGE_NAME        = "S11 Standings - User Reported!A3:F"
EVENTS_RANGE_NAME           = "S11 Events - User Reported!A2:F"
EVENTS_TIMESTAMP_RANGE_NAME = "S11 Events - User Reported!I1:J1"
EVENTS_URLS_RANGE_NAME      = "S11 Events - User Reported!A2:A"

# Discord channel names (production values)
# Override via .env locally to point at test channels, e.g.:
#   ANNOUNCEMENTS_CHANNEL=test-announcements
#   RESULTS_REPORTING_CHANNEL=test-results-reporting
ANNOUNCEMENTS_CHANNEL     = "announcements"
RESULTS_REPORTING_CHANNEL = "results-reporting"
RESULTS_CHANNEL           = "results"
DECKLISTS_CHANNEL         = "decklists"
WELCOME_CHANNEL           = "general"

# Regex to validate RPH event URLs submitted in results threads
EVENTS_URL_RE = r'https://tcg.ravensburgerplay.com/events/[0-9]+'