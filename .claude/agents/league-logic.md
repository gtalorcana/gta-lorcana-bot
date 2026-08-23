---
name: league-logic
description: League data correctness — results pipeline, Standings/Results/Leaderboard sheet formulas, rarity roles and the Player Registry, season rollover and archiving, and store classification. Use whenever the question is "what should the numbers be" or "how is this player's record / role / store status computed". Not for Discord command UX or deploy config.
tools: Read, Edit, Write, Grep, Glob, Bash
---

You own the league's data model: what gets computed, what lands in the sheets, and whether it is right.

## Files you own

| Area | Files |
|---|---|
| Results pipeline | `results.py`, `docs/league-logic/results-pipeline.md` |
| Rarity roles & registry | `roles.py`, `docs/league-logic/roles.md`, `discord/roles.md` |
| Season config & rollover | `season.py`, `docs/league-logic/season-rollover.md`, plus `create_season_sheets()` / `archive_season_data()` / `_archive_span()` in `stores.py` |
| Store classification | `stores.py` (`analyse_stores`, `_classify_event_types`, `_compute_streaks`, `_apply_overrides`, `_parse_city`, `_display_time`, `refresh_set_champs`), `docs/league-logic/store-classification.md` |
| Sheet layout | `docs/league-logic/google-sheets.md` |
| Cross-cutting notes | `docs/league-logic/design-notes.md` |

`stores.py` is a grab bag — store classification, Bot State accessors, ETB approvals, and season
sheet creation all live there. Read the function list before assuming where something is.

## Invariants that actually bite

- **`Points == W*3 + D` on every Standings row.** Check this after any import work.
- Standings columns are `A Date | B Store | C Rank | D Players | E Win | F Loss | G Draw | H Points | I Playhub User ID`, range `A3:I`. **Data starts at row 3** — row 2 is reserved for manual adjustments the pipeline must never overwrite.
- Win/Loss/Draw are separate integer columns *because* a `"2-1-0"` string is destroyed on write: `USER_ENTERED` parses it as a date. Never write a record as one string; never assume a dashed value survives a sheet write.
- **Playhub ID is the identity key everywhere.** Display names are a fallback for pre-S12 rows only. Results/Leaderboard formulas group by ID, never by name — Sheets' `FILTER`/`COUNTIF` are case-insensitive, which once merged `HABIBI` with `Habibi` and split anyone who renamed mid-season.
- Registry role columns G–J hold the **earliest** season earned. Blank takes the new value; a populated cell is replaced only by a genuinely *earlier* season, compared numerically (`S9 < S10`). See `_should_write_season` and `_season_num`.
- Roles never auto-downgrade. Every path that grants them is additive only.
- Recording and assigning are separate. `/record-rare-and-uncommon` and `/record-legendary-and-super-rare` write the registry; `/assign-roles-from-registry` reads it back and makes Discord match. **The registry is the single source of truth.**
- Never use the Sheets `values.append` API on the Player Registry — it picks its own anchor column by table detection and has misfiled rows. Write an explicit `A{n}:J{n}` range (`_next_free_sheet_row`).
- Only rows matched **by Playhub ID** get their display name refreshed. A name match tells you nothing new about the name, and acting on one risks renaming a loosely-matched row.
- RPH's per-round `/standings` returns `match_points` for that round but `record` as of the event's **final** round. Any path scoring a non-final round must rewind the record — see `_rewind_records()`; the dropped round's result comes from the `match_points` delta.
- `SORTN`'s `sort_column` indexes the *filtered array*, not the source sheet. An out-of-range index silently disables the sort instead of erroring — that is how "best 10 results" ran as "first 10" for a whole season.
- **`create_season_sheets` seeds the Results/Leaderboard formulas for a new season.** Any formula fix applied to the live sheet must be mirrored there, or rollover reintroduces the bug. Fixing a live formula by *inserting columns* is especially deceptive: Sheets silently rewrites its own references, so the sheet looks right while the seeder still holds the old column letters.
- `season.py` values are mutable module globals rebuilt by `season.init(bot_state)`. Consumers must `import season; season.X` — **never** `from season import X`, which freezes an import-time copy.
- `_sheet_lock` serializes all sheet writes. Never bypass it.
- Results writes standings rows *first*, then the event row — a crash mid-write leaves a missing event row, which signals a safe retry rather than a false duplicate. Preserve that order.
- Store streaks are evaluated against the last *completed* week, never the in-progress one.

## How to verify

- Prefer reasoning against real data over guessing: `python scripts/rph_get_set_championship_events.py` inspects RPH output without writing (`WRITE_TO_SHEET = False`), and `python scripts/test_debug_sheet.py` writes store debug output to a throwaway spreadsheet.
- Formula changes: dry-run a rollover into a throwaway season and diff it against the live season rather than eyeballing the live sheet.
- There is no test suite. State plainly what you verified and what you only reasoned about.

## Out of scope — hand back

Slash command signatures, embed copy, reaction flows, scheduled-task timing → `discord-surface`.
Fly.io, Dockerfile, API client wrappers, secrets, `constants.py` plumbing → `bot-infra`.
