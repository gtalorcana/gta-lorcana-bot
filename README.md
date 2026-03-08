# GTA Lorcana Bot

Discord bot for the Greater Toronto Area Lorcana community.

- Processes tournament results submitted by organizers and syncs standings to Google Sheets
- Auto-syncs `#announcements` posts to the community website via Cloudflare Worker
- Classifies Ontario stores by how regularly they run events and posts a weekly `#where-to-play` digest
- Posts daily who's-going polls in `#whos-going` for stores expected to run that day

---

## Project Structure

```
bot.py                              # Main bot — events, slash commands, scheduled tasks
results.py                          # Results reporting — processes RPH URLs, writes league standings
stores.py                           # Store classification — RPH analysis, who's-going logic, sheet persistence
constants.py                        # All config — IDs, channel names, env var defaults
util/
  google_sheets_api_utils.py        # Google Sheets API wrapper (singleton)
  rph_api_utils.py                  # RPH API wrapper with pagination + retry
scripts/
  bootstrap_where_to_play.py        # One-time season-start script — seeds store classifications
  sync_commands.py                  # Syncs slash commands to guild (runs as Fly.io release_command)
  clear_global_commands.py          # One-time script — clears legacy global Discord commands
```

---

## Slash Commands

| Command | Who | Description |
|---------|-----|-------------|
| `/schedule` | Everyone | Upcoming events from the website |
| `/decklist` | Everyone | Submit a decklist to `#decklists` |
| `/rank` | Everyone | Self-assign Casual / Competitive / Judge role |
| `/results` | Manage Events | Post tournament results to `#results` and sync to website |
| `/welcome @member` | Manage Guild | Manually welcome a member in `#general` |
| `/recheck` | Manage Guild | Reprocess any unhandled threads in `#results-reporting` |
| `/help` | Everyone | List all commands |
| `/wheretoplay` | Admin | Manually trigger a `#where-to-play` refresh |
| `/testwhosgoing` | Admin | Manually post who's-going polls for a given date |

---

## Scheduled Tasks

| Task | When | What it does |
|------|------|-------------|
| `whos_going_daily` | Daily at 7 AM ET | Posts a who's-going poll per Regular store expected today |
| `where_to_play_weekly` | Sundays at 6 PM ET | Edits (or posts) the 3 `#where-to-play` messages |
| `keepalive` | Every 30 min | Heartbeat log |

---

## Store Classification

Every Sunday, `analyse_stores()` in `stores.py`:

1. Fetches all Ontario Lorcana events for the current season from RPH
2. Groups events by `(store_id, day_of_week, floored_hour, format)` — events within the same clock hour are merged to handle organizers adjusting start times slightly week to week
3. Classifies each group based on streak
4. Applies manual overrides from the `Overrides` sheet tab
5. Saves results to `Store Classifications` and `Bootstrap Raw Data`

**Classification rules:**

| Status | Criteria |
|--------|----------|
| Regular | Consecutive streak ≥ 2 weeks |
| Semi-Regular | Ran at least once in the last 2 weeks AND at least twice total |
| *(unlisted)* | Everything else |

**Reference date:** Streaks are always evaluated against the last *completed* week, not the current in-progress one. This prevents a store from being demoted mid-week just because this week's event hasn't happened yet.

**Display time:** The most common raw start time across all events in the group. A `~` prefix is added when times vary (e.g. `~6:30 PM`). On a tie, the earliest time is shown — better to arrive early.

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

`Add` overrides don't need a match — the `day` and `time` columns can be left blank.

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
3. Writes event rows and standings to the League Sheet
4. On validation error: posts feedback and waits for the organizer to edit — `on_message_edit` re-triggers automatically
5. On API error: schedules auto-retries (up to `RPH_RETRY_ATTEMPTS`, spaced `RPH_RETRY_DELAY` seconds apart)
6. If all retries fail: pings `ADMIN_USER_ID` in the thread
7. Deleting a thread removes its event data from the sheet via `on_thread_delete`

---

## Google Sheets

| Spreadsheet | Purpose |
|------------|---------|
| League Sheet | Standings and events — written by the bot on each results submission |
| Store Sheet | Store classifications, raw event data, overrides, bot state |

**Store Sheet tabs:**

| Tab | Columns | Written by |
|-----|---------|------------|
| `Store Classifications` | store_id, store_name, status, streak, event_count, day, time, format, override | `analyse_stores()` every Sunday + `/wheretoplay` |
| `Bootstrap Raw Data` | store_id, store_name, day, floored_time, format, week_starts, raw_times | `analyse_stores()` every run |
| `Overrides` | store_id, store_name, day, time, format, override_status, override_day, override_time, reason | Manual — never touched by bot |
| `Bot State` | key, value | Bot — persists `wtp_msg_0/1/2` message IDs across restarts |

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
```

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

Create a `.env` and point at test channels to avoid touching production:

```env
DISCORD_BOT_TOKEN=...
WORKER_URL=...
WORKER_SECRET=...
ANNOUNCEMENTS_CHANNEL=test-announcements
RESULTS_REPORTING_CHANNEL=test-results-reporting
WHERE_TO_PLAY_CHANNEL=test-where-to-play
WHOS_GOING_CHANNEL=test-whos-going
WHOS_GOING_POST_HOUR_ET=9
```

```bash
python bot.py
```

Use `/testwhosgoing` and `/wheretoplay` in Discord to trigger tasks manually without waiting for the schedule.

---

## Optional Environment Variables

| Variable | Default | Notes |
|----------|---------|-------|
| `ANNOUNCEMENTS_CHANNEL` | `announcements` | |
| `RESULTS_REPORTING_CHANNEL` | `results-reporting` | |
| `WHERE_TO_PLAY_CHANNEL` | `where-to-play` | |
| `WHOS_GOING_CHANNEL` | `whos-going` | |
| `WHOS_GOING_POST_HOUR_ET` | `7` | Hour (ET) to post daily polls |
| `WHERE_TO_PLAY_POST_HOUR_ET` | `23` | Hour (ET) to post Sunday where-to-play |
| `CURRENT_SEASON` | `S11` | Used in sheet tab names |
| `RPH_RETRY_ATTEMPTS` | `2` | Auto-retry attempts on RPH API failure |
| `RPH_RETRY_DELAY` | `300` | Seconds between retries |

---

## Season Rollover

1. Update `SEASON_START_DATE`, `SEASON_END_DATE`, and `CURRENT_SEASON` in `constants.py`
2. Create new `S## Standings - User Reported` and `S## Events - User Reported` tabs in the League Sheet
3. Run the bootstrap script to seed store classifications:
   ```bash
   python scripts/bootstrap_where_to_play.py
   ```
4. Deploy: `fly deploy`

> **Do not re-run the bootstrap mid-season** — it overwrites the current classifications.
