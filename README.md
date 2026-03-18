# GTA Lorcana Bot

Discord bot for the Greater Toronto Area Lorcana community.

- Processes tournament results submitted by organizers and syncs standings to Google Sheets
- Auto-syncs `#announcements` posts to the community website via Cloudflare Worker
- Classifies Ontario stores by how regularly they run events and posts a weekly `#where-to-play` digest
- Posts daily who's-going polls in `#whos-going` for stores expected to run that day
- DMs subscribers when a spot opens at a full RPH event
- Assigns league rarity roles (Common → Uncommon → Rare → Super Rare → Legendary) based on participation and standings

---

## Project Structure

```
bot.py                                      # Main bot — events, slash commands, scheduled tasks
results.py                                  # Results reporting — processes RPH URLs, writes league standings
stores.py                                   # Store classification — RPH analysis, who's-going logic, set champs refresh
roles.py                                    # League rarity role management — player mapping, fuzzy matching, role sync
clients.py                                  # Shared API singletons (GoogleSheetsApi, RphApi) — instantiated once to avoid OOM
constants.py                                # All config — IDs, channel names, env var defaults
util/
  google_sheets_api_utils.py               # Google Sheets API wrapper (singleton)
  rph_api_utils.py                         # RPH API wrapper with pagination + retry
scripts/
  sync_commands.py                         # Syncs slash commands to guild (runs as Fly.io release_command)
  clear_global_commands.py                 # One-time script — clears legacy global Discord commands
  backfill_player_linking.py              # One-time script — post fuzzy-match suggestions for historical standings data
  rph_get_set_championship_events.py       # Manual run script — inspect/write set champs events
  test_debug_sheet.py                      # Local test script — runs analyse_stores() against a test spreadsheet
```

---

## Slash Commands

| Command | Who | Description |
|---------|-----|-------------|
| `/schedule` | Everyone | Upcoming events from the website |
| `/watch-rph-event` | Everyone | Subscribe to DM alerts when a spot opens at a full RPH event |
| `/unwatch-rph-event` | Everyone | Unsubscribe from a watched event |
| `/list-watches` | Everyone | Show all currently watched events and subscriber counts |
| `/help` | Everyone | List all commands |
| `/recheck` | Manage Guild | Reprocess any unhandled threads in `#results-reporting` |
| `/link @member playhub_id` | Manage Guild | Manually link a Discord member to a Playhub ID |
| `/sync-roles` | Manage Guild | Compute and apply Uncommon/Rare upgrades from current standings |
| `/assign-roles-from-invitational` | Manage Guild | Preview and assign Legendary/Super Rare from an invitational event |
| `/wheretoplay` | Admin | Manually trigger a `#where-to-play` refresh |
| `/testwhosgoing` | Admin | Manually post who's-going polls for a given date |

---

## Scheduled Tasks

| Task | When | What it does |
|------|------|-------------|
| `whos_going_daily` | Daily at 7 AM ET | Posts a who's-going poll per Regular store expected today |
| `where_to_play_weekly` | Sundays at 11 PM ET | Edits (or posts) the 3 `#where-to-play` messages |
| `set_champs_daily` | Daily at 7 AM ET, 2 weeks before `SET_CHAMPS_START_DATE` through `SET_CHAMPS_END_DATE` | Refreshes the Set Champs sheet from RPH |
| `rph_watcher` | Every 15 min | Checks watched events for open spots and DMs subscribers |
| `keepalive` | Every 30 min | Heartbeat log |

---

## League Rarity Roles

Members earn rarity roles based on league participation. Roles are additive — earning a higher tier does not remove the lower one.

| Role | How earned |
|------|------------|
| Common | Assigned automatically on server join |
| Uncommon | 10+ distinct events played in the season |
| Rare | Finished top 32 on the season leaderboard |
| Super Rare | Top finisher at an invitational event |
| Legendary | Winner of an invitational event |

**How it works:**

1. Every time results are processed, the bot fuzzy-matches any new Playhub players against Discord members and posts suggestions to the mod channel. Mods react ✅/❌ to confirm or skip each match.
2. At season end, run `/sync-roles` to compute and apply Uncommon/Rare upgrades based on the full standings.
3. After an invitational, run `/assign-roles-from-invitational <event_url>` to assign Super Rare / Legendary.

