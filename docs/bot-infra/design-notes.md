# Bot Infra — Design Notes

## Key Design Decisions

- Bot State sheet is key-value; all runtime state (message IDs, watches, recheck guards) lives there
- Each spreadsheet ID may init a separate Google Sheets client — audit `clients.py` for OOM risk if adding new spreadsheets

---

## Infrastructure Notes

- **Memory:** `analyse_stores()` and RPH event fetching are the heavy ops — `gc.collect()` calls are TODO until upgraded to 1GB RAM on Fly.io
- **Google Sheets clients:** each spreadsheet ID may init a separate client — OOM risk if the number of spreadsheets grows
- **Bot State scalability:** works fine for a single-server bot but won't scale to concurrent multi-server writes — replace with Postgres/SQLite/Redis when white-labelling

---

## constants.py Notes

- `WHERE_TO_PLAY_POST_DAY` / `WHERE_TO_PLAY_POST_HOUR_ET` — code config, keep in constants (can override via .env)
- `EVENTS_URL_RE`, `RPH_*` URLs — code config, keep in constants
