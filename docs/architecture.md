# Architecture & Design Notes

## Key Design Decisions

- `ADMIN_USER_IDS` is a list (not set) — supports indexing for pings and `in` checks
- `_sheet_lock` serializes all sheet writes — never bypass it
- Bot State sheet is key-value; all runtime state (message IDs, watches, recheck guards) lives there
- Roles never auto-downgrade; role columns in Player Registry only written if blank (preserve earliest season earned)
- Each spreadsheet ID may init a separate Google Sheets client — audit `clients.py` for OOM risk if adding new spreadsheets

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

## Infrastructure Notes

- **Memory:** `analyse_stores()` and RPH event fetching are the heavy ops — `gc.collect()` calls are TODO until upgraded to 1GB RAM on Fly.io
- **Google Sheets clients:** each spreadsheet ID may init a separate client — OOM risk if the number of spreadsheets grows
- **Bot State scalability:** works fine for a single-server bot but won't scale to concurrent multi-server writes — replace with Postgres/SQLite/Redis when white-labelling

---

## Season Config (`season.py`)

Season values (dates, sheet names, range names) live in `season.py` as mutable module globals.
`season.init(bot_state)` is called at startup and by `/season-rollover` to rebuild all derived values.

All consumers must use `import season; season.X` — not `from season import X` — to get the live
call-time value rather than a frozen import-time copy.

Fallback values in `constants.py` are used when Bot State keys are absent.

## constants.py Notes

- `WHERE_TO_PLAY_POST_DAY` / `WHERE_TO_PLAY_POST_HOUR_ET` — code config, keep in constants (can override via .env)
- `EVENTS_URL_RE`, `RPH_*` URLs — code config, keep in constants