**Player mapping sheet (`Playhub <-> Discord IDs` in Store Sheet):**

| Column | Value |
|--------|-------|
| A | Discord user ID (0 = skipped/unmatched) |
| B | Playhub numeric ID |
| C | Display name |
| D | Linked timestamp (UTC ISO) |
| E | How it was linked (`fuzzy-confirmed`, `manual:<mod>`, `skipped`) |

**Backfill script** (run once after initial deploy to link historical data):
```bash
python scripts/backfill_player_linking.py
```
Posts suggestions one at a time to the mod channel. Safe to stop and restart — confirmed links are written immediately, so the script picks up where it left off.

---

## RPH Event Watcher

Users can subscribe to DM alerts for any RPH event that is full or filling up.

**Commands:**
- `/watch-rph-event event_id:413990 end_date:2026-03-29` — subscribe; bot confirms current registration status immediately
- `/unwatch-rph-event event_id:413990` — unsubscribe (other subscribers unaffected)
- `/list-watches` — see all active watches on the server

**How it works:**
Every 15 minutes, `rph_watcher` fetches each watched event from RPH and DMs all subscribers if `registered_user_count < capacity` and `queue_status == ACCEPTING_SIGNUPS`. Watches expire automatically after `end_date`.

**Finding the event ID:** it's the number at the end of the RPH event URL:
`https://tcg.ravensburgerplay.com/events/`**`413990`**

**Bot State:** each watch is stored as `rph_watch:<event_id>` → `{"name": "...", "end_date": "YYYY-MM-DD", "subscribers": [user_id, ...]}`.

> To restrict these commands to specific roles, use Discord's server settings: **Server Settings → Integrations → GTA Lorcana Bot** — no code changes needed.

---

## Set Championships

The `set_champs_daily` task calls `refresh_set_champs()` in `stores.py` every morning during the set champs window. It fetches all Ontario Lorcana events in the `SET_CHAMPS` date range (including upcoming and in-progress), filters to events whose name contains `"Set Champ"` (case-insensitive — matches "Set Championship", "Set Champs", etc.), and overwrites the Set Champs sheet.

**Set Champs sheet columns (A2:H):**
```
Date | Time (Toronto) | Store Name | Full Address | Player Cap | Format | Event Name | RPH Link
```

The task starts **2 weeks before** `SET_CHAMPS_START_DATE` so the sheet is populated ahead of time as stores register their events on RPH.

**Manual run** (inspect output before writing):
```bash
python scripts/rph_get_set_championship_events.py
```

Set `WRITE_TO_SHEET = True` in the script once the output looks correct. Set `NAME_FILTER = None` to see all events in the window and verify the filter keyword.

---

## Store Classification

Every Sunday, `analyse_stores()` in `stores.py`:

1. Fetches all Ontario Lorcana events for the current season from RPH
2. Groups events by `(store_id, day_of_week, floored_hour, format)` — events within the same clock hour are merged to handle organizers adjusting start times slightly week to week
3. Classifies each group based on streak
4. Applies manual overrides from the `Overrides` sheet tab
5. Saves results to `Store Classifications` (post-override) and `Store Debug` (pre-override, raw RPH data)

**Classification rules:**

| Status | Criteria |
|--------|----------|
| Regular | Consecutive streak ≥ 2 weeks |
| Semi-Regular | Ran at least once in the last 2 weeks AND at least twice total |
| *(unlisted)* | Everything else |

**Reference date:** Streaks are always evaluated against the last *completed* week, not the current in-progress one. This prevents a store from being demoted mid-week just because this week's event hasn't happened yet.

**Display time:** The most common raw start time across all events in the group. A `~` prefix is added when times vary (e.g. `~6:30 PM`). On a tie, the earliest time is shown — better to arrive early.

**City parsing:** City is extracted from the RPH `full_address` field by anchoring on the 2-letter province code (e.g. `ON`, `QC`) and taking the token immediately before it. Handles edge cases like missing commas before postal codes, lowercase city names, and multi-word cities (e.g. `Old Toronto`, `Greater Sudbury`).

---

## Overrides

The `Overrides` tab in the Store Sheet is manually maintained. The bot reads it on every `analyse_stores()` run and never writes to it.

**Sheet columns:**
```
store_id | store_name | day | time | format | override_status | override_day | override_time | reason
```

