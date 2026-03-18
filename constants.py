import os
from dotenv import load_dotenv
load_dotenv()

# Bot Secrets
DISCORD_BOT_TOKEN = os.getenv("DISCORD_BOT_TOKEN")
WORKER_URL        = os.getenv("WORKER_URL")
WORKER_SECRET     = os.getenv("WORKER_SECRET")

# League Variables
# Discord user ID of the bot admin (pinged when all auto-retries fail)
ADMIN_USER_ID = 904550642213875723
CURRENT_SEASON = os.getenv("CURRENT_SEASON", "S11")
SEASON_START_DATE   = "2026-02-13"
SEASON_END_DATE     = "2026-04-24"

# League Constants
START_OF_DAY    = "T05%3A00%3A00.000Z"
END_OF_DAY      = "T04%3A59%3A59.999Z"
SEASON_START_DT = SEASON_START_DATE + START_OF_DAY
SEASON_END_DT   = SEASON_END_DATE + END_OF_DAY


# Google Sheets Constants
LEAGUE_SPREADSHEET_ID = "1dSv5lzVwhot1DR0e2FghyS7R1Vm1ODZtH3d8e3C55Zo"

# Separate spreadsheet for store event data (classifications, raw debug data, overrides)
STORE_SPREADSHEET_ID  = "1cKiZqVu88_umUbrGPXk-dmZhHEzx1uL-pGR6dQOJyaU"

STORE_CLASSIFICATIONS_SHEET_NAME  = "Store Classifications"
STORE_CLASSIFICATIONS_RANGE_NAME  = STORE_CLASSIFICATIONS_SHEET_NAME + "!A1:H"

# Overrides tab — manually maintained, never overwritten by the bot
# Columns: store_id | store_name | day | time | format | override_status | reason
STORE_OVERRIDES_SHEET_NAME  = "Overrides"
STORE_OVERRIDES_RANGE_NAME  = STORE_OVERRIDES_SHEET_NAME + "!A1:I"

# Bot state — persists runtime values (e.g. message IDs) across restarts
BOT_STATE_SHEET_NAME  = "Bot State"
BOT_STATE_RANGE_NAME  = BOT_STATE_SHEET_NAME + "!A1:B"

STORE_DEBUG_SHEET_NAME = "Store Debug"
STORE_DEBUG_RANGE_NAME = STORE_DEBUG_SHEET_NAME + "!A1:Z"  # wide enough for 4 week columns + fixed cols

STANDINGS_SHEET_NAME        = CURRENT_SEASON + " Standings - User Reported"
EVENTS_SHEET_NAME           = CURRENT_SEASON + " Events - User Reported"
STANDINGS_RANGE_NAME        = STANDINGS_SHEET_NAME + "!" + "A3:G"
EVENTS_RANGE_NAME           = EVENTS_SHEET_NAME + "!" + "A2:G"
EVENTS_TIMESTAMP_RANGE_NAME = EVENTS_SHEET_NAME + "!" + "J1:K1"

# Discord channel IDs
RESULTS_REPORTING_CHANNEL_URL = "https://discord.com/channels/1253915141716578314/"
MOD_CHANNEL_ID = int(os.getenv("MOD_CHANNEL_ID", 0))
CHANNELS = {
    "announcements": 1256090387978784778,
    "results_reporting": 1253943193519784028,
    "where_to_play": 1479988278164852746
}

# League rarity role IDs — set as env vars or Fly.io secrets
COMMON_ROLE_ID     = int(os.getenv("COMMON_ROLE_ID",     0))
UNCOMMON_ROLE_ID   = int(os.getenv("UNCOMMON_ROLE_ID",   0))
RARE_ROLE_ID       = int(os.getenv("RARE_ROLE_ID",       0))
SUPER_RARE_ROLE_ID = int(os.getenv("SUPER_RARE_ROLE_ID", 0))
LEGENDARY_ROLE_ID  = int(os.getenv("LEGENDARY_ROLE_ID",  0))

# Player mapping sheet (STORE_SPREADSHEET_ID — internal, not the public league sheet)
PLAYER_MAPPING_SHEET_NAME = "Playhub <-> Discord IDs"
PLAYER_MAPPING_RANGE_NAME = PLAYER_MAPPING_SHEET_NAME + "!A2:E"

# Who's-Going & Where-to-Play settings
# Override via .env for local testing, e.g. WHOS_GOING_POST_HOUR_ET=9
WHERE_TO_PLAY_POST_DAY        = 6   # Sunday (0=Mon … 6=Sun)
WHERE_TO_PLAY_POST_HOUR_ET    = int(os.getenv("WHERE_TO_PLAY_POST_HOUR_ET", 23))  # 11PM ET Sunday
WHERE_TO_PLAY_MIN_CONSECUTIVE_WEEKS    = 2   # weeks in a row to become Regular

# Roles members can self-assign via /rank
SELF_ASSIGN_ROLES = ["Casual", "Competitive", "Judge"]

# Regex to validate RPH event URLs submitted in results threads
EVENTS_URL_RE = r'https://tcg.ravensburgerplay.com/events/[0-9]+'
RPH_GAME_STORES_URL = "https://api.cloudflare.ravensburgerplay.com/hydraproxy/api/v2/game-stores/?"
RPH_EVENTS_URL      = "https://api.cloudflare.ravensburgerplay.com/hydraproxy/api/v2/events/?"
RPH_STANDINGS_URL   = "https://api.cloudflare.ravensburgerplay.com/hydraproxy/api/v2/tournament-rounds/{round_id}/standings"
RPH_RETRY_ATTEMPTS = int(os.getenv("RPH_RETRY_ATTEMPTS", 2))
RPH_RETRY_DELAY    = int(os.getenv("RPH_RETRY_DELAY", 300))

# Get events.json from Github
GITHUB_OWNER = "gtalorcana"
GITHUB_REPO  = "gtalorcana.ca"
UPCOMING_EVENTS_JSON_URL   = f"https://raw.githubusercontent.com/{GITHUB_OWNER}/{GITHUB_REPO}/main/data/upcoming_events.json"

# Scripts Constants
SET_CHAMPS_SPREADSHEET_ID = "1sF-TJ5ue5_sOCCpj9UlV_RvXbuJ9l2GwxTFVPthpCrc"
SET_CHAMPS_EVENTS_SHEET_NAME = CURRENT_SEASON + " Set Champs"
SET_CHAMPS_EVENTS_RANGE_NAME = SET_CHAMPS_EVENTS_SHEET_NAME + "!" + "A2:H"
SET_CHAMPS_START_DATE   = "2026-04-04"
SET_CHAMPS_END_DATE     = "2026-04-24"
SET_CHAMPS_START_DT     = SET_CHAMPS_START_DATE + START_OF_DAY
SET_CHAMPS_END_DT       = SET_CHAMPS_END_DATE + END_OF_DAY