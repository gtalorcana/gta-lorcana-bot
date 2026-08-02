# Google Sheets

## Spreadsheets

| Spreadsheet | Purpose |
|------------|---------|
| League Sheet | Standings, events, leaderboard, and Set Champs — one set of tabs per season |
| Bot Database Sheet | Store classifications, debug data, overrides, bot state, player registry |
| Archive Sheet | Historical seasons (read-only after archiving via `/archive-season`) |

---

## League Sheet Tabs (per season)

Created by `/season-rollover`. `Standings` and `Events` are written by the results pipeline;
`Results` and `Leaderboard` are formula-driven (seeded once, then dragged down by the operator).

| Tab | Columns | Notes |
|-----|---------|-------|
| `S## Standings` | date, store, rank, display_name, record, match_points, playhub_id | A–G; one row per player per event |
| `S## Results` | **Player ID**, Players, 1st…10th, Points, Events Attended, Last Event…, Ranking… | A–P; Player ID (col A) `VLOOKUP`ed from Standings (display_name → playhub_id), mirroring the name spill in col B |
| `S## Leaderboard` | Rank, **Player ID**, Name, Points, Events Attended | A–E; row 1 = header, A auto-sequences (1..N) off the spill, B–E spill from a mask formula at `B2` |

> `/record-rare-and-uncommon` reads the Leaderboard by column position (`A2:E`) and matches players to the
> registry by **Player ID** (col B), falling back to Name only when the ID is blank — because RPH
> display names change over time but the Discord↔Playhub ID link is permanent.

**`Ban List`** — a persistent (non-season) tab in the League sheet. Banned players are listed by
**Playhub ID in column A** (col B is a free-text name for reference). The Leaderboard formula
excludes any Results row whose Player ID matches `Ban List!A2:A`. `/season-rollover` recreates the
tab (empty) if it's ever missing, since a broken reference would `#REF!` the whole Leaderboard.

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