**Override types:**

| `override_status` | Behaviour |
|------------------|-----------|
| `Regular` | Force to Regular; optionally replace `day` and/or `time` |
| `Semi-Regular` | Force to Semi-Regular; optionally replace `day` and/or `time` |
| `Exclude` | Remove the entry entirely |
| `Add` | Inject a brand new entry — `override_day` and `override_time` required |

Match-based overrides (`Regular`, `Semi-Regular`, `Exclude`) match on exact `(store_id, day, time, format)`. These must match what's in the `Store Classifications` tab exactly.

`Add` overrides don't need a match — the `day` and `time` columns can be left blank. City is automatically looked up from the RPH event data if the store appears anywhere in the season.

**Examples:**

Fix a store showing the wrong day in RPH data:
```
1776 | Game 3 TCG & Hobby | Wednesday | 7:00 PM | Core Constructed | Regular | Tuesday | 6:30 PM | Bad RPH data
```

Manually add a store missing from RPH entirely:
```
1467 | Enter The Battlefield Newmarket |  |  | Core Constructed | Add | Wednesday | 7:00 PM | Missing from RPH
```

---

## Results Reporting Pipeline

1. Organizer creates a thread in `#results-reporting` with an RPH event URL as the first message
2. Bot validates the URL format, fetches event data and standings from RPH
3. Writes standings rows first, then the event row — so if a crash occurs mid-write, the missing event row signals a safe retry rather than a false duplicate
4. On validation error: posts feedback and waits for the organizer to edit — `on_message_edit` re-triggers automatically
5. On API error: schedules auto-retries (up to `RPH_RETRY_ATTEMPTS`, spaced `RPH_RETRY_DELAY` seconds apart)
6. If all retries fail: pings `ADMIN_USER_ID` in the thread
7. Deleting a thread removes its event data from the sheet via `on_thread_delete`
8. After a successful write, the bot fuzzy-matches any new Playhub players and posts linking suggestions to the mod channel

**Duplicate vs retry detection:** same URL + same thread = retry (allowed, overwrites). Same URL + different thread = true duplicate (rejected).

---

## Google Sheets

| Spreadsheet | Purpose |
|------------|---------|
| League Sheet | Standings and events — written by the bot on each results submission |
| Store Sheet | Store classifications, debug data, overrides, bot state, player mapping |
| Set Champs Sheet | Set Championship events — written daily by `set_champs_daily` during the window |

**Store Sheet tabs:**

| Tab | Columns | Written by |
|-----|---------|------------|
| `Store Classifications` | store_id, store_name, city, status, day, time, format, override | `analyse_stores()` every Sunday + `/wheretoplay` — post-override |
| `Store Debug` | store_id, store_name, city, full_address, day, floored_time, format, status, streak, week of \<date\> ×4, event_ids | `analyse_stores()` every run — pre-override, raw RPH data |
| `Overrides` | store_id, store_name, day, time, format, override_status, override_day, override_time, reason | Manual — never touched by bot |
| `Bot State` | key, value | Bot — see below |
| `Playhub <-> Discord IDs` | discord_id, playhub_id, display_name, linked_at, linked_by | Bot — player linking |

**Bot State keys:**

| Key | Value | Purpose |
|-----|-------|---------|
| `wtp_msg_0` / `wtp_msg_1` / `wtp_msg_2` | Discord message ID | Persists `#where-to-play` message IDs across restarts so the bot edits in-place rather than reposting |
| `recheck:<thread_id>` | `1` | Crash-loop guard — set before a startup recheck attempt, cleared on success |
| `rph_watch:<event_id>` | JSON `{name, end_date, subscribers: [user_id, ...]}` | Active event spot watchers — one key per watched event |

> **Tech debt:** Bot State in Google Sheets works fine for a single-server bot but won't scale to concurrent multi-server writes. When white-labelling, replace with a proper per-guild database (Postgres, SQLite, or Redis).

---

## Crash-Loop Prevention

On startup, the bot automatically rechecks any unprocessed threads from the last 3 days (threads without a ✅ reaction). This catches threads that were mid-flight when the bot crashed or restarted.

To prevent a bad thread from causing an infinite crash loop, the bot tracks each startup recheck attempt in Bot State:

