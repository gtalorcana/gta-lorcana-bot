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


## RPH fetch width and memory

`RphApi.iter_events()` yields events page by page; `get_events()` is `list(iter_events(...))`.
The digests iterate and keep only what they match, so peak retention is one page plus the
matches rather than every event in the window. This matters most for the CCQ digest, the widest
fetch the bot runs.

Measured 2026-09-28, six-month upcoming window at 373 miles with both countries:

| Fetch | Events | ~Pages |
|---|---|---|
| CCQ, league format filter kept | 2,728 | 55 |
| CCQ, no format filter | 3,373 | 68 |
| CCQ, Canada only | 843 | 17 |

**`num_miles` defaults to 250 in `util/rph_api_utils.py` and must stay there.** That default
defines the store universe behind store classification, `#where-to-play` and results
eligibility; widening it would pull Quebec and US stores into all three, silently and with no
error. The 373-mile CCQ radius and its `countries=None` are per-fetch overrides in
`stores.fetch_ccqs()` — the only call site that passes either.
