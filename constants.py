import os
from dotenv import load_dotenv
load_dotenv()

# Bot Secrets
DISCORD_BOT_TOKEN = os.getenv("DISCORD_BOT_TOKEN")
WORKER_URL        = os.getenv("WORKER_URL")
WORKER_SECRET     = os.getenv("WORKER_SECRET")

# League Variables
# Discord user ID of the bot admin (pinged when all auto-retries fail)
ADMIN_USER_ID = "904550642213875723"
CURRENT_SEASON = os.getenv("CURRENT_SEASON", "S11")
SEASON_START_DATE   = "2026-02-13"
SEASON_END_DATE     = "2026-04-24"


# League Constants
START_OF_DAY        = "T05%3A00%3A00.000Z"
END_OF_DAY          = "T04%3A59%3A59.999Z"
SEASON_START_DT     = SEASON_START_DATE + START_OF_DAY
SEASON_END_DT       = SEASON_END_DATE + END_OF_DAY


# Google Sheets Constants
LEAGUE_SPREADSHEET_ID = "1dSv5lzVwhot1DR0e2FghyS7R1Vm1ODZtH3d8e3C55Zo"

STANDINGS_SHEET_NAME        = CURRENT_SEASON + " Standings - User Reported"
EVENTS_SHEET_NAME           = CURRENT_SEASON + " Events - User Reported"
STANDINGS_RANGE_NAME        = STANDINGS_SHEET_NAME + "!" + "A3:F"
EVENTS_RANGE_NAME           = EVENTS_SHEET_NAME + "!" + "A2:G"
EVENTS_TIMESTAMP_RANGE_NAME = EVENTS_SHEET_NAME + "!" + "J1:K1"

# Discord channel names (production values)
# Override via .env locally to point at test channels, e.g.:
#   ANNOUNCEMENTS_CHANNEL=test-announcements
#   RESULTS_REPORTING_CHANNEL=test-results-reporting
ANNOUNCEMENTS_CHANNEL     = os.getenv("ANNOUNCEMENTS_CHANNEL", "announcements")
RESULTS_REPORTING_CHANNEL = os.getenv("RESULTS_REPORTING_CHANNEL", "results-reporting")
RESULTS_REPORTING_CHANNEL_URL = "https://discord.com/channels/1253915141716578314/"
RESULTS_CHANNEL           = "results"
DECKLISTS_CHANNEL         = "decklists"
WELCOME_CHANNEL           = "general"

# Roles members can self-assign via /rank
SELF_ASSIGN_ROLES = ["Casual", "Competitive", "Judge"]

# Regex to validate RPH event URLs submitted in results threads
EVENTS_URL_RE = r'https://tcg.ravensburgerplay.com/events/[0-9]+'
RPH_GAME_STORES_URL = "https://api.cloudflare.ravensburgerplay.com/hydraproxy/api/v2/game-stores/?"
RPH_EVENTS_URL      = "https://api.cloudflare.ravensburgerplay.com/hydraproxy/api/v2/events/?"
RPH_STANDINGS_URL   = "https://api.cloudflare.ravensburgerplay.com/hydraproxy/api/v2/tournament-rounds/{round_id}/standings"
# Override via .env locally, e.g. RPH_RETRY_DELAY=10 for faster testing
RPH_RETRY_ATTEMPTS = int(os.getenv("RPH_RETRY_ATTEMPTS", 2))
RPH_RETRY_DELAY    = int(os.getenv("RPH_RETRY_DELAY", 300))  # seconds

# Fetch upcoming_events.json from GitHub for the /schedule command
GITHUB_OWNER = "gtalorcana"
GITHUB_REPO  = "gtalorcana.ca"
UPCOMING_EVENTS_JSON_URL   = f"https://raw.githubusercontent.com/{GITHUB_OWNER}/{GITHUB_REPO}/main/data/upcoming_events.json"

# Scripts Constants
SET_CHAMPS_SPREADSHEET_ID = "1sF-TJ5ue5_sOCCpj9UlV_RvXbuJ9l2GwxTFVPthpCrc"
SET_CHAMPS_EVENTS_SHEET_NAME = CURRENT_SEASON + " Set Champs"
SET_CHAMPS_EVENTS_RANGE_NAME = SET_CHAMPS_EVENTS_SHEET_NAME + "!" + "A2:F"
SET_CHAMPS_START_DATE   = "2026-04-04"
SET_CHAMPS_END_DATE     = "2026-04-24"
SET_CHAMPS_START_DT     = SET_CHAMPS_START_DATE + START_OF_DAY
SET_CHAMPS_END_DT       = SET_CHAMPS_END_DATE + END_OF_DAY