1. Before processing a thread, `recheck:<thread_id>` is written to Bot State
2. If the bot crashes mid-processing and restarts, the key is already set
3. On the next startup, that thread is **skipped** — the bot adds ❌ and pings the admin instead
4. If processing completes successfully, the key is cleared

This means a bad thread will be attempted exactly once on startup. After that it requires manual intervention via `/recheck` or by deleting and resubmitting the thread.

---

## Deployment

### Fly.io Secrets

```
DISCORD_BOT_TOKEN
DISCORD_GUILD_ID
WORKER_URL
WORKER_SECRET
GOOGLE_CREDENTIALS_JSON
GOOGLE_TOKEN_JSON
MOD_CHANNEL_ID
COMMON_ROLE_ID
UNCOMMON_ROLE_ID
RARE_ROLE_ID
SUPER_RARE_ROLE_ID
LEGENDARY_ROLE_ID
```

Role IDs and `MOD_CHANNEL_ID` have hardcoded defaults in `constants.py` matching the production server — only need to be set as secrets if deploying to a different server.

Admin commands are accessible to anyone with Manage Guild permission, or any Discord user ID listed in `ADMIN_USER_IDS` in `constants.py`.

### Deploy

```bash
fly deploy
```

Slash commands are synced automatically on every deploy via the `release_command` in `fly.toml` — no manual step needed.

### Google Token Refresh

Google access tokens expire after ~1 hour and are refreshed automatically in-process. The underlying refresh token is long-lived but will eventually expire if unused for 6+ months or if revoked.

If the bot logs `401` or `invalid_grant`:

1. Delete `var/token.json` locally
2. Run `python bot.py` locally — a browser OAuth flow will regenerate it
3. Update the Fly.io secret:
   ```bash
   fly secrets set GOOGLE_TOKEN_JSON="$(cat var/token.json)" --app gta-lorcana-bot
   ```

---

## Local Development

```bash
pip install -r requirements.txt
```

Place `var/token.json` and `var/credentials.json` in the `var/` directory (gitignored).

Create a `.env` for local overrides:

```env
DISCORD_BOT_TOKEN=...
WORKER_URL=...
WORKER_SECRET=...
MOD_CHANNEL_ID=...
WHOS_GOING_POST_HOUR_ET=9
WHERE_TO_PLAY_POST_HOUR_ET=23
```

```bash
python bot.py
```

Use `/testwhosgoing` and `/wheretoplay` in Discord to trigger tasks manually without waiting for the schedule.

To test store debug sheet writes against a copy of the spreadsheet without touching production:
```bash
python scripts/test_debug_sheet.py
```
Set `TEST_STORE_SPREADSHEET_ID` in the script to a spreadsheet ID with a blank `Store Debug` tab.

---

## Optional Environment Variables

| Variable | Default | Notes |
|----------|---------|-------|
| `MOD_CHANNEL_ID` | *(hardcoded)* | Mod channel for linking suggestions and role previews |
| `COMMON_ROLE_ID` | *(hardcoded)* | |
| `UNCOMMON_ROLE_ID` | *(hardcoded)* | |
| `RARE_ROLE_ID` | *(hardcoded)* | |
| `SUPER_RARE_ROLE_ID` | *(hardcoded)* | |
| `LEGENDARY_ROLE_ID` | *(hardcoded)* | |
| `WHOS_GOING_POST_HOUR_ET` | `7` | Hour (ET) to post daily polls |
| `WHERE_TO_PLAY_POST_HOUR_ET` | `23` | Hour (ET) to post Sunday where-to-play |
| `CURRENT_SEASON` | `S11` | Used in sheet tab names |
| `RPH_RETRY_ATTEMPTS` | `2` | Auto-retry attempts on RPH API failure |
| `RPH_RETRY_DELAY` | `300` | Seconds between retries |

---

## Season Rollover

1. Update `SEASON_START_DATE`, `SEASON_END_DATE`, `CURRENT_SEASON`, `SET_CHAMPS_START_DATE`, and `SET_CHAMPS_END_DATE` in `constants.py`
2. Create new `S## Standings - User Reported`, `S## Events - User Reported`, and `S## Set Champs` tabs in the relevant sheets
3. Deploy: `fly deploy`
4. Run the backfill script to link any new players from historical data: `python scripts/backfill_player_linking.py`
5. At season end, run `/sync-roles` to assign Uncommon/Rare based on final standings
