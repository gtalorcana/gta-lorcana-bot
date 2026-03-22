# GTA Lorcana Bot — Session Context

See README.md for full architecture, commands, and deployment docs.

---

## Current Season

- Season: S11
- Start: 2026-02-13
- End: 2026-04-24
- Set Champs: 2026-04-04 → 2026-04-24

---

## TODO

### Season Rollover (next priority)
- Move `CURRENT_SEASON`, `SEASON_START_DATE`, `SEASON_END_DATE` from `constants.py` to the **Bot State sheet** so season changes don't require a redeploy
- Add a `/season-rollover` command (or script) that updates those Bot State keys and reloads derived sheet name constants in-memory
- Note: sheet name constants (`STANDINGS_SHEET_NAME` etc.) are built at module import time from `CURRENT_SEASON` — they'll need to be lazily resolved or rebuilt after a rollover

### Auto-edit Discord league-rules post
- Store the message ID of the league-rules post in Bot State
- `/season-rollover` (or a separate command) edits that message in-place from `discord/league-rules.md`

### constants.py cleanup (ongoing)
- `SELF_ASSIGN_ROLES` — already removed from constants, nothing left to do
- `WHERE_TO_PLAY_POST_DAY` / `WHERE_TO_PLAY_POST_HOUR_ET` — code config, keep in constants (can override via .env)
- `EVENTS_URL_RE`, `RPH_*` URLs — code config, keep in constants
- Sheet name/range constants — currently fine; will need refactor if CURRENT_SEASON moves to Bot State
- `ARCHIVE_SPREADSHEET_ID` — only used in scripts; could move to a scripts-specific block or separate file
- `SET_CHAMPS_SPREADSHEET_ID` — consider consolidating under `BOT_DATABASE_SPREADSHEET_ID` to reduce Google API client instances (OOM concern)

### Infrastructure
- Memory: `analyse_stores()` and RPH event fetching are the heavy ops — `gc.collect()` calls are TODO until upgraded to 1GB RAM on Fly.io
- Google Sheets clients: each spreadsheet ID may init a separate client — audit `clients.py` for OOM risk

---

## Key Design Notes

- `ADMIN_USER_IDS` is a list (not set) — supports indexing for pings and `in` checks
- `_sheet_lock` serializes all sheet writes — never bypass it
- Bot State sheet is key-value; all runtime state (message IDs, watches, recheck guards) lives there
- Roles never auto-downgrade; role columns in Player Registry only written if blank (preserve earliest season earned)
