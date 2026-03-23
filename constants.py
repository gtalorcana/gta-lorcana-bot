import os
from dotenv import load_dotenv
load_dotenv()

# Bot Secrets
DISCORD_BOT_TOKEN = os.getenv("DISCORD_BOT_TOKEN")
WORKER_URL        = os.getenv("WORKER_URL")
WORKER_SECRET     = os.getenv("WORKER_SECRET")

# Discord IDs
DISCORD_GUILD_ID = os.getenv("DISCORD_GUILD_ID", "1253915141716578314")

RESULTS_REPORTING_CHANNEL_URL = "https://discord.com/channels/" + DISCORD_GUILD_ID +"/"
MOD_CHANNEL_ID = int(os.getenv("MOD_CHANNEL_ID", 1483753550960922717))
CHANNELS = {
    "announcements": 1256090387978784778,
    "results_reporting": 1253943193519784028,
    "where_to_play": 1479988278164852746
}

# League rarity role IDs — override via .env for local testing
COMMON_ROLE_ID     = int(os.getenv("COMMON_ROLE_ID",     1347683977040891966))
UNCOMMON_ROLE_ID   = int(os.getenv("UNCOMMON_ROLE_ID",   1347683778318827656))
RARE_ROLE_ID       = int(os.getenv("RARE_ROLE_ID",       1347683773256568964))
SUPER_RARE_ROLE_ID = int(os.getenv("SUPER_RARE_ROLE_ID", 1347683770815479819))
LEGENDARY_ROLE_ID  = int(os.getenv("LEGENDARY_ROLE_ID",  1347683767367503953))

# All accounts authorised to run admin/mod bot commands
ADMIN_USER_IDS = [904550642213875723, 361716209324130305]

# Where-to-Play settings
WHERE_TO_PLAY_POST_DAY        = 6   # Sunday (0=Mon … 6=Sun)
WHERE_TO_PLAY_POST_HOUR_ET    = int(os.getenv("WHERE_TO_PLAY_POST_HOUR_ET", 23))  # 11PM ET Sunday
WHERE_TO_PLAY_MIN_CONSECUTIVE_WEEKS    = 2   # weeks in a row to become Regular

# Regex to validate RPH event URLs submitted in results threads
EVENTS_URL_RE = r'https://tcg.ravensburgerplay.com/events/[0-9]+'
RPH_GAME_STORES_URL = "https://api.cloudflare.ravensburgerplay.com/hydraproxy/api/v2/game-stores/?"
RPH_EVENTS_URL      = "https://api.cloudflare.ravensburgerplay.com/hydraproxy/api/v2/events/?"
RPH_STANDINGS_URL   = "https://api.cloudflare.ravensburgerplay.com/hydraproxy/api/v2/tournament-rounds/{round_id}/standings"
RPH_USERS_URL        = "https://api.cloudflare.ravensburgerplay.com/hydraproxy/api/v2/users/"
SHOPIFY_CLIENT_ID    = os.getenv("SHOPIFY_CLIENT_ID")
SHOPIFY_STORE_DOMAIN = os.getenv("SHOPIFY_STORE_DOMAIN", "enterthebattlefield.myshopify.com")
RPH_RETRY_ATTEMPTS = int(os.getenv("RPH_RETRY_ATTEMPTS", 2))
RPH_RETRY_DELAY    = int(os.getenv("RPH_RETRY_DELAY", 300))

# Get events.json from Github
GITHUB_OWNER = "gtalorcana"
GITHUB_REPO  = "gtalorcana.ca"
UPCOMING_EVENTS_JSON_URL   = f"https://raw.githubusercontent.com/{GITHUB_OWNER}/{GITHUB_REPO}/main/data/upcoming_events.json"

# League Constants — fallback values used by season.py
# These are overridden at runtime by Bot State values via season.init()
CURRENT_SEASON      = os.getenv("CURRENT_SEASON", "S11")

# RPH API date suffix helpers — used by season.py to build datetime strings
START_OF_DAY = "T05%3A00%3A00.000Z"
END_OF_DAY   = "T04%3A59%3A59.999Z"

# Google Sheets Constants
LEAGUE_SPREADSHEET_ID = "1dSv5lzVwhot1DR0e2FghyS7R1Vm1ODZtH3d8e3C55Zo"

# Separate spreadsheet for bot backend data
BOT_DATABASE_SPREADSHEET_ID  = "1cKiZqVu88_umUbrGPXk-dmZhHEzx1uL-pGR6dQOJyaU"

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

# Player Registry — combined mapping + role audit (STORE_SPREADSHEET_ID)
# Columns (A–J): Playhub Name | Legendary | Super Rare | Rare | Uncommon |
#                Discord ID | Discord Display Name | Playhub ID | Linked At | Link Method
PLAYER_REGISTRY_SHEET_NAME = "Player Registry"
PLAYER_REGISTRY_RANGE_NAME = PLAYER_REGISTRY_SHEET_NAME + "!A2:J"

# Scripts Constants

# Archive spreadsheet — historical seasons (S1–S10)
ARCHIVE_SPREADSHEET_ID = "1382ddPYx3dRKDTvSd60jiu4Yd_-SwkBTRy0djt84F2o"

# Historical invitational results (manually maintained)
INVITATIONAL_RESULTS_SHEET_NAME = "Invitational Results"
INVITATIONAL_RESULTS_RANGE_NAME = INVITATIONAL_RESULTS_SHEET_NAME + "!A2:C"  # Season, Player Name, Finish

