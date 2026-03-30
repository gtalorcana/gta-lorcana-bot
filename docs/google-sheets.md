# Google Sheets

## Spreadsheets

| Spreadsheet | Purpose |
|------------|---------|
| League Sheet | Standings, events, leaderboard, and Set Champs — one set of tabs per season |
| Bot Database Sheet | Store classifications, debug data, overrides, bot state, player registry |
| Archive Sheet | Historical seasons (read-only after archiving via `/archive-season`) |

---

## Bot Database Sheet Tabs

| Tab | Columns | Written by |
|-----|---------|------------|
| `Store Classifications` | store_id, store_name, city, status, day, time, format, override | `analyse_stores()` every Sunday + `/wheretoplay` — post-override |
| `Store Debug` | store_id, store_name, city, full_address, day, floored_time, format, status, streak, week of \<date\> ×4, event_ids | `analyse_stores()` every run — pre-override, raw RPH data |
| `Overrides` | store_id, store_name, day, time, format, override_status, override_day, override_time, reason | Manual — never touched by bot |
| `Bot State` | key, value | Bot — see below |
| `Player Registry` | See [roles.md](roles.md) | Bot — player linking + role audit |

---

## Bot State Keys

| Key | Value | Purpose |
|-----|-------|---------|
| `season` | `S11` | Current season identifier |
| `season_start_date` | `2026-02-13` | Season start date (used to filter RPH events) |
| `season_end_date` | `2026-04-24` | Season end date |
| `set_champs_start_date` | `2026-04-04` | Set Champs window start |
| `set_champs_end_date` | `2026-04-26` | Set Champs window end |
| `wtp_msg_0` / `wtp_msg_1` / `wtp_msg_2` | Discord message ID | Persists `#where-to-play` message IDs across restarts so the bot edits in-place rather than reposting |
| `recheck:<thread_id>` | `1` | Crash-loop guard — set before a startup recheck attempt, cleared on success |
| `rph_watch:<event_id>` | JSON `{name, end_date, subscribers: [user_id, ...]}` | Active event spot watchers — one key per watched event |

> When entering dates manually, prefix with `'` (e.g. `'2026-02-13`) to prevent Google Sheets from converting to a date serial number.

> **Tech debt:** Bot State in Google Sheets works fine for a single-server bot but won't scale to concurrent multi-server writes. When white-labelling, replace with a proper per-guild database (Postgres, SQLite, or Redis).